"""Leitor do Papiro: devolve o texto completo de uma matéria do AlternativeTo.
O site fica atrás do Cloudflare; o Scrapling (Fetcher leve, curl_cffi imitando o TLS do Chrome/Safari) passa sem abrir navegador.
Só a rede interna do app usa. GET /ler?url=<matéria>  ->  {titulo, paragrafos, externos, og, autor}
GET /saude -> ok
GET /mini?f=<caminho no cerebro>  ->  miniatura webp (480 px de largura) da capa, com cache em disco. Desempenho do Selah (23 out).
Na partida, gera em segundo plano as miniaturas que faltam."""
import json, os, sys, time, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import threading, hashlib
from scrapling.fetchers import Fetcher

CEREBRO = os.environ.get('CEREBRO_DIR', '/cerebro')
MINI_DIR = os.environ.get('MINI_DIR', '/leitor/mini')
MINI_W, MINI_Q = 480, 72
_trava = threading.Lock()

def mini(rel):
    """caminho relativo dentro do cerebro -> (bytes webp, etag). Só imagens, sem sair da pasta."""
    rel = os.path.normpath('/' + rel).lstrip('/')
    if not re.search(r'\.(jpe?g|png|webp)$', rel, re.I): raise ValueError('não é imagem')
    org = os.path.join(CEREBRO, rel)
    st = os.stat(org)
    chave = hashlib.sha1(f'{rel}|{st.st_size}|{int(st.st_mtime)}|{MINI_W}|{MINI_Q}'.encode()).hexdigest()[:20]
    dst = os.path.join(MINI_DIR, chave + '.webp')
    if not os.path.exists(dst):
        from PIL import Image
        with Image.open(org) as im:
            im = im.convert('RGB')
            if im.width > MINI_W: im = im.resize((MINI_W, round(im.height * MINI_W / im.width)), Image.LANCZOS)
            os.makedirs(MINI_DIR, exist_ok=True)
            tmp = dst + '.tmp'
            im.save(tmp, 'WEBP', quality=MINI_Q, method=4)
            os.replace(tmp, dst)
    with open(dst, 'rb') as f: return f.read(), chave

def aquecer():
    """gera as miniaturas que faltam, devagar, pra primeira abertura já sair leve"""
    base = os.path.join(CEREBRO, 'Reels', 'capas')
    if not os.path.isdir(base): return
    n = 0
    for nome in sorted(os.listdir(base)):
        try:
            mini('Reels/capas/' + nome); n += 1; time.sleep(0.02)
        except Exception: pass
    print('[leitor] miniaturas prontas', n, flush=True)


HOSTS = {'alternativeto.net', 'www.alternativeto.net'}
TENTATIVAS = [('chrome', 0), ('chrome', 4), ('safari', 0), ('safari', 8)]  # (perfil, espera antes em s)
espaco = lambda t: re.sub(r'\s+', ' ', t or '').strip()


def um(p, sel):
    r = p.css(sel)
    return r[0] if len(r) else None


def extrair(p):
    h1 = um(p, 'h1')
    tit = espaco(h1.get_all_text()) if h1 is not None else ''
    ps = [espaco(x.get_all_text(separator='')) for x in p.css('div.md_News p, div.md_News li')]
    ps = [x for x in ps if len(x) > 30]
    ext = []
    for h in p.css('h4'):
        if espaco(h.get_all_text()) == 'External links':
            ul = h.next
            while ul is not None and ul.tag != 'ul': ul = ul.next
            if ul is not None: ext = [espaco(a.get_all_text()) for a in ul.css('a')][:8]
            break
    og = um(p, 'meta[property="og:image"]')
    autor = um(p, 'a.profile-link')
    return {'titulo': tit, 'paragrafos': ps, 'externos': [x for x in ext if x],
            'og': og.attrib.get('content', '') if og is not None else '', 'autor': espaco(autor.get_all_text()) if autor is not None else ''}


def ler(url):
    erro = ''
    for perfil, espera in TENTATIVAS:
        if espera: time.sleep(espera)
        try:
            p = Fetcher.get(url, impersonate=perfil, stealthy_headers=True, timeout=30, retries=1)
            if p.status != 200: erro = f'{perfil}: HTTP {p.status}'; continue
            d = extrair(p)
            if d['paragrafos']: d['perfil'] = perfil; return d
            erro = f'{perfil}: sem texto'
        except Exception as e:
            erro = f'{perfil}: {str(e)[:160]}'
    raise RuntimeError(erro)


class H(BaseHTTPRequestHandler):
    def responder(self, cod, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(cod); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/saude': return self.responder(200, {'ok': True})
        if u.path == '/mini':
            try:
                b, etag = mini((parse_qs(u.query).get('f') or [''])[0])
            except FileNotFoundError: return self.responder(404, {'erro': 'sem arquivo'})
            except Exception as e: return self.responder(503, {'erro': str(e)})
            self.send_response(200); self.send_header('Content-Type', 'image/webp'); self.send_header('ETag', '"' + etag + '"')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b); return
        if u.path != '/ler': return self.responder(404, {'erro': 'rota'})
        alvo = (parse_qs(u.query).get('url') or [''])[0]
        a = urlparse(alvo)
        if a.scheme != 'https' or a.hostname not in HOSTS: return self.responder(400, {'erro': 'só matérias do AlternativeTo'})
        t0 = time.time()
        try:
            d = ler(alvo); print('[leitor] ok', d['perfil'], len(d['paragrafos']), f'{time.time()-t0:.1f}s', alvo, flush=True)
            self.responder(200, d)
        except Exception as e:
            print('[leitor] falhou', alvo, e, flush=True); self.responder(502, {'erro': str(e)})

    def log_message(self, *a): pass


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1].startswith('http'): print(json.dumps(ler(sys.argv[1]), ensure_ascii=False, indent=1)); sys.exit()
    porta = int(os.environ.get('PORT', '8790'))
    print('[leitor] ouvindo', porta, flush=True)
    threading.Thread(target=aquecer, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', porta), H).serve_forever()
