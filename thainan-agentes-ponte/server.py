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
VERSAO = "1.1.0"
# Registro de clientes (1.1.0): uma linha JSON por requisição, no volume do app. Só observa:
# não muda a resposta. Nunca grava Authorization, cookies nem chaves (mascarados), nem o texto
# das mensagens do usuário (só contagens, tipos de parte e o começo do prompt de sistema).
REGISTRO = os.environ.get("REGISTRO", "1") != "0"
REGISTRO_ARQ = os.environ.get("REGISTRO_ARQ", "/data/registro/clientes.jsonl")
REGISTRO_MAX = int(os.environ.get("REGISTRO_MAX_BYTES", str(20 * 1024 * 1024)))
_reg_lock = threading.Lock()
SENSIVEIS = ("authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key")
PALAVRAS_SENSIVEIS = ("key", "token", "secret", "auth", "cookie", "password", "senha", "session")


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


def nod_instruction(text):
    """O Nod embrulha o pedido: preâmbulo + JSON com "currentInstruction" (o texto do dono),
    "mode", "recentConversation" etc. Devolve (pedido, modo) ou (None, None) se não achar."""
    i = text.find('{"mode":')
    if i < 0:
        return None, None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, i)
    except Exception:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    ci = obj.get("currentInstruction")
    return (ci if isinstance(ci, str) else None), obj.get("mode")


def mascarar(texto):
    """Troca por *** qualquer trecho com cara de chave (sk-..., Bearer ..., longos alfanuméricos)."""
    import re
    texto = re.sub(r"(?i)bearer\s+\S+", "Bearer ***", texto)
    texto = re.sub(r"\b(sk|pk|rk|lf|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{8,}", "***", texto)
    texto = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "***", texto)
    return texto


def cabecalhos_seguros(headers):
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in SENSIVEIS or any(w in kl for w in PALAVRAS_SENSIVEIS):
            out[k] = "*** (%d caracteres)" % len(v or "")
        else:
            out[k] = mascarar(v or "")[:300]
    return out


def resumo_corpo(body):
    """Só a forma do pedido: nada do conteúdo do dono."""
    msgs = body.get("messages") or []
    papeis, partes = {}, {}
    tool_calls = 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        papeis[m.get("role")] = papeis.get(m.get("role"), 0) + 1
        c = m.get("content")
        if isinstance(c, list):
            for p in c:
                t = p.get("type") if isinstance(p, dict) else type(p).__name__
                partes[t] = partes.get(t, 0) + 1
        elif isinstance(c, str):
            partes["string"] = partes.get("string", 0) + 1
        tool_calls += len(m.get("tool_calls") or [])
    sistema = next((text_of(m.get("content")) for m in msgs
                    if isinstance(m, dict) and m.get("role") in ("system", "developer")), "")
    ultimo_user = next((text_of(m.get("content")) for m in reversed(msgs)
                        if isinstance(m, dict) and m.get("role") == "user"), "")
    _, modo = nod_instruction(ultimo_user) if ultimo_user else (None, None)
    tools = body.get("tools") or []
    nomes = []
    for t in tools:
        if isinstance(t, dict):
            f = t.get("function") if isinstance(t.get("function"), dict) else t
            nomes.append(str(f.get("name") or t.get("type") or "?")[:80])
    imagens = partes.get("image_url", 0) + partes.get("input_image", 0)
    rf = body.get("response_format")
    return {
        "modelo": body.get("model"),
        "stream": bool(body.get("stream")),
        "stream_options": body.get("stream_options"),
        "tools": {"presente": bool(tools), "qtd": len(tools), "nomes": nomes},
        "tool_choice": body.get("tool_choice") if isinstance(body.get("tool_choice"), str) else (
            "objeto" if body.get("tool_choice") else None),
        "parallel_tool_calls": body.get("parallel_tool_calls"),
        "imagem": {"presente": imagens > 0, "partes": imagens},
        "partes": partes,
        "mensagens": {"total": len(msgs), "papeis": papeis, "tool_calls_assistente": tool_calls},
        "sistema_inicio": mascarar(sistema[:160]).replace("\n", " | "),
        "sistema_chars": len(sistema),
        "embrulho_nod": {"presente": modo is not None, "modo": modo},
        "chaves_corpo": sorted(body.keys()),
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
        "reasoning_effort": body.get("reasoning_effort"),
        "response_format": rf.get("type") if isinstance(rf, dict) else rf,
    }


def registrar(entrada):
    if not REGISTRO:
        return
    try:
        linha = json.dumps(entrada, ensure_ascii=False, default=str) + "\n"
        with _reg_lock:
            os.makedirs(os.path.dirname(REGISTRO_ARQ), exist_ok=True)
            try:
                if os.path.getsize(REGISTRO_ARQ) > REGISTRO_MAX:
                    os.replace(REGISTRO_ARQ, REGISTRO_ARQ + ".1")
            except FileNotFoundError:
                pass
            with open(REGISTRO_ARQ, "a", encoding="utf-8") as f:
                f.write(linha)
    except Exception as e:  # o registro nunca derruba a resposta
        log("registro falhou:", e)


def ultimas_linhas(n):
    try:
        with open(REGISTRO_ARQ, "rb") as f:
            f.seek(0, 2)
            tam = f.tell()
            f.seek(max(0, tam - 2 * 1024 * 1024))
            linhas = f.read().decode(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for l in linhas[-n:]:
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def conn_for(url, timeout):
    u = urllib.parse.urlparse(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    return cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=timeout), u.path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ponte-agentes/1.1"

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

    def entrada_base(self, path):
        return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S-03:00", time.gmtime(time.time() - 3 * 3600)), "versao": VERSAO,
                "metodo": self.command, "rota": path, "origem": self.client_address[0],
                "user_agent": self.headers.get("User-Agent"),
                "cabecalhos": cabecalhos_seguros(self.headers),
                "chave_ok": self.authorized()}

    # rotas
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path in ("/v1/registro", "/registro"):
            if not self.authorized():
                self.send_json(401, {"error": {"message": "chave inválida"}})
                return
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            n = max(1, min(500, int((q.get("n") or ["50"])[0])))
            self.send_json(200, {"arquivo": REGISTRO_ARQ, "linhas": ultimas_linhas(n)})
            return
        if path not in ("", "/health"):
            registrar(self.entrada_base(path))
        if path in ("", "/health"):
            self.send_json(200, {"ok": True, "versao": VERSAO, "modelo": MODEL_ID, "flow": FLOW_ID,
                                 "aux": AUX_MODEL, "registro": REGISTRO_ARQ if REGISTRO else None})
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
            registrar(self.entrada_base(path))
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
        try:
            e = self.entrada_base(path)
            e["corpo"] = resumo_corpo(body)
            ult = next((text_of(m.get("content")) for m in reversed(msgs)
                        if isinstance(m, dict) and m.get("role") == "user"), "")
            ped, _ = nod_instruction(ult) if ult else (None, None)
            # rota que a ponte vai seguir (a decisão abaixo continua igual à 1.0.1)
            e["rota_ponte"] = ("frente" if (body.get("tools") and not (ped is not None and not ped.strip()))
                               else "auxiliar")
            registrar(e)
        except Exception as ex:
            log("registro falhou:", ex)
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
        pedido, modo = nod_instruction(last)
        if pedido is not None:
            if not pedido.strip():
                # turno sem pedido do dono (trabalho de fundo do Nod): não aciona os agentes
                log("frente", "turno sem pedido (modo %s): vai para o modelo auxiliar" % modo)
                self.aux(body)
                return
            last = pedido
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
          except (BrokenPipeError, ConnectionResetError):
            log("aux", "cliente fechou a conexão antes do fim")
        finally:
            c.close()
            log("aux", AUX_MODEL, r.status, "%.1fs" % (time.time() - t0))


if __name__ == "__main__":
    if not LANGFLOW_API_KEY:
        log("aviso: LANGFLOW_API_KEY vazio (pôr no ambiente personalizado do app no Umbrel)")
    if not LITELLM_KEY:
        log("aviso: LITELLM_KEY vazio (tarefas auxiliares do Nod vão falhar)")
    log("registro de clientes:", REGISTRO_ARQ if REGISTRO else "desligado")
    log("ponte ouvindo na porta", PORT, "modelo", MODEL_ID, "flow", FLOW_ID)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
