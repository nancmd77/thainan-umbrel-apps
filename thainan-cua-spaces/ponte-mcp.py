#!/usr/bin/env python3
# Ponte MCP do Cua Spaces (thainan-cua-spaces r3).
# Roda o MCP oficial do cua (`cua mcp`, stdio) e o expõe como Streamable HTTP
# (respostas em JSON, sem SSE) em POST /mcp. Uma sessão MCP = um processo `cua mcp`.
# Só na rede interna do Docker do Umbrel (porta sem publicação), sem bearer.
# Só biblioteca padrão do Python, nada baixado.
import json, os, queue, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CUA = os.environ.get("CUA_BIN", "/usr/local/bin/cua")
SPACE_URL = os.environ.get("PONTE_SPACE_URL", "http://thainan-cua-spaces_space_1:3211")
PORT = int(os.environ.get("PONTE_PORT", "8627"))
PERMS = os.environ.get("PONTE_PERMISSIONS", "computer:all")
TIMEOUT = float(os.environ.get("PONTE_TIMEOUT", "300"))
IDLE = float(os.environ.get("PONTE_IDLE", "1800"))
MAX_SESSIONS = int(os.environ.get("PONTE_MAX_SESSIONS", "8"))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def register_space():
    """Registra o Space em modo direto (token em CUA_ENV_TOKEN) e devolve o nome do alvo."""
    for tentativa in range(60):
        r = subprocess.run([CUA, "do", "--embedded", "switch", "url", SPACE_URL],
                           capture_output=True, text=True)
        if r.returncode == 0:
            with open(os.path.expanduser("~/.cua/do_target.json")) as f:
                name = json.load(f)["name"]
            log("Space registrado:", name)
            return name
        log("Space ainda não respondeu, tentando de novo:", (r.stderr or r.stdout).strip()[-200:])
        time.sleep(5)
    sys.exit("Space não respondeu")


SANDBOX = None


class Session:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.proc = subprocess.Popen(
            [CUA, "mcp", "--embedded", "--sandbox", SANDBOX, "--permissions", PERMS],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.wlock = threading.Lock()
        self.pending = {}
        self.plock = threading.Lock()
        self.last = time.time()
        threading.Thread(target=self.reader, daemon=True).start()

    def send(self, msg):
        with self.wlock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" in msg and "id" in msg:
                # pedido do servidor ao cliente (ping, roots...): responde aqui
                if msg["method"] == "ping":
                    self.send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                else:
                    self.send({"jsonrpc": "2.0", "id": msg["id"],
                               "error": {"code": -32601, "message": "não suportado pela ponte"}})
                continue
            if "id" in msg:
                key = json.dumps(msg["id"])
                with self.plock:
                    q = self.pending.pop(key, None)
                if q:
                    q.put(msg)
        with self.plock:
            for q in self.pending.values():
                q.put(None)
            self.pending.clear()

    def request(self, msg):
        q = queue.Queue(1)
        with self.plock:
            self.pending[json.dumps(msg["id"])] = q
        self.send(msg)
        try:
            r = q.get(timeout=TIMEOUT)
        except queue.Empty:
            r = None
        if r is None:
            r = {"jsonrpc": "2.0", "id": msg["id"],
                 "error": {"code": -32000, "message": "sem resposta do cua mcp"}}
        self.last = time.time()
        return r

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


SESSIONS = {}
SLOCK = threading.Lock()


def reaper():
    while True:
        time.sleep(60)
        now = time.time()
        with SLOCK:
            for sid, s in list(SESSIONS.items()):
                if now - s.last > IDLE or s.proc.poll() is not None:
                    s.close()
                    del SESSIONS[sid]
                    log("sessão encerrada:", sid[:8])


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, code, body=None, sid=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        if sid:
            self.send_header("Mcp-Session-Id", sid)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/healthz"):
            return self.reply(200, {"ok": True, "sessoes": len(SESSIONS), "space": SANDBOX})
        self.send_response(405)
        self.send_header("Allow", "POST, DELETE")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        sid = self.headers.get("Mcp-Session-Id")
        with SLOCK:
            s = SESSIONS.pop(sid, None)
        if s:
            s.close()
        self.reply(200 if s else 404)

    def do_POST(self):
        if not self.path.startswith("/mcp"):
            return self.reply(404, {"error": "use /mcp"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return self.reply(400, {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "JSON inválido"}})
        msgs = payload if isinstance(payload, list) else [payload]
        sid = self.headers.get("Mcp-Session-Id")
        is_init = any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs)
        if is_init:
            with SLOCK:
                if len(SESSIONS) >= MAX_SESSIONS:
                    old = min(SESSIONS.values(), key=lambda x: x.last)
                    old.close()
                    SESSIONS.pop(old.id, None)
                s = Session()
                SESSIONS[s.id] = s
            sid = s.id
            log("sessão nova:", sid[:8])
        else:
            with SLOCK:
                s = SESSIONS.get(sid)
            if not s:
                return self.reply(404, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32001, "message": "sessão desconhecida"}})
        s.last = time.time()
        out = []
        for m in msgs:
            if isinstance(m, dict) and "method" in m and "id" in m:
                out.append(s.request(m))
            else:
                s.send(m)
        if not out:
            return self.reply(202, None, sid)
        self.reply(200, out if isinstance(payload, list) else out[0], sid)


if __name__ == "__main__":
    SANDBOX = register_space()
    threading.Thread(target=reaper, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    srv.daemon_threads = True
    log(f"ponte MCP em :{PORT}/mcp -> cua mcp --sandbox {SANDBOX} --permissions {PERMS}")
    srv.serve_forever()
