#!/usr/bin/env python3
"""Ponte OpenAI-compatible -> Antigravity CLI (agy), modo Orchestrator.

Só biblioteca padrão. Rotas:
  GET  /health                      sem chave
  GET  /v1/models                   Bearer
  POST /v1/chat/completions         Bearer (stream e não-stream)
  GET  /login                       Basic (senha = chave do app): página de login do agy
  POST /login/start | /login/code   Basic
  GET  /login/status                Basic
Cada pedido roda `agy -p` num diretório temporário próprio, apagado no fim.
"""
import base64, html, json, os, pty, re, select, shutil, signal, struct, subprocess
import tempfile, threading, time, uuid, fcntl, termios
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8648"))
KEY = os.environ.get("BRIDGE_KEY", "")
AGY = os.environ.get("AGY_BIN", "/data/bin/agy")
AGY_HOME = os.environ.get("AGY_HOME", "/data/home")
RUN_USER = os.environ.get("RUN_USER", "agy")
TIMEOUT = int(os.environ.get("AGY_TIMEOUT", "600"))
MAX_CONC = int(os.environ.get("MAX_CONC", "2"))
EXTRA_ARGS = os.environ.get("AGY_EXTRA_ARGS", "--dangerously-skip-permissions").split()
MODEL_MAP = {}
for part in os.environ.get("MODEL_MAP", "orchestrator-neon=Orchestrator (Neon);orchestrator-argon=Orchestrator (Argon)").split(";"):
    if "=" in part:
        a, b = part.split("=", 1)
        MODEL_MAP[a.strip()] = b.strip()
WORK_ROOT = os.environ.get("WORK_ROOT", "/tmp/agy-req")
LOG_DIR = "/data/logs"
SEM = threading.BoundedSemaphore(MAX_CONC)
os.makedirs(WORK_ROOT, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def agy_env():
    env = {k: v for k, v in os.environ.items() if k not in ("BRIDGE_KEY", "APP_PASSWORD", "APP_SEED")}
    env.update(HOME=AGY_HOME, TERM="xterm-256color", NO_COLOR="1", LANG="C.UTF-8")
    return env


def demote():
    """Roda o agy como usuário sem privilégio (se existir)."""
    try:
        import pwd
        pw = pwd.getpwnam(RUN_USER)
        def f():
            os.setgid(pw.pw_gid); os.setuid(pw.pw_uid)
        return f
    except Exception:
        return None


# ---------------------------------------------------------------- prompt
def text_of(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    out = []
    for p in content:
        if isinstance(p, dict):
            if p.get("type") in ("text", "input_text"):
                out.append(p.get("text", ""))
            elif p.get("type") in ("image_url", "input_image"):
                out.append("[imagem omitida]")
        elif isinstance(p, str):
            out.append(p)
    return "\n".join(out)


def build_prompt(messages):
    system, turns = [], []
    for m in messages:
        role = m.get("role", "user")
        t = text_of(m.get("content"))
        if role in ("system", "developer"):
            system.append(t)
        elif role == "assistant":
            if m.get("tool_calls"):
                t += "\n[chamadas de ferramenta: " + json.dumps(m["tool_calls"], ensure_ascii=False)[:2000] + "]"
            turns.append(("Assistente", t))
        elif role == "tool":
            turns.append(("Resultado de ferramenta", t))
        else:
            turns.append(("Usuário", t))
    parts = []
    if system:
        parts.append("## Instruções do sistema\n" + "\n\n".join(system))
    if len(turns) == 1 and turns[0][0] == "Usuário":
        parts.append(turns[0][1])
    else:
        conv = "\n\n".join(f"### {r}\n{t}" for r, t in turns)
        parts.append("## Conversa até aqui\n" + conv + "\n\n## Tarefa\nResponda à última mensagem do Usuário, em texto, sem repetir o histórico.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- agy
def pick_text(obj):
    """Acha o texto final numa saída JSON do agy, seja qual for o formato."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for k in ("result", "response", "final_response", "text", "content", "output", "message"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, (dict, list)):
                t = pick_text(v)
                if t:
                    return t
    if isinstance(obj, list):
        for it in reversed(obj):
            t = pick_text(it)
            if t:
                return t
    return ""


def find_usage(obj):
    if isinstance(obj, dict):
        for k in ("usage", "usageMetadata", "token_usage"):
            if isinstance(obj.get(k), dict):
                return obj[k]
        for v in obj.values():
            u = find_usage(v)
            if u:
                return u
    return None


def run_agy(prompt, model_name, timeout):
    wd = tempfile.mkdtemp(prefix="r-", dir=WORK_ROOT)
    try:
        os.chmod(wd, 0o777)
        args = [AGY, "-p", prompt, "--model", model_name, "--output-format", "json",
                "--print-timeout", f"{timeout}s"] + EXTRA_ARGS
        t0 = time.time()
        p = subprocess.run(args, cwd=wd, env=agy_env(), capture_output=True, text=True,
                           timeout=timeout + 30, preexec_fn=demote(), stdin=subprocess.DEVNULL)
        dt = time.time() - t0
        out, err = p.stdout.strip(), p.stderr.strip()
        text, usage, raw = "", None, None
        try:
            raw = json.loads(out)
        except Exception:
            # pode vir NDJSON ou texto
            for line in out.splitlines()[::-1]:
                try:
                    raw = json.loads(line); break
                except Exception:
                    continue
        if raw is not None:
            text = pick_text(raw)
            usage = find_usage(raw)
            if isinstance(raw, dict) and raw.get("is_error") and not text:
                text = ""
        if not text and raw is None:
            text = out
        return {"ok": p.returncode == 0 and bool(text.strip()), "text": text, "usage": usage,
                "rc": p.returncode, "stderr": err[-3000:], "stdout": out[-3000:], "secs": round(dt, 1)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "", "rc": -1, "stderr": "timeout", "stdout": "", "secs": timeout}
    finally:
        shutil.rmtree(wd, ignore_errors=True)


_models_cache = {"t": 0, "out": "", "ok": False}


def agy_models(force=False):
    if not force and time.time() - _models_cache["t"] < 300:
        return _models_cache
    try:
        p = subprocess.run([AGY, "models"], env=agy_env(), capture_output=True, text=True, timeout=60,
                           preexec_fn=demote(), cwd=WORK_ROOT, stdin=subprocess.DEVNULL)
        out = (p.stdout + "\n" + p.stderr).strip()
        ok = p.returncode == 0 and "sign in" not in out.lower()
    except Exception as e:
        out, ok = str(e), False
    _models_cache.update(t=time.time(), out=out, ok=ok)
    return _models_cache


def est_tokens(s):
    return max(1, len(s) // 4)


# ---------------------------------------------------------------- login (pty)
ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[=>]")
LOGIN = {"pid": None, "fd": None, "buf": "", "url": "", "state": "parado", "started": 0, "sent_code": False}
LOCK = threading.Lock()


def login_reader(pid, fd):
    answered = set()
    while True:
        try:
            r, _, _ = select.select([fd], [], [], 1)
        except Exception:
            break
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                break
            if not d:
                break
            s = d.decode("utf-8", "replace")
            with LOCK:
                LOGIN["buf"] = (LOGIN["buf"] + s)[-200000:]
                m = re.findall(r"\x1b\]8;[^;]*;(https://accounts\.google\.com/[^\x07\x1b]+)", LOGIN["buf"])
                if m:
                    LOGIN["url"] = m[-1]
                    if LOGIN["state"] == "iniciando":
                        LOGIN["state"] = "aguardando código"
            clean = ANSI.sub("", s)
            for key, resp in (("Select login method", b"\r"), ("color scheme", b"\r"), ("Color scheme", b"\r"),
                              ("Choose a theme", b"\r")):
                if key in clean and key not in answered:
                    answered.add(key)
                    time.sleep(0.5)
                    try:
                        os.write(fd, resp)
                    except OSError:
                        pass
        if time.time() - LOGIN["started"] > 1800:
            break
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        os.waitpid(pid, 0)
    except Exception:
        pass
    with LOCK:
        if LOGIN["pid"] == pid:
            LOGIN.update(pid=None, fd=None)
            if LOGIN["state"] not in ("conectado",):
                LOGIN["state"] = "parado"


def login_start():
    login_stop()
    os.makedirs(AGY_HOME, exist_ok=True)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            f = demote()
            if f:
                f()
            os.chdir(WORK_ROOT)
            os.execve(AGY, ["agy"], agy_env())
        finally:
            os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 220, 0, 0))
    with LOCK:
        LOGIN.update(pid=pid, fd=fd, buf="", url="", state="iniciando", started=time.time(), sent_code=False)
    threading.Thread(target=login_reader, args=(pid, fd), daemon=True).start()


def login_stop():
    with LOCK:
        pid = LOGIN["pid"]
        LOGIN.update(pid=None, fd=None)
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


def login_code(code):
    with LOCK:
        fd = LOGIN["fd"]
    if not fd:
        return False, "Nenhum login em andamento. Clique em Iniciar login."
    code = code.strip()
    os.write(fd, code.encode())
    time.sleep(0.4)
    os.write(fd, b"\r")
    with LOCK:
        LOGIN["state"] = "verificando"
        LOGIN["sent_code"] = True
    for _ in range(30):
        time.sleep(2)
        m = agy_models(force=True)
        if m["ok"]:
            with LOCK:
                LOGIN["state"] = "conectado"
            login_stop()
            return True, "Login feito."
    with LOCK:
        LOGIN["state"] = "falhou ou ainda verificando"
    return False, "Não confirmou em 60 s. Veja a tela abaixo."


def screen_tail():
    with LOCK:
        s = ANSI.sub("", LOGIN["buf"])
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    return s[-2500:]


PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ponte agy: login</title><style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;background:#111;color:#eee}
h1{font-size:22px}a{color:#8ab4f8;word-break:break-all}button,input{font-size:16px;padding:10px 14px;border:1px solid #555;background:#222;color:#eee}
input{width:100%;box-sizing:border-box;margin:8px 0}pre{background:#000;padding:10px;white-space:pre-wrap;font-size:12px;max-height:280px;overflow:auto}
.st{padding:10px;border:1px solid #444;margin:12px 0}</style></head><body>
<h1>Antigravity CLI na ponte</h1>
<div class="st">Conta: <b id="ok">__OK__</b><br>Login: <span id="state">__STATE__</span></div>
<p>1. Clique em Iniciar login. 2. Abra o link do Google que aparecer, entre com a conta e copie o código. 3. Cole aqui e envie.</p>
<form method="post" action="/login/start"><button>Iniciar login</button></form>
<p id="url">__URL__</p>
<form method="post" action="/login/code"><input name="code" placeholder="Código de autorização" autocomplete="off"><button>Enviar código</button></form>
<p>__MSG__</p>
<details><summary>Tela do agy</summary><pre id="scr">__SCREEN__</pre></details>
<details><summary>Modelos</summary><pre>__MODELS__</pre></details>
<script>
setInterval(async()=>{try{const r=await fetch('/login/status');const j=await r.json();
document.getElementById('state').textContent=j.state;document.getElementById('ok').textContent=j.ok?'conectado':'sem login';
if(j.url){document.getElementById('url').innerHTML='<a target="_blank" rel="noopener" href="'+j.url.replace(/"/g,'&quot;')+'">Abrir login do Google</a>'}
document.getElementById('scr').textContent=j.screen}catch(e){}},3000)
</script></body></html>"""


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "agy-ponte/1.0"

    def log_message(self, fmt, *a):
        log(self.address_string(), fmt % a)

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "charset" not in ctype else ""))
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code, msg, typ="invalid_request_error"):
        self._send(code, {"error": {"message": msg, "type": typ, "code": code}})

    def _bearer_ok(self):
        a = self.headers.get("Authorization", "")
        k = a[7:].strip() if a.lower().startswith("bearer ") else self.headers.get("x-api-key", "")
        return bool(KEY) and k == KEY

    def _basic_ok(self):
        a = self.headers.get("Authorization", "")
        if a.lower().startswith("basic "):
            try:
                u, _, pw = base64.b64decode(a[6:]).decode().partition(":")
                return bool(KEY) and pw == KEY
            except Exception:
                return False
        return self._bearer_ok()

    def _need_basic(self):
        self._send(401, "Precisa de senha (a senha do app no Umbrel).", "text/plain",
                   {"WWW-Authenticate": 'Basic realm="agy-ponte", charset="UTF-8"'})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/health", "/healthz"):
            return self._send(200, {"ok": True, "login": _models_cache["ok"], "fila_max": MAX_CONC})
        if path in ("/v1/models", "/models"):
            if not self._bearer_ok():
                return self._err(401, "chave inválida", "authentication_error")
            now = int(time.time())
            return self._send(200, {"object": "list", "data": [
                {"id": k, "object": "model", "created": now, "owned_by": "antigravity-cli", "agy_model": v}
                for k, v in MODEL_MAP.items()]})
        if path in ("/", "/login"):
            if not self._basic_ok():
                return self._need_basic()
            return self._page()
        if path == "/login/status":
            if not self._basic_ok():
                return self._need_basic()
            m = agy_models()
            with LOCK:
                st, url = LOGIN["state"], LOGIN["url"]
            return self._send(200, {"ok": m["ok"], "state": st, "url": url, "screen": screen_tail()})
        if path == "/agy/models":
            if not self._basic_ok():
                return self._need_basic()
            return self._send(200, {"ok": agy_models(True)["ok"], "out": _models_cache["out"]})
        return self._err(404, "rota não existe")

    def _page(self, msg=""):
        m = agy_models()
        with LOCK:
            st, url = LOGIN["state"], LOGIN["url"]
        u = f'<a target="_blank" rel="noopener" href="{html.escape(url)}">Abrir login do Google</a>' if url else ""
        body = (PAGE.replace("__OK__", "conectado" if m["ok"] else "sem login").replace("__STATE__", html.escape(st))
                .replace("__URL__", u).replace("__MSG__", html.escape(msg))
                .replace("__SCREEN__", html.escape(screen_tail())).replace("__MODELS__", html.escape(m["out"][-3000:])))
        self._send(200, body, "text/html")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/login/start":
            if not self._basic_ok():
                return self._need_basic()
            login_start()
            for _ in range(40):
                time.sleep(0.5)
                if LOGIN["url"]:
                    break
            return self._redirect()
        if path == "/login/code":
            if not self._basic_ok():
                return self._need_basic()
            raw = self._body().decode()
            from urllib.parse import parse_qs
            code = ""
            if raw.strip().startswith("{"):
                code = json.loads(raw).get("code", "")
            else:
                code = (parse_qs(raw).get("code") or [""])[0]
            ok, msg = login_code(code)
            return self._page(msg)
        if path in ("/v1/chat/completions", "/chat/completions"):
            if not self._bearer_ok():
                return self._err(401, "chave inválida", "authentication_error")
            return self._chat()
        return self._err(404, "rota não existe")

    def _redirect(self):
        self.send_response(303)
        self.send_header("Location", "/login")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _chat(self):
        try:
            req = json.loads(self._body() or b"{}")
        except Exception:
            return self._err(400, "JSON inválido")
        model = req.get("model", "")
        model = model.split("/", 1)[1] if model.startswith("openai/") else model
        agy_model = MODEL_MAP.get(model)
        if not agy_model:
            return self._err(404, f"modelo {model!r} não existe aqui; use: {', '.join(MODEL_MAP)}", "not_found_error")
        msgs = req.get("messages") or []
        if not msgs:
            return self._err(400, "messages vazio")
        if not agy_models()["ok"] and not agy_models(True)["ok"]:
            return self._err(503, "agy sem login na ponte: abra /login e entre com a conta Google", "api_error")
        prompt = build_prompt(msgs)
        timeout = min(int(req.get("timeout") or TIMEOUT), 3600)
        stream = bool(req.get("stream"))
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        result = {}

        def work():
            with SEM:
                result.update(run_agy(prompt, agy_model, timeout))

        th = threading.Thread(target=work, daemon=True)
        th.start()
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def ev(obj):
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()

            def chunk(delta, fin=None, usage=None):
                o = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
                if usage:
                    o["usage"] = usage
                ev(o)
            try:
                chunk({"role": "assistant", "content": ""})
                while th.is_alive():
                    th.join(10)
                    if th.is_alive():
                        self.wfile.write(b": aguardando agy\n\n"); self.wfile.flush()
                if not result.get("ok"):
                    log("erro agy", model, result.get("rc"), result.get("stderr", "")[-500:])
                    chunk({"content": "[erro na ponte agy: " + (result.get("stderr") or result.get("stdout") or "sem resposta")[-600:] + "]"}, "stop")
                else:
                    txt = result["text"]
                    for i in range(0, len(txt), 400):
                        chunk({"content": txt[i:i + 400]})
                    chunk({}, "stop", self._usage(prompt, txt))
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        th.join()
        if not result.get("ok"):
            log("erro agy", model, result.get("rc"), result.get("stderr", "")[-500:])
            return self._err(502, "agy falhou (rc=%s): %s" % (result.get("rc"), (result.get("stderr") or result.get("stdout") or "")[-1500:]), "api_error")
        txt = result["text"]
        log("ok", model, result.get("secs"), "s", len(txt), "chars")
        self._send(200, {"id": cid, "object": "chat.completion", "created": created, "model": model,
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": txt}, "finish_reason": "stop"}],
                         "usage": self._usage(prompt, txt)})

    @staticmethod
    def _usage(prompt, txt):
        p, c = est_tokens(prompt), est_tokens(txt)
        return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


if __name__ == "__main__":
    if not KEY:
        log("AVISO: BRIDGE_KEY vazia; a API vai recusar tudo")
    log("ponte agy na porta", PORT, "modelos", MODEL_MAP, "conc", MAX_CONC, "timeout", TIMEOUT)
    threading.Thread(target=agy_models, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
