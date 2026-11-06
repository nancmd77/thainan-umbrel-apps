#!/usr/bin/env python3
"""
Servidor HTTP leve para configuracao da chave Moondream Cloud do OpenBot.
Porta padrao: 8593 (configuravel via variavel PORT).
"""

import html
import http.server
import os
import sys
import urllib.parse

PORT = int(os.environ.get("PORT", "8593"))
KEY_PATH = os.environ.get("CONFIG_KEY_PATH", "/config/moondream.key")


def get_key_status():
    """Retorna (presente, sufixo_4_digitos)."""
    env_key = os.environ.get("MOONDREAM_API_KEY", "").strip()
    if env_key:
        last4 = env_key[-4:] if len(env_key) >= 4 else env_key
        return True, last4, "env"

    if os.path.isfile(KEY_PATH):
        try:
            with open(KEY_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    last4 = content[-4:] if len(content) >= 4 else content
                    return True, last4, "file"
        except Exception:
            pass

    return False, "", "none"


def log_startup_status():
    present, _, _ = get_key_status()
    if present:
        print("moondream: chave presente", flush=True)
    else:
        print("moondream: chave ausente", flush=True)


def render_html(notice=None, error=None):
    present, last4, source = get_key_status()

    if present:
        status_badge = f'<span class="badge badge-success">salva (termina em {html.escape(last4)})</span>'
        status_info = f"Chave ativa configurada (fonte: {source})."
    else:
        status_badge = '<span class="badge badge-warning">chave ausente</span>'
        status_info = "Nenhuma chave configurada. Insira sua chave da Moondream Cloud abaixo."

    alert_box = ""
    if notice:
        alert_box = f'<div class="alert alert-success">{html.escape(notice)}</div>'
    elif error:
        alert_box = f'<div class="alert alert-error">{html.escape(error)}</div>'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Configuração Moondream - OpenBot</title>
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 32px;
      width: 100%;
      max-width: 480px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
    }}
    h1 {{
      font-size: 1.5rem;
      margin-bottom: 8px;
      color: #38bdf8;
    }}
    p.desc {{
      font-size: 0.9rem;
      color: #94a3b8;
      margin-bottom: 24px;
      line-height: 1.4;
    }}
    .status-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 20px;
      padding-bottom: 16px;
      border-bottom: 1px solid #334155;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
    }}
    .badge-success {{
      background: rgba(34, 197, 94, 0.2);
      color: #4ade80;
      border: 1px solid #22c55e;
    }}
    .badge-warning {{
      background: rgba(234, 179, 8, 0.2);
      color: #facc15;
      border: 1px solid #eab308;
    }}
    .field {{
      margin-bottom: 20px;
    }}
    label {{
      display: block;
      font-size: 0.85rem;
      font-weight: 500;
      color: #cbd5e1;
      margin-bottom: 8px;
    }}
    input[type="password"] {{
      width: 100%;
      padding: 12px 14px;
      background: #0f172a;
      border: 1px solid #475569;
      border-radius: 8px;
      color: #ffffff;
      font-size: 0.95rem;
      outline: none;
      transition: border-color 0.2s;
    }}
    input[type="password"]:focus {{
      border-color: #38bdf8;
    }}
    button.btn {{
      width: 100%;
      padding: 12px;
      border-radius: 8px;
      border: none;
      background: #0284c7;
      color: #ffffff;
      font-weight: 600;
      font-size: 0.95rem;
      cursor: pointer;
      transition: background 0.2s;
    }}
    button.btn:hover {{
      background: #0369a1;
    }}
    .alert {{
      padding: 12px 16px;
      border-radius: 8px;
      font-size: 0.85rem;
      margin-bottom: 20px;
    }}
    .alert-success {{
      background: rgba(34, 197, 94, 0.15);
      border: 1px solid #22c55e;
      color: #86efac;
    }}
    .alert-error {{
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid #ef4444;
      color: #fca5a5;
    }}
    .footer {{
      margin-top: 24px;
      text-align: center;
      font-size: 0.8rem;
      color: #64748b;
    }}
    .footer a {{
      color: #38bdf8;
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Mira Visual Moondream</h1>
    <p class="desc">Chave de API do Moondream Cloud para mira visual e computer use no OpenBot.</p>
    
    <div class="status-row">
      <span style="font-size:0.85rem;color:#94a3b8;">Status atual:</span>
      {status_badge}
    </div>

    {alert_box}

    <form method="POST" action="/save">
      <div class="field">
        <label for="key">Chave Moondream</label>
        <input type="password" id="key" name="key" placeholder="Cole sua chave aqui..." autocomplete="off" required />
      </div>
      <button type="submit" class="btn">Salvar</button>
    </form>

    <div class="footer">
      Thainan OpenBot &bull; <a href="/">&#8592; Voltar ao painel principal</a>
    </div>
  </div>
</body>
</html>"""


class MoondreamConfigHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        notice = None
        if "saved" in qs:
            _, last4, _ = get_key_status()
            notice = f"salva (termina em {last4})"

        body = render_html(notice=notice).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/save", "/moondream/save", "/"):
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
            params = urllib.parse.parse_qs(raw_body)
            key = params.get("key", [""])[0].strip()

            if not key:
                body = render_html(error="A chave nao pode estar vazia.").encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            try:
                os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
                with open(KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(key)
                os.chmod(KEY_PATH, 0o600)
            except Exception as exc:
                body = render_html(error=f"Erro ao salvar arquivo: {exc}").encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            redirect_target = "/?saved=1" if path != "/moondream/save" else "/moondream?saved=1"
            self.send_response(303)
            self.send_header("Location", redirect_target)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Reduz verbosidade de logs HTTP
        pass


def run():
    log_startup_status()
    server_address = ("0.0.0.0", PORT)
    httpd = http.server.ThreadingHTTPServer(server_address, MoondreamConfigHandler)
    sys.stderr.write(f"[moondream_config] Servidor de configuracao iniciado na porta {PORT}...\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
