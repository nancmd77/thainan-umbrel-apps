"""Leitor do Papiro: devolve o texto completo de uma matéria (AlternativeTo com extração própria; outros sites, genérica).
O site fica atrás do Cloudflare; o Scrapling (Fetcher leve, curl_cffi imitando o TLS do Chrome/Safari) passa sem abrir navegador.
Só a rede interna do app usa. GET /ler?url=<matéria>  ->  {titulo, paragrafos, externos, links, apps_at, og, autor};  GET /app?url=<app no AlternativeTo> -> {nome, site, github}
GET /feed?url=<feed>  ->  o XML do feed, pra fontes que barram o Selah (403/429 do Cloudflare); só os domínios de FEED_HOSTS; último recurso = navegador (StealthyFetcher). Papiro 0.7 (30 out).
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
FEED_HOSTS = {'linustechtips.com', 'www.linustechtips.com', 'adrenaline.com.br', 'www.adrenaline.com.br', 'phys.org', 'www.phys.org'}
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
            if ul is not None: ext = [(espaco(a.get_all_text()), a.attrib.get('href', '')) for a in ul.css('a')][:8]
            break
    # links do corpo da matéria (anúncio oficial, notas de versão, site do app): só http(s) de fora do AlternativeTo
    corpo = [(espaco(a.get_all_text()), a.attrib.get('href', '')) for a in p.css('div.md_News a')]
    links, vistos = [], set()
    for nome, url in [(n, u) for n, u in ext] + corpo:
        if not re.match(r'^https?://', url or '') or re.search(r'//([^/]*\.)?alternativeto\.net(/|$)', url) or url in vistos or not nome: continue
        vistos.add(url); links.append({'nome': nome[:140], 'url': url[:500], 'ext': (nome, url) in ext})
    # apps citados (páginas do AlternativeTo): o Selah pergunta o site oficial e o GitHub de cada um em /app
    apps_at, va = [], set()
    for nome, url in corpo:
        m = re.match(r'^https://alternativeto\.net/software/([^/]+)/', url or '')
        if m and m.group(1) not in va and nome: va.add(m.group(1)); apps_at.append({'nome': nome[:60], 'url': f'https://alternativeto.net/software/{m.group(1)}/about/'})
    og = um(p, 'meta[property="og:image"]')
    autor = um(p, 'a.profile-link')
    return {'titulo': tit, 'paragrafos': ps, 'externos': [n for n, _ in ext if n], 'links': links[:12], 'apps_at': apps_at[:6],
            'og': og.attrib.get('content', '') if og is not None else '', 'autor': espaco(autor.get_all_text()) if autor is not None else ''}


FORA = re.compile(r'menu|nav|comment|footer|sidebar|related|newsletter|share|social|subscribe|promo|widget|cookie|banner', re.I)


def fora(e):
    """bloco de navegação/rodapé/comentários: não é a matéria"""
    while e is not None and e.tag not in ('body', 'html'):
        if e.tag in ('nav', 'header', 'footer', 'aside', 'form') or FORA.search(e.attrib.get('class', '') + ' ' + e.attrib.get('id', '')): return True
        e = e.parent
    return False


def extrair_generico(p, url):
    """Qualquer site: o bloco que junta mais texto em <p>/<li> (pontuação no pai e meia no avô, fora menus/rodapés);
    sem <p> (texto solto com <br>), o <article> ou corpo da matéria quebrado em linhas; sem nada, og:description."""
    meta = lambda k: (um(p, f'meta[property="{k}"]') or um(p, f'meta[name="{k}"]'))
    h1, ogt, ogd, og = um(p, 'h1'), meta('og:title'), meta('og:description'), meta('og:image')
    tit = espaco(h1.get_all_text()) if h1 is not None else (ogt.attrib.get('content', '') if ogt is not None else '')
    LIXO = re.compile(r'^(veja também|leia também|join the discussion|enter your email|subscribe|sign up|you can follow)', re.I)
    def texto(els): return [x for x in (espaco(e.get_all_text(separator='')) for e in els if not fora(e)) if len(x) > 40 and not LIXO.match(x)]
    nota, el = {}, {}
    for e in p.css('p, li'):
        t = espaco(e.get_all_text(separator=''))
        if len(t) < (80 if e.tag == 'li' else 40) or fora(e): continue
        for pai, peso in ((e.parent, 1), (e.parent.parent if e.parent is not None else None, .5)):
            if pai is None: continue
            k = pai._root; el[k] = pai; nota[k] = nota.get(k, 0) + len(t) * peso
    caixa = el[max(nota, key=nota.get)] if nota else None
    ps = texto(caixa.css('p, li')) if caixa is not None else []
    if sum(map(len, ps)) < 400:  # texto solto (ex.: TechPowerUp): linhas do corpo da matéria
        for c in p.css('[itemprop="articleBody"], .newspost, .entry-content, .post-content, .article-body, article'):
            linhas = [x for x in (espaco(l) for l in c.get_all_text(separator='\n').split('\n')) if len(x) > 60]
            if sum(map(len, linhas)) > sum(map(len, ps)): caixa, ps = c, linhas
            if sum(map(len, ps)) >= 400: break
    if not ps and ogd is not None and len(ogd.attrib.get('content', '')) > 40: ps = [espaco(ogd.attrib['content'])]
    host = re.sub(r'^www\.', '', urlparse(url).hostname or '')
    links, vistos = [], set()
    for a in (caixa.css('a') if caixa is not None else []):
        nome, u = espaco(a.get_all_text()), a.attrib.get('href', '')
        h = re.sub(r'^www\.', '', urlparse(u).hostname or '')
        if not re.match(r'^https?://', u) or not nome or u in vistos or h == host or h.endswith('.' + host): continue
        vistos.add(u); links.append({'nome': nome[:140], 'url': u[:500], 'ext': False})
    return {'titulo': tit, 'paragrafos': ps[:40], 'externos': [], 'links': links[:12], 'apps_at': [],
            'og': og.attrib.get('content', '') if og is not None else '', 'autor': ''}


def ler(url):
    erro = ''
    for perfil, espera in TENTATIVAS:
        if espera: time.sleep(espera)
        try:
            p = Fetcher.get(url, impersonate=perfil, stealthy_headers=True, timeout=30, retries=1)
            if p.status != 200: erro = f'{perfil}: HTTP {p.status}'; continue
            d = extrair(p) if urlparse(url).hostname in HOSTS else extrair_generico(p, url)
            if d['paragrafos']: d['perfil'] = perfil; return d
            erro = f'{perfil}: sem texto'
        except Exception as e:
            erro = f'{perfil}: {str(e)[:160]}'
    raise RuntimeError(erro)


def feed(url):
    """XML cru do feed (mesmo Fetcher e tentativas da matéria)."""
    erro = ''
    for perfil, espera in TENTATIVAS[:3]:
        if espera: time.sleep(espera)
        try:
            p = Fetcher.get(url, impersonate=perfil, stealthy_headers=True, timeout=30, retries=1)
            b = p.body if isinstance(p.body, bytes) else str(p.body).encode()
            if p.status == 200 and re.search(rb'<(rss|feed|rdf:RDF)[\s>]', b[:2000]): return b
            erro = f'{perfil}: HTTP {p.status}'
        except Exception as e:
            erro = f'{perfil}: {str(e)[:160]}'
    # Cloudflare "managed" (fórum da LTT): só um navegador de verdade passa. Mais lento (~15 s), por isso é o último recurso.
    try:
        from scrapling.fetchers import StealthyFetcher
        p = StealthyFetcher.fetch(url, headless=True, solve_cloudflare=True, timeout=90000)
        b = p.body if isinstance(p.body, bytes) else str(p.body).encode()
        if p.status == 200 and re.search(rb'<(rss|feed|rdf:RDF)[\s>]', b[:2000]): return b
        erro += f' | navegador: HTTP {p.status}'
    except Exception as e:
        erro += f' | navegador: {str(e)[:160]}'
    raise RuntimeError(erro)


def app(url):
    """Página do app no AlternativeTo -> site oficial e GitHub (os links marcados como "Official Website" e "GitHub")."""
    erro = ''
    for perfil, espera in TENTATIVAS[:3]:
        if espera: time.sleep(espera)
        try:
            p = Fetcher.get(url, impersonate=perfil, stealthy_headers=True, timeout=30, retries=1)
            if p.status != 200: erro = f'{perfil}: HTTP {p.status}'; continue
            site = gh = ''
            for a in p.css('a'):
                t, h = espaco(a.get_all_text()), a.attrib.get('href', '')
                if not re.match(r'^https?://', h) or 'sponsored' in (a.attrib.get('rel') or ''): continue
                if not site and t.startswith('Official Website'): site = h
                if not gh and re.match(r'^https://github\.com/[^/]+', h) and t in ('GitHub', 'View on GitHub'): gh = h
            h1 = um(p, 'h1')
            return {'nome': espaco(h1.get_all_text()) if h1 is not None else '', 'site': site[:300], 'github': gh[:300]}
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
        if u.path == '/app':
            alvo = (parse_qs(u.query).get('url') or [''])[0]
            if not re.match(r'^https://alternativeto\.net/software/[\w.-]+/about/$', alvo): return self.responder(400, {'erro': 'só páginas de app do AlternativeTo'})
            try: return self.responder(200, app(alvo))
            except Exception as e: print('[leitor] app falhou', alvo, e, flush=True); return self.responder(502, {'erro': str(e)})
        if u.path == '/feed':
            alvo = (parse_qs(u.query).get('url') or [''])[0]
            if urlparse(alvo).scheme != 'https' or urlparse(alvo).hostname not in FEED_HOSTS: return self.responder(400, {'erro': 'feed fora da lista'})
            try: b = feed(alvo)
            except Exception as e: print('[leitor] feed falhou', alvo, e, flush=True); return self.responder(502, {'erro': str(e)})
            self.send_response(200); self.send_header('Content-Type', 'application/xml; charset=utf-8')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b); return
        if u.path != '/ler': return self.responder(404, {'erro': 'rota'})
        alvo = (parse_qs(u.query).get('url') or [''])[0]
        a = urlparse(alvo)
        # qualquer site público (0.7): https e nome de domínio; nada de IP, localhost ou nome sem ponto (rede interna)
        if a.scheme != 'https' or not a.hostname or '.' not in a.hostname or re.match(r'^[\d.]+$|^\[', a.hostname) or a.hostname.endswith(('.local', '.lan', '.internal')): return self.responder(400, {'erro': 'endereço não permitido'})
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
