"""thainan-changelogs: Scrapling lê changelogs, organiza e manda pro pushr.

Fluxo: changedetection vigia GET /<fonte>.txt (lista já agrupada, uma linha por novidade).
Quando muda, ele chama POST /hook/<fonte>; aqui comparamos com o que já foi avisado e
mandamos ao pushr só o que é novo (ou atualizado, com replaceKey pra substituir o aviso antigo).
"""
import hashlib, html, json, os, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import aa

DATA = os.environ.get("DATA_DIR", "/data")
PUSHR = os.environ.get("PUSHR_URL", "http://192.168.18.171:3291")
PORT = int(os.environ.get("PORT", "8599"))
MAX_SOLTOS = 6  # acima disso, manda um resumo só

FONTES = {"aa": {"nome": "Artificial Analysis", "url": aa.URL, "parse": aa.parse}}
LOCK = threading.Lock()


def _p(n):
    return os.path.join(DATA, n)


def ler(n, padrao):
    try:
        with open(_p(n)) as f:
            return json.load(f)
    except Exception:
        return padrao


def gravar(n, obj):
    tmp = _p(n) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _p(n))


def token():
    try:
        return open(_p("pushr_token")).read().strip()
    except Exception:
        return ""


def log(msg):
    L = ler("log.json", [])
    L.append({"quando": time.strftime("%Y-%m-%d %H:%M"), "msg": msg})
    gravar("log.json", L[-80:])


def buscar(fonte):
    from scrapling.fetchers import Fetcher
    f = FONTES[fonte]
    p = Fetcher.get(f["url"], timeout=40)
    if p.status != 200:
        raise RuntimeError(f"{f['url']} respondeu {p.status}")
    itens = f["parse"](p.html_content)
    if not itens:
        raise RuntimeError("página veio sem itens (layout mudou?)")
    return itens


def enviar(payload):
    tk = token()
    if not tk:
        raise RuntimeError("sem token do pushr")
    corpo = json.dumps(payload).encode()
    idem = hashlib.sha256(corpo).hexdigest()[:40]
    r = Request(PUSHR + "/notify", data=corpo, method="POST", headers={
        "Authorization": "Bearer " + tk, "Content-Type": "application/json", "Idempotency-Key": idem})
    with urlopen(r, timeout=20) as resp:
        return resp.status


def checar(fonte, mandar=True):
    """Compara com o estado; manda o que é novo. Primeira vez só grava a base."""
    with LOCK:
        itens = buscar(fonte)
        est = ler(f"estado_{fonte}.json", None)
        if est is None:
            gravar(f"estado_{fonte}.json", {i["chave"]: i["linha"] for i in itens})
            log(f"{fonte}: base gravada com {len(itens)} itens, nada enviado")
            return []
        novos = [i for i in itens if est.get(i["chave"]) != i["linha"]]
        if not novos:
            return []
        nome = FONTES[fonte]["nome"]
        if mandar:
            if len(novos) > MAX_SOLTOS:
                corpo = "\n".join(i["nome"] for i in novos[:12])
                if len(novos) > 12:
                    corpo += f"\ne mais {len(novos) - 12}"
                enviar({"title": f"{nome}: {len(novos)} novidades", "body": corpo,
                        "url": FONTES[fonte]["url"], "replaceKey": f"{fonte}:resumo:{novos[0]['dia']}"})
            else:
                for i in novos:
                    tit = i["aviso_titulo"]
                    if i["chave"] in est:
                        tit = tit.replace(" avaliado", "") + " atualizado"
                    enviar({"title": tit, "body": i["aviso_corpo"], "url": i["url_aviso"],
                            "replaceKey": (f"{fonte}:" + i["chave"])[:128]})
        for i in novos:
            est[i["chave"]] = i["linha"]
        gravar(f"estado_{fonte}.json", est)
        log(f"{fonte}: {len(novos)} enviado(s): " + "; ".join(i["nome"] for i in novos)[:300])
        return novos


PAGINA = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Changelogs</title>
<style>body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 16px;color:#111}
h1{font-size:22px}h2{font-size:16px;margin-top:28px}input,button{font:inherit;padding:8px 10px;border:1px solid #bbb;border-radius:0}
button{background:#111;color:#fff;border-color:#111;cursor:pointer}li{margin:4px 0}.ok{color:#176b2c}.falta{color:#a33}
pre{white-space:pre-wrap;background:#f5f5f5;padding:10px}</style></head><body>
<h1>Changelogs pro pushr</h1>
<p>Token do pushr: %(tok)s</p>
<form method="post" action="/token"><input name="t" type="password" placeholder="Cole aqui o token pshr_ do app" size="40"> <button>Salvar token</button></form>
<form method="post" action="/teste" style="margin-top:10px"><button>Mandar aviso de teste</button></form>
<h2>Fontes</h2><ul>%(fontes)s</ul>
<h2>Últimos registros</h2><pre>%(log)s</pre></body></html>"""


class H(BaseHTTPRequestHandler):
    def _out(self, code, txt, tipo="text/plain; charset=utf-8"):
        b = txt.encode()
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            tk = token()
            st = '<span class="ok">salvo</span>' if tk else '<span class="falta">falta colar</span>'
            fontes = "".join(
                f"<li>{html.escape(f['nome'])}: <a href='/{k}.txt'>lista organizada</a>, "
                f"{len(ler('estado_' + k + '.json', {}))} itens já vistos</li>" for k, f in FONTES.items())
            lg = "\n".join(f"{x['quando']}  {x['msg']}" for x in reversed(ler("log.json", [])[-25:]))
            return self._out(200, PAGINA % {"tok": st, "fontes": fontes, "log": html.escape(lg) or "nada ainda"},
                             "text/html; charset=utf-8")
        if path.endswith(".txt") and path[1:-4] in FONTES:
            try:
                itens = buscar(path[1:-4])
                return self._out(200, "\n".join(i["linha"] for i in itens) + "\n")
            except Exception as e:
                return self._out(502, f"erro: {e}\n")
        if path == "/healthz":
            return self._out(200, "ok\n")
        self._out(404, "nada aqui\n")

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(n).decode(errors="replace") if n else ""
        if path == "/token":
            from urllib.parse import parse_qs
            t = (parse_qs(corpo).get("t") or [""])[0].strip()
            if t.startswith("pshr_"):
                with open(_p("pushr_token"), "w") as f:
                    f.write(t)
                os.chmod(_p("pushr_token"), 0o600)
                log("token do pushr salvo")
            self.send_response(303); self.send_header("Location", "/"); self.end_headers(); return
        if path == "/teste":
            try:
                enviar({"title": "Changelogs conectado", "body": "Teste do Scrapling + changedetection.",
                        "replaceKey": "changelogs:teste"})
                log("teste enviado ao pushr")
            except Exception as e:
                log(f"teste falhou: {e}")
            self.send_response(303); self.send_header("Location", "/"); self.end_headers(); return
        if path.startswith("/hook/") and path[6:] in FONTES:
            fonte = path[6:]
            def run():
                try:
                    checar(fonte)
                except Exception as e:
                    log(f"{fonte}: erro {e}")
                    traceback.print_exc()
            threading.Thread(target=run, daemon=True).start()
            return self._out(202, "ok\n")
        self._out(404, "nada aqui\n")


def base_inicial():
    for k in FONTES:
        if ler(f"estado_{k}.json", None) is None:
            try:
                checar(k, mandar=False)
            except Exception as e:
                log(f"{k}: base falhou: {e}")


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    threading.Thread(target=base_inicial, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
