"""Ponte OpenAI chat/completions -> API de agentes do Langflow (Responses API).

Por que existe: o Nod (e outros clientes "OpenAI-compatible") chama POST /v1/chat/completions
com o histórico inteiro e com `tools`; o /api/v1/responses do Langflow 1.13 aceita só `input`
em texto, recusa `tools` ("Tools are not supported yet") e faz stream num formato próprio
(object "response.chunk"). A ponte traduz:

- pedido COM tools (turno de conversa do agente do Nod) -> Frente do Langflow, só a última
  mensagem do usuário; a sessão do Langflow (previous_response_id) é derivada da primeira
  mensagem do usuário da conversa, então cada conversa do Nod vira uma sessão da Frente.
  As ferramentas do Nod não são usadas: quem age são os especialistas do Langflow.
- pedido SEM tools (tarefas auxiliares do Nod: checagem do modelo, memória, revisões em JSON)
  -> repassado igual ao LiteLLM com um modelo comum (AUX_MODEL), para o Nod não quebrar.

Só biblioteca padrão do Python. Configuração por variáveis de ambiente (segredos só no env).
"""
import hashlib
import http.client
import json
import os
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8645"))
LANGFLOW_URL = os.environ.get("LANGFLOW_URL", "http://langflow_app_1:7860").rstrip("/")
LANGFLOW_API_KEY = os.environ.get("LANGFLOW_API_KEY", "")
FLOW_ID = os.environ.get("FLOW_ID", "ae20424f-6b22-4df3-9c63-ba911804c2da")
MODEL_ID = os.environ.get("MODEL_ID", "agentes-nonve")
CONTEXT = int(os.environ.get("CONTEXT_LENGTH", "200000"))
BRIDGE_KEY = os.environ.get("BRIDGE_KEY", "")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://thainan-litellm_web_1:4000/v1").rstrip("/")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "")
AUX_MODEL = os.environ.get("AUX_MODEL", "gemini-3.8-flash-high")
DEBUG = os.environ.get("DEBUG", "") == "1"
KEEPALIVE = 15


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def text_of(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, dict) and p.get("type") in ("text", "input_text"):
                out.append(p.get("text") or "")
            elif isinstance(p, dict) and p.get("type") in ("image_url", "input_image"):
                out.append("[imagem anexada: a API de agentes recebe só texto por esta ponte]")
        return "\n".join(x for x in out if x)
    return str(content)


def conn_for(url, timeout):
    u = urllib.parse.urlparse(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    return cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=timeout), u.path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ponte-agentes/1.0"

    def log_message(self, fmt, *args):
        if DEBUG:
            log("http", fmt % args)

    # utilidades
    def send_json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authorized(self):
        if not BRIDGE_KEY:
            return True
        h = self.headers.get("Authorization", "")
        return h == "Bearer " + BRIDGE_KEY

    def start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def write_chunk(self, data: bytes):
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def end_chunks(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # rotas
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/health"):
            self.send_json(200, {"ok": True, "modelo": MODEL_ID, "flow": FLOW_ID, "aux": AUX_MODEL})
            return
        if path in ("/v1/models", "/models"):
            if not self.authorized():
                self.send_json(401, {"error": {"message": "chave inválida"}})
                return
            self.send_json(200, {"object": "list", "data": [{
                "id": MODEL_ID, "object": "model", "owned_by": "langflow-nonve",
                "created": 0, "context_length": CONTEXT}]})
            return
        self.send_json(404, {"error": {"message": "não encontrado"}})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self.send_json(404, {"error": {"message": "não encontrado"}})
            return
        if not self.authorized():
            self.send_json(401, {"error": {"message": "chave inválida"}})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self.send_json(400, {"error": {"message": "JSON inválido"}})
            return
        msgs = body.get("messages") or []
        if DEBUG:
            log("pedido", "tools=%d" % len(body.get("tools") or []), "stream=%s" % body.get("stream"),
                [(m.get("role"), len(text_of(m.get("content")))) for m in msgs][-8:])
        if body.get("tools"):
            self.agent(body, msgs)
        else:
            self.aux(body)

    # turno de conversa -> Frente do Langflow
    def agent(self, body, msgs):
        users = [m for m in msgs if m.get("role") == "user"]
        if not users:
            self.send_json(400, {"error": {"message": "sem mensagem do usuário"}})
            return
        first = text_of(users[0].get("content"))
        last = text_of(users[-1].get("content"))
        session = "nod-" + hashlib.sha256(first.encode()).hexdigest()[:24]
        stream = bool(body.get("stream"))
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        t0 = time.time()
        log("frente", "sessão", session, "turno", len(users), "chars", len(last), "stream", stream)
        if DEBUG:
            log("ultima", last[:400].replace("\n", " | "))

        payload = json.dumps({"model": FLOW_ID, "input": last, "stream": True,
                              "previous_response_id": session}).encode()
        c, base = conn_for(LANGFLOW_URL, 1800)
        try:
            c.request("POST", base + "/api/v1/responses", body=payload,
                      headers={"Content-Type": "application/json", "x-api-key": LANGFLOW_API_KEY})
            r = c.getresponse()
        except Exception as e:
            self.send_json(502, {"error": {"message": f"Langflow fora do ar: {e}"}})
            return
        if r.status != 200:
            txt = r.read().decode(errors="replace")[:800]
            self.send_json(502, {"error": {"message": f"Langflow {r.status}: {txt}"}})
            return

        def chunk(delta, finish=None):
            return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": MODEL_ID,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        parts = []
        finish = "stop"
        lock = threading.Lock()
        done = threading.Event()
        if stream:
            self.start_sse()
            self.write_chunk(b"data: " + json.dumps(chunk({"role": "assistant", "content": ""})).encode() + b"\n\n")

            def ping():
                while not done.wait(KEEPALIVE):
                    with lock:
                        try:
                            self.write_chunk(b": aguardando a API de agentes\n\n")
                        except Exception:
                            return
            threading.Thread(target=ping, daemon=True).start()
        try:
            buf = b""
            while True:
                line = r.readline()
                if not line:
                    break
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except Exception:
                    continue
                if ev.get("object") != "response.chunk":
                    continue
                piece = (ev.get("delta") or {}).get("content") or ""
                if ev.get("finish_reason") == "error":
                    finish = "stop"
                    piece = "\n\n[erro na API de agentes] " + piece
                if piece:
                    parts.append(piece)
                    if stream:
                        with lock:
                            self.write_chunk(b"data: " + json.dumps(chunk({"content": piece}), ensure_ascii=False).encode() + b"\n\n")
        finally:
            done.set()
            c.close()
        text = "".join(parts)
        dt = time.time() - t0
        log("frente", "fim", session, "%.1fs" % dt, "chars", len(text))
        usage = {"prompt_tokens": max(1, len(last) // 4), "completion_tokens": max(1, len(text) // 4)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        if stream:
            with lock:
                fin = chunk({}, finish)
                if (body.get("stream_options") or {}).get("include_usage"):
                    fin["usage"] = usage
                self.write_chunk(b"data: " + json.dumps(fin).encode() + b"\n\n")
                self.write_chunk(b"data: [DONE]\n\n")
                self.end_chunks()
        else:
            self.send_json(200, {"id": cid, "object": "chat.completion", "created": created, "model": MODEL_ID,
                                 "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                              "finish_reason": finish}], "usage": usage})

    # tarefas auxiliares do Nod -> LiteLLM, repasse direto
    def aux(self, body):
        body = dict(body)
        body["model"] = AUX_MODEL
        for k in ("tools", "tool_choice", "parallel_tool_calls"):
            body.pop(k, None)
        payload = json.dumps(body).encode()
        c, base = conn_for(LITELLM_URL, 600)
        t0 = time.time()
        try:
            c.request("POST", base + "/chat/completions", body=payload,
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + LITELLM_KEY})
            r = c.getresponse()
        except Exception as e:
            self.send_json(502, {"error": {"message": f"LiteLLM fora do ar: {e}"}})
            return
        try:
            if body.get("stream") and r.status == 200:
                self.start_sse()
                while True:
                    b = r.read1(65536) if hasattr(r, "read1") else r.read(4096)
                    if not b:
                        break
                    self.write_chunk(b)
                self.end_chunks()
            else:
                b = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", r.getheader("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        finally:
            c.close()
            log("aux", AUX_MODEL, r.status, "%.1fs" % (time.time() - t0))


if __name__ == "__main__":
    if not LANGFLOW_API_KEY:
        log("aviso: LANGFLOW_API_KEY vazio (pôr no ambiente personalizado do app no Umbrel)")
    if not LITELLM_KEY:
        log("aviso: LITELLM_KEY vazio (tarefas auxiliares do Nod vão falhar)")
    log("ponte ouvindo na porta", PORT, "modelo", MODEL_ID, "flow", FLOW_ID)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
