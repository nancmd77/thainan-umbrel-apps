#!/usr/bin/env python3
"""
Servidor sidecar de login web para o OpenBot (thainan-openbot).
Comunica-se com o OpenBot através do socket unix /run/openbot/control.sock.
"""

import base64
import html
import http.client
import http.server
import json
import os
import re
import socket
import sys
import urllib.parse

SOCKET_PATH = os.environ.get("OPENBOT_SOCKET_PATH", "/run/openbot/control.sock")
SERVER_PORT = int(os.environ.get("OPENBOT_WEB_PORT", "8592"))
DEFAULT_EMAIL = "thainancaldeira75@gmail.com"
MOONDREAM_KEY_PATH = os.environ.get("CONFIG_KEY_PATH", "/config/moondream.key")
LIQUID_KEY_PATH = os.environ.get("LIQUID_KEY_PATH", "/config/liquid.key")
TYPESAFE_KEY_PATH = os.environ.get("TYPESAFE_KEY_PATH", "/config/typesafe.key")


def get_moondream_status():
    env_key = os.environ.get("MOONDREAM_API_KEY", "").strip()
    if env_key:
        return True, env_key[-4:] if len(env_key) >= 4 else env_key
    if os.path.isfile(MOONDREAM_KEY_PATH):
        try:
            with open(MOONDREAM_KEY_PATH, "r", encoding="utf-8") as f:
                c = f.read().strip()
                if c:
                    return True, c[-4:] if len(c) >= 4 else c
        except Exception:
            pass
    return False, ""

def get_liquid_status():
    env_key = os.environ.get("LIQUID_API_KEY", "").strip()
    if env_key:
        return True, env_key[-4:] if len(env_key) >= 4 else env_key
    if os.path.isfile(LIQUID_KEY_PATH):
        try:
            with open(LIQUID_KEY_PATH, "r", encoding="utf-8") as f:
                c = f.read().strip()
                if c:
                    return True, c[-4:] if len(c) >= 4 else c
        except Exception:
            pass
    return False, ""


def get_typesafe_status():
    env_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if env_key:
        return True, env_key[-4:] if len(env_key) >= 4 else env_key
    for p in [TYPESAFE_KEY_PATH, "/data/config/typesafe.key"]:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    c = f.read().strip()
                    if c:
                        return True, c[-4:] if len(c) >= 4 else c
            except Exception:
                pass
    return False, ""


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, sock_path, timeout=15):
        super().__init__("localhost", timeout=timeout)
        self.sock_path = sock_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.sock_path)


def query_control_socket(method, path, data=None):
    """Envia requisição HTTP sobre o socket unix do OpenBot."""
    if not os.path.exists(SOCKET_PATH):
        return {
            "_ok": False,
            "_http_status": 503,
            "error": "socket_not_found",
            "message": "O socket do OpenBot não foi encontrado. O servidor pode estar inicializando.",
        }

    conn = UnixHTTPConnection(SOCKET_PATH)
    try:
        body = None
        headers = {}
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")

        lines = {}
        for line in raw.splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                lines[k.strip()] = v.strip()

        lines["_http_status"] = resp.status
        lines["_ok"] = resp.status == 200
        return lines
    except Exception as exc:
        return {
            "_ok": False,
            "_http_status": 502,
            "error": "connection_error",
            "message": f"Falha na comunicação com o OpenBot: {exc}",
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def ansi_to_html(text):
    """Converte sequências ANSI do terminal em spans HTML estilizados."""
    tokens = re.split(r"(\x1b\[[0-9;]*m)", text)
    bg = "#ffffff"
    fg = "#000000"
    out = []
    for tok in tokens:
        if not tok:
            continue
        m = re.match(r"\x1b\[([0-9;]*)m", tok)
        if m:
            codes = m.group(1).split(";") if m.group(1) else ["0"]
            for c in codes:
                if c in ("0", ""):
                    bg = "#ffffff"
                    fg = "#000000"
                elif c == "30":
                    fg = "#000000"
                elif c == "37":
                    fg = "#ffffff"
                elif c == "40":
                    bg = "#000000"
                elif c == "47":
                    bg = "#ffffff"
        else:
            escaped = html.escape(tok)
            out.append(f'<span style="background:{bg};color:{fg}">{escaped}</span>')
    return "".join(out)


def render_html_page(status_info=None, notice=None, error_msg=None, challenge=None, qr_html=None):
    if status_info is None:
        status_info = query_control_socket("GET", "/v1/status")

    is_ready = status_info.get("_ok", False)
    account_status = status_info.get("account", "unknown")
    is_signed_in = account_status == "signed_in"
    user_email = status_info.get("email", "")
    server_phase = status_info.get("server", "desconhecido")
    server_name = status_info.get("server_name", "OpenBot")
    version = status_info.get("version", "0.62.0")

    if not is_ready:
        badge_text = "Iniciando"
        badge_color = "#d97706"
        status_desc = "O servidor OpenBot está carregando os componentes virtuais. Aguarde alguns instantes."
    elif is_signed_in:
        badge_text = "Conectado"
        badge_color = "#16a34a"
        status_desc = f"Servidor vinculado à conta {user_email}."
    elif account_status == "code_sent":
        badge_text = "Aguardando código"
        badge_color = "#2563eb"
        status_desc = "Código enviado por e-mail. Digite o código de verificação abaixo."
    else:
        badge_text = "Não conectado"
        badge_color = "#dc2626"
        status_desc = "Servidor desconectado. Solicite o código de acesso para entrar."

    notice_block = ""
    if notice:
        notice_block = f'<div class="alert alert-success">{html.escape(notice)}</div>'
    elif error_msg:
        notice_block = f'<div class="alert alert-error">{html.escape(error_msg)}</div>'

    qr_section = ""
    if qr_html:
        qr_section = f"""
        <div class="card">
          <h2>Código QR para aplicativo móvel</h2>
          <p class="subtitle">Abra o aplicativo OpenBot no celular, selecione escanear código QR e aponte para a imagem abaixo. O código expira em 2 minutos.</p>
          <div class="qr-container">
            <pre class="qr-box">{qr_html}</pre>
          </div>
        </div>
        """

    login_section = ""
    if is_ready and not is_signed_in:
        login_section = f"""
        <div class="card">
          <h2>Entrar com e-mail</h2>
          <p class="subtitle">Insira seu e-mail para receber um código de uso único do OpenBot.</p>
          <form id="form-start" method="POST" action="/login/start">
            <div class="field">
              <label for="email">Endereço de e-mail</label>
              <input type="email" id="email" name="email" value="{html.escape(DEFAULT_EMAIL)}" required />
            </div>
            <div class="actions">
              <button type="submit" id="btn-start" class="btn btn-primary">Enviar código</button>
            </div>
          </form>

          <hr class="divider" />

          <h2>Confirmar código</h2>
          <p class="subtitle">Digite o código numérico recebido na sua caixa de entrada.</p>
          <form id="form-verify" method="POST" action="/login/verify">
            <input type="hidden" id="challenge" name="challenge" value="{html.escape(challenge or '')}" />
            <div class="field">
              <label for="code">Código de verificação</label>
              <input type="text" id="code" name="code" placeholder="Exemplo: 123456" autocomplete="one-time-code" required />
            </div>
            <div class="actions">
              <button type="submit" id="btn-verify" class="btn btn-primary">Entrar</button>
            </div>
          </form>
        </div>
        """

    actions_section = ""
    if is_ready and is_signed_in:
        actions_section = f"""
        <div class="card">
          <h2>Ações da conta</h2>
          <p class="subtitle">Gerencie a conexão e os dispositivos vinculados ao servidor.</p>
          <div class="actions-row">
            <form id="form-mobile" method="POST" action="/mobile-connect">
              <button type="submit" id="btn-mobile" class="btn btn-secondary">Conectar celular (código QR)</button>
            </form>
            <form id="form-logout" method="POST" action="/logout" onsubmit="return confirm('Deseja realmente desconectar este servidor?');">
              <button type="submit" class="btn btn-danger">Sair da conta</button>
            </form>
          </div>
        </div>
        """
    has_md, md_last4 = get_moondream_status()
    if has_md:
        md_badge = f'<span class="badge" style="background-color:#16a34a">Salva (termina em {html.escape(md_last4)})</span>'
    else:
        md_badge = '<span class="badge" style="background-color:#d97706">Chave ausente</span>'

    moondream_section = f"""
    <div class="card">
      <div class="status-row">
        <h2>Mira visual Moondream</h2>
        {md_badge}
      </div>
      <p class="subtitle">Chave de API do Moondream Cloud para mira visual e computer use de agentes.</p>
      <form id="form-moondream" method="POST" action="/moondream/save">
        <div class="field">
          <label for="moondream-key">Chave Moondream</label>
          <input type="password" id="moondream-key" name="key" placeholder="Cole sua chave aqui..." autocomplete="off" required />
        </div>
        <div class="actions">
          <button type="submit" id="btn-moondream" class="btn btn-secondary">Salvar</button>
        </div>
      </form>
    </div>
    """
    has_liquid, liquid_last4 = get_liquid_status()
    if has_liquid:
        liquid_badge = f'<span class="badge" style="background-color:#16a34a">Salva (termina em {html.escape(liquid_last4)})</span>'
    else:
        liquid_badge = '<span class="badge" style="background-color:#d97706">Chave ausente</span>'

    liquid_section = f"""
    <div class="card">
      <div class="status-row">
        <h2>Modelo de decisão Liquid AI (SystemOne)</h2>
        {liquid_badge}
      </div>
      <p class="subtitle">Chave de API do Liquid AI (modelo d1-ultra) para decisão rápida de passos em Computer Use.</p>
      <form id="form-liquid" method="POST" action="/liquid/save">
        <div class="field">
          <label for="liquid-key">Chave Liquid AI</label>
          <input type="password" id="liquid-key" name="key" placeholder="Cole sua chave Liquid aqui..." autocomplete="off" required />
        </div>
        <div class="actions">
          <button type="submit" id="btn-liquid" class="btn btn-secondary">Salvar</button>
        </div>
      </form>
    </div>
    """

    has_ts, typesafe_last4 = get_typesafe_status()
    if has_ts:
        typesafe_badge = f'<span class="badge" style="background-color:#16a34a">Salva (termina em {html.escape(typesafe_last4)})</span>'
    else:
        typesafe_badge = '<span class="badge" style="background-color:#d97706">Chave ausente</span>'

    typesafe_section = f"""
    <div class="card">
      <div class="status-row">
        <h2>Decisao reserva Jev (TypeSafe)</h2>
        {typesafe_badge}
      </div>
      <p class="subtitle">Chave da API oficial da TypeSafe (modelo jev-latest), usada como reserva quando a Liquid AI falhar.</p>
      <form id="form-typesafe" method="POST" action="/typesafe/save">
        <div class="field">
          <label for="typesafe-key">Chave TypeSafe</label>
          <input type="password" id="typesafe-key" name="key" placeholder="Cole sua chave TypeSafe aqui..." autocomplete="off" required />
        </div>
        <div class="actions">
          <button type="submit" id="btn-typesafe" class="btn btn-secondary">Salvar</button>
        </div>
      </form>
    </div>
    """


    page = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OpenBot - Controle do servidor</title>
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      border-radius: 0 !important;
    }}
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --danger: #dc2626;
      --danger-hover: #b91c1c;
      --success: #16a34a;
    }}
    body {{
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.5;
      padding: 24px 16px;
    }}
    .container {{
      max-width: 640px;
      margin: 0 auto;
    }}
    header {{
      margin-bottom: 24px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
    }}
    h1 {{
      font-size: 24px;
      font-weight: 600;
      letter-spacing: -0.02em;
      margin-bottom: 4px;
    }}
    header p {{
      color: var(--text-muted);
      font-size: 14px;
    }}
    .card {{
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      padding: 20px;
      margin-bottom: 20px;
    }}
    h2 {{
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 4px;
    }}
    p.subtitle {{
      color: var(--text-muted);
      font-size: 13px;
      margin-bottom: 16px;
    }}
    .status-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 600;
      color: #ffffff;
      border-radius: 0;
      background-color: {badge_color};
    }}
    .info-list {{
      list-style: none;
      font-size: 13px;
      color: var(--text-muted);
      border-top: 1px solid var(--border);
      padding-top: 12px;
    }}
    .info-list li {{
      margin-bottom: 6px;
    }}
    .info-list strong {{
      color: var(--text);
    }}
    .field {{
      margin-bottom: 14px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 500;
      margin-bottom: 6px;
      color: var(--text);
    }}
    input[type="email"],
    input[type="text"],
    input[type="password"] {{
      display: block;
      width: 100%;
      height: 40px;
      padding: 10px;
      background-color: #0b1120;
      border: 1px solid var(--border);
      border-radius: 0;
      color: var(--text);
      font-size: 14px;
      box-sizing: border-box;
    }}
    input:focus {{
      outline: 2px solid var(--primary);
      border-color: transparent;
    }}
    .actions {{
      display: flex;
      gap: 10px;
    }}
    .actions-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .btn {{
      display: inline-block;
      padding: 10px 16px;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      border: 1px solid transparent;
      border-radius: 0;
      text-align: center;
      text-decoration: none;
    }}
    .btn-primary {{
      background-color: var(--primary);
      color: #ffffff;
    }}
    .btn-primary:hover {{
      background-color: var(--primary-hover);
    }}
    .btn-secondary {{
      background-color: transparent;
      border-color: var(--border);
      color: var(--text);
    }}
    .btn-secondary:hover {{
      background-color: #26334d;
    }}
    .btn-danger {{
      background-color: transparent;
      border-color: var(--danger);
      color: #ef4444;
    }}
    .btn-danger:hover {{
      background-color: var(--danger);
      color: #ffffff;
    }}
    .divider {{
      border: 0;
      border-top: 1px solid var(--border);
      margin: 20px 0;
    }}
    .alert {{
      padding: 12px 14px;
      font-size: 13px;
      margin-bottom: 20px;
      border: 1px solid transparent;
    }}
    .alert-success {{
      background-color: rgba(22, 163, 74, 0.15);
      border-color: var(--success);
      color: #4ade80;
    }}
    .alert-error {{
      background-color: rgba(220, 38, 38, 0.15);
      border-color: var(--danger);
      color: #f87171;
    }}
    .qr-container {{
      background-color: #ffffff;
      padding: 20px;
      display: flex;
      justify-content: center;
      border: 1px solid var(--border);
      margin-top: 12px;
    }}
    .qr-box {{
      font-family: monospace;
      font-size: 13px;
      line-height: 13px;
      letter-spacing: 0;
      background-color: #ffffff;
      color: #000000;
      user-select: all;
    }}
    footer {{
      margin-top: 32px;
      text-align: center;
      font-size: 12px;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>OpenBot</h1>
      <p>Servidor headless e autenticação de agentes</p>
    </header>

    {notice_block}

    <div class="card">
      <div class="status-row">
        <h2>Status do servidor</h2>
        <span class="badge">{badge_text}</span>
      </div>
      <p class="subtitle">{status_desc}</p>
      <ul class="info-list">
        <li><strong>Nome do servidor:</strong> {html.escape(server_name)}</li>
        <li><strong>Fase do servidor:</strong> {html.escape(server_phase)}</li>
        <li><strong>Versão instalada:</strong> {html.escape(version)}</li>
        {"<li><strong>E-mail:</strong> " + html.escape(user_email) + "</li>" if user_email else ""}
      </ul>
    </div>

    {qr_section}
    {login_section}
    {actions_section}
    {liquid_section}
    {typesafe_section}
    {moondream_section}

    <footer>
      <p>OpenBot rodando no Nonve via Umbrel</p>
    </footer>
  </div>

  <script>
    // Se o servidor estiver iniciando, atualiza a pagina automaticamente
    {"setTimeout(function() { window.location.reload(); }, 4000);" if not is_ready else ""}

    // Interceptacao assincrona dos formularios para melhor experiencia
    const formStart = document.getElementById("form-start");
    if (formStart) {{
      formStart.addEventListener("submit", async function(e) {{
        e.preventDefault();
        const btn = document.getElementById("btn-start");
        const email = document.getElementById("email").value;
        btn.disabled = true;
        btn.textContent = "Enviando...";
        try {{
          const res = await fetch("/api/login/start", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ email: email }})
          }});
          const data = await res.json();
          if (data.ok) {{
            document.getElementById("challenge").value = data.challenge || "";
            alert("Código de verificação enviado para " + email + ". Verifique sua caixa de entrada.");
            document.getElementById("code").focus();
          }} else {{
            alert(data.message || "Erro ao solicitar código.");
          }}
        }} catch (err) {{
          alert("Falha na requisição: " + err);
        }} finally {{
          btn.disabled = false;
          btn.textContent = "Enviar código";
        }}
      }});
    }}

    const formVerify = document.getElementById("form-verify");
    if (formVerify) {{
      formVerify.addEventListener("submit", async function(e) {{
        e.preventDefault();
        const btn = document.getElementById("btn-verify");
        const challenge = document.getElementById("challenge").value;
        const code = document.getElementById("code").value;
        if (!challenge) {{
          alert("Solicite primeiro o código antes de confirmar.");
          return;
        }}
        btn.disabled = true;
        btn.textContent = "Verificando...";
        try {{
          const res = await fetch("/api/login/verify", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ challenge: challenge, code: code }})
          }});
          const data = await res.json();
          if (data.ok) {{
            window.location.reload();
          }} else {{
            alert(data.message || "Código inválido ou expirado.");
          }}
        }} catch (err) {{
          alert("Falha na requisição: " + err);
        }} finally {{
          btn.disabled = false;
          btn.textContent = "Entrar";
        }}
      }});
    }}
  </script>
</body>
</html>"""
    return page


class OpenBotWebHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format_str, *args):
        # Suprime impressao de segredos e corpos de requisicao nos logs
        client = self.client_address[0] if self.client_address else "local"
        sys.stderr.write(f"[web] {client} - {format_str % args}\n")

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8", errors="replace")

    def _read_form_or_json(self):
        raw = self._read_body()
        ct = self.headers.get("Content-Type", "")
        if "application/json" in ct:
            try:
                return json.loads(raw)
            except Exception:
                return {}
        else:
            parsed = urllib.parse.parse_qs(raw)
            return {k: v[0] for k, v in parsed.items() if v}

    def _reply_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply_html(self, status, html_content):
        body = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            content = render_html_page()
            self._reply_html(200, content)
        elif path in ("/config", "/moondream"):
            content = render_html_page()
            self._reply_html(200, content)

        elif path == "/api/status":
            info = query_control_socket("GET", "/v1/status")
            self._reply_json(200 if info.get("_ok") else 503, {
                "ok": info.get("_ok", False),
                "account": info.get("account", "unknown"),
                "email": info.get("email", ""),
                "server": info.get("server", "unknown"),
                "server_name": info.get("server_name", ""),
                "version": info.get("version", "0.62.0"),
                "message": info.get("message") or info.get("account_message") or info.get("server_message") or "",
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]

        # API: Iniciar login
        if path in ("/api/login/start", "/login/start"):
            data = self._read_form_or_json()
            email = data.get("email", "").strip() or DEFAULT_EMAIL
            resp = query_control_socket("POST", "/v1/login/start", {"email": email})
            is_ok = resp.get("_ok", False)

            if path.startswith("/api/"):
                self._reply_json(200 if is_ok else 400, {
                    "ok": is_ok,
                    "challenge": resp.get("challenge", ""),
                    "expires_at": resp.get("expires_at", ""),
                    "error": resp.get("error", ""),
                    "message": resp.get("message", "Código enviado com sucesso." if is_ok else "Falha ao enviar código."),
                })
            else:
                if is_ok:
                    page = render_html_page(
                        notice=f"Código de verificação enviado para {email}. Verifique seu e-mail.",
                        challenge=resp.get("challenge", ""),
                    )
                else:
                    page = render_html_page(error_msg=resp.get("message", "Erro ao solicitar código."))
                self._reply_html(200 if is_ok else 400, page)

        # API: Verificar código
        elif path in ("/api/login/verify", "/login/verify"):
            data = self._read_form_or_json()
            challenge = data.get("challenge", "").strip()
            code = data.get("code", "").strip()
            resp = query_control_socket("POST", "/v1/login/verify", {"challenge": challenge, "code": code})
            is_ok = resp.get("_ok", False)

            if path.startswith("/api/"):
                self._reply_json(200 if is_ok else 400, {
                    "ok": is_ok,
                    "account": resp.get("account", ""),
                    "email": resp.get("email", ""),
                    "error": resp.get("error", ""),
                    "message": resp.get("message", "Login efetuado com sucesso." if is_ok else "Código inválido."),
                })
            else:
                if is_ok:
                    page = render_html_page(notice="Login realizado com sucesso.")
                else:
                    page = render_html_page(error_msg=resp.get("message", "Código inválido."), challenge=challenge)
                self._reply_html(200 if is_ok else 400, page)

        # API: Mobile Connect QR
        elif path in ("/api/mobile-connect", "/mobile-connect"):
            resp = query_control_socket("POST", "/v1/mobile-connect")
            is_ok = resp.get("_ok", False)
            qr_raw = resp.get("qr", "")
            qr_html = ""
            if is_ok and qr_raw:
                try:
                    terminal_text = base64.b64decode(qr_raw).decode("utf-8", errors="replace")
                    qr_html = ansi_to_html(terminal_text)
                except Exception:
                    qr_html = ""

            if path.startswith("/api/"):
                self._reply_json(200 if is_ok else 400, {
                    "ok": is_ok,
                    "qr_html": qr_html,
                    "expires_at": resp.get("expires_at", ""),
                    "error": resp.get("error", ""),
                    "message": resp.get("message", ""),
                })
            else:
                if is_ok and qr_html:
                    page = render_html_page(qr_html=qr_html)
                else:
                    page = render_html_page(error_msg=resp.get("message", "Não foi possível gerar o código QR."))
                self._reply_html(200 if is_ok else 400, page)

        # API: Logout
        elif path in ("/api/logout", "/logout"):
            resp = query_control_socket("POST", "/v1/logout")
            is_ok = resp.get("_ok", False)
            if path.startswith("/api/"):
                self._reply_json(200 if is_ok else 400, {
                    "ok": is_ok,
                    "error": resp.get("error", ""),
                    "message": resp.get("message", "Desconectado com sucesso."),
                })
            else:
                page = render_html_page(notice="Desconectado com sucesso.")
                self._reply_html(200, page)

        # API: Moondream Key
        elif path in ("/moondream/save", "/api/moondream/save"):
            data = self._read_form_or_json()
            key = data.get("key", "").strip()
            if not key:
                if path.startswith("/api/"):
                    self._reply_json(400, {"ok": False, "message": "A chave nao pode estar vazia."})
                else:
                    page = render_html_page(error_msg="A chave nao pode estar vazia.")
                    self._reply_html(400, page)
                return

            try:
                os.makedirs(os.path.dirname(MOONDREAM_KEY_PATH), exist_ok=True)
                with open(MOONDREAM_KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(key)
                os.chmod(MOONDREAM_KEY_PATH, 0o600)
                last4 = key[-4:] if len(key) >= 4 else key
                msg = f"salva (termina em {last4})"
                if path.startswith("/api/"):
                    self._reply_json(200, {"ok": True, "message": msg, "last4": last4})
                else:
                    page = render_html_page(notice=msg)
                    self._reply_html(200, page)
            except Exception as exc:
                if path.startswith("/api/"):
                    self._reply_json(500, {"ok": False, "message": str(exc)})
                else:
                    page = render_html_page(error_msg=f"Erro ao salvar: {exc}")
                    self._reply_html(500, page)

        # API: Liquid Key
        elif path in ("/liquid/save", "/api/liquid/save"):
            data = self._read_form_or_json()
            key = data.get("key", "").strip()
            if not key:
                if path.startswith("/api/"):
                    self._reply_json(400, {"ok": False, "message": "A chave nao pode estar vazia."})
                else:
                    page = render_html_page(error_msg="A chave nao pode estar vazia.")
                    self._reply_html(400, page)
                return

            try:
                os.makedirs(os.path.dirname(LIQUID_KEY_PATH), exist_ok=True)
                with open(LIQUID_KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(key)
                os.chmod(LIQUID_KEY_PATH, 0o600)
                last4 = key[-4:] if len(key) >= 4 else key
                msg = f"Chave Liquid AI salva (termina em {last4})"
                if path.startswith("/api/"):
                    self._reply_json(200, {"ok": True, "message": msg, "last4": last4})
                else:
                    page = render_html_page(notice=msg)
                    self._reply_html(200, page)
            except Exception as exc:
                if path.startswith("/api/"):
                    self._reply_json(500, {"ok": False, "message": str(exc)})
                else:
                    page = render_html_page(error_msg=f"Erro ao salvar: {exc}")
                    self._reply_html(500, page)

        # API: TypeSafe Key
        elif path in ("/typesafe/save", "/api/typesafe/save"):
            data = self._read_form_or_json()
            key = data.get("key", "").strip()
            if not key:
                if path.startswith("/api/"):
                    self._reply_json(400, {"ok": False, "message": "A chave nao pode estar vazia."})
                else:
                    page = render_html_page(error_msg="A chave nao pode estar vazia.")
                    self._reply_html(400, page)
                return

            try:
                os.makedirs(os.path.dirname(TYPESAFE_KEY_PATH), exist_ok=True)
                with open(TYPESAFE_KEY_PATH, "w", encoding="utf-8") as f:
                    f.write(key)
                os.chmod(TYPESAFE_KEY_PATH, 0o600)
                last4 = key[-4:] if len(key) >= 4 else key
                msg = f"Chave TypeSafe salva (termina em {last4})"
                if path.startswith("/api/"):
                    self._reply_json(200, {"ok": True, "message": msg, "last4": last4})
                else:
                    page = render_html_page(notice=msg)
                    self._reply_html(200, page)
            except Exception as exc:
                if path.startswith("/api/"):
                    self._reply_json(500, {"ok": False, "message": str(exc)})
                else:
                    page = render_html_page(error_msg=f"Erro ao salvar: {exc}")
                    self._reply_html(500, page)
        else:
            self.send_response(404)
            self.end_headers()


def run():
    server_address = ("0.0.0.0", SERVER_PORT)
    httpd = http.server.ThreadingHTTPServer(server_address, OpenBotWebHandler)
    sys.stderr.write(f"[web] Servidor de login OpenBot iniciado na porta {SERVER_PORT}...\n")
    has_md, _ = get_moondream_status()
    if has_md:
        sys.stderr.write("moondream: chave presente\n")
    else:
        sys.stderr.write("moondream: chave ausente\n")

    has_liq, _ = get_liquid_status()
    if has_liq:
        sys.stderr.write("liquid: chave presente\n")
    else:
        sys.stderr.write("liquid: chave ausente\n")

    has_ts, _ = get_typesafe_status()
    if has_ts:
        sys.stderr.write("typesafe: chave presente\n")
    else:
        sys.stderr.write("typesafe: chave ausente\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
