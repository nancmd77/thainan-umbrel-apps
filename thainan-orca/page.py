"""Página do thainan-orca (atrás do login do Umbrel): status e links de pareamento do Orca."""
import html, json, os, re, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PAGE_PORT", "8650"))
LAN = os.environ.get("LAN_ADDRESS", "192.168.18.171")
TS = os.environ.get("TAILSCALE_ADDRESS", "nonve.taila412b1.ts.net")
H = os.environ.get("HOME", "/data/home")
PATHX = f"{H}/.local/bin:{H}/.npm-global/bin:" + os.environ.get("PATH", "")

CSS = """
body{background:#111;color:#eee;font:17px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:32px;max-width:860px}
h1{font-size:28px;margin:0 0 8px} h2{font-size:20px;margin:28px 0 8px}
a,button{color:#111;background:#eee;border:0;border-radius:0;padding:10px 16px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block;margin:4px 8px 4px 0}
pre{background:#1c1c1c;padding:14px;white-space:pre-wrap;word-break:break-all;border-radius:0;font-size:14px;line-height:1.15}
code{background:#1c1c1c;padding:2px 6px} p{margin:6px 0} .muted{color:#aaa}
"""


def run(args, timeout=60):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PATH": PATHX})
        return p.returncode, (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    except Exception as e:
        return -1, str(e)


def version(cmd):
    rc, out = run([cmd, "--version"], 20)
    return out.splitlines()[0][:80] if rc == 0 and out else "não instalado"


def page(body):
    return f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Orca</title><style>{CSS}</style>{body}"


def home():
    ver = open("/data/orca/current/VERSION").read().strip() if os.path.exists("/data/orca/current/VERSION") else "?"
    rc, st = run(["orca-ide", "status"], 20)
    ag = "".join(f"<p>{n}: {html.escape(version(c))}</p>" for n, c in
                 [("Claude Code", "claude"), ("Codex", "codex"), ("OpenCode", "opencode"), ("Antigravity (agy)", "agy"), ("git", "git")])
    btn = lambda label, q: f"<a href='/par?{q}'>{label}</a>"
    return page(f"""
<h1>Orca no Nonve</h1>
<p class=muted>Servidor do Orca {html.escape(ver)}, porta 6768. Os agentes rodam aqui, com o PC desligado.</p>
<h2>Parear o Orca do computador</h2>
<p>No Orca: Settings, Remote Orca Servers, Add Server, e cole o link.</p>
{btn("Link pra rede de casa", "end=lan")}{btn("Link pelo Tailscale", "end=ts")}
<h2>Parear o celular</h2>
<p>No Orca Mobile: Pair, e escaneie o código ou cole o link.</p>
{btn("Celular na rede de casa", "end=lan&mobile=1")}{btn("Celular pelo Tailscale", "end=ts&mobile=1")}
<h2>Agentes instalados</h2>{ag}
<p class=muted>O login de cada agente é feito num terminal do Orca conectado aqui: <code>claude</code>, <code>codex login</code>, <code>opencode auth login</code>, <code>agy</code>.</p>
<h2>Status</h2><pre>{html.escape(st[-3000:])}</pre>
""")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self.send(200, home())
        if u.path == "/par":
            q = parse_qs(u.query)
            addr = TS if q.get("end", ["lan"])[0] == "ts" else LAN
            args = ["orca-ide", "pair", "--address", addr]
            if q.get("mobile"):
                args.append("--mobile")
            rc, out = run(args, 60)
            links = sorted(set(re.findall(r"(?:orca|https?)://[^\s\"'<>]+", out)))
            lk = "".join(f"<pre>{html.escape(l)}</pre>" for l in links)
            return self.send(200, page(f"""
<h1>Link de pareamento</h1>
<p>Endereço: <code>{html.escape(addr)}:6768</code>. Trate o link como senha: ele dá acesso a este Orca.</p>
{lk}<h2>Saída completa</h2><pre>{html.escape(out)}</pre><a href='/'>Voltar</a>"""))
        self.send(404, page("<p>Não encontrado.</p><a href='/'>Voltar</a>"))


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
