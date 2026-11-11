"""Product Hunt: Apollo (não CSS), cache em disco e um único navegador reaproveitado."""
import atexit
import json
import os
import re
import time
import multiprocessing
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')
INTERVALOS = {'hoje': 2 * 3600, 'dia': 86400, 'semana': 12 * 3600, 'mes': 12 * 3600}
BASE = 'https://www.producthunt.com'
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='producthunt')
_navegador = None
_canal = None


def alvo(lista, agora=None):
    if lista not in INTERVALOS:
        raise ValueError('lista deve ser hoje, dia, semana ou mes')
    dia = ((agora or datetime.now(PACIFIC)).astimezone(PACIFIC) - timedelta(minutes=1)).date()
    if lista == 'dia': dia -= timedelta(days=1)
    if lista in ('hoje', 'dia'):
        caminho = f'daily/{dia.year}/{dia.month}/{dia.day}'
    elif lista == 'semana':
        ano, semana, _ = dia.isocalendar()
        caminho = f'weekly/{ano}/{semana}'
    else:
        caminho = f'monthly/{dia.year}/{dia.month}'
    return f'{BASE}/leaderboard/{caminho}'


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.dentro = False
        self.partes = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script': self.dentro = True; self.partes = []

    def handle_data(self, data):
        if self.dentro: self.partes.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.dentro:
            self.scripts.append(''.join(self.partes)); self.dentro = False


def objetos_apollo(html):
    """Decodifica JSON puro ou JSON embutido em strings JS, sem executar JavaScript.

    O transporte pode envolver os dados em chamadas/push e escapar JSON em strings.
    raw_decode reconhece os limites e ignora chaves/aspas dentro de textos de produtos.
    """
    parser = Scripts(); parser.feed(html)
    decoder = json.JSONDecoder()
    for script in parser.scripts:
        if not any(t in script for t in ('ApolloSSRDataTransport', '__typename', '__APOLLO')): continue
        fila = [script]
        vistos = set()
        while fila:
            texto = fila.pop()
            if texto in vistos: continue
            vistos.add(texto)
            i = 0
            while i < len(texto):
                if texto[i] not in '{["': i += 1; continue
                try: obj, fim = decoder.raw_decode(texto, i)
                except (ValueError, RecursionError): i += 1; continue
                i = fim
                if isinstance(obj, (dict, list)): yield obj
                elif isinstance(obj, str) and ('__typename' in obj or 'ApolloSSRDataTransport' in obj): fila.append(obj)


def posts_apollo(html):
    posts = {}
    def visitar(obj):
        if isinstance(obj, dict):
            if obj.get('__typename') == 'Post' and obj.get('id') is not None and obj.get('name'):
                id_ = str(obj['id'])
                # Objetos parciais repetidos não apagam campos de um Post mais completo.
                posts[id_] = {**posts.get(id_, {}), **{k: v for k, v in obj.items() if v is not None}}
            for valor in obj.values(): visitar(valor)
        elif isinstance(obj, list):
            for valor in obj: visitar(valor)
    for obj in objetos_apollo(html): visitar(obj)
    return list(posts.values())


def conexoes(obj):
    if isinstance(obj, dict):
        if obj.get('__typename') == 'HomefeedItemConnection': yield obj
        for valor in obj.values(): yield from conexoes(valor)
    elif isinstance(obj, list):
        for valor in obj: yield from conexoes(valor)


def paginar(page, capturas, html):
    """Rola a página real e recolhe JSON Apollo das respostas, até hasNextPage=false."""
    iniciais = [c for obj in objetos_apollo(html) for c in conexoes(obj)]
    if not iniciais: raise ValueError('Apollo sem conexão do leaderboard')
    info = iniciais[-1]['pageInfo']
    recebidas = []
    def receber(response):
        if response.status != 200 or 'json' not in response.headers.get('content-type', ''): return
        try: obj = response.json()
        except Exception: return
        cs = list(conexoes(obj))
        if cs: capturas.append(obj); recebidas.extend(cs)
    page.on('response', receber)
    try:
        cursores = set()
        while info.get('hasNextPage'):
            cursor = info.get('endCursor')
            if cursor in cursores: raise ValueError('Apollo repetiu cursor da paginação')
            cursores.add(cursor)
            antes = len(recebidas)
            page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            fim = time.monotonic() + 20
            while len(recebidas) == antes and time.monotonic() < fim: page.wait_for_timeout(500)
            if len(recebidas) == antes: raise ValueError('Product Hunt não carregou a próxima página')
            info = recebidas[-1]['pageInfo']
    finally: page.remove_listener('response', receber)


def texto(valor):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', str(valor or '')))).strip()


def numero(valor):
    if isinstance(valor, str) and re.fullmatch(r'\d+', valor): return int(valor)
    return valor if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


def normalizar(post):
    id_ = str(post['id'])
    slug = (post.get('product') or {}).get('slug')
    pagina = f'/products/{slug}' if slug else f'/posts/{post.get("slug") or id_}'
    uuid = post.get('thumbnailImageUuid')
    ranks = {p: numero(post.get(c)) for p, c in [('dia', 'dailyRank'), ('semana', 'weeklyRank'), ('mes', 'monthlyRank')]}
    return {
        'id': 'ph' + id_, 'phId': id_, 'fonte': 'producthunt', 'sigla': 'PH', 'nomeFonte': 'Product Hunt',
        'titulo': post['name'], 'link': urljoin(BASE, pagina),
        'data': post.get('featuredAt') or post.get('createdAt') or '',
        'resumo': texto(post.get('tagline')), 'descricao': texto(post.get('description')),
        'votos': None if post.get('hideVotesCount') else numero(post.get('latestScore')),
        'votosOcultos': bool(post.get('hideVotesCount')), 'launchDayScore': numero(post.get('launchDayScore')),
        'comentarios': numero(post.get('commentsCount')), 'ranks': ranks,
        'topicos': [e['node']['name'] for e in (post.get('topics') or {}).get('edges', []) if (e.get('node') or {}).get('name')],
        'site': urljoin(BASE, post.get('shortenedUrl') or f'/r/p/{id_}'),
        'imagem': f'https://ph-files.imgix.net/{uuid}' if uuid else '', 'apps': [post['name']],
        'nota': (post.get('product') or {}).get('reviewsRating') or None, 'avaliacoes': numero((post.get('product') or {}).get('reviewsCount')),
    }


def extrair(html, lista, dia=None):
    itens = [normalizar(p) for p in posts_apollo(html)]
    if lista in ('hoje', 'dia') and dia:
        itens = [it for it in itens if it['data'][:10] == dia]
    rank = {'dia': 'dia', 'semana': 'semana', 'mes': 'mes'}.get(lista)
    if rank:
        itens = [it for it in itens if it['ranks'][rank] and it['ranks'][rank] > 0]
        itens.sort(key=lambda it: (it['ranks'][rank], -(it['votos'] or 0)))
    else:
        itens = [it for it in itens if it['ranks']['dia'] and it['ranks']['dia'] > 0]
        itens.sort(key=lambda it: (-(it['votos'] or 0), it['ranks']['dia'] or 999999))
    if not itens: raise ValueError('Apollo sem Posts do ranking; cache anterior preservado')
    for pos, it in enumerate(itens, 1): it['listas'] = {lista: pos}
    return itens


def navegador(canal):
    """Playwright fica no mesmo processo/thread; prazo externo também limita o solver Cloudflare."""
    os.setsid()
    from scrapling.fetchers import StealthySession
    with StealthySession(headless=True, solve_cloudflare=True, max_pages=1, timeout=90000, retries=1) as sessao:
        while True:
            url = canal.recv()
            if url.startswith('produto:'):
                # página de um produto: o documento SSR já traz galeria, makers, links e descrição no Apollo
                url = url[8:]
                try:
                    docs = []
                    def preparar_p(page):
                        def doc(response):
                            if response.status == 200 and response.request.resource_type == 'document':
                                h = response.text()
                                if '"pageVariant"' in h: docs.append(h)
                        page.on('response', doc)
                    def acao_p(page):
                        fim = time.monotonic() + 30
                        while not docs and time.monotonic() < fim: page.wait_for_timeout(400)
                    sessao.fetch(url, solve_cloudflare=True, page_setup=preparar_p, page_action=acao_p)
                    canal.send({'html': docs[-1]} if docs else {'erro': 'Product Hunt: página do produto sem dados'})
                except Exception as e: canal.send({'erro': str(e)[:240]})
                continue
            try:
                capturas, documentos, concluida = [], [], []
                def preparar(page):
                    def documento(response):
                        if response.status != 200 or response.request.resource_type != 'document': return
                        h = response.text()
                        if any(c for obj in objetos_apollo(h) for c in conexoes(obj)): documentos.append(h)
                    page.on('response', documento)
                def acao(page):
                    # Cloudflare pode redirecionar após o primeiro load; o DOM hidratado já pode ter consumido os scripts SSR.
                    fim = time.monotonic() + 30
                    while not documentos and time.monotonic() < fim: page.wait_for_timeout(500)
                    if not documentos: raise ValueError('Apollo sem documento do leaderboard após Cloudflare')
                    paginar(page, capturas, documentos[-1])
                    concluida.append(True)
                sessao.fetch(url, solve_cloudflare=True, page_setup=preparar, page_action=acao)
                # Scrapling registra e engole exceções de page_action; não aceita uma página parcial como sucesso.
                if not concluida: raise RuntimeError('Product Hunt: paginação não concluída')
                html = documentos[-1]
                for obj in capturas:
                    html += '<script type="application/json">' + json.dumps(obj).replace('</', '<\\/') + '</script>'
                canal.send({'html': html})
            except Exception as e: canal.send({'erro': str(e)[:240]})


def fechar():
    global _navegador, _canal
    if _navegador is not None:
        if _navegador.is_alive():
            try: os.killpg(_navegador.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        _navegador.join(timeout=5)
        _navegador = None
    if _canal is not None: _canal.close(); _canal = None


def buscar(url):
    global _navegador, _canal
    if _navegador is None or not _navegador.is_alive():
        fechar()
        ctx = multiprocessing.get_context('spawn')
        _canal, filho = ctx.Pipe()
        _navegador = ctx.Process(target=navegador, args=(filho,), daemon=True)
        _navegador.start(); filho.close()
    try:
        _canal.send(url)
        if not _canal.poll(210): raise TimeoutError('Product Hunt: navegador excedeu 210 s')
        resultado = _canal.recv()
        if resultado.get('erro'): raise RuntimeError(resultado['erro'])
        return resultado['html']
    except Exception:
        fechar()
        raise


def coletar(lista):
    url = alvo(lista)
    pasta = Path(os.environ.get('PH_CACHE_DIR', str(Path(os.environ.get('STATE_DIR', '/tmp/papiro-leitor')) / 'producthunt')))
    pasta.mkdir(parents=True, exist_ok=True)
    chave = url.rsplit('/leaderboard/', 1)[1].replace('/', '-')
    arquivo = pasta / f'{lista}-{chave}.json'
    cache = None
    try: cache = json.loads(arquivo.read_text())
    except (OSError, ValueError): pass
    if cache and cache.get('paginado') and time.time() - cache['quando'] / 1000 < INTERVALOS[lista]: return {**cache, 'cache': True}
    inicio = time.monotonic()
    try:
        html = buscar(url)
        partes = url.rsplit('/', 3)
        dia = '-'.join([partes[1], partes[2].zfill(2), partes[3].zfill(2)]) if lista in ('hoje', 'dia') else None
        itens = extrair(html, lista, dia)
        resultado = {'lista': lista, 'url': url, 'itens': itens, 'quando': int(time.time() * 1000), 'segundos': round(time.monotonic() - inicio, 3), 'cache': False, 'paginado': True}
        tmp = arquivo.with_suffix('.tmp'); tmp.write_text(json.dumps(resultado, ensure_ascii=False)); tmp.replace(arquivo)
        # HTML útil para diagnosticar mudanças no transporte, sem outra visita ao Cloudflare.
        (pasta / f'{lista}.html').write_text(html)
        print('[producthunt]', lista, len(itens), resultado['segundos'], 's', url, flush=True)
        return resultado
    except Exception as e:
        return {**(cache or {}), 'erro': str(e)[:240], 'lista': lista}


def _achar_produto(objs):
    """O Apollo repete o produto em vários objetos; fica com o que traz mais galeria, makers e links."""
    achados = []
    def visitar(o):
        if isinstance(o, dict):
            p = o.get('product')
            if isinstance(p, dict) and isinstance(p.get('pageVariant'), dict): achados.append(p)
            for v in o.values(): visitar(v)
        elif isinstance(o, list):
            for v in o: visitar(v)
    for o in objs: visitar(o)
    def peso(p):
        pv = p['pageVariant']; lan, prod = pv.get('launch') or {}, pv.get('product') or {}
        return len(prod.get('media') or lan.get('media') or []) + len(lan.get('makers') or []) + len(lan.get('links') or []) + bool(lan.get('description'))
    return (max(achados, key=peso), achados) if achados else (None, [])


def extrair_produto(html):
    """Galeria, descrição, makers, links e números da página do produto (Apollo SSR)."""
    p, todos = _achar_produto(objetos_apollo(html))
    if not p: raise ValueError('página do produto sem Apollo')
    def primeiro(f):
        for q in [p, *todos]:
            try: v = f(q)
            except (AttributeError, KeyError, TypeError): v = None
            if v not in (None, '', [], {}): return v
        return None
    pv = p.get('pageVariant') or {}
    lan, prod = pv.get('launch') or {}, pv.get('product') or {}
    midia = []
    for m in (prod.get('media') or lan.get('media') or []):
        u = m.get('imageUuid') or ''
        if not re.match(r'^[a-f0-9-]+\.(png|jpe?g|gif|webp)$', u): continue
        midia.append({'uuid': u, 'w': m.get('originalWidth'), 'h': m.get('originalHeight'), 'video': m.get('mediaType') == 'video'})
    links = []
    for l in lan.get('links') or []:
        if l.get('redirectPath', '').startswith('/r/'):
            links.append({'nome': texto(l.get('storeName')), 'site': texto(l.get('websiteName')), 'url': BASE + l['redirectPath']})
    alternativas = []
    for e in (primeiro(lambda q: q['alternatives']['edges']) or [])[:4]:
        q = ((e or {}).get('node') or {}).get('product') or {}
        if q.get('name'): alternativas.append({'nome': texto(q['name']), 'legenda': texto(q.get('tagline'))})
    site = primeiro(lambda q: q['latestLaunch']['product']['websiteUrl']) or primeiro(lambda q: q['websiteUrl']) or ''
    return {
        'descricao': [t for t in (texto(x) for x in str(lan.get('description') or p.get('description') or '').split('\n')) if t],
        'midia': midia[:8],
        'makers': [{'nome': texto(m.get('name')), 'headline': texto(m.get('headline'))} for m in (lan.get('makers') or [])[:6] if m.get('name')],
        'links': links[:6],
        'alternativas': alternativas,
        'site': site if str(site).startswith('https://') else '',
        'seguidores': numero(primeiro(lambda q: q['followersCount'])),
        'nota': primeiro(lambda q: q['reviewsRating']) or None,
        'avaliacoes': numero(primeiro(lambda q: q['reviewsCount'])),
        'preco': texto(lan.get('pricingType')),
    }


def detalhe(url):
    if not re.match(r'^https://www\.producthunt\.com/(products|posts)/[\w.-]+(/launches/[\w.-]+)?$', url): raise ValueError('só páginas de produto do Product Hunt')
    pasta = Path(os.environ.get('PH_CACHE_DIR', str(Path(os.environ.get('STATE_DIR', '/tmp/papiro-leitor')) / 'producthunt'))) / 'produtos'
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / (re.sub(r'[^\w.-]+', '_', url.split('.com/', 1)[1]) + '.json')
    try:
        c = json.loads(arquivo.read_text())
        if time.time() - c['quando'] / 1000 < 7 * 86400: return {**c, 'cache': True}
    except (OSError, ValueError, KeyError): pass
    inicio = time.monotonic()
    d = extrair_produto(buscar('produto:' + url))
    d.update({'url': url, 'quando': int(time.time() * 1000), 'segundos': round(time.monotonic() - inicio, 3)})
    tmp = arquivo.with_suffix('.tmp'); tmp.write_text(json.dumps(d, ensure_ascii=False)); tmp.replace(arquivo)
    print('[producthunt] produto', url, len(d['midia']), d['segundos'], 's', flush=True)
    return d


def produto(url):
    return _EXECUTOR.submit(detalhe, url).result()


def producthunt(lista):
    if lista not in INTERVALOS: raise ValueError('lista deve ser hoje, dia, semana ou mes')
    return _EXECUTOR.submit(coletar, lista).result()


atexit.register(fechar)
