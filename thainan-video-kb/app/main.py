"""Video KB worker: link Reels/TikTok/página/PDF -> transcrição ou texto -> resumo -> SilverBullet + Open Notebook (Karakeep opcional).

Só stdlib (+ yt-dlp opcional, fallback do Cobalt). Tudo num arquivo de propósito.
"""
import base64
import glob
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from contextlib import closing, contextmanager
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit

DATA = os.environ.get("DATA_DIR", "/data")
# v1.12: sem SilverBullet, as páginas e a mídia vão pra esta pasta (mesmo formato do space); o Selah lê daqui.
SB_LOCAL = os.environ.get("SB_LOCAL", "")
PORT = int(os.environ.get("PORT", "8080"))
SCRIBE_MODEL = "scribe_v2"  # doc POST /v1/speech-to-text (confirmado 2026-10)
COOKIE_ERR = re.compile(r"login|log in|sign in|cookie|authentica|401|403|private|checkpoint", re.I)
SITES = ("instagram.com", "tiktok.com")  # sempre vídeo; outros links: sonda do yt-dlp decide
GRIDS_MAX = 4_000_000  # folhas 3x3 pro Gemini: acima disso redimensiona
MIN_TEXT = 200  # menos que isso não dá resumo honesto
KIND_PT = {"video": "Vídeo", "pagina": "Página", "pdf": "PDF", "texto": "Texto"}

# (chave, rótulo, tipo, dica, padrão). Tipo "h" abre seção recolhível. Padrões = comportamento da v1.4 (menos a qualidade do vídeo).
FIELDS = [
    ("h_con", "Conexões", "h", "", ""),
    ("elevenlabs_key", "Chave ElevenLabs", "secret", "Scribe (transcrição).", ""),
    ("llm_url", "URL do 9Router", "text", "OpenAI-compatível (termina em /v1).", "http://thainan-9router_web_1:20128/v1"),
    ("llm_key", "Chave 9Router", "secret", "Resumo com Gemini.", ""),
    ("cobalt_url", "URL do Cobalt", "text", "Baixa o vídeo.", "http://192.168.18.171:9013"),
    ("karakeep_url", "URL do Karakeep", "text", "", "http://192.168.18.171:8620"),
    ("karakeep_key", "API key do Karakeep", "secret", "Karakeep > Configurações > API keys.", ""),
    ("notebook_url", "URL do Open Notebook (API)", "text", "Porta 5055.", "http://thainan-opennotebook_web_1:5055"),
    ("notebook_pass", "Senha do Open Notebook", "secret", "Deixe vazio se não houver senha.", ""),
    ("sb_url", "URL do SilverBullet", "text", "Segundo cérebro (porta 3002). Vazio = desligado.", "http://192.168.18.171:3002"),
    ("sb_token", "Token do SilverBullet", "secret", "Token de API do SilverBullet.", ""),
    ("webhook_token", "Token do webhook", "secret", "Mesmo valor que você põe no webhook do Karakeep. Vazio = webhook desligado.", ""),
    ("h_ia", "IA e textos", "h", "", ""),
    ("model", "Modelo de resumo", "text", "Nome do modelo no 9Router. Sempre na variante high.", "ag/gemini-3.8-flash-high"),
    ("model_visao", "Modelo de visão", "text", "Usado quando vão as imagens do vídeo. Vazio = o de resumo.", ""),
    ("model_sintese", "Modelo de síntese", "text", "Síntese do assunto e reestilo. Vazio = o de resumo.", ""),
    ("model_links", "Modelo dos links", "text", "Acha o que o vídeo cita e escolhe o link certo. Vazio = o de resumo.", "ag/gemini-3.8-flash-high"),
    ("decider_on", "Segunda opinião do Decider", "bool", "Modelo de decisão confere as escolhas fechadas: se o link entra, se a página é a certa, se é meme e qual assunto fixo. Abaixo de 85% de certeza, fica de fora (meme só é pulado quando o Gemini e o Decider concordam). Fora do ar = vale o Gemini.", "1"),
    ("model_reserva", "Modelo reserva", "text", "Tentado uma vez se o principal falhar. Vazio = sem reserva.", ""),
    ("resumo_frases", "Resumo: máximo de frases", "num", "O resumo tem de 2 até este número de frases.", "4"),
    ("pontos_max", "Pontos-chave: máximo", "num", "De 3 até este número.", "7"),
    ("tags_max", "Tags: máximo", "num", "De 3 até este número.", "8"),
    ("assuntos_fixos", "Assuntos fixos", "lines", "Um por linha. Preenchido: a IA só escolhe daqui (se errar, vai pro primeiro). Vazio = livre.", ""),
    ("estilo_extra", "Instrução extra de estilo", "lines", "Soma ao estilo fixo (humanizado, curto, TDAH). Não substitui.", ""),
    ("sintese_on", "Síntese por assunto no SilverBullet", "bool", "", "1"),
    ("reestilo_on", "Reescrever no estilo os Reels antigos", "bool", "", "1"),
    ("h_video", "Vídeo e mídia", "h", "", ""),
    ("dl_ordem", "Ordem do download", "choice", "Se o primeiro falhar, tenta o outro.", "cobalt"),
    ("dl_qualidade", "Qualidade do download", "choice", "Vídeos curtos. Máxima = original.", "max"),
    ("curto_min", "Vídeo curto: até (min)", "num", "Até aqui é curto. Acima é longo.", "3"),
    ("curto_mb", "Vídeo curto: limite no SilverBullet (MB)", "num", "Passou disso: comprime em H.264 pra caber. Cabe: vai o original.", "60"),
    ("longo_politica", "Vídeos longos no SilverBullet", "choice", "", "link"),
    ("longo_res", "Vídeo longo: resolução", "choice", "Usada no download e na compressão de longos.", "720"),
    ("longo_mb", "Vídeo longo: limite (MB)", "num", "Só vale com \"enviar comprimido\".", "200"),
    ("tl_quadros", "Linha do tempo: quadros", "num", "Máximo de quadros na página do Reel.", "16"),
    ("tl_largura", "Linha do tempo: largura dos quadros (px)", "num", "", "360"),
    ("idioma", "Idioma da transcrição", "text", "Vazio = automático. Ou código ISO: por, eng, spa.", ""),
    ("max_frames", "Visão: máximo de frames", "num", "Frames que o crv tira do vídeo (viram folhas 3x3 pro Gemini).", "60"),
    ("max_min", "Duração máxima de vídeo (min)", "num", "Vídeo mais longo não é transcrito (custo do Scribe). Vale pra links fora de IG/TikTok.", "60"),
    ("h_dest", "Destinos", "h", "Desligar para de gravar ali. Entrada dos links: caixa no topo da fila e coletores (Karakeep é opcional).", ""),
    ("kk_on", "Usar o Karakeep (entrada por bookmark e gravar resumo, nota, tags, lista)", "bool", "Desligado: o Video KB lê páginas e PDFs sozinho.", "0"),
    ("nb_on", "Enviar ao Open Notebook", "bool", "", "1"),
    ("links_on", "Links do que o vídeo apresenta", "bool", "Ferramenta, produto, livro, episódio de podcast ou repositório que é o assunto do vídeo ou que o criador recomenda: o Video KB acha a página certa e põe no card. Menção de passagem não entra.", "0"),
    ("searx_url", "Busca (SearXNG)", "text", "Busca própria usada pra achar os links. Vem junto com o app.", "http://thainan-video-kb_searx_1:8080"),
    ("pular_humor", "Pular memes e humor", "bool", "Meme, piada, esquete ou trend engraçada sem informação útil não entra (fica como Ignorado na lista). Post só de foto também não entra.", "1"),
    ("sb_on", "Escrever no SilverBullet", "bool", "", "1"),
    ("sb_reels", "SilverBullet: pasta dos Reels", "text", "Mudar o nome não apaga a pasta antiga.", "Reels"),
    ("sb_temas", "SilverBullet: pasta dos assuntos", "text", "", "Temas"),
    ("sb_pagina", "SilverBullet: página principal", "text", "", "Video KB"),
    ("h_mural", "Mural", "h", "Galeria de cards (capa + vídeo) no topo de Video KB e index.", ""),
    ("mural_on", "Gerar o mural", "bool", "", "1"),
    ("h_col", "Coletores e fila", "h", "Cada item novo entra direto na fila (e vira bookmark no Karakeep só se ele estiver ligado).", ""),
    ("col_min", "Intervalo da coleta (min)", "num", "", "30"),
    ("first_run", "Itens na 1ª coleta", "num", "1ª coleta de cada fonte: só os N mais recentes. O resto vira visto.", "20"),
    ("crawl_tries", "Karakeep lendo a página: tentativas", "num", "", "3"),
    ("crawl_wait", "Karakeep lendo a página: espera (s)", "num", "", "120"),
    ("poll_secs", "Busca de bookmarks novos no Karakeep (s)", "num", "Reserva do webhook.", "300"),
    ("col_youtube", "YouTube: playlists", "bool", "", ""),
    ("yt_playlists", "Playlists do YouTube", "lines", "Uma por linha: URL da playlist, :ytwatchlater (Assistir mais tarde) ou LL (Curtidos).", ":ytwatchlater\nLL"),
    ("yt_cookies", "Cookies do YouTube", "area", "cookies.txt (Netscape). Obrigatório pra Assistir mais tarde e Curtidos.", ""),
    ("col_instagram", "Instagram: salvos", "bool", "", ""),
    ("ig_user", "Usuário do Instagram", "text", "Sem @. Usado no login e nos salvos.", ""),
    ("ig_pass", "Senha do Instagram", "secret", "Com usuário e senha, o Video KB entra sozinho na sua conta, guarda a sessão e refaz o login quando ela cai. Só a lista de salvos usa a conta. Cookies viram reserva.", ""),
    ("ig_totp", "Chave do autenticador (2FA)", "secret", "Opcional. O código-semente (letras e números) que o Instagram mostra ao ligar o app autenticador. Com ela, o código de 6 dígitos sai sozinho.", ""),
    ("ig_codigo", "Código de verificação", "text", "Só quando o Instagram pedir código por e-mail ou SMS (o aviso aparece em Coletores). Usado uma vez e apagado.", ""),
    ("hiker_key", "Chave da HikerAPI", "secret", "Opcional. Legenda, números, capa e vídeo de cada Reel vêm por ela, sem usar sua conta. hikerapi.com > Tokens.", ""),
    ("ig_cookies", "Cookies do Instagram", "area", "Reserva. cookies.txt (Netscape), só se não houver senha.", ""),
    ("col_reddit", "Reddit: salvos", "bool", "", ""),
    ("reddit_feed", "Feed RSS dos salvos do Reddit", "secret", "reddit.com/prefs/feeds > linha \"saved\" > RSS. Não precisa de cookie.", ""),
    ("col_tiktok", "TikTok: favoritos (experimental)", "bool", "", ""),
    ("tt_user", "Usuário do TikTok", "text", "Sem @.", ""),
    ("tt_cookies", "Cookies do TikTok", "area", "cookies.txt (Netscape). Usado pelo coletor e pelo download.", ""),
    ("h_tela", "Tela", "h", "", ""),
    ("ui_tema", "Tema da tela", "choice", "Automático segue o sistema (claro/escuro).", "auto"),
]
NUM = {"resumo_frases": (2, 10), "pontos_max": (3, 15), "tags_max": (3, 20), "curto_min": (1, 60), "curto_mb": (5, 2000), "longo_mb": (10, 4000),
       "tl_quadros": (1, 60), "tl_largura": (120, 1920), "max_frames": (9, 200), "max_min": (1, 600),
       "col_min": (5, 1440), "first_run": (1, 200), "crawl_tries": (0, 20), "crawl_wait": (30, 3600), "poll_secs": (60, 3600)}
CHOICES = {"dl_ordem": [("cobalt", "Cobalt, depois yt-dlp"), ("ytdlp", "yt-dlp, depois Cobalt")],
           "dl_qualidade": [("max", "Máxima (original)"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")],
           "longo_politica": [("link", "Não enviar o vídeo: link do original + capa + quadros"), ("comprimir", "Enviar comprimido")],
           "longo_res": [("1080", "1080p"), ("720", "720p"), ("480", "480p"), ("360", "360p")],
           "ui_tema": [("auto", "Automático"), ("claro", "Claro"), ("escuro", "Escuro")]}
SECRETS = {k for k, _, t, _, _ in FIELDS if t in ("secret", "area")}
COOKIES = {"instagram.com": "ig_cookies", "tiktok.com": "tt_cookies", "youtube.com": "yt_cookies", "youtu.be": "yt_cookies"}
DEFAULTS = {k: d for k, _, _, _, d in FIELDS}
TYPE = {k: t for k, _, t, _, _ in FIELDS}
LABEL = {k: l for k, l, _, _, _ in FIELDS}

KEY = {"tipo": "kind", "visao": "visao", "transcricao": "transcript", "texto": "transcript", "resumo": "analysis", "links": "links", "karakeep": "kk", "notebook": "nb", "silverbullet": "sb7"}
STAGE_PT = {"tipo": "Identificando", "texto": "Lendo o texto", "audio": "Baixando vídeo", "visao": "Vendo o vídeo", "transcricao": "Transcrevendo", "resumo": "Resumindo", "links": "Buscando links",
            "karakeep": "Gravando no Karakeep", "notebook": "Enviando ao Open Notebook", "silverbullet": "Preparando pro Selah" if os.environ.get("SB_LOCAL") else "Escrevendo no SilverBullet",
            "novo": "Aguardando", "pronto": "Concluído"}
STATUS_PT = {"fila": "Na fila", "rodando": "Rodando", "erro": "Erro", "pronto": "Pronto", "ignorado": "Ignorado"}

WAKE = threading.Event()
LOCK = threading.Lock()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------- config (chaves só aqui, chmod 600, nunca em log) ----------
def cfg_path():
    return os.path.join(DATA, "config.json")


def cfg():
    try:
        with open(cfg_path()) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: alto(v) if k in MODELOS else v for k, v in saved.items()}}


MODELOS = ("model", "model_visao", "model_sintese", "model_links", "model_reserva")


def alto(v):
    """Regra do nan (20 out 2026): modelos sempre em high. Gemini salvo em minimal/low/medium vira -high."""
    return re.sub(r"^(ag/gemini-[\w.-]+?)-(minimal|low|medium)$", r"\1-high", v) if isinstance(v, str) else v


def num(c, k):
    """Número da config; inválido (config antiga ou editada à mão) = padrão."""
    try:
        return int(c.get(k))
    except (TypeError, ValueError):
        return int(DEFAULTS[k])


def valida(k, v):
    """Campo numérico do formulário/importação: inteiro dentro de NUM[k]. Erro = ValueError com mensagem pro usuário."""
    lo, hi = NUM[k]
    if not re.fullmatch(r"-?\d+", str(v).strip()) or not lo <= int(v) <= hi:
        raise ValueError(f"{LABEL[k]}: use um número de {lo} a {hi}")
    return str(int(v))


def cfg_save(updates):
    c = cfg()
    c.update(updates)
    tmp = cfg_path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(c, f)
    os.replace(tmp, cfg_path())
    os.chmod(cfg_path(), 0o600)


def mask(v):
    return "" if not v else "••••" + (v[-4:] if len(v) >= 8 else "")


def scrub(s):
    s = str(s)
    for k in SECRETS:
        v = cfg().get(k)
        if v and len(v) >= 4:
            s = s.replace(v, "***")
    return s


# ---------- db ----------
def q(sql, args=()):
    with LOCK, closing(sqlite3.connect(os.path.join(DATA, "kb.db"), timeout=30)) as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]
        c.commit()
        return rows


def init():
    os.makedirs(os.path.join(DATA, "tmp"), exist_ok=True)
    q("""CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY, url TEXT UNIQUE, orig TEXT, bookmark_id TEXT,
        stage TEXT DEFAULT 'novo', status TEXT DEFAULT 'fila', error TEXT DEFAULT '',
        data TEXT DEFAULT '{}', created REAL, updated REAL)""")
    q("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
    q("CREATE TABLE IF NOT EXISTS seen(fonte TEXT, url TEXT, PRIMARY KEY(fonte, url))")  # coletores: já visto
    cols = {r["name"] for r in q("PRAGMA table_info(jobs)")}
    for col, ddl in (("kind", "TEXT DEFAULT ''"), ("after", "REAL DEFAULT 0")):  # v1.1: migra banco da 1.0
        if col not in cols:
            q(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
    q("UPDATE jobs SET status='fila' WHERE status='rodando'")  # crash no meio -> retoma
    if not q("SELECT 1 FROM kv WHERE k='v1.8.0'"):  # v1.8.0: nan tirou o Karakeep da stack (17 out); liga de novo em Configurações
        cfg_save({"kk_on": "0"})
        q("INSERT OR REPLACE INTO kv VALUES('v1.8.0','1')")
    if not q("SELECT 1 FROM kv WHERE k='v1.6.3'"):  # v1.6.3: URLs relativas no mural; regera nos dois
        q("INSERT OR REPLACE INTO kv VALUES('mural_sujo','1')")
        q("INSERT OR REPLACE INTO kv VALUES('v1.6.3','1')")


def norm(url):
    u = urlsplit(url.strip())
    host = re.sub(r"^(www|m)\.", "", (u.hostname or "").lower())
    # IG/TikTok: query é só rastreio. Outros sites: query pode ser o conteúdo (watch?v=), só tira utm_*
    qs = "" if is_target(url) else urlencode([(k, v) for k, v in parse_qsl(u.query) if not k.startswith("utm_")])
    return f"https://{host}{u.path.rstrip('/')}" + (f"?{qs}" if qs else "")  # ponytail: short links (vm.tiktok) não deduplicam


def is_target(url):
    h = (urlsplit(url).hostname or "").lower()
    return any(h == s or h.endswith("." + s) for s in SITES)


def add_job(url, bookmark_id=None):
    """Idempotente por URL normalizada (sem URL: pelo id do bookmark). Retorna True se criou."""
    if not url and not bookmark_id:
        return False
    n = norm(url) if url else f"karakeep:{bookmark_id}"
    now = time.time()
    if q("SELECT id FROM jobs WHERE url=?", (n,)):
        if bookmark_id:
            q("UPDATE jobs SET bookmark_id=? WHERE url=? AND bookmark_id IS NULL", (bookmark_id, n))
        return False
    q("INSERT INTO jobs(url,orig,bookmark_id,created,updated) VALUES(?,?,?,?,?)",
      (n, (url or "").strip(), bookmark_id, now, now))
    WAKE.set()
    return True


def job(jid):
    r = q("SELECT * FROM jobs WHERE id=?", (jid,))
    if r:
        r[0]["data"] = json.loads(r[0]["data"] or "{}")
    return r[0] if r else None


def save(j):
    q("UPDATE jobs SET bookmark_id=?,stage=?,status=?,error=?,data=?,kind=?,after=?,updated=? WHERE id=?",
      (j["bookmark_id"], j["stage"], j["status"], j["error"], json.dumps(j["data"]), j["data"].get("kind", ""),
       j.get("after") or 0, time.time(), j["id"]))


# ---------- http ----------
def req(method, url, headers=None, body=None, timeout=60, raw=False):
    """Única porta de rede (os testes trocam esta função)."""
    h = dict(headers or {})
    h.setdefault("User-Agent", UA)  # urllib puro leva 403/1010 do Cloudflare (HikerAPI)
    data = body
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    where = urlsplit(url)
    where = f"{where.netloc}{where.path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=timeout) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(scrub(f"{method} {where} -> HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(scrub(f"{method} {where} falhou: {getattr(e, 'reason', e)}"))
    if raw:
        return payload
    return json.loads(payload) if payload else {}


def multipart(fields, fname, fbytes):
    b = uuid.uuid4().hex
    out = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    out.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
               f"Content-Type: application/octet-stream\r\n\r\n".encode() + fbytes + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


# ---------- etapas ----------
def need(c, *keys):
    miss = [k for k in keys if not c.get(k)]
    if miss:
        raise RuntimeError("Configure em Configurações: " + ", ".join(miss))


def cobalt_video(c, url, out, qual):
    r = req("POST", c["cobalt_url"].rstrip("/") + "/", {"Accept": "application/json"},
            {"url": url, "downloadMode": "auto", "videoQuality": qual}, timeout=90)
    link = r.get("url") if r.get("status") in ("tunnel", "redirect") else (r.get("audio") if r.get("status") == "picker" else None)
    if not link:
        raise RuntimeError("Cobalt: " + str((r.get("error") or {}).get("code") or r.get("status") or r)[:150])
    if r["status"] == "tunnel":  # Cobalt devolve o próprio host (ex.: nonve.local), que o container pode não resolver
        link = urlsplit(link)._replace(scheme=urlsplit(c["cobalt_url"]).scheme, netloc=urlsplit(c["cobalt_url"]).netloc).geturl()
    path = out + (os.path.splitext(r.get("filename") or "")[1] or (".mp3" if r["status"] == "picker" else ".mp4"))
    with open(path, "wb") as f:
        f.write(req("GET", link, timeout=180, raw=True))
    return path


@contextmanager
def cookie_file(text):
    """cookies.txt temporário (600), apagado logo depois. Sem texto: None."""
    if not text:
        yield None
        return
    path = os.path.join(DATA, "tmp", "ck-" + uuid.uuid4().hex)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        yield path
    finally:
        os.remove(path)


def site_cookies(c, url):
    h = (urlsplit(url).hostname or "").lower()
    return next((c.get(k) for d, k in COOKIES.items() if h == d or h.endswith("." + d)), None)


def ytdlp_video(c, url, out, qual):
    h = "" if qual == "max" else f"[height<={qual}]"
    cmd = [sys.executable, "-m", "yt_dlp", "-f", f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/b{h}[ext=mp4]/bv*{h}+ba/b{h}/b/ba",
           "--merge-output-format", "mp4", "--no-playlist", "-o", out + ".%(ext)s", url]
    with cookie_file(site_cookies(c, url)) as ck:
        p = subprocess.run(cmd + (["--cookies", ck] if ck else []), capture_output=True, text=True, timeout=300)
    files = [f for f in glob.glob(out + ".*") if not f.endswith(".part")]
    if p.returncode or not files:
        raise RuntimeError("yt-dlp: " + scrub((p.stderr.strip().splitlines() or ["falhou"])[-1])[:200])
    return files[0]


# ---------- Instagram sem cookie (v1.10) ----------
# Conta do usuário (instagrapi) SÓ pra lista de salvos; cada Reel (legenda, números, capa, vídeo) pela HikerAPI,
# que usa as contas dela. Sem chave da HikerAPI: o próprio login faz o resto. Nada disso: cookies + Cobalt/yt-dlp, como antes.
IG_LOCK = threading.Lock()
IG = {"cl": None, "chave": None}
IG_INFO = {}  # url normalizada -> (quando, info): legenda, métricas e capa do mesmo Reel saem de uma chamada só
IG_ERR = "Instagram (login): "


def ig_sessao():
    return os.path.join(DATA, "ig-session.json")


def is_ig(url):
    h = (urlsplit(url).hostname or "").lower()
    return h == "instagram.com" or h.endswith(".instagram.com")


def ig_login_on(c):
    return bool((c.get("ig_user") or "").strip() and c.get("ig_pass"))


def ig_dump(cl):
    tmp = ig_sessao() + ".tmp"
    cl.dump_settings(tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ig_sessao())


def ig_client(c, novo=False):
    """Cliente logado, reaproveitando a sessão salva. Instagram pediu código: usa o de Configurações (uma vez) ou avisa."""
    user = c["ig_user"].strip().lstrip("@")
    chave = (user, c["ig_pass"], c.get("ig_totp") or "")
    with IG_LOCK:
        if IG["cl"] is not None and IG["chave"] == chave and not novo:
            return IG["cl"]
        try:
            from instagrapi import Client
        except ImportError:
            raise RuntimeError(IG_ERR + "biblioteca instagrapi não instalada (o app instala ao reiniciar)")
        codigo = re.sub(r"\D", "", c.get("ig_codigo") or "")

        def pede_codigo(_user, escolha):
            if codigo:
                return codigo
            raise RuntimeError(IG_ERR + f"o Instagram mandou um código ({str(escolha).split('.')[-1].lower()}). "
                               "Cole em Configurações > Código de verificação; a próxima coleta usa.")

        cl = Client()
        cl.delay_range = [2, 5]
        cl.challenge_code_handler = pede_codigo
        if os.path.exists(ig_sessao()) and not novo:
            try:
                cl.load_settings(ig_sessao())
            except Exception as e:
                log("sessão do Instagram ilegível, novo login:", scrub(e)[:120])
        seed = re.sub(r"\s", "", c.get("ig_totp") or "").upper()
        try:
            cl.login(user, c["ig_pass"], verification_code=cl.totp_generate_code(seed) if seed else codigo)
        except RuntimeError:
            raise
        except Exception as e:
            nome = type(e).__name__
            dica = {"BadPassword": "senha recusada", "TwoFactorRequired": "precisa do 2FA: preencha a chave do autenticador ou o código",
                    "ChallengeRequired": "o Instagram quer confirmar que é você: abra o app, aprove 'Fui eu' e tente de novo",
                    "PleaseWaitFewMinutes": "o Instagram pediu pra esperar uns minutos", "FeedbackRequired": "o Instagram limitou a conta por agora"}
            raise RuntimeError(IG_ERR + dica.get(nome, nome) + ". " + scrub(e)[:160])
        if codigo:
            cfg_save({"ig_codigo": ""})  # código de uso único
        ig_dump(cl)
        IG.update(cl=cl, chave=chave)
        return cl


def ig_call(c, fn):
    """Chama com o cliente logado; sessão derrubada no meio: um novo login e tenta de novo uma vez."""
    try:
        r = fn(ig_client(c))
    except RuntimeError:
        raise
    except Exception as e:
        if type(e).__name__ not in ("LoginRequired", "ReloginAttemptExceeded", "ClientUnauthorizedError"):
            raise RuntimeError(IG_ERR + scrub(f"{type(e).__name__}: {e}")[:200])
        log("Instagram derrubou a sessão; novo login")
        r = fn(ig_client(c, novo=True))
    with IG_LOCK:
        if IG["cl"] is not None:
            try:
                ig_dump(IG["cl"])
            except OSError:
                pass
    return r


def ig_norm(m):
    """Media (instagrapi ou HikerAPI, mesmo formato) -> o dict que o resto do worker já lê do yt-dlp -j."""
    m = json.loads(json.dumps(m, default=str))
    u = m.get("user") or {}
    video = m.get("video_url") or next((r.get("video_url") for r in m.get("resources") or [] if r.get("video_url")), None)
    ts = m.get("taken_at_ts")
    if not ts and m.get("taken_at"):
        try:
            from datetime import datetime
            ts = int(datetime.fromisoformat(str(m["taken_at"]).replace("Z", "+00:00")).timestamp())
        except ValueError:
            ts = None
    escondido = m.get("like_and_view_counts_disabled")
    num_ok = lambda v: v if isinstance(v, int) and v >= 0 and not escondido else None
    thumb = m.get("thumbnail_url") or next((r.get("thumbnail_url") for r in m.get("resources") or [] if r.get("thumbnail_url")), None)
    return {"description": m.get("caption_text") or "", "timestamp": ts, "uploader": u.get("full_name") or u.get("username") or "",
            "channel": u.get("username") or "", "view_count": num_ok(m.get("play_count") or m.get("view_count") or None),
            "like_count": num_ok(m.get("like_count")), "comment_count": num_ok(m.get("comment_count")),
            "thumbnail": thumb, "thumbnails": [{"url": thumb}] if thumb else [], "video_url": video,
            "duration": m.get("video_duration") or 0}


def ig_info(c, url):
    """Um Reel por URL: HikerAPI (sem usar a conta) → login próprio. Guardado 1 h em memória."""
    n = norm(url)
    if n in IG_INFO and time.time() - IG_INFO[n][0] < 3600:
        return IG_INFO[n][1]
    erros = []
    if c.get("hiker_key"):
        try:
            info = ig_norm(req("GET", "https://api.hikerapi.com/v1/media/by/url?" + urlencode({"url": url}),
                               {"x-access-key": c["hiker_key"], "Accept": "application/json"}, timeout=60))
            IG_INFO[n] = (time.time(), info)
            return info
        except Exception as e:
            erros.append("HikerAPI: " + scrub(e)[:160])
    if ig_login_on(c):
        try:
            info = ig_norm(ig_call(c, lambda cl: cl.media_info(cl.media_pk_from_url(url)).model_dump(mode="json")))
            IG_INFO[n] = (time.time(), info)
            return info
        except Exception as e:
            erros.append(scrub(e)[:200])
    raise RuntimeError(" | ".join(erros) or "sem HikerAPI nem login do Instagram")


def ig_on(c, url):
    return is_ig(url) and bool(c.get("hiker_key") or ig_login_on(c))


def ig_video(c, url, out, qual):
    """Vídeo original direto do CDN do Instagram (link vem da HikerAPI ou do login)."""
    info = ig_info(c, url)
    if not info.get("video_url"):
        raise RuntimeError("There is no video in this post")
    path = out + ".mp4"
    with open(path, "wb") as f:
        f.write(req("GET", info["video_url"], {"User-Agent": UA}, timeout=300, raw=True))
    return path


def ig_saved(c, n=30):
    """Lista dos salvos (todas as coleções) pela conta do usuário: 1 a 2 chamadas por coleta."""
    medias = ig_call(c, lambda cl: [m.model_dump(mode="json") for m in cl.collection_medias("ALL_MEDIA_AUTO_COLLECTION", amount=n)])
    return [f"https://www.instagram.com/p/{m['code']}/" for m in medias if m.get("code")]


class Later(Exception):
    """Ainda não dá (Karakeep lendo a página): reagenda em vez de falhar."""


def kk(c):
    return c["karakeep_url"].rstrip("/") + "/api/v1", {"Authorization": "Bearer " + c["karakeep_key"]}


def kk_ativo(c):
    """Karakeep em uso: ligado em Configurações e com URL e chave."""
    return c.get("kk_on") == "1" and bool(c.get("karakeep_url") and c.get("karakeep_key"))


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def baixa_url(url, limite=25 * 1024 * 1024):
    """(content-type, bytes) do link, até `limite`. Sem Karakeep: o Video KB lê a página/PDF sozinho."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}), timeout=60) as r:
            return (r.headers.get("Content-Type") or "").lower(), r.read(limite)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Página respondeu HTTP {e.code}: bloqueada ou exige login?")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(scrub(f"Não consegui abrir a página: {getattr(e, 'reason', e)}"))


def eh_pdf(url, ctype=""):
    return "pdf" in ctype or urlsplit(url).path.lower().endswith(".pdf")


def pdf_texto(dados):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("Leitor de PDF (pypdf) não instalado: reinicie o app pra ele instalar")
    try:
        return "\n\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(dados)).pages[:200])
    except Exception as e:
        raise RuntimeError(f"PDF não abriu ({type(e).__name__}): corrompido ou protegido?")


def meta_html(h, *nomes):
    for n in nomes:
        m = (re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']+)', h, re.I)
             or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(n)}["\']', h, re.I))
        if m:
            return html.unescape(m[1]).strip()
    return ""


def ensure_bookmark(j, c):
    if not j["bookmark_id"]:  # link colado à mão: cria o bookmark (Karakeep devolve o existente se a URL já está lá)
        base, h = kk(c)
        j["bookmark_id"] = req("POST", base + "/bookmarks", h, {"type": "link", "url": j["orig"]})["id"]
        save(j)
    return j["bookmark_id"]


def bookmark(c, bid):
    base, h = kk(c)
    return req("GET", f"{base}/bookmarks/{bid}?includeContent=true", h)


def has_media(url):
    """Duração (s) do vídeo/áudio que o yt-dlp acha nesse link (Reddit, YouTube, X...). 0 = não é vídeo / sem yt-dlp."""
    try:
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "-j", "--no-playlist", "--no-warnings", url],
                           capture_output=True, text=True, timeout=60)
        return (p.returncode == 0 and json.loads(p.stdout.splitlines()[0]).get("duration")) or 0
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return 0


def step_tipo(j, c):
    if j["orig"] and is_target(j["orig"]):
        j["data"]["kind"] = "video"
        return
    if not kk_ativo(c):  # sem Karakeep: o próprio link decide
        if not j["orig"]:
            raise RuntimeError("Item antigo do Karakeep sem link: ligue o Karakeep em Configurações ou apague")
        dur, lim = (0 if eh_pdf(j["orig"]) else has_media(j["orig"])), num(c, "max_min")
        if dur > lim * 60:
            raise RuntimeError(f"Vídeo longo ({dur // 60:.0f} min, limite {lim} min): não transcrevi pra não gastar Scribe. "
                               "Aumente o limite em Configurações e reprocesse.")
        j["data"]["kind"] = "video" if dur else "pdf" if eh_pdf(j["orig"]) else "pagina"
        j["data"]["dur"] = dur
        return
    ct = bookmark(c, ensure_bookmark(j, c)).get("content") or {}
    t = ct.get("type")
    if t == "link":
        if not j["orig"]:
            j["orig"] = ct.get("url") or ""
        dur, lim = has_media(ct.get("url") or j["orig"]), num(c, "max_min")
        if dur > lim * 60:
            raise RuntimeError(f"Vídeo longo ({dur // 60:.0f} min, limite {lim} min): não transcrevi pra não gastar Scribe. "
                               "Aumente o limite em Configurações e reprocesse.")
        j["data"]["kind"] = "video" if dur else "pagina"
        j["data"]["dur"] = dur  # curto/longo (qualidade do download e política do SilverBullet)
    elif t == "asset" and ct.get("assetType") == "pdf":
        j["data"]["kind"] = "pdf"
    elif t == "text":
        j["data"]["kind"] = "texto"
    else:
        raise RuntimeError(f"Tipo não suportado ({t or 'desconhecido'}{'/' + ct['assetType'] if ct.get('assetType') else ''}): nada pra resumir")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, a):
        self.skip += tag in ("script", "style")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style") and self.skip > 0
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr"):
            self.out.append("\n")

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def html_text(h):
    p = _Text()
    p.feed(h or "")
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(p.out))).strip()


def step_texto(j, c):
    """Página/PDF/texto: sem Karakeep lê o link direto; com Karakeep usa o que ele extraiu. Nada de inventar."""
    if not kk_ativo(c):
        return texto_direto(j, c)
    ct = bookmark(c, ensure_bookmark(j, c)).get("content") or {}
    kind = j["data"]["kind"]
    if kind == "texto":
        text = ct.get("text") or ""
    elif kind == "pdf":
        text = ct.get("content") or ""
    else:
        if ct.get("crawlStatus") == "pending" or (not ct.get("htmlContent") and not ct.get("crawledAt") and ct.get("crawlStatus") != "failure"):
            raise Later("Karakeep ainda lendo a página")
        text = html_text(ct.get("htmlContent")) or ct.get("description") or ""
        if ct.get("title") and len(text) >= MIN_TEXT:
            text = ct["title"] + "\n\n" + text
    text = text.strip()
    if len(text) < MIN_TEXT:
        if kind == "pdf" and not text and j["data"].get("tentativas", 0) < num(c, "crawl_tries"):
            raise Later("Karakeep ainda extraindo o PDF")
        raise RuntimeError(f"Sem texto suficiente pra resumir ({len(text)} caracteres extraídos pelo Karakeep). "
                           + {"pdf": "PDF escaneado/imagem?", "texto": "Nota curta demais.", "pagina": "Página bloqueada ou só imagem/vídeo?"}[kind])
    j["data"]["transcript"] = text


def texto_direto(j, c):
    kind = j["data"]["kind"]
    if kind == "texto":
        raise RuntimeError("Nota do Karakeep: ligue o Karakeep em Configurações ou apague o item")
    ctype, dados = baixa_url(j["orig"])
    if kind == "pdf" or eh_pdf(j["orig"], ctype):
        j["data"]["kind"], text = "pdf", pdf_texto(dados)
    else:
        h = dados.decode("utf-8", "replace")
        titulo = meta_html(h, "og:title", "twitter:title") or html.unescape((re.search(r"<title[^>]*>(.*?)</title>", h, re.I | re.S) or [None, ""])[1]).strip()
        corpo = re.search(r"<(article|main)\b.*?</\1>", h, re.I | re.S)
        text = html_text(re.sub(r"<(nav|header|footer|aside|form|svg|noscript)\b.*?</\1>", "", corpo[0] if corpo else h, flags=re.I | re.S))
        text = text or meta_html(h, "og:description", "description")
        if titulo and len(text) >= MIN_TEXT:
            text = titulo + "\n\n" + text
        if pub := meta_html(h, "article:published_time", "og:published_time", "date", "pubdate"):
            j["data"].setdefault("meta", {})["postado"] = pub[:10]
        if a := meta_html(h, "author", "article:author", "og:site_name"):
            j["data"].setdefault("meta", {}).setdefault("autor", a[:80])
        if img := meta_html(h, "og:image", "twitter:image"):
            j["data"]["og_image"] = urllib.parse.urljoin(j["orig"], img)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < MIN_TEXT:
        raise RuntimeError(f"Sem texto suficiente pra resumir ({len(text)} caracteres). "
                           + ("PDF escaneado/imagem?" if j["data"]["kind"] == "pdf" else "Página bloqueada, exige login ou é só imagem/vídeo?"))
    j["data"]["transcript"] = text[:200000]


def baixar(c, url, out, qual):
    """Vídeo na qualidade pedida ("max" = original), na ordem da config; falhou um, tenta o outro."""
    fns = [cobalt_video, ytdlp_video] if c.get("dl_ordem") != "ytdlp" else [ytdlp_video, cobalt_video]
    if ig_on(c, url):  # v1.10: Instagram pelo link original da HikerAPI/login primeiro
        fns = [ig_video] + fns
    erros = []
    for fn in fns:
        try:
            return fn(c, url, out, qual)
        except Exception as e:
            erros.append(str(e))
    raise RuntimeError(" | ".join(erros))


def qual_download(c, dur):
    """Curto: qualidade da config. Longo: no máximo a resolução de longos (não baixa 4K de 1 h)."""
    q = c.get("dl_qualidade") or "max"
    if dur > num(c, "curto_min") * 60:
        return str(min(int(c.get("longo_res") or 720), 99999 if q == "max" else int(q)))
    return q


SO_FOTO = ("No video formats found", "There is no video in this post")


def step_audio(j, c):
    need(c, "elevenlabs_key")  # sem chave não adianta baixar
    try:
        j["data"]["audio"] = baixar(c, j["orig"], os.path.join(DATA, "tmp", str(j["id"])), qual_download(c, j["data"].get("dur", 0)))
    except RuntimeError as e:  # carrossel/foto do Instagram: Cobalt devolve picker e o yt-dlp não acha vídeo
        if c.get("pular_humor") == "1" and not j["data"].get("forcar") and any(s in str(e) for s in SO_FOTO):
            raise Ignorado("post só de foto (só vídeo entra)")
        raise


def so_audio(media):
    """Scribe só precisa do áudio: mp3 mono 64k, bem menor que o vídeo. Sem ffmpeg ou falhou: manda o arquivo como está."""
    if media.endswith(".mp3") or not shutil.which("ffmpeg"):
        return media
    out = os.path.splitext(media)[0] + "-audio.mp3"
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", media, "-vn", "-ac", "1", "-b:a", "64k", out], capture_output=True, timeout=900)
    return out if p.returncode == 0 and os.path.exists(out) else media


def step_transcricao(j, c):
    need(c, "elevenlabs_key")
    path = so_audio(j["data"]["audio"])
    campos = {"model_id": SCRIBE_MODEL, "tag_audio_events": "false", "diarize": "false", **({"language_code": c["idioma"]} if c.get("idioma") else {})}
    try:
        with open(path, "rb") as f:
            body, ctype = multipart(campos, os.path.basename(path), f.read())
    finally:
        if path != j["data"]["audio"]:
            os.remove(path)
    r = req("POST", "https://api.elevenlabs.io/v1/speech-to-text",
            {"xi-api-key": c["elevenlabs_key"], "Content-Type": ctype}, body, timeout=300)
    text = (r.get("text") or "").strip()
    if not text:
        raise RuntimeError("Transcrição vazia (vídeo sem fala?)")
    j["data"]["transcript"] = text
    j["data"]["transcript_t"] = timed(r.get("words") or []) or text


def mmss(t):
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def timed(words):
    """Palavras do Scribe -> linhas "[mm:ss] frase" (quebra no fim de frase ou a cada ~12 s)."""
    out, cur, t0 = [], "", None
    for w in words:
        if w.get("type") not in ("word", "spacing"):
            continue
        if t0 is None and w.get("type") == "word":
            t0 = w.get("start") or 0
        cur += w.get("text", "")
        if w.get("type") == "word" and (cur.rstrip().endswith((".", "?", "!")) or (w.get("end") or 0) - t0 > 12):
            out.append(f"[{mmss(t0)}] {cur.strip()}")
            cur, t0 = "", None
    if cur.strip():
        out.append(f"[{mmss(t0 or 0)}] {cur.strip()}")
    return "\n".join(out)


def run_crv(media, out, n):
    p = subprocess.run([sys.executable, "-m", "claude_real_video", media, "-o", out, "--overwrite",
                        "--no-transcribe", "--grid", "--max-frames", str(n)],
                       capture_output=True, text=True, timeout=900, env={**os.environ, "CRV_NO_MEMORY": "1", "CRV_NO_HINT": "1"})
    if p.returncode:
        raise RuntimeError("crv: " + scrub((p.stderr.strip().splitlines() or [f"saiu com {p.returncode}"])[-1])[:200])


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def fit_grids(paths, limit=GRIDS_MAX):
    """Total das folhas acima do limite: reduz todas na mesma proporção (Pillow vem com o crv)."""
    total = sum(os.path.getsize(p) for p in paths)
    if total <= limit:
        return
    from PIL import Image
    k = (limit / total) ** 0.5 * 0.9
    for p in paths:
        with Image.open(p) as im:
            im.convert("RGB").resize((max(1, int(im.width * k)), max(1, int(im.height * k)))).save(p, "JPEG", quality=80)


def step_visao(j, c):
    """crv no vídeo já baixado: folhas 3x3 + tempos dos frames. Falhou = segue sem visão (nunca trava o job)."""
    d = j["data"]
    media, out = d.pop("audio", None), os.path.join(DATA, "tmp", f"crv-{j['id']}")
    try:
        if not media or not os.path.exists(media):
            raise RuntimeError("vídeo baixado não está mais aqui")
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg não instalado")
        run_crv(media, out, num(c, "max_frames"))
        grids = sorted(glob.glob(os.path.join(out, "grids", "*.jpg")))
        if not grids:
            raise RuntimeError("crv não gerou folhas (foto/carrossel?)")
        fit_grids(grids)
        with open(os.path.join(out, "frames.json")) as f:
            frames = " ".join(f"{x['file']}={mmss(x['timestamp_sec'])}" for x in json.load(f)["frames"])
        d["visao"] = {"dir": out, "grids": grids, "frames": frames}
    except Exception as e:
        d["visao"] = {"erro": scrub(e)[:200]}
        d["aviso"] = "sem visão: " + d["visao"]["erro"]
        shutil.rmtree(out, ignore_errors=True)
    finally:
        if media and os.path.exists(media):
            if sb_ligado(c) and d.get("kind", "video") == "video":
                os.makedirs(os.path.join(DATA, "sbmedia"), exist_ok=True)  # vai pro SilverBullet na última etapa
                shutil.move(media, os.path.join(DATA, "sbmedia", f"{j['id']}{os.path.splitext(media)[1] or '.mp4'}"))
            else:
                os.remove(media)


PROMPT = (
    "Você analisa conteúdo salvo: transcrição de vídeo curto, página, post ou PDF. Responda SOMENTE um JSON, em português do Brasil, com as chaves: "
    '"titulo" (até 80 caracteres), "gancho" (1 frase de até 100 caracteres dizendo do que se trata, para ler sem abrir), "resumo" (2 a {frases} frases), "pontos_chave" (3 a {pontos} strings), '
    '"tags" (3 a {tags}, minúsculas, sem #, 1 a 2 palavras), "tema" (1 a 3 palavras, Title Case, genérico para agrupar conteúdos '
    'parecidos, ex.: "Marketing", "Saúde", "Finanças"), '
    '"transcricao_limpa" (a transcrição com pontuação e erros de reconhecimento corrigidos, sem inventar conteúdo).'
)
ESTILO = (
    " ESTILO do resumo, pontos-chave, o que aparece e síntese (nunca da transcrição, que fica fiel ao original): "
    "humanizado: sem cara de IA, sem 'vale ressaltar', 'além disso', 'em suma', 'crucial', 'fundamental', 'jornada', 'mergulhar', "
    "sem regra de três forçada, sem travessão de efeito, sem frase de abertura ou fecho genérica, sem elogio vazio; "
    "caveman ultra: frase curta, corta artigo e enchimento quando der, sem rodeio, fato direto, palavra concreta; "
    "TDAH: o mais útil primeiro, um assunto por item, ação prática quando existir, nada de parágrafo longo."
)


def estilo(c):
    """ESTILO fixo sempre vai; a instrução do usuário só soma."""
    extra = (c.get("estilo_extra") or "").strip()
    return ESTILO + (" Instrução extra do usuário (soma ao estilo acima, não substitui): " + extra if extra else "")


def prompt(c, video):
    return (PROMPT if video else PROMPT_TEXTO).format(frases=num(c, "resumo_frases"), pontos=num(c, "pontos_max"), tags=num(c, "tags_max")) + HUMOR


class Ignorado(Exception):
    """Item fora do que o usuário quer guardar (meme/humor, post só de foto): para sem erro."""


def llm(c, body, modelo, timeout=180):
    """Chat no 9Router. Principal falhou e há reserva diferente: tenta uma vez com a reserva."""
    url, h = c["llm_url"].rstrip("/") + "/chat/completions", {"Authorization": "Bearer " + c["llm_key"]}
    try:
        return req("POST", url, h, {**body, "model": modelo}, timeout=timeout)
    except Exception:
        if not c.get("model_reserva") or c["model_reserva"] == modelo:
            raise
        log(f"modelo {modelo} falhou; tentando a reserva")
        return req("POST", url, h, {**body, "model": c["model_reserva"]}, timeout=timeout)


def linhas(v):
    return [x.strip() for x in (v or "").splitlines() if x.strip()]


def tema_fixo(tema, fixos):
    """Assuntos fixos: o tema tem que ser um deles (grafia da lista); a IA errou = primeiro da lista."""
    return next((f for f in fixos if fold(f) == fold(tema)), fixos[0])


PROMPT_VISAO = (
    ' Você também recebe folhas 3x3 com frames do vídeo (cada célula tem o nome do frame; os tempos estão na lista "frames") '
    'e a transcrição com tempos [mm:ss]. Inclua a chave "o_que_aparece": lista curta e cronológica de strings "mm:ss: ..." '
    "com o que aparece na tela, texto escrito na tela e ações, alinhado com o que é dito. Só o que dá pra ver, sem inventar."
)
PROMPT_TEXTO = PROMPT.split(', "transcricao_limpa"')[0] + "."
HUMOR = (' Inclua também "humor": true se o conteúdo é meme, piada, esquete de humor, trend ou entretenimento engraçado sem informação útil para guardar; '
         'false se ensina, informa ou mostra algo útil, mesmo com tom leve ou piada no meio.')  # página/PDF/texto: não reescreve o original


def fold(s):
    """Chave de comparação: sem acento, minúscula, espaços únicos."""
    s = unicodedata.normalize("NFKD", re.sub(r"\s+", " ", str(s)).strip())
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def pick_tema(tema, existentes):
    """Casa com caderno existente (sem diferenciar maiúscula/acento); senão normaliza (trim + 1ª maiúscula)."""
    t = re.sub(r"\s+", " ", str(tema or "")).strip()[:60] or "Geral"
    return next((n for n in existentes if fold(n) == fold(t)), t[:1].upper() + t[1:])


def nomes(fn):
    """Lista de nomes existentes; falhou = segue sem lista."""
    try:
        return [n for n in fn() if n]
    except Exception as e:
        log("sem lista existente:", scrub(e))
        return []


def nb_headers(c):
    return {"Authorization": "Bearer " + c["notebook_pass"]} if c.get("notebook_pass") else {}


def notebooks(c):
    return req("GET", c["notebook_url"].rstrip("/") + "/api/notebooks", nb_headers(c), timeout=20)


def sb_tags_existentes():
    """Sem Karakeep: tags já usadas nos itens prontos (as 200 mais comuns), pra reaproveitar a grafia."""
    n = {}
    for r in q("SELECT data FROM jobs WHERE data LIKE '%\"analysis\"%'"):
        for t in (json.loads(r["data"] or "{}").get("analysis") or {}).get("tags") or []:
            n[t] = n.get(t, 0) + 1
    return sorted(n, key=lambda t: -n[t])[:200]


def kk_tags(c):
    r = req("GET", c["karakeep_url"].rstrip("/") + "/api/v1/tags",
            {"Authorization": "Bearer " + c["karakeep_key"]}, timeout=20)
    return [t["name"] for t in r.get("tags", [])][:200]  # ponytail: 200 mais usadas; paginar se o prompt ficar curto


def parse_analysis(raw, transcript, tags_max=8):
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    a = json.loads(raw)
    tags = [re.sub(r"\s+", " ", str(t)).strip().lstrip("#").lower() for t in a.get("tags") or []]
    return {
        "titulo": str(a.get("titulo") or "Vídeo sem título").strip()[:120],
        "gancho": str(a.get("gancho") or "").strip()[:120],
        "resumo": str(a.get("resumo") or "").strip(),
        "pontos_chave": [str(p).strip() for p in a.get("pontos_chave") or [] if str(p).strip()],
        "tags": list(dict.fromkeys(t for t in tags if t))[:tags_max],
        "tema": re.sub(r"\s+", " ", str(a.get("tema") or "Geral")).strip()[:60] or "Geral",
        "transcricao_limpa": str(a.get("transcricao_limpa") or "").strip() or transcript,
        "o_que_aparece": [str(x).strip() for x in (a.get("o_que_aparece") if isinstance(a.get("o_que_aparece"), list)
                          else [a.get("o_que_aparece")] if a.get("o_que_aparece") else []) if str(x).strip()],
        "humor": a.get("humor") is True or str(a.get("humor")).lower() == "true",
    }


def step_resumo(j, c):
    need(c, "llm_url", "llm_key")
    fixos = linhas(c.get("assuntos_fixos"))
    temas = fixos or (nomes(lambda: [n["name"] for n in notebooks(c)]) if c.get("notebook_url") and c.get("nb_on") == "1" else [])
    ctx = ("\n\nAssuntos permitidos: " + json.dumps(fixos, ensure_ascii=False) + '. O "tema" TEM que ser um desses, escrito igual.' if fixos
           else "\n\nCadernos (temas) que já existem: " + json.dumps(temas, ensure_ascii=False)
           + ". O tema DEVE ser um desses, escrito igual, quando couber. Só crie tema novo se nenhum servir: nome curto em português do Brasil."
           if temas else "")
    tags = nomes(lambda: kk_tags(c)) if kk_ativo(c) else sb_tags_existentes()
    ctx += ("\nTags que já existem: " + json.dumps(tags, ensure_ascii=False)
            + ". Reaproveite essas quando couberem; crie tag nova só se nenhuma servir." if tags else "")
    d = j["data"]
    video, vis = d.get("kind", "video") == "video", d.get("visao") or {}
    texto = (d.get("transcript_t") or d["transcript"])[:30000]
    imgs = [g for g in vis.get("grids", []) if os.path.exists(g)]
    if vis.get("grids"):
        d.pop("aviso", None)  # visão ok; aviso antigo era de resumo anterior

    def ask(com_imagens):
        user = texto
        if com_imagens:
            user = [{"type": "text", "text": f"frames: {vis['frames']}\n\nTranscrição com tempos:\n{texto}"}] + [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64(g)}}
                for g in imgs]
        return llm(c, {"stream": False, "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": prompt(c, video) + (PROMPT_VISAO if video and com_imagens else "") + estilo(c) + ctx},
                                    {"role": "user", "content": user}]},
                   (c.get("model_visao") or c["model"]) if com_imagens else c["model"], timeout=300 if com_imagens else 180)

    if video and imgs:
        try:
            r = ask(True)
        except Exception as e:  # 9Router/modelo recusou imagem (ou payload grande): tenta só com o texto
            d["aviso"] = "sem visão: modelo não aceitou as imagens (" + scrub(e)[:150] + ")"
            r = ask(False)
    else:
        if video and vis.get("grids") and not imgs:
            d["aviso"] = "sem visão: folhas sumiram antes do resumo"
        r = ask(False)
    try:
        a = parse_analysis(r["choices"][0]["message"]["content"], j["data"]["transcript"], num(c, "tags_max"))
    except (KeyError, IndexError, ValueError, TypeError) as e:
        raise RuntimeError(f"Resposta do modelo inválida ({type(e).__name__})")
    if fixos and not any(fold(f) == fold(a["tema"]) for f in fixos):  # Gemini saiu da lista: Decider escolhe; incerto = primeiro (regra antiga)
        crit = {str(n): f for n, f in enumerate(fixos[:40])}
        dd = decidir(c, "tema fixo", {"titulo": a["titulo"], "resumo": a["resumo"], "tema_sugerido": a["tema"], "tags": a["tags"]},
                     {"type": "choice", "instructions": "Em qual destes assuntos este vídeo se encaixa melhor?", "criteria": crit})
        if dd and dd["prob"] >= DECIDER_LIMIAR:
            a["tema"] = crit[dd["valor"]]
    a["tema"] = tema_fixo(a["tema"], fixos) if fixos else pick_tema(a["tema"], temas)
    by = {fold(t): t for t in tags}
    a["tags"] = list(dict.fromkeys(by.get(fold(t), t) for t in a["tags"]))  # grafia da tag existente
    a["estilo"] = 2  # já nasce no ESTILO, com gancho
    j["data"]["analysis"] = a
    if vis.get("dir"):
        shutil.rmtree(vis["dir"], ignore_errors=True)  # folhas já usadas
    if a["humor"] and c.get("pular_humor") == "1" and not d.get("forcar"):
        ok = decider_sim(c, "meme?", {"titulo": a["titulo"], "resumo": a["resumo"], "pontos_chave": a["pontos_chave"][:7]},
                         "Este post é só meme, piada, esquete, trend ou entretenimento engraçado, SEM informação útil para guardar? "
                         "Se ensina, informa ou mostra algo útil, mesmo com tom leve, a resposta é não.",
                         "Sim: só humor, nada útil para guardar.", "Não: tem informação útil.")
        if ok is False:  # Decider discorda ou está incerto: guarda (perder vídeo útil é pior)
            a["humor"] = False
            log(f"job {j['id']}: Gemini achou humor, Decider não confirmou; guardado")
        else:
            raise Ignorado("meme/humor: " + a["titulo"][:80])


# ---------- links (v1.9): o que o vídeo apresenta ou recomenda vira link conferido no card ----------
# Regra do nan (20 out): só quando o item é o assunto do vídeo ou recomendação clara; menção de passagem não entra.
# Na dúvida, sem link. Busca: SearXNG próprio + APIs oficiais (GitHub, Apple Podcasts, Google Books). A IA escolhe; o link é conferido.
LINK_TIPOS = ("ferramenta", "produto", "livro", "podcast", "repositorio", "curso", "jogo", "site")
PROMPT_LINKS = (
    "Você analisa um vídeo curto que o usuário salvou para consultar depois. Recebe título, resumo, pontos-chave, o que aparece na tela, "
    "a transcrição e a legenda do post. Tarefa: listar SÓ os itens que o usuário vai querer abrir depois de ver este vídeo. "
    "Tipos: ferramenta (app, programa, site, serviço, extensão, IA), produto (objeto físico à venda), livro, podcast (episódio ou programa), "
    "repositorio (projeto no GitHub/GitLab), curso, jogo, site.\n"
    "ENTRA só se pelo menos um vale: (a) assunto: o vídeo existe para apresentar, ensinar a usar, analisar, comparar como tema central ou vender ESTE item; se o vídeo relaciona, compara ou analisa duas ou três obras ou itens juntos (ex.: um jogo e um livro), CADA UM entra como assunto; "
    "(b) recomendado: o criador recomenda o item a quem assiste, de forma explícita ('baixa', 'usa', 'testa', 'leia', 'link na bio', 'eu recomendo'); "
    "(c) necessario: sem o item não dá para fazer o que o vídeo ensina.\n"
    "NÃO ENTRA: menção de passagem; exemplo ou comparação dentro de outro assunto; ferramenta usada só para gravar/editar/postar; marca que só aparece na tela; "
    "a própria rede social; empresa ou pessoa citada como caso; categoria genérica ('IA', 'Linux', 'Python', 'celular'); ferramenta famosa citada só de leve "
    "(ex.: ChatGPT, Google, Excel quando não são o foco); patrocínio sem relação com o assunto; obra citada só como referência cultural sem convite a consumir. "
    "Se o vídeo é sobre uma ideia, hábito ou opinião e só cita coisas de leve, a lista fica vazia. Lista vazia é resposta normal e frequente. Na dúvida, deixe fora. Máximo 3, do mais central ao menos.\n"
    'Responda só JSON: {"itens":[{"tipo":"...","nome":"nome exato do item","marca_autor":"fabricante, autor, dono do repositório ou canal, se souber",'
    '"papel":"assunto|recomendado|necessario","evidencia":"trecho curto da transcrição ou legenda que prova o papel",'
    '"busca":"consulta curta para achar a página oficial do item","loja_citada":"loja ou site citado no vídeo para comprar ou baixar, se houver",'
    '"url_citada":"URL dita, mostrada na tela ou na legenda, se houver","confianca":0.0}]}. '
    "confianca: 0 a 1, quanto você tem certeza de que o item cumpre a regra ENTRA."
)
PROMPT_ESCOLHA = (
    "Você escolhe o link certo para um item citado num vídeo. Recebe o item, o contexto do vídeo e candidatos numerados (título, URL, trecho). "
    "Escolha o candidato que leva AO PRÓPRIO ITEM, no lugar mais útil para quem quer usar, ler, ouvir ou comprar: "
    "repositório oficial; site oficial da ferramenta/serviço; página do episódio ou do podcast (Spotify, Apple Podcasts ou site do programa); "
    "página do livro (editora, Amazon, Google Books); jogo que sai para PC: a página dele na Steam (store.steampowered.com), senão o site oficial; produto: a loja que o vídeo cita, senão a página oficial do fabricante, senão um anúncio do item exato numa loja grande. "
    "Nunca escolha notícia, review, blog, vídeo, rede social, fórum, wiki ou loja de outro item quando existir a página do item. "
    "O autor pode aparecer só pelo apelido/usuário; nome no perfil ou titular no LICENSE igual ao citado conta como mesmo autor. "
    "Se nenhum candidato é claramente o item certo (nome diferente, outro produto, outro autor), responda i = -1. Errar o link é pior que não ter link. "
    'Responda só JSON: {"i": número, "rotulo": "nome curto do item para o botão", "porque": "até 8 palavras: por que este item está no vídeo"}.'
)


def busca_searx(c, consulta, n=8):
    base = (c.get("searx_url") or "").rstrip("/")
    if not base:
        return []
    r = req("GET", base + "/search?" + urllib.parse.urlencode({"q": consulta, "format": "json", "language": "pt-BR", "safesearch": 0}), {"User-Agent": UA}, timeout=30)
    return [{"titulo": x.get("title", ""), "url": x.get("url", ""), "trecho": (x.get("content") or "")[:220]} for x in (r.get("results") or [])[:n] if x.get("url")]


def busca_github(consulta, n=5):
    r = req("GET", "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": consulta, "sort": "stars", "per_page": n}),
            {"Accept": "application/vnd.github+json", "User-Agent": "video-kb"}, timeout=20)
    return [{"titulo": x["full_name"] + f" ({x.get('stargazers_count', 0)} estrelas)", "url": x["html_url"], "trecho": (x.get("description") or "")[:220]}
            for x in r.get("items", [])[:n]]


PALAVRA_VAZIA = set("a o as os de da do das dos em e para pra com no na nos nas um uma repositorio repositório github projeto open source "
                    "markdown md the of for in and to with repo".split())


def github_consultas(nome, busca="", autor=""):
    """Consultas pro GitHub, da mais exata pra mais solta; sem repetir."""
    limpa = lambda t: [w for w in re.findall(r"[\w-]+", fold(t)) if w not in PALAVRA_VAZIA and len(w) > 2]
    sig, sigb = limpa(nome), limpa(busca)
    cand = [nome, " ".join(sigb), " ".join(sig), " ".join(sigb[:3]), " ".join(sig[:2]),
            f"{sig[0]} user:{autor}" if sig and re.fullmatch(r"[\w-]+", autor or "") else ""]
    out = []
    for x in cand:
        x = (x or "").strip()
        if x and x.lower() not in (y.lower() for y in out):
            out.append(x)
    return out[:5]


def github_dono(u):
    """github.com/dono/repo: nome do perfil e titular do LICENSE (o dono costuma aparecer só pelo apelido). Falhou = vazio."""
    m = re.match(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", u or "")
    if not m:
        return ""
    dono, repo = m[1], m[2].removesuffix(".git")
    h = {"Accept": "application/vnd.github+json", "User-Agent": "video-kb"}
    partes = []
    try:
        perfil = req("GET", f"https://api.github.com/users/{dono}", h, timeout=15)
        partes.append(f"dono @{dono}" + (f" ({perfil['name']})" if perfil.get("name") else ""))
    except Exception:
        partes.append(f"dono @{dono}")
    try:
        lic = req("GET", f"https://api.github.com/repos/{dono}/{repo}/license", h, timeout=15)
        txt = base64.b64decode(lic.get("content") or "").decode("utf-8", "replace")
        cp = re.search(r"Copyright\s*(?:\(c\)|©)?\s*(?:\d{4}[\s,-]*)*(.+)", txt, re.I)
        if cp:
            partes.append("titular no LICENSE: " + cp[1].strip()[:80])
    except Exception:
        pass
    return "; ".join(partes)


def busca_podcast(consulta, n=5):
    out = []
    for ent in ("podcastEpisode", "podcast"):
        r = req("GET", "https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": consulta, "media": "podcast", "entity": ent, "limit": n, "country": "BR"}), timeout=20)
        out += [{"titulo": (x.get("trackName") or "") + " · " + (x.get("collectionName") or ""), "url": x.get("trackViewUrl") or x.get("collectionViewUrl"),
                 "trecho": (x.get("description") or x.get("artistName") or "")[:220]} for x in r.get("results", []) if x.get("trackViewUrl") or x.get("collectionViewUrl")]
    return out


def busca_livro(consulta, n=4):
    r = req("GET", "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode({"q": consulta, "maxResults": n, "printType": "books"}), timeout=20)
    return [{"titulo": v.get("title", "") + " · " + ", ".join(v.get("authors") or []) + (f" ({v['publishedDate'][:4]})" if v.get("publishedDate") else ""),
             "url": v.get("canonicalVolumeLink") or v.get("infoLink"), "trecho": (v.get("description") or "")[:220]}
            for v in (x.get("volumeInfo") or {} for x in r.get("items", [])) if v.get("canonicalVolumeLink") or v.get("infoLink")]


def busca_steam(consulta, n=4):
    """Loja da Steam (regra do nan, 20 out: jogo de PC = link da Steam). Página em pt-BR, preço em reais."""
    r = req("GET", "https://store.steampowered.com/api/storesearch/?" + urllib.parse.urlencode({"term": consulta, "cc": "BR", "l": "portuguese"}), timeout=20)
    return [{"titulo": f"{x.get('name', '')} · Steam" + (" (PC)" if (x.get("platforms") or {}).get("windows") else ""),
             "url": f"https://store.steampowered.com/app/{x['id']}/", "trecho": "Página do jogo na loja Steam"}
            for x in (r.get("items") or [])[:n] if x.get("type") == "app" and x.get("id")]


RUIM = re.compile(r"(instagram|tiktok|facebook|youtube|youtu\.be|twitter|x\.com|threads|linkedin|pinterest|reddit)\.", re.I)


def candidatos(c, it, urls_leg):
    """Candidatos de várias fontes, sem repetir; falha de uma fonte não para as outras."""
    nome, tipo = it["nome"], it["tipo"]
    alvo = " ".join(x for x in (nome, it.get("marca_autor", "")) if x)
    fontes = []
    if it.get("url_citada"):
        fontes.append(lambda: [{"titulo": "URL citada no vídeo", "url": it["url_citada"], "trecho": ""}])
    leg = [u for u in urls_leg if fold(nome).split(" ")[0][:5] in fold(u) or len(urls_leg) <= 2]
    if leg:
        fontes.append(lambda: [{"titulo": "Link da legenda do post", "url": u, "trecho": ""} for u in leg[:3]])
    if tipo == "repositorio":  # v1.12.1: a IA dá um nome descritivo, não o do repo; a busca do GitHub exige todas as palavras, então tenta variações
        for qg in github_consultas(nome, it.get("busca") or "", it.get("marca_autor") or ""):
            fontes.append(lambda qg=qg: busca_github(qg))
        fontes.append(lambda: busca_searx(c, f"site:github.com {nome}"))
    if tipo == "podcast":
        fontes.append(lambda: busca_podcast(alvo))
    if tipo == "livro":
        fontes.append(lambda: busca_livro(alvo))
    if tipo == "jogo":
        fontes.append(lambda: busca_steam(nome))
    q_web = it.get("busca") or alvo
    if tipo == "produto" and it.get("loja_citada"):
        fontes.append(lambda: busca_searx(c, f"{nome} {it['loja_citada']}"))
    fontes.append(lambda: busca_searx(c, q_web))
    if tipo in ("ferramenta", "site", "jogo", "curso"):
        fontes.append(lambda: busca_searx(c, q_web + " site oficial"))
    vistos, out = set(), []
    for f in fontes:
        try:
            for x in f():
                u = (x.get("url") or "").strip().rstrip(".,)")
                k = re.sub(r"^https?://(www\.)?|/$", "", u.lower())
                if u.startswith("http") and k not in vistos and not RUIM.search(u):
                    vistos.add(k)
                    out.append({**x, "url": u})
        except Exception as e:
            log("busca de link falhou:", scrub(e)[:120])
    out = out[:14]
    # v1.12.2: o escolhedor também recusava repo certo por "outro autor" (dono só pelo apelido). Os 3 primeiros do GitHub levam o dono real
    for x in [x for x in out if re.match(r"https?://(www\.)?github\.com/[\w.-]+/[\w.-]+", x["url"])][:3]:
        dono = github_dono(x["url"])
        if dono:
            x["trecho"] = ((x.get("trecho") or "") + " | " + dono).strip(" |")
    return out


def url_viva(u):
    """Abre a página: 404/410/sem resposta = morto. 401/403/429 (loja que barra robô) contam como vivos."""
    import urllib.request as ur
    try:
        with ur.urlopen(ur.Request(u, headers={"User-Agent": UA, "Accept-Language": "pt-BR"}), timeout=20) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code in (401, 403, 405, 429, 503)
    except Exception:
        return False


def site_de(u):
    return re.sub(r"^www\.", "", urllib.parse.urlparse(u).netloc.lower())


def json_llm(r):
    return json.loads(re.sub(r"^```(?:json)?|```$", "", r["choices"][0]["message"]["content"].strip(), flags=re.M).strip())


# ---------- Decider (v1.11, nan 20 out): escolha fechada, sem texto, via 9Router /v1/systemone ----------
# Ordem: pplx-decider → jev → microsoft (til obrigatório). Jev Router NÃO. Abaixo de 85% = incerto. Falhou tudo = None (vale o Gemini).
DECIDER_MODELOS = ("openrouter/~perplexity/pplx-decider-latest", "openrouter/~typesafe/jev-latest", "openrouter/microsoft/microsoft-decision-1")
DECIDER_LIMIAR = 0.85


def decidir(c, nome, state, pergunta):
    """pergunta: {"type": "noul"|"choice", "instructions", "criteria"}. Volta {"valor", "prob"} (prob = chance do valor) ou None."""
    if c.get("decider_on", "1") != "1" or not c.get("llm_url") or not c.get("llm_key"):
        return None
    url, h = c["llm_url"].rstrip("/") + "/systemone", {"Authorization": "Bearer " + c["llm_key"]}
    for m in DECIDER_MODELOS:
        try:
            r = req("POST", url, h, {"model": m, "state": state, "questions": {"q": pergunta}}, timeout=40)
            a = (r.get("answers") or {}).get("q") or {}
            if pergunta["type"] == "noul":
                if not isinstance(a.get("noul"), (int, float)):
                    raise RuntimeError("resposta sem noul")
                p = float(a["noul"])
                out = {"valor": p >= 0.5, "prob": max(p, 1 - p), "sim": p}
            else:
                ch = a.get("choice")
                if ch not in pergunta["criteria"]:
                    raise RuntimeError("escolha fora da lista")
                pr = a.get("probabilities") or {}
                out = {"valor": ch, "prob": float(pr.get(ch, a.get("confidence") or 0))}
            log(f"decider {nome}: {out['valor']} p={out['prob']:.2f} modelo={str(r.get('model') or m).split('/')[-1]}")
            return out
        except Exception as e:
            log(f"decider {nome} falhou com {m.split('/')[-1]}: {scrub(e)[:150]}")
    return None


def decider_sim(c, nome, state, instrucao, sim, nao):
    """True = Decider confirma (>=85%); False = nega ou está incerto; None = fora do ar (vale o Gemini)."""
    d = decidir(c, nome, state, {"type": "noul", "instructions": instrucao, "criteria": {"true": sim, "false": nao}})
    return None if d is None else d["sim"] >= DECIDER_LIMIAR


def step_links(j, c):
    d, a = j["data"], j["data"]["analysis"]
    if "legenda" not in d and j["orig"] and d.get("kind", "video") == "video":
        try:
            d["legenda"] = (yt_info(c, j["orig"]).get("description") or "")[:3000]
        except Exception as e:
            d["legenda"] = ""
            log(f"job {j['id']} sem legenda: {scrub(e)[:120]}")
    leg = d.get("legenda") or ""
    urls_leg = list(dict.fromkeys(u.rstrip(".,)") for u in re.findall(r"https?://[^\s<>\"')\]]+", leg)))
    ctx = (f"Título: {a['titulo']}\nTema: {a.get('tema', '')}\nResumo: {a['resumo']}\nPontos-chave:\n" + "\n".join("- " + p for p in a.get("pontos_chave", []))
           + ("\nO que aparece na tela:\n" + "\n".join("- " + x for x in a.get("o_que_aparece", [])) if a.get("o_que_aparece") else "")
           + f"\n\nLegenda do post:\n{leg or '(sem legenda)'}\n\nTranscrição:\n" + (a.get("transcricao_limpa") or d.get("transcript", ""))[:20000])
    modelo = c.get("model_links") or c["model"]
    itens = json_llm(llm(c, {"stream": False, "response_format": {"type": "json_object"},
                              "messages": [{"role": "system", "content": PROMPT_LINKS}, {"role": "user", "content": ctx}]}, modelo)).get("itens") or []
    links, log_links = [], []
    for it in itens[:3]:
        if not isinstance(it, dict) or not str(it.get("nome") or "").strip():
            continue
        it = {k: str(v).strip() if not isinstance(v, (int, float)) else v for k, v in it.items()}
        tipo = fold(it.get("tipo", "")).replace("ó", "o")
        it["tipo"] = tipo if tipo in LINK_TIPOS else "site"
        conf = float(it.get("confianca") or 0)
        if conf < 0.75 or it.get("papel") not in ("assunto", "recomendado", "necessario"):
            log_links.append(f"fora: {it['nome']} ({it.get('papel')}, {conf})")
            continue
        ok = decider_sim(c, f"link entra? {it['nome']}", {"video": ctx[:6000], "item": {k: it.get(k) for k in ("tipo", "nome", "marca_autor", "papel", "evidencia")}},
                         "Um vídeo citou este item. Ele deve virar link no card? Só entra se o item é o ASSUNTO do vídeo, se o criador o RECOMENDA "
                         "explicitamente a quem assiste, ou se é NECESSÁRIO para fazer o que o vídeo ensina. Menção de passagem, exemplo, ferramenta de gravação, "
                         "marca que só aparece, categoria genérica ou referência cultural sem convite não entram.",
                         "Entra: assunto, recomendação clara ou necessário.", "Fica fora: menção de passagem ou não cumpre a regra.")
        if ok is False:
            log_links.append(f"decider barrou: {it['nome']}")
            continue
        cands = candidatos(c, it, urls_leg)
        if not cands:
            log_links.append(f"sem candidatos: {it['nome']}")
            continue
        lista = "\n".join(f"{i}. {x['titulo']} | {x['url']} | {x['trecho']}" for i, x in enumerate(cands))
        esc_ = json_llm(llm(c, {"stream": False, "response_format": {"type": "json_object"}, "messages": [
            {"role": "system", "content": PROMPT_ESCOLHA},
            {"role": "user", "content": f"Item: {json.dumps(it, ensure_ascii=False)}\n\nVídeo: {a['titulo']}. {a['resumo']}\n\nCandidatos:\n{lista}"}]}, modelo))
        i = int(esc_.get("i", -1)) if str(esc_.get("i", "-1")).lstrip("-").isdigit() else -1
        if not 0 <= i < len(cands):
            log_links.append(f"nenhum candidato certo: {it['nome']} (viu {len(cands)}: " + ", ".join(re.sub(r"^https?://(www\.)?", "", x["url"])[:50] for x in cands[:5]) + ")")
            continue
        u = cands[i]["url"]
        if "github.com/" in u and "dono @" not in (cands[i].get("trecho") or ""):  # v1.12.1: o Decider recusava repo certo porque o dono aparece só pelo apelido (ex.: @ctresb = João Davi)
            dono = github_dono(u)
            if dono:
                cands[i] = {**cands[i], "trecho": (cands[i].get("trecho") or "") + " | " + dono}
        ok = decider_sim(c, f"página certa? {it['nome']}", {"item": {k: it.get(k) for k in ("tipo", "nome", "marca_autor", "busca", "loja_citada", "url_citada")},
                                                             "video": a["titulo"] + ". " + a["resumo"], "pagina": cands[i]},
                         "Esta página leva ao PRÓPRIO item (mesmo nome, mesmo autor/fabricante/dono), no lugar oficial ou mais útil para usar, ler, ouvir ou comprar? "
                         "Jogo de PC na Steam conta como página do próprio item (edição completa do mesmo jogo também). "
                         "O dono pode aparecer só pelo apelido/usuário; nome no perfil ou titular no LICENSE igual ao autor citado conta como mesmo autor. "
                         "Projeto parecido, fork, plugin de outro autor, notícia, review, blog, vídeo ou rede social = não.",
                         "Sim, é a página do próprio item.", "Não: outro item, outro autor ou página sobre o item.")
        if ok is False:
            log_links.append(f"decider recusou a página: {it['nome']} {u}")
            continue
        if not url_viva(u):
            log_links.append(f"link morto: {it['nome']} {u}")
            continue
        links.append({"tipo": it["tipo"], "nome": (str(esc_.get("rotulo") or it["nome"]).strip())[:60], "url": u, "site": site_de(u),
                      "porque": str(esc_.get("porque") or "").strip()[:80], "papel": it["papel"]})
    d["links"] = links
    log(f"job {j['id']} links: {[x['nome'] + ' ' + x['url'] for x in links]} | {log_links}")


LINKS_TESTE = ("AirDrop no Linux com placas Wi-Fi comuns usando omdrop-owl", "A relação profunda entre GTA IV e Crime e Castigo de Dostoiévski",
               "Como economizar R$ 50 mil por mês clonando seu cérebro com IA")


def sb_regrava(j, c):
    sb = j["data"].get("sb") or {}
    if sb.get("page") and j["data"].get("analysis"):
        fm, tags, tema, video = sb_reel_campos(j, c, sb.get("capa", ""))
        sb_put(c, sb["page"] + ".md", sb_reel_md(j, sb["page"], j["data"]["analysis"], sb, sb.get("capa", ""), tema, fm, tags, video))


def links_teste():
    """v1.9 teste do nan (20 out): links só nos 3 reels de teste, uma vez por rodada (flag no kv). Não liga links_on."""
    c = cfg()
    flag = "links_teste_" + os.environ.get("REF", "x")
    if q("SELECT 1 FROM kv WHERE k=?", (flag,)) or not sb_ligado(c):
        return
    alvo = {fold(t) for t in LINKS_TESTE}
    for r in q("SELECT id FROM jobs WHERE status='pronto' AND data LIKE '%\"sb7\"%'"):
        j = job(r["id"])
        if fold((j["data"].get("analysis") or {}).get("titulo", "")) not in alvo:
            continue
        try:
            j["data"].pop("links", None)
            step_links(j, c)
            save(j)
            sb_regrava(j, c)
        except Exception as e:
            log(f"job {j['id']} teste de links falhou: {scrub(e)[:300]}")
    q("INSERT OR REPLACE INTO kv VALUES(?,'1')", (flag,))
    q("INSERT OR REPLACE INTO kv VALUES('mural_sujo','1')")


NOTE_MARK = "Pontos-chave (Video KB):"  # nota do nan fica; só o bloco depois do marcador é nosso


def step_karakeep(j, c):
    need(c, "karakeep_url", "karakeep_key")
    (base, h), a = kk(c), j["data"]["analysis"]
    bid = ensure_bookmark(j, c)
    old = req("GET", f"{base}/bookmarks/{bid}", h).get("note") or ""
    ours = NOTE_MARK + "\n" + "\n".join("- " + p for p in a["pontos_chave"]) + (
        "\n\nO que aparece:\n" + "\n".join("- " + x for x in a["o_que_aparece"]) if a.get("o_que_aparece") else "")
    note = (old[:old.index(NOTE_MARK)].rstrip() + "\n\n" if NOTE_MARK in old else (old.rstrip() + "\n\n" if old.strip() else "")) + ours
    req("PATCH", f"{base}/bookmarks/{bid}", h, {"summary": a["resumo"], "note": note})
    if a["tags"]:
        req("POST", f"{base}/bookmarks/{bid}/tags", h, {"tags": [{"tagName": t, "attachedBy": "human"} for t in a["tags"]]})
    lists = req("GET", base + "/lists", h).get("lists", [])
    lid = next((x["id"] for x in lists if fold(x["name"]) == fold(a["tema"])), None)
    if not lid:
        lid = req("POST", base + "/lists", h, {"name": a["tema"], "icon": "📚"})["id"]
    req("PUT", f"{base}/lists/{lid}/bookmarks/{bid}", h)
    j["data"]["kk"] = True


def sb_local(path):
    """Caminho dentro de SB_LOCAL; nunca sai dela."""
    f = os.path.realpath(os.path.join(SB_LOCAL, path))
    if not f.startswith(os.path.realpath(SB_LOCAL) + os.sep):
        raise ValueError("caminho fora da pasta: " + path)
    return f


def sb_put(c, path, data, ctype="text/markdown"):
    if SB_LOCAL:
        f = sb_local(path)
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f + ".tmp", "wb") as o:
            o.write(data if isinstance(data, bytes) else data.encode())
        os.replace(f + ".tmp", f)
        return 200
    import urllib.request as ur
    rq = ur.Request(c["sb_url"].rstrip("/") + "/.fs/" + ur.quote(path), data=data if isinstance(data, bytes) else data.encode(),
                    method="PUT", headers={"Authorization": "Bearer " + c["sb_token"], "Content-Type": ctype})
    with ur.urlopen(rq, timeout=60) as r:
        return r.status


def sb_orfas(page, orig):
    """v1.12.1: apaga outras páginas (pasta local) do MESMO original. Uma página por item, sempre."""
    if not SB_LOCAL or not orig:
        return 0
    pasta_ = os.path.dirname(sb_local(page + ".md"))
    alvo, n = "link: " + json.dumps(orig, ensure_ascii=False), 0
    for f in glob.glob(os.path.join(pasta_, "*.md")):
        if os.path.realpath(f) == os.path.realpath(sb_local(page + ".md")):
            continue
        try:
            with open(f, encoding="utf-8") as h:
                topo = h.read(4000)
        except OSError:
            continue
        partes = topo.lstrip().split("---")
        fm = partes[1] if topo.lstrip().startswith("---") and len(partes) > 2 else ""
        if any(l.strip() == alvo for l in fm.splitlines()):
            os.remove(f)
            n += 1
            log("página duplicada apagada:", os.path.basename(f))
    return n


def sb_orfas_todas():
    n = 0
    for r in q("SELECT id FROM jobs WHERE status='pronto'"):
        j = job(r["id"])
        pag = (j["data"].get("sb") or {}).get("page")
        if pag:
            try:
                n += sb_orfas(pag, j["orig"])
            except Exception as e:
                log("limpeza de duplicadas falhou:", scrub(e)[:120])
    if n:
        log(f"{n} página(s) duplicada(s) apagada(s)")
    return n


def sb_nome(t):
    return re.sub(r"[\\/#\[\]|^:*?\"<>]+", " ", t).strip()[:90] or "Sem título"


def yt_info(c, url):
    """Metadados do original. Instagram: HikerAPI/login (v1.10) e, se falhar, yt-dlp (-j) com os cookies do site."""
    if ig_on(c, url):
        try:
            return ig_info(c, url)
        except Exception as e:
            log("Instagram sem metadados, tentando yt-dlp:", scrub(e)[:160])
    with cookie_file(site_cookies(c, url)) as ck:
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "-j", "--no-playlist", "--no-warnings", url] + (["--cookies", ck] if ck else []),
                           capture_output=True, text=True, timeout=60)
    if not p.stdout.strip():  # antes: "list index out of range" quando o login caía
        raise RuntimeError("yt-dlp: " + scrub((p.stderr.strip().splitlines() or ["sem resposta"])[-1])[:200])
    return json.loads(p.stdout.splitlines()[0])


METRICAS = (("views", "view_count"), ("likes", "like_count"), ("comentarios", "comment_count"), ("compart", "repost_count"))


def meta_de_info(info):
    """Data de postagem, autor e números do original (só o que o site informa; nada estimado)."""
    m = {}
    ts, ud = info.get("timestamp"), info.get("upload_date")
    if ts:
        m["postado"] = time.strftime("%Y-%m-%d", time.localtime(ts))
    elif ud and re.fullmatch(r"\d{8}", str(ud)):
        m["postado"] = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}"
    nome, handle = info.get("uploader") or "", info.get("channel") or info.get("uploader_id") or ""
    if nome or handle:
        m["autor"] = (f"{nome} (@{handle})" if nome and handle and nome != handle else nome or "@" + str(handle).lstrip("@"))[:80]
    for k, src in METRICAS:
        if isinstance(info.get(src), int) and info[src] >= 0:
            m[k] = info[src]
    m["lido"] = time.strftime("%Y-%m-%d")
    return m


def sb_meta(j, c, forcar=False):
    """Uma vez por item (ou de novo com forcar): guarda em data["meta"]. Falhou = segue sem métricas."""
    d = j["data"]
    if (d.get("meta") and not forcar) or not j["orig"] or d.get("kind", "video") != "video":
        return
    try:
        d["meta"] = {**d.get("meta", {}), **meta_de_info(yt_info(c, j["orig"]))}
    except Exception as e:
        log(f"job {j['id']} sem métricas do original: {scrub(e)[:120]}")


def num_br(n):
    """1234 -> 1,2 mil; 1500000 -> 1,5 mi (pt-BR)."""
    for lim, suf in ((1_000_000_000, " bi"), (1_000_000, " mi"), (1_000, " mil")):
        if n >= lim:
            v = f"{n / lim:.1f}".rstrip("0").rstrip(".")
            return v.replace(".", ",") + suf
    return str(n)


def data_br(iso):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso or "")[:10])
    return f"{m[3]}/{m[2]}/{m[1]}" if m else ""


def yt_capa(c, url):
    """Capa que o criador escolheu (miniatura do Reel/TikTok no perfil) via yt-dlp, em JPEG."""
    info = yt_info(c, url)
    u = info.get("thumbnail") or max(info["thumbnails"], key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))["url"]
    from PIL import Image
    out = io.BytesIO()
    with Image.open(io.BytesIO(req("GET", u, timeout=60, raw=True))) as im:
        im.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


def sb_capa(j, c):
    """Capa do card no SilverBullet: miniatura original (yt-dlp) → imagem que o Karakeep guardou → sem capa."""
    p = f"{pasta(c, 'sb_reels')}/capas/{j['id']}.jpg"
    if j["orig"]:
        try:
            sb_put(c, p, yt_capa(c, j["orig"]), "image/jpeg")
            return p
        except Exception as e:
            log(f"job {j['id']} sem capa do yt-dlp: {scrub(e)[:120]}")
    if j["data"].get("og_image"):  # página lida direto: imagem de compartilhamento
        try:
            sb_put(c, p, req("GET", j["data"]["og_image"], {"User-Agent": UA}, raw=True), "image/jpeg")
            return p
        except Exception as e:
            log(f"job {j['id']} sem og:image: {scrub(e)[:120]}")
    if not (kk_ativo(c) and j["bookmark_id"]):
        return ""
    try:
        base, h = kk(c)
        ct = bookmark(c, j["bookmark_id"]).get("content") or {}
        aid = ct.get("imageAssetId") or ct.get("screenshotAssetId")
        if not aid:
            return ""
        import urllib.request as ur
        with ur.urlopen(ur.Request(f"{base}/assets/{aid}", headers=h), timeout=60) as r:
            img = r.read()
        sb_put(c, p, img, "image/jpeg")
        return p
    except Exception as e:
        log(f"job {j['id']} sem capa: {scrub(e)[:120]}")
        return ""


def sb_get(c, path):
    """Bytes do arquivo no SilverBullet; None só se não existe (404). Outro erro sobe: nunca confundir "fora do ar" com "vazio"."""
    if SB_LOCAL:
        f = sb_local(path)
        if not os.path.exists(f):
            return None
        with open(f, "rb") as o:
            return o.read()
    import urllib.request as ur
    try:
        with ur.urlopen(ur.Request(c["sb_url"].rstrip("/") + "/.fs/" + ur.quote(path), headers={"Authorization": "Bearer " + c["sb_token"]}), timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


SB_TEMA = """
# {tema}

{sintese}
${{reelCards("{tema}")}}
"""


def sb_sintese(j, c, tema):
    """Síntese do assunto: Gemini junta os resumos de todos os Reels do tema. Regerada a cada Reel novo."""
    if c.get("sintese_on") != "1":
        return ""
    itens = [x["analysis"] for x in (json.loads(r["data"] or "{}") for r in q("SELECT data FROM jobs WHERE id<>?", (j["id"],)))
             if x.get("analysis") and fold(x["analysis"].get("tema", "")) == fold(tema)] + [j["data"]["analysis"]]
    lista = "\n\n".join(f"- {x['titulo']}: {x['resumo']}" for x in itens[-40:])
    try:
        r = llm(c, {"stream": False, "messages": [
                    {"role": "system", "content": "Você escreve em português do Brasil, direto, sem floreio e sem cara de texto de IA. "
                     "Recebe resumos de vídeos salvos sobre um mesmo assunto. Escreva em Markdown: um parágrafo curto com o que esses vídeos têm em comum "
                     "e o que dá pra aprender juntando eles; depois '### Ideias que se repetem' com 3 a 6 tópicos curtos; "
                     "se houver, '### Onde eles discordam' com 1 a 3 tópicos. Não invente nada que não esteja nos resumos. Sem título principal."},
                    {"role": "user", "content": f"Assunto: {tema}\n\n{lista}"}]}, c.get("model_sintese") or c["model"])
        txt = r["choices"][0]["message"]["content"].strip()
        return f"## Síntese\n_Atualizada pela IA em {time.strftime('%d/%m/%Y')} com {len(itens)} Reels._\n\n{txt}\n\n## Reels\n"
    except Exception as e:
        log(f"job {j['id']} sem síntese: {scrub(e)[:120]}")
        return ""


def sb_reestilo(j, c):
    """Uma vez por Reel antigo: reescreve resumo/pontos/o que aparece no ESTILO (sem tocar fatos, título, tema, tags, transcrição)."""
    a = j["data"]["analysis"]
    if a.get("estilo", 0) >= 2 or c.get("reestilo_on") != "1":
        return
    campos = {k: a[k] for k in ("resumo", "pontos_chave", "o_que_aparece") if a.get(k) and not a.get("estilo")}
    campos["gancho"] = a.get("gancho") or ""
    try:
        r = llm(c, {"stream": False, "messages": [
                    {"role": "system", "content": "Reescreva os campos deste JSON em português do Brasil sem mudar fatos, sem acrescentar nem tirar informação. "
                     f"Responda SOMENTE o JSON com as mesmas chaves e tipos (resumo string 2 a {num(c, 'resumo_frases')} frases; listas continuam listas de strings). "
                     "Se \"gancho\" vier vazio, escreva 1 frase de até 100 caracteres dizendo do que se trata." + estilo(c)},
                    {"role": "user", "content": json.dumps({**campos, "titulo": a["titulo"], "resumo_atual": a["resumo"]}, ensure_ascii=False)}]},
                   c.get("model_sintese") or c["model"])
        txt = r["choices"][0]["message"]["content"].strip()
        novo = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        for k, v in campos.items():
            if type(novo.get(k)) is type(v) and novo[k]:
                a[k] = novo[k]
        a["gancho"] = str(a.get("gancho") or "")[:120]
        a["estilo"] = 2
    except Exception as e:
        log(f"job {j['id']} sem reestilo: {scrub(e)[:120]}")


MB = 1024 * 1024


def duracao(path):
    """Segundos do vídeo pelo ffprobe. 0 = não deu pra medir."""
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60)
        return float(p.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def plano_video(c, tamanho, dur):
    """O que fazer com o vídeo no SilverBullet: ("enviar"|"comprimir"|"link", limite em bytes, altura máx. ou None).
    Curto (dur desconhecida conta como curto: IG/TikTok): cabe = original; não cabe = comprime mantendo a resolução.
    Longo: política "link" não envia; "comprimir" envia até o limite próprio, na resolução de longos."""
    if dur <= num(c, "curto_min") * 60:
        lim = num(c, "curto_mb") * MB
        return ("enviar" if tamanho <= lim else "comprimir"), lim, None
    if c.get("longo_politica") != "comprimir":
        return "link", 0, None
    lim = num(c, "longo_mb") * MB
    return ("enviar" if tamanho <= lim else "comprimir"), lim, int(c.get("longo_res") or 720)


def bitrate_alvo(limite, dur, audio_kbps=96):
    """kbps de vídeo pra caber em `limite` bytes: total disponível com 8% de margem (contêiner, picos) menos o áudio. Mínimo 100."""
    return max(100, int(limite * 8 * 0.92 / dur / 1000) - audio_kbps)


def comprimir(src, dst, limite, dur, altura=None):
    """H.264 + AAC com CRF 23 e teto de bitrate (maxrate/bufsize) calculado pelo limite. Devolve dst, ou None se não coube
    ou não ficou menor que o original (nunca aumenta o arquivo)."""
    if not dur or not shutil.which("ffmpeg"):
        return None
    v = bitrate_alvo(limite, dur)
    vf = ["-vf", f"scale=-2:'min({altura},ih)'"] if altura else []  # sem altura: mantém a resolução
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, *vf, "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                        "-maxrate", f"{v}k", "-bufsize", f"{2 * v}k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                        "-movflags", "+faststart", dst], capture_output=True, text=True, timeout=3600)
    ok = p.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) <= limite and os.path.getsize(dst) < os.path.getsize(src)
    return dst if ok else None


def pasta(c, k):
    return (c.get(k) or DEFAULTS[k]).strip().strip("/") or DEFAULTS[k]


def sb_midia(j, c):
    """Vídeo no SilverBullet (toca no card) + linha do tempo: quadros do crv com timecode e o que acontece. Uma vez por Reel."""
    d, a = j["data"], j["data"]["analysis"]
    sb = d.setdefault("sb", {})
    if sb.get("midia") or d.get("kind", "video") != "video" or not j["orig"]:
        return
    pasta_m, R, larg = os.path.join(DATA, "sbmedia"), pasta(c, "sb_reels"), num(c, "tl_largura")
    os.makedirs(pasta_m, exist_ok=True)
    media = next(iter(glob.glob(os.path.join(pasta_m, f"{j['id']}.*"))), None)
    out, cmp = os.path.join(DATA, "tmp", f"tl-{j['id']}"), os.path.join(DATA, "tmp", f"cmp-{j['id']}.mp4")
    try:
        if not media:  # Reel antigo: baixa de novo
            media = baixar(c, j["orig"], os.path.join(pasta_m, str(j["id"])), qual_download(c, d.get("dur", 0)))
        if media.endswith(".mp3"):
            raise RuntimeError("só áudio, sem vídeo")
        dur = duracao(media) or d.get("dur", 0)
        acao, lim, alt = plano_video(c, os.path.getsize(media), dur)
        enviar = media if acao == "enviar" else comprimir(media, cmp, lim, dur, alt) if acao == "comprimir" else None
        if enviar:
            sb_put(c, f"{R}/videos/{j['id']}.mp4", open(enviar, "rb").read(), "video/mp4")
            sb["video"] = f"{R}/videos/{j['id']}.mp4"
        elif acao == "comprimir":
            log(f"job {j['id']}: vídeo não coube em {lim // MB} MB nem comprimido; página fica com capa e link")
        run_crv(media, out, 24)
        with open(os.path.join(out, "frames.json")) as f:
            frames = json.load(f)["frames"]
        def seg(x):
            m = re.match(r"\s*(\d+):(\d{2})", x)
            return int(m[1]) * 60 + int(m[2]) if m else None
        eventos = [(seg(x), re.sub(r"^\s*\d+:\d{2}\s*[:\-–]?\s*", "", x)) for x in a.get("o_que_aparece") or []]
        n = num(c, "tl_quadros")
        eventos = [e for e in eventos if e[0] is not None][:n] or [(int(fr["timestamp_sec"]), "") for fr in frames[::max(1, len(frames) // 8)]][:min(8, n)]
        from PIL import Image
        linha = []
        for i, (t, txt) in enumerate(eventos):
            fr = min(frames, key=lambda x: abs(x["timestamp_sec"] - t))
            src = next(iter(glob.glob(os.path.join(out, "**", fr["file"]), recursive=True)), None)
            if not src:
                continue
            p = os.path.join(out, f"q{i}.jpg")
            with Image.open(src) as im:
                k = larg / max(im.width, 1)
                im.convert("RGB").resize((larg, max(1, int(im.height * k)))).save(p, "JPEG", quality=78)
            dest = f"{R}/quadros/{j['id']}/{i:02d}.jpg"
            sb_put(c, dest, open(p, "rb").read(), "image/jpeg")
            linha.append({"t": mmss(t), "img": dest, "txt": txt})
        sb["linha"] = linha
    except Exception as e:
        log(f"job {j['id']} sem mídia no SilverBullet: {scrub(e)[:120]}")
    finally:
        sb["midia"] = True
        shutil.rmtree(out, ignore_errors=True)
        for f in glob.glob(os.path.join(pasta_m, f"{j['id']}.*")) + [cmp] * os.path.exists(cmp):
            os.remove(f)


MURAL = re.compile(r"\$\{widget\.html\(\[==\[<!--vkb-->.*?\]==\]\)\}\n\n?|```excalidraw\n.*?```\n\n?", re.S)  # mural nosso + Excalidraw (removido na 1.6.2)


# ---- v1.7: página do Reel e mural redesenhados (widget.html; o SB tira <div>/<img>/<video> do HTML cru) ----
CHIP = "display:inline-block;padding:2px 10px;border-radius:999px;background:#8882;font-size:.78em;line-height:1.6;white-space:nowrap"
BTN = ("display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:999px;border:1px solid #8885;"
       "text-decoration:none;color:inherit;font-size:.9em;font-weight:600")


def esc(s):
    """HTML seguro dentro de ${widget.html([==[...]==])}: escapa <>&" e chaves (o SB interpreta ${...}) e o fecho ]==]."""
    return html.escape(str(s)).replace("{", "&#123;").replace("}", "&#125;").replace("]==]", "]&#61;&#61;]")


def rel(page, alvo):
    """Link pra `alvo` (ex.: ".fs/Reels/videos/1.mp4") sem "/" no começo: o SB põe <base href> na raiz do space,
    então resolve igual em qualquer página e com ou sem Path (/cerebro). "../" quebra (testado no SB, 17 out)."""
    return urllib.request.quote(alvo)


def widget(tag, corpo):
    return f"${{widget.html([==[<!--{tag}-->{corpo}]==])}}\n\n"


def fonte_nome(url):
    u = (url or "").lower()
    return next((n for k, n in (("instagram", "Instagram"), ("tiktok", "TikTok"), ("youtu", "YouTube"), ("reddit", "Reddit"),
                                ("x.com", "X"), ("twitter", "X")) if k in u), "o original")


def metricas_html(meta, tam=".85em"):
    """▶ views · ♥ curtidas · 💬 comentários · ↻ compartilhamentos (só os que o site informou)."""
    ic = (("views", "▶", "visualizações"), ("likes", "♥", "curtidas"), ("comentarios", "💬", "comentários"), ("compart", "↻", "compartilhamentos"))
    itens = [f'<span title="{t}" style="white-space:nowrap"><span style="opacity:.6">{i}</span> {num_br(meta[k])}</span>'
             for k, i, t in ic if isinstance(meta.get(k), int)]
    return f'<div style="display:flex;flex-wrap:wrap;gap:4px 12px;font-size:{tam};font-weight:600">{"".join(itens)}</div>' if itens else ""


def datas_html(meta, salvo, tam=".8em"):
    partes = ([f"Postado {data_br(meta['postado'])}"] if data_br(meta.get("postado")) else []) + ([f"Salvo {data_br(salvo)}"] if data_br(salvo) else [])
    return f'<div style="font-size:{tam};opacity:.75">{esc(" · ".join(partes))}</div>' if partes else ""


LINK_ROTULO = {"ferramenta": "Ferramenta", "produto": "Produto", "livro": "Livro", "podcast": "Podcast", "repositorio": "Repositório", "curso": "Curso", "jogo": "Jogo", "site": "Site"}


def reel_md(fm_txt, page, a, orig, sb, capa, tema_page, data, tags, linha, video, texto, meta=None, links=None):
    """Markdown da página do Reel: frontmatter + topo (player 9:16 ao lado de assunto, datas, métricas, título, gancho, resumo, botões),
    pontos-chave, linha do tempo em grade e transcrição recolhível. Sem `# título` em markdown: o título está no topo."""
    meta = meta or {}
    r = lambda p: rel(page, p)
    if sb.get("video"):
        midia = (f'<video controls preload="metadata" playsinline src="{r(".fs/" + sb["video"])}"'
                 + (f' poster="{r(".fs/" + capa)}"' if capa else "")
                 + ' style="width:100%;aspect-ratio:9/16;object-fit:contain;background:#000;border-radius:16px;display:block;'
                   'box-shadow:0 8px 28px #0005"></video>')
    elif capa:
        midia = (f'<a href="{esc(orig)}" target="_blank" rel="noopener" style="display:block;position:relative">'
                 f'<img src="{r(".fs/" + capa)}" style="width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:16px;display:block">'
                 '<span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">'
                 '<span style="width:64px;height:64px;border-radius:50%;background:#000a;color:#fff;font-size:26px;display:flex;'
                 'align-items:center;justify-content:center">▶</span></span></a>')
    else:
        midia = ""
    tema_nome = tema_page.rsplit("/", 1)[-1]
    autor = f'<span style="font-size:.85em;font-weight:600">{esc(meta["autor"])}</span>' if meta.get("autor") else ""
    botoes = ((f'<a href="{esc(orig)}" target="_blank" rel="noopener" style="{BTN}">↗ Abrir no {esc(fonte_nome(orig))}</a> ' if orig else "")
              + f'<a href="{r(tema_page)}" style="{BTN}">Mais sobre {esc(tema_nome)}</a>')
    topo = ('<div style="display:flex;flex-wrap:wrap;gap:28px;align-items:flex-start;margin:6px 0 4px">'
            + (f'<div style="flex:0 1 300px;min-width:220px;max-width:340px">{midia}</div>' if midia else "")
            + '<div style="flex:1 1 300px;min-width:0">'
            + f'<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px"><span style="{CHIP}">{esc(a.get("tema", ""))}</span>{autor}</div>'
            + f'<div style="font-size:1.7em;font-weight:800;line-height:1.2;margin:10px 0 8px">{esc(a["titulo"])}</div>'
            + (f'<div style="font-size:1.08em;font-style:italic;margin-bottom:12px;opacity:.9">{esc(a["gancho"])}</div>' if a.get("gancho") else "")
            + f'<div style="display:flex;flex-direction:column;gap:6px;margin-bottom:14px">{datas_html(meta, data, ".85em")}{metricas_html(meta, ".95em")}</div>'
            + f'<div style="line-height:1.6;margin-bottom:16px">{esc(a["resumo"])}</div>'
            + f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px">{botoes}</div>'
            + "</div></div>")
    md = fm_txt + widget("vkb-reel", topo)
    if links:
        md += "## Links\n" + widget("vkb-links", '<div style="display:flex;flex-wrap:wrap;gap:10px">' + "".join(
            f'<a href="{esc(x["url"])}" target="_blank" rel="noopener" style="display:flex;flex-direction:column;gap:2px;padding:10px 14px;border-radius:12px;'
            f'border:1px solid #8885;text-decoration:none;color:inherit;min-width:180px;max-width:320px">'
            f'<span style="font:600 .7em ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase;opacity:.7">{esc(LINK_ROTULO.get(x["tipo"], x["tipo"]))} · {esc(x.get("site", ""))}</span>'
            f'<span style="font-weight:700">{esc(x["nome"])} ↗</span>'
            + (f'<span style="font-size:.85em;opacity:.8">{esc(x["porque"])}</span>' if x.get("porque") else "") + "</a>" for x in links) + "</div>")
    if a.get("pontos_chave"):
        md += "## Pontos-chave\n" + "\n".join("- " + p for p in a["pontos_chave"]) + "\n\n"
    if linha:
        cards = "".join(
            '<div style="border-radius:12px;overflow:hidden;background:#8881;border:1px solid #8883">'
            f'<div style="position:relative"><img src="{r(".fs/" + x["img"])}" loading="lazy" style="width:100%;aspect-ratio:9/16;object-fit:cover;display:block">'
            f'<span style="position:absolute;left:8px;top:8px;padding:1px 8px;border-radius:6px;background:#000b;color:#fff;font:600 .78em ui-monospace,monospace">{esc(x["t"])}</span></div>'
            f'<div style="padding:8px 10px;font-size:.85em;line-height:1.45">{esc(x["txt"])}</div></div>'
            for x in linha)
        md += "## Linha do tempo\n" + widget("vkb-linha", f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px">{cards}</div>')
    elif a.get("o_que_aparece"):
        md += "## O que aparece\n" + "\n".join("- " + x for x in a["o_que_aparece"]) + "\n\n"
    titulo_txt = "Transcrição" if video else "Texto"
    paras = "".join(f'<p style="margin:0 0 .8em">{esc(p)}</p>' for p in re.split(r"\n\s*\n|\n", texto or "") if p.strip())
    md += ("## " + titulo_txt + "\n" + widget("vkb-transcricao",
           f'<details style="border:1px solid #8883;border-radius:12px;padding:10px 14px"><summary style="cursor:pointer;font-weight:600">'
           f'Mostrar {titulo_txt.lower()} completa</summary><div style="margin-top:10px;line-height:1.65;opacity:.9">{paras}</div></details>'))
    return md


def datas_card(meta, salvo):
    """Rodapé do card: rótulo pequeno em cima da data, Postado e Salvo lado a lado, separado do texto por uma linha."""
    itens = [(r, data_br(v)) for r, v in (("Postado", (meta or {}).get("postado")), ("Salvo", salvo)) if data_br(v)]
    if not itens:
        return ""
    col = "".join(f'<div><div style="font-size:.85em;opacity:.55;text-transform:uppercase;letter-spacing:.06em;font-weight:600">{r}</div>'
                  f'<div style="font-weight:600;opacity:.9">{v}</div></div>' for r, v in itens)
    return f'<div style="margin-top:auto;padding-top:9px;border-top:1px solid #8883;display:flex;gap:18px;font-size:.72em">{col}</div>'


def previa_html(frames, resumo, base):
    """Hover (CSS do Config.md, .vkb-card:hover): quadros do vídeo passando em sequência + resumo por cima da capa. Sem JS."""
    fr = (frames * 6)[:6] if frames else []
    imgs = "".join(f'<img src="{rel(base, f)}" loading="lazy" style="animation-delay:{i}s">' for i, f in enumerate(fr))
    txt = f'<div class="vkb-prev-txt">{esc(resumo)}</div>' if resumo else ""
    return f'<div class="vkb-prev">{imgs}{txt}</div>' if imgs or txt else ""


def previa_frames(s):
    """Até 6 quadros da linha do tempo, espalhados (".fs/..." para o widget e para o frontmatter)."""
    lin = [x["img"] for x in s.get("linha") or [] if x.get("img")]
    if len(lin) > 6:
        lin = [lin[round(i * (len(lin) - 1) / 5)] for i in range(6)]
    return [".fs/" + x for x in lin]


def mural_card(page, a, s, data, meta=None):
    """Card do mural: capa 9:16 com assunto e métricas por cima; título e gancho inteiros (sem corte), letra menor;
    datas de postagem e de quando foi salvo. O card todo abre a página do Reel."""
    meta = meta or {}
    capa = s.get("capa")
    img = (f'<img src="{rel("index", ".fs/" + capa)}" loading="lazy" style="width:100%;height:100%;object-fit:cover;display:block">' if capa
           else '<div style="width:100%;height:100%;background:linear-gradient(135deg,#8884,#8881)"></div>')
    play = ('<span style="position:absolute;right:10px;top:10px;width:30px;height:30px;border-radius:50%;background:#000a;color:#fff;'
            'font-size:12px;display:flex;align-items:center;justify-content:center">▶</span>' if s.get("video") else "")
    mt = metricas_html(meta, ".78em")
    return (f'<a class="vkb-card" href="{urllib.request.quote(s["page"])}" style="display:flex;flex-direction:column;text-decoration:none;color:inherit;border-radius:14px;overflow:hidden;'
            'background:#8881;border:1px solid #8884">'
            f'<div style="position:relative;aspect-ratio:4/5;background:#000">{img}'
            + (f'<div style="position:absolute;inset:auto 0 0 0;padding:28px 10px 8px;background:linear-gradient(transparent,#000d);color:#fff">{mt}</div>' if mt else "")
            + f'<span style="position:absolute;left:10px;top:10px;padding:3px 10px;border-radius:999px;background:#000b;color:#fff;font-size:.72em;font-weight:600">{esc(a.get("tema", ""))}</span>'
            + previa_html(previa_frames(s), a.get("resumo", ""), "index")
            + f'{play}</div>'
            f'<div style="padding:12px 12px 12px;display:flex;flex-direction:column;gap:6px;flex:1">'
            f'<div style="font-weight:700;font-size:.9em;line-height:1.3;overflow-wrap:anywhere">{esc(a.get("titulo", ""))}</div>'
            f'<div style="font-size:.78em;line-height:1.4;opacity:.85;overflow-wrap:anywhere">{esc(a.get("gancho", ""))}</div>'
            f'{datas_card(meta, data)}</div></a>')


def mural_bloco(cards):
    return widget("vkb", f'<div style="opacity:.75;font-size:.85em;margin:-4px 0 12px">{len(cards)} vídeos salvos · mais recentes primeiro</div>'
                  '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:18px">' + "".join(cards) + "</div>")


def mural_html(c):
    """Cards de todos os Reels, mais recentes primeiro (mural_card). widget.html: o SB tira <div>/<img> do HTML cru da página."""
    cards = []
    for r in q("SELECT data, created FROM jobs ORDER BY created DESC"):
        d = json.loads(r["data"] or "{}")
        s, a = d.get("sb") or {}, d.get("analysis") or {}
        if s.get("page"):
            cards.append(mural_card(s["page"], a, s, time.strftime("%Y-%m-%d", time.localtime(r["created"] or time.time())), d.get("meta")))
    return mural_bloco(cards)


ASSUNTOS = "## Assuntos\n${reelAssuntos()}"


def sb_mural(c):
    """Põe o mural antes de "## Assuntos" em Video KB.md e index.md (index.md = página inicial real do space) e tira o bloco Excalidraw.
    Idempotente: troca o mural anterior. Só GET/PUT no SB. Não mexe em Quadro.excalidraw (notas do nan)."""
    bloco = mural_html(c) if c.get("mural_on") == "1" else ""
    for pagina in (pasta(c, "sb_pagina") + ".md", "index.md"):
        pg = sb_get(c, pagina)
        if pg is None:
            continue
        txt = pg.decode()
        base = MURAL.sub("", txt)
        if ASSUNTOS in base:  # v1.8.3: assuntos (chips) no topo, mural depois
            novo = base.replace(ASSUNTOS, ASSUNTOS + "\n\n" + bloco, 1)
        else:
            novo = base.replace("## Assuntos", bloco + "## Assuntos", 1)
        novo = sb_topo(re.sub(r"\n{3,}", "\n\n", novo))
        if novo != txt:
            sb_put(c, pagina, novo)


def sb_topo(txt):
    """Linha vazia no início do corpo. O SB abre a página com o cursor ali e mostra essa linha crua:
    vídeo vira texto `![](...mp4)`, título mostra o `#`. Testado no SB 2.12.1."""
    m = re.match(r"---\n.*?\n---\n", txt, re.S)
    i = m.end() if m else 0
    return txt if txt[i:i + 1] == "\n" else txt[:i] + "\n" + txt[i:]


def sb_topo_todos(c):
    """Uma vez: põe a linha vazia (sb_topo) nas páginas já gravadas. Só GET/PUT no SB, sem IA nem refazer mídia."""
    paginas = [json.loads(r["data"] or "{}").get("sb", {}).get("page") for r in q("SELECT data FROM jobs")]
    paginas += [pasta(c, "sb_temas") + "/" + sb_nome(json.loads(r["data"])["analysis"]["tema"])
                for r in q("SELECT data FROM jobs WHERE data LIKE '%\"sb7\"%'")]
    for p in sorted({p + ".md" for p in paginas if p}) + [pasta(c, "sb_pagina") + ".md", "index.md"]:
        pg = sb_get(c, p)
        if pg is not None and sb_topo(pg.decode()) != pg.decode():
            sb_put(c, p, sb_topo(pg.decode()))
    q("INSERT OR REPLACE INTO kv VALUES('sb_topo','1')")


def sb_ligado(c):
    if SB_LOCAL:  # o Selah depende dessas páginas: sempre ligado
        return True
    return c.get("sb_on") == "1" and bool(c.get("sb_url") and c.get("sb_token"))


def mural():
    """Regera o mural quando a fila esvazia e algum Reel mudou: N Reels (ex.: os antigos na 1ª vez) = uma escrita só."""
    c = cfg()
    if sb_ligado(c) and not q("SELECT 1 FROM kv WHERE k='sb_topo'"):
        try:
            sb_topo_todos(c)
        except Exception as e:  # tenta de novo na próxima fila vazia
            log(f"sb_topo: {scrub(e)[:200]}")
    if sb_ligado(c) and not q("SELECT 1 FROM kv WHERE k='sb_v183'"):
        try:
            sb_regrava_todos(c)
        except Exception as e:  # tenta de novo na próxima fila vazia
            log(f"sb_v183: {scrub(e)[:200]}")
    if not sb_ligado(c) or not q("SELECT 1 FROM kv WHERE k='mural_sujo' AND v='1'"):
        return
    q("INSERT OR REPLACE INTO kv VALUES('mural_sujo','0')")
    try:
        sb_mural(c)
    except Exception as e:  # ponytail: tenta de novo no próximo Reel ou ao salvar o Config
        log(f"mural não gerado: {scrub(e)[:200]}")


def sb_refila():
    """SilverBullet ligado: prontos sem a marca atual passam de novo pelo step; mural regera."""
    if sb_ligado(cfg()):
        q("UPDATE jobs SET status='fila' WHERE status='pronto' AND data NOT LIKE '%\"sb7\"%'")
        q("INSERT OR REPLACE INTO kv VALUES('mural_sujo','1')")
        WAKE.set()


def sb_reel_md(j, page, a, sb, capa, tema, fm, tags, video):
    """Página do Reel (v1.7): frontmatter (as queries do space leem) + reel_md."""
    fm_txt = ("---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fm.items())
              + "tags:\n" + "".join(f"  - {json.dumps(t, ensure_ascii=False)}\n" for t in tags) + "---\n\n")  # linha vazia: ver sb_topo
    d = j["data"]
    return reel_md(fm_txt, page, a, j["orig"] or "", sb, capa, tema, fm["data"], tags, sb.get("linha") or [], video,
                   a.get("transcricao_limpa", "") if video else d.get("transcript", "")[:20000], d.get("meta"), d.get("links"))


def sb_reel_campos(j, c, capa):
    """frontmatter, tags, página do assunto e se é vídeo: o mesmo pro step e pra regravação da v1.7."""
    d, a = j["data"], j["data"]["analysis"]
    sb = d.get("sb") or {}
    tema = pasta(c, "sb_temas") + "/" + sb_nome(a["tema"])
    tags = [re.sub(r"[^\w-]+", "-", t.strip()).strip("-").lower() for t in a["tags"] if t.strip()]
    fm = {"type": "reel", "title": a["titulo"], "tema": a["tema"], "data": time.strftime("%Y-%m-%d", time.localtime(j.get("created") or time.time())),
          "cover": (".fs/" + capa) if capa else "", "link": j["orig"] or "", "gancho": a.get("gancho", ""), "resumo": a["resumo"],
          "video": (".fs/" + sb["video"]) if sb.get("video") else ""}
    meta = d.get("meta") or {}
    fm.update({k: meta[k] for k in ("postado", "autor", "views", "likes", "comentarios", "compart") if k in meta})
    if d.get("links"):
        fm["links"] = [{k: x[k] for k in ("tipo", "nome", "url", "site", "porque") if x.get(k)} for x in d["links"]]
    if pv := previa_frames(sb):
        fm["previa"] = pv  # Lua dos assuntos lê
    return fm, tags, tema, d.get("kind", "video") == "video"


def sb_regrava_todos(c, flag="sb_v183"):
    """Uma vez por versão do layout: regrava as páginas prontas. Só PUT no SB (v1.8: + métricas do original pelo yt-dlp); sem IA, sem baixar vídeo."""
    for r in q("SELECT id FROM jobs WHERE data LIKE '%\"sb7\"%'"):
        j = job(r["id"])
        sb_meta(j, c)
        save(j)
        sb = j["data"].get("sb") or {}
        if not sb.get("page") or not j["data"].get("analysis"):
            continue
        fm, tags, tema, video = sb_reel_campos(j, c, sb.get("capa", ""))
        sb_put(c, sb["page"] + ".md", sb_reel_md(j, sb["page"], j["data"]["analysis"], sb, sb.get("capa", ""), tema, fm, tags, video))
    q("INSERT OR REPLACE INTO kv VALUES(?,'1')", (flag,))
    q("INSERT OR REPLACE INTO kv VALUES('mural_sujo','1')")


def step_silverbullet(j, c):
    if not SB_LOCAL:
        need(c, "sb_url", "sb_token")
    sb_reestilo(j, c)
    sb_midia(j, c)
    sb_meta(j, c)
    d, a = j["data"], j["data"]["analysis"]
    sb = d.get("sb") or {}
    # v1.11.2: reprocessar mantém a página que já existe (título novo da IA não cria cópia nem quebra link dos Temas)
    page = sb.get("page") if str(sb.get("page") or "").startswith(pasta(c, "sb_reels") + "/") else pasta(c, "sb_reels") + "/" + sb_nome(a["titulo"])
    capa = sb.get("capa", "") if sb.get("capa_orig") else sb_capa(j, c) or sb.get("capa", "")  # v1.4: troca a capa uma vez
    fm, tags, tema, video = sb_reel_campos(j, c, capa)
    md = sb_reel_md(j, page, a, sb, capa, tema, fm, tags, video)
    sb_put(c, page + ".md", md)
    sb_orfas(page, j["orig"])
    sb_put(c, tema + ".md", SB_TEMA.format(tema=a["tema"].replace('"', ""), sintese=sb_sintese(j, c, a["tema"])))
    d["sb"] = {**sb, "page": page, "capa": capa, "capa_orig": True, "tema": tema, "t": time.time()}
    d["sb7"] = True
    q("INSERT OR REPLACE INTO kv VALUES('mural_sujo','1')")


def step_notebook(j, c):
    need(c, "notebook_url")
    base = c["notebook_url"].rstrip("/") + "/api"
    h = nb_headers(c)
    a = j["data"]["analysis"]
    nid = next((n["id"] for n in notebooks(c) if fold(n["name"]) == fold(a["tema"])), None)
    if not nid:
        nid = req("POST", base + "/notebooks", h, {"name": a["tema"], "description": "Criado pelo Video KB"})["id"]
    video = j["data"].get("kind", "video") == "video"
    content = (f"# {a['titulo']}\n\n" + (f"Link: {j['orig']}\n\n" if j["orig"] else "") + f"## Resumo\n{a['resumo']}\n\n## Pontos-chave\n"
               + "\n".join("- " + p for p in a["pontos_chave"])
               + f"\n\nTags: {', '.join(a['tags'])}\n\n"
               + ("## O que aparece na tela\n" + "\n".join("- " + x for x in a["o_que_aparece"]) + "\n\n" if a.get("o_que_aparece") else "")
               + (f"## Transcrição\n{a['transcricao_limpa']}\n" if video else f"## Texto\n{j['data']['transcript']}\n"))
    if j["data"].get("kind") == "pagina":  # página: a URL é a fonte (o Open Notebook baixa e indexa)
        src = {"type": "link", "url": j["orig"]}
    else:  # vídeo: transcrição limpa; PDF/texto: o texto extraído, sem reescrita
        src = {"type": "text", "content": content}
    s = req("POST", base + "/sources/json", h, {**src, "notebooks": [nid], "title": a["titulo"],
                                                "embed": True, "async_processing": True}, timeout=120)
    j["data"]["nb"] = s.get("id") or True


STEP_FN = {"tipo": step_tipo, "texto": step_texto, "audio": step_audio, "transcricao": step_transcricao, "visao": step_visao, "resumo": step_resumo,
           "links": step_links, "karakeep": step_karakeep, "notebook": step_notebook, "silverbullet": step_silverbullet}


def done(s, d):
    if s == "audio":
        return "transcript" in d or os.path.exists(d.get("audio", ""))
    return KEY[s] in d


def steps(d):
    if "kind" not in d:
        return ["tipo"]
    c = cfg()
    return ["tipo", *(["audio", "transcricao", "visao"] if d["kind"] == "video" else ["texto"]), "resumo", *(["links"] if c.get("links_on") == "1" else [])] + [
        s for s, on in (("karakeep", kk_ativo(c)), ("notebook", c.get("nb_on") == "1"), ("silverbullet", sb_ligado(c))) if on]


def run(jid):
    j = job(jid)
    c = cfg()
    while s := next((s for s in steps(j["data"]) if not done(s, j["data"])), None):
        j.update(stage=s, status="rodando", error="")
        save(j)
        try:
            need(c, "llm_url", "llm_key", *(["notebook_url"] if c.get("nb_on") == "1" else []))
            STEP_FN[s](j, c)
        except Later as e:  # reagenda; depois de crawl_tries vira erro com motivo
            n, tries, wait = j["data"].get("tentativas", 0) + 1, num(c, "crawl_tries"), num(c, "crawl_wait")
            j["data"]["tentativas"] = n
            if n > tries:
                j.update(status="erro", error=f"{e} depois de {tries} tentativas. Reprocesse mais tarde.")
            else:
                j.update(status="fila", error=f"{e}: tentativa {n}/{tries}, de novo em {max(1, wait // 60)} min", after=time.time() + wait)
            save(j)
            return
        except Ignorado as e:  # não é erro: some da fila, fica visível como Ignorado
            j.update(status="ignorado", error=str(e)[:300])
            for f in glob.glob(os.path.join(DATA, "sbmedia", f"{jid}.*")) + glob.glob(os.path.join(DATA, "tmp", f"{jid}*")):
                shutil.rmtree(f, ignore_errors=True) if os.path.isdir(f) else os.remove(f)
            save(j)
            log(f"job {jid} ignorado: {j['error']}")
            return
        except Exception as e:  # erro fica no job; reprocessar retoma desta etapa
            j.update(status="erro", error=scrub(e)[:500])
            save(j)
            log(f"job {jid} erro em {s}: {j['error']}")
            return
        save(j)
        if refazer_pedido(jid):  # pediram reprocessar enquanto rodava
            j["status"] = "fila"
            zerar(j)
            log(f"job {jid} refeito a pedido")
            return
    j.update(stage="pronto", status="pronto", error="")
    save(j)
    log(f"job {jid} pronto")


def retry(jid):
    j = job(jid)
    if j and j["status"] == "erro":
        j["data"].pop("tentativas", None)
        j.update(status="fila", error="", after=0)
        save(j)
    WAKE.set()


def worker():
    while True:
        r = q("SELECT id FROM jobs WHERE status='fila' AND after<=? ORDER BY id LIMIT 1", (time.time(),))
        if r:
            run(r[0]["id"])
        else:
            mural()
            WAKE.wait(5)
            WAKE.clear()


# ---------- entrada: webhook + polling ----------
def handle_webhook(headers, body):
    """Retorna (http_status, dict). Karakeep manda {jobId,bookmarkId,userId,url,type,operation} com Authorization: Bearer <token>."""
    tok = cfg().get("webhook_token")
    if not tok:
        return 503, {"error": "webhook desligado (sem token)"}
    sent = (headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(sent.encode(), tok.encode()):
        return 401, {"error": "token inválido"}
    try:
        p = json.loads(body)
        op, url, bid = p.get("operation"), p.get("url"), p.get("bookmarkId")
    except (ValueError, AttributeError):
        return 400, {"error": "json inválido"}
    if op != "created" or not (url or bid):
        return 200, {"ok": True, "queued": False}
    return 200, {"ok": True, "queued": add_job(url, bid)}


def poll_once():
    c = cfg()
    if not kk_ativo(c):
        return 0
    row = q("SELECT v FROM kv WHERE k='poll_since'")
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    if not row:  # primeira vez: só marca o ponto de partida (não importa o histórico)
        q("INSERT INTO kv VALUES('poll_since',?)", (now,))
        return 0
    since = row[0]["v"]
    bs = req("GET", c["karakeep_url"].rstrip("/") + "/api/v1/bookmarks?limit=50",
             {"Authorization": "Bearer " + c["karakeep_key"]}).get("bookmarks", [])
    n, newest = 0, since
    for b in bs:
        ts, url = (b.get("createdAt") or "")[:19], (b.get("content") or {}).get("url")
        if ts >= since:  # >= : add_job é idempotente
            n += add_job(url, b.get("id"))
        newest = max(newest, ts)
    q("UPDATE kv SET v=? WHERE k='poll_since'", (newest,))
    return n


def poller():
    while True:
        try:
            poll_once()
        except Exception as e:
            log("polling falhou:", scrub(e))
        time.sleep(num(cfg(), "poll_secs"))


# ---------- coletores (v1.2) ----------
def tool(args, cookies=None, timeout=900):
    """yt-dlp / gallery-dl via python -m. Devolve stdout; falha = RuntimeError com a última linha (sem segredo)."""
    with cookie_file(cookies) as ck:
        p = subprocess.run([sys.executable, "-m", *args, *(["--cookies", ck] if ck else [])],
                           capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(scrub((p.stderr.strip().splitlines() or [f"saiu com {p.returncode}"])[-1])[:300])
    return p.stdout


def gdl_items(c, url, cookies, n=30):
    """gallery-dl -j: lista de kwdicts. Erro vem como mensagem -1 com exit 0, então checa."""
    msgs = json.loads(tool(["gallery_dl", "-j", "--range", f"1-{n}", "--sleep-request", "6-12", url], cookies) or "[]")
    for m in msgs:
        if m and m[0] == -1:
            raise RuntimeError(scrub(f"{m[-1].get('error')}: {m[-1].get('message')}")[:300])
    return [m[-1] for m in msgs if isinstance(m, list) and m and isinstance(m[-1], dict)]


def col_youtube(c):
    out = []
    for line in (c.get("yt_playlists") or "").splitlines():
        if u := {"LL": "https://www.youtube.com/playlist?list=LL", "WL": ":ytwatchlater"}.get(line.strip(), line.strip()):
            urls = tool(["yt_dlp", "--flat-playlist", "--print", "url", "--playlist-end", "200", "--no-warnings", u], c.get("yt_cookies")).split()
            out.append((u, urls))  # ponytail: "mais recentes" = ordem da playlist (Curtidos: novo primeiro; Assistir mais tarde: depende da config do YouTube)
    return out


def col_instagram(c):
    if ig_login_on(c):  # v1.10: login próprio, sem cookie
        return [("saved", ig_saved(c))]
    need(c, "ig_user", "ig_cookies")
    kws = gdl_items(c, f"https://www.instagram.com/{c['ig_user'].strip().lstrip('@')}/saved/", c["ig_cookies"])
    return [("saved", [f"https://www.instagram.com/p/{k['post_shortcode']}/" for k in kws if k.get("post_shortcode")])]


def col_reddit(c):
    need(c, "reddit_feed")
    xml = req("GET", c["reddit_feed"], {"User-Agent": "video-kb/1.2 (Umbrel)"}, raw=True)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    return [("saved", [e.get("href") for e in ET.fromstring(xml).findall("a:entry/a:link", ns) if e.get("href")])]


def col_tiktok(c):
    need(c, "tt_user", "tt_cookies")
    kws = gdl_items(c, f"https://www.tiktok.com/@{c['tt_user'].strip().lstrip('@')}/saved", c["tt_cookies"], n=20)
    return [("saved", [f"https://www.tiktok.com/@{(k.get('author') or {}).get('uniqueId') or 'x'}/video/{k['id']}"
                       for k in kws if k.get("id")])]


COLLECTORS = {"youtube": ("YouTube: playlists", col_youtube), "instagram": ("Instagram: salvos", col_instagram),
              "reddit": ("Reddit: salvos", col_reddit), "tiktok": ("TikTok: favoritos (experimental)", col_tiktok)}


def ingest(c, fonte, urls):
    """Itens novos -> fila (e bookmark no Karakeep se ligado). Dedupe por URL normalizada + tabela seen."""
    seen = {r["url"] for r in q("SELECT url FROM seen WHERE fonte=?", (fonte,))}
    first, novos = not seen, 0
    for i, u in enumerate(dict.fromkeys(urls)):
        n = norm(u)
        if n in seen:
            continue
        if not (first and i >= num(c, "first_run")) and not q("SELECT 1 FROM jobs WHERE url=?", (n,)):  # 1ª vez: resto só vira "visto"
            if kk_ativo(c):
                base, h = kk(c)
                novos += add_job(u, req("POST", base + "/bookmarks", h, {"type": "link", "url": u})["id"])
            else:
                novos += add_job(u)
        q("INSERT OR IGNORE INTO seen VALUES(?,?)", (fonte, n))
        seen.add(n)
    return novos


def collect_once():
    c = cfg()
    for name, (label, fn) in COLLECTORS.items():
        if c.get("col_" + name) != "1":
            continue
        st = {"quando": time.time(), "novos": 0, "erro": ""}
        try:
            for fonte, urls in fn(c):
                st["novos"] += ingest(c, f"{name}:{fonte}", urls)
        except Exception as e:
            msg = scrub(e)[:300]
            st["erro"] = ("Cookies expiraram ou não valem mais: exporte de novo e cole em Configurações. "
                          if COOKIE_ERR.search(msg) and not msg.startswith(("Configure em", IG_ERR)) else "") + msg
            log(f"coletor {name}: {st['erro']}")
        q("INSERT OR REPLACE INTO kv VALUES(?,?)", ("col:" + name, json.dumps(st)))


def collector():
    time.sleep(60)  # deixa o servidor subir
    while True:
        try:
            collect_once()
        except Exception as e:
            log("coletores falharam:", scrub(e))
        time.sleep(num(cfg(), "col_min") * 60)


# ---------- UI ----------
e = html.escape
# Visual inspirado no Linkding (github.com/sissbruecker/linkding, MIT): lista limpa, busca no topo, tags na lateral.
CLARO = "--bg:#fff;--fg:#3b4351;--mut:#5f6b7f;--card:#f6f7f9;--line:#dadee4;--acc:#5755d9;--acchov:#4240c9;--accfg:#fff;--tag:#1d7a70;--err:#b42318;--ok:#22743a;--warn:#8a5a00"
ESCURO = "--bg:#161822;--fg:#d3d6df;--mut:#9aa1b3;--card:#1e2130;--line:#30354a;--acc:#8e8cf6;--acchov:#a9a7ff;--accfg:#121326;--tag:#5fcabf;--err:#ff8f80;--ok:#74d48f;--warn:#e9c463"
CSS = (f":root{{{CLARO};color-scheme:light}}@media(prefers-color-scheme:dark){{:root:not([data-tema=claro]){{{ESCURO};color-scheme:dark}}}}"
       f":root[data-tema=escuro]{{{ESCURO};color-scheme:dark}}" + """
*{box-sizing:border-box}html{scrollbar-color:var(--line) var(--bg)}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;caret-color:var(--acc)}
::selection{background:var(--acc);color:var(--accfg)}
a{color:var(--acc);text-decoration:none;text-underline-offset:3px}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px;border-radius:4px}
.top{max-width:1240px;margin:0 auto;padding:20px 24px;display:flex;flex-wrap:wrap;align-items:center;gap:12px 24px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:1.15rem;letter-spacing:.02em;color:var(--acc);margin-right:auto}
.brand:hover{text-decoration:none}.brand svg{width:32px;height:32px}
.top nav{display:flex;gap:20px}.top nav a{padding:10px 2px;min-height:44px;display:inline-flex;align-items:center}
.top nav a[aria-current]{color:var(--fg);font-weight:600}
.add{display:flex;gap:8px;flex:1 1 360px;max-width:520px}.add input{flex:1}
main{max-width:1240px;margin:0 auto;padding:0 24px 48px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:48px}
.head{display:flex;flex-wrap:wrap;align-items:center;gap:12px;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:8px}
h1,h2.side{font-size:1.2rem;font-weight:600;margin:0}h1{font-size:1.4rem;margin-right:auto}h1 small{color:var(--mut);font-weight:400;font-size:.9rem;margin-left:6px}
h2.side{border-bottom:1px solid var(--line);padding-bottom:12px;margin:24px 0 12px}aside h2.side:first-child{margin-top:0}
.busca{display:flex;flex-wrap:wrap;gap:8px}.busca input{width:260px}.busca select{width:auto}
input,textarea,select{min-height:40px;padding:8px 10px;font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;width:100%}
input::placeholder,textarea::placeholder{color:var(--mut)}input:hover,select:hover,textarea:hover{border-color:var(--mut)}
button{min-height:40px;padding:8px 16px;font:inherit;font-weight:600;border-radius:6px;border:1px solid var(--acc);background:var(--acc);color:var(--accfg);cursor:pointer}
button:hover{background:var(--acchov);border-color:var(--acchov)}
button.sec{background:transparent;color:var(--fg);border-color:var(--line)}button.sec:hover{border-color:var(--mut)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0;font-size:.875rem;color:var(--mut)}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.lista{list-style:none;margin:0;padding:0}
.item{display:flex;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}
.thumb{flex:none;width:54px;height:72px;border-radius:6px;overflow:hidden;background:var(--card);display:flex;align-items:center;justify-content:center;color:var(--mut);font-weight:700;text-transform:uppercase}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}.thumb:hover{text-decoration:none}
.item>div{min-width:0;flex:1}
.item h2{font-size:1rem;font-weight:600;margin:0;line-height:1.35;overflow-wrap:anywhere}
.desc{margin:2px 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tag{color:var(--tag)}.desc .tag{margin-right:6px}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px;font-size:.84rem;color:var(--mut);font-variant-numeric:tabular-nums}
.meta form{display:inline}.sep{color:var(--line)}
.lnk{background:none;border:0;padding:6px 0;min-height:32px;color:var(--mut);font-weight:400;font-size:.84rem}
.lnk:hover{background:none;color:var(--acc);text-decoration:underline}.meta a{color:var(--mut);padding:6px 0}.meta a:hover{color:var(--acc)}
.st.erro{color:var(--err);font-weight:600}.st.ignorado{opacity:.7}.st.rodando,.st.fila{color:var(--warn)}
.err{color:var(--err);font-size:.875rem;margin:4px 0 0;overflow-wrap:anywhere}
.hint{color:var(--mut);font-size:.875rem;margin:0 0 4px}
.empty{color:var(--mut);padding:32px 0}
.temas{list-style:none;margin:0;padding:0}.temas li{display:flex;justify-content:space-between;gap:8px;padding:3px 0}
.temas a{overflow-wrap:anywhere}.n{color:var(--mut);font-variant-numeric:tabular-nums;font-size:.875rem}
.nuvem p{margin:0 0 10px;line-height:1.9}.nuvem a{color:var(--tag);margin-right:10px;white-space:nowrap}.nuvem b{color:var(--fg)}
.ativo{font-weight:700;text-decoration:underline}
.cols{padding-left:18px;margin:0;font-size:.9rem}.cols li{margin:0 0 8px}
.flash{padding:10px 14px;border-radius:6px;border:1px solid var(--line);background:var(--card);margin:0 0 16px}
.flash.err{border-color:var(--err)}
.cfg{max-width:760px}
label{display:block;font-weight:600;margin:16px 0 4px}
.chk{display:flex;gap:8px;align-items:center;font-weight:600;margin:16px 0 4px}.chk input{width:20px;min-height:20px;height:20px;accent-color:var(--acc)}
details{border-bottom:1px solid var(--line);padding:0 0 4px;margin:0 0 8px}
summary{min-height:48px;display:flex;align-items:center;gap:12px;font-weight:600;font-size:1.1rem;cursor:pointer;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"";width:7px;height:7px;border-right:2px solid currentColor;border-bottom:2px solid currentColor;transform:rotate(-45deg);transition:transform .15s ease-out}
details[open]>summary::before{transform:rotate(45deg)}
@media(prefers-reduced-motion:reduce){summary::before{transition:none}}
summary:hover{color:var(--acc)}details[open]{padding-bottom:20px}.row{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
@media(max-width:900px){.grid{grid-template-columns:1fr;gap:24px}.busca input{width:100%}.busca{width:100%}}
@media(max-width:560px){.top,main{padding-left:14px;padding-right:14px}.add{flex-basis:100%;max-width:none}.top nav{gap:16px}}
""")
LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="16" fill="currentColor"/>'
        '<path d="M13 10.5v11l9-5.5z" fill="var(--accfg)"/></svg>')


def page(active, body, flash=""):
    nav = "".join(f'<a href="{h}"{" aria-current=page" if h == active else ""}>{t}</a>' for h, t in (("/", "Itens"), ("/config", "Configurações")))
    fl = f'<p class="flash" role="status">{e(flash)}</p>' if flash else ""
    tema = {"claro": ' data-tema="claro"', "escuro": ' data-tema="escuro"'}.get(cfg().get("ui_tema"), "")
    return (f'<!doctype html><html lang="pt-BR"{tema}><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Video KB</title><style>{CSS}</style></head><body><header class="top"><a class="brand" href="/">{LOGO}VIDEO KB</a>'
            '<form class="add" method="post" action="/add"><label for="u" class="vh">Adicionar link</label>'
            '<input id="u" name="url" type="url" required placeholder="Cole um link (Reels, TikTok, YouTube, página, PDF)" autocomplete="off">'
            f'<button type="submit">Adicionar</button></form><nav aria-label="Principal">{nav}</nav></header><main>{fl}{body}</main></body></html>')


def fmt_time(t):  # Brasil sem horário de verão: UTC-3 fixo
    return time.strftime("%d/%m %H:%M", time.gmtime(t - 3 * 3600))


def quando(t, agora=None):
    """Data relativa no fuso do Brasil: hoje, ontem, há N dias, depois dd/mm/aa."""
    dia = lambda x: int((x - 3 * 3600) // 86400)
    n = dia(agora or time.time()) - dia(t)
    return "hoje" if n <= 0 else "ontem" if n == 1 else f"há {n} dias" if n < 7 else time.strftime("%d/%m/%y", time.gmtime(t - 3 * 3600))


def fonte(url):
    h = re.sub(r"^(www|m|vm|old)\.", "", (urlsplit(url or "").hostname or "").lower())
    if h == "youtu.be":
        return "youtube"
    return next((n for n in ("instagram", "tiktok", "youtube", "reddit") if n in h.split(".")), h or "karakeep")


def itens():
    """Jobs prontos pra tela: campos de exibição + texto de busca (título, resumo, tags, transcrição) sem acento."""
    out = []
    for r in q("SELECT * FROM jobs ORDER BY id DESC"):
        d = json.loads(r["data"] or "{}")
        a, sb = d.get("analysis") or {}, d.get("sb") or {}
        titulo = a.get("titulo") or r["orig"] or f"Bookmark {r['bookmark_id']}"
        out.append({"id": r["id"], "titulo": titulo, "orig": r["orig"] or "", "fonte": fonte(r["orig"]), "created": r["created"] or 0,
                    "status": r["status"], "stage": r["stage"], "kind": r["kind"], "error": r["error"], "aviso": d.get("aviso", ""),
                    "texto": a.get("gancho") or a.get("resumo", ""), "tags": a.get("tags") or [], "tema": a.get("tema", ""),
                    "capa": bool(sb.get("capa")), "page": sb.get("page", ""),
                    "busca": fold(" ".join([titulo, a.get("gancho", ""), a.get("resumo", ""), " ".join(a.get("tags") or []),
                                            a.get("transcricao_limpa") or d.get("transcript", ""), r["orig"] or ""]))})
    return out


def filtrar(its, p):
    """Busca: palavras (todas) no texto; "#tag" na busca = filtro de tag. Mais status, fonte, tag e tema exatos."""
    words = (p.get("q") or "").split()
    tags = [fold(w[1:]) for w in words if w.startswith("#") and len(w) > 1] + ([fold(p["tag"])] if p.get("tag") else [])
    words = [fold(w) for w in words if not w.startswith("#")]
    return [i for i in its
            if all(w in i["busca"] for w in words) and all(t in map(fold, i["tags"]) for t in tags)
            and (not p.get("status") or i["status"] == p["status"]) and (not p.get("fonte") or i["fonte"] == p["fonte"])
            and (not p.get("tema") or fold(i["tema"]) == fold(p["tema"]))]


def url_com(p, **mudar):
    v = {k: x for k, x in {**p, **mudar}.items() if x}
    return "/" + ("?" + urlencode(v) if v else "")


def item_html(i, sb_url):
    tags = "".join(f'<a class="tag" href="{e(url_com({}, tag=t))}">#{e(t)}</a>' for t in i["tags"])
    thumb = (f'<img src="/capa/{i["id"]}" alt="" loading="lazy" width="54" height="72">' if i["capa"] else e(i["fonte"][:1] or "?"))
    link = f' href="{e(i["orig"])}" rel="noreferrer"' if i["orig"] else ""
    st = (f'<span class="st {i["status"]}">{e(STATUS_PT.get(i["status"], i["status"]))}'
          f'{": " + e(STAGE_PT.get(i["stage"], i["stage"])) if i["status"] in ("fila", "rodando") else ""}</span>') if i["status"] != "pronto" else ""
    acoes = []
    if i["page"] and sb_url:
        acoes.append(f'<a href="{e(sb_url.rstrip("/") + "/" + urllib.request.quote(i["page"]))}" rel="noreferrer">Abrir no SilverBullet</a>')
    conf = "" if i["status"] in ("erro", "ignorado") else ' onsubmit="return confirm(\'Refazer este item do zero? Baixa, transcreve e resume de novo.\')"'
    acoes.append(f'<form method="post" action="/item/{i["id"]}/reprocessar"{conf}><button class="lnk" type="submit">'
                 f'{"Tentar de novo" if i["status"] == "erro" else "Guardar mesmo assim" if i["status"] == "ignorado" else "Reprocessar"}</button></form>')
    acoes.append(f'<form method="post" action="/remover/{i["id"]}" onsubmit="return confirm(\'Apagar da lista do Video KB? O que já está no SilverBullet fica.\')">'
                 '<button class="lnk" type="submit">Apagar</button></form>')
    return (f'<li class="item"><a class="thumb"{link} tabindex="-1" aria-hidden="true">{thumb}</a><div>'
            f'<h2><a{link}>{e(i["titulo"])}</a></h2>'
            + (f'<p class="desc">{tags}{e(i["texto"])}</p>' if tags or i["texto"] else "")
            + f'<div class="meta"><span>{quando(i["created"])}</span><span>{e(i["fonte"])}</span><span>{KIND_PT.get(i["kind"], "Tipo ?")}</span>{st}'
            f'<span class="sep" aria-hidden="true">|</span>{"".join(acoes)}</div>'
            + (f'<p class="{"hint" if i["status"] == "ignorado" else "err"}">{e(i["error"])}</p>' if i["error"] else "") + (f'<p class="hint">{e(i["aviso"])}</p>' if i["aviso"] else "")
            + "</div></li>")


def queue_page(flash="", p=None):
    p = {k: v for k, v in (p or {}).items() if k in ("q", "status", "fonte", "tag", "tema") and v}
    c, todos = cfg(), itens()
    lista = filtrar(todos, p)
    sb_url = c.get("sb_url") if sb_ligado(c) and not SB_LOCAL else ""
    sel = lambda nome, ops, rot: (f'<label for="f-{nome}" class="vh">{rot}</label><select id="f-{nome}" name="{nome}">'
                                  f'<option value="">{rot}: todos</option>' + "".join(
        f'<option value="{e(v)}"{" selected" if p.get(nome) == v else ""}>{e(t)}</option>' for v, t in ops) + "</select>")
    fontes = sorted({i["fonte"] for i in todos})
    busca = (f'<form class="busca" method="get" action="/" role="search">'
             + "".join(f'<input type="hidden" name="{k}" value="{e(p[k])}">' for k in ("tag", "tema") if p.get(k))
             + f'<label for="q" class="vh">Buscar</label><input id="q" name="q" type="search" value="{e(p.get("q", ""))}" placeholder="Buscar palavras ou #tags">'
             + sel("status", [(s, STATUS_PT[s]) for s in ("pronto", "fila", "rodando", "erro", "ignorado")], "Status")
             + sel("fonte", [(f, f) for f in fontes], "Fonte") + '<button class="sec" type="submit">Buscar</button></form>')
    chips = [f'{rot}: <strong>{e(p[k])}</strong>' for k, rot in (("tema", "Assunto"), ("tag", "Tag")) if p.get(k)]
    chips = (f'<p class="chips">{" · ".join(chips)} <a href="/">Limpar filtros</a></p>' if p else "")
    vazio = ('<p class="empty">Nada encontrado com esses filtros. <a href="/">Limpar filtros</a></p>' if todos
             else '<p class="empty">Nenhum item ainda. Cole um link no topo ou ligue um coletor.</p>')
    corpo = (f'<ul class="lista">{"".join(item_html(i, sb_url) for i in lista[:200])}</ul>' if lista else vazio)
    if len(lista) > 200:
        corpo += f'<p class="hint">Mostrando 200 de {len(lista)}. Refine a busca.</p>'
    # lateral: assuntos e tags com contagem (sobre todos os itens), tags agrupadas pela inicial como no Linkding
    temas, tags = {}, {}
    for i in todos:
        if i["tema"]:
            temas[i["tema"]] = temas.get(i["tema"], 0) + 1
        for t in i["tags"]:
            tags[t] = tags.get(t, 0) + 1
    ativo = lambda k, v: ' class="ativo" aria-current="true"' if fold(p.get(k, "")) == fold(v) else ""
    lt = "".join(f'<li><a href="{e(url_com({}, tema=t))}"{ativo("tema", t)}>{e(t)}</a><span class="n">{n}</span></li>'
                 for t, n in sorted(temas.items(), key=lambda x: (-x[1], fold(x[0]))))
    grupos = {}
    for t in sorted(tags, key=fold):
        grupos.setdefault(fold(t)[:1].upper(), []).append(t)
    nuvem = "".join("<p>" + "".join(f'<a href="{e(url_com({}, tag=t))}"{ativo("tag", t)}>' + (f"<b>{e(t[:1])}</b>{e(t[1:])}" if k == 0 else e(t))
                                    + f'<span class="n"> {tags[t]}</span></a>' for k, t in enumerate(g)) + "</p>" for g in grupos.values())
    cols = []
    for name, (label, _) in COLLECTORS.items():
        if c.get("col_" + name) != "1":
            continue
        st = json.loads((q("SELECT v FROM kv WHERE k=?", ("col:" + name,)) or [{"v": "{}"}])[0]["v"])
        qd = f"última coleta {fmt_time(st['quando'])}, {st['novos']} novo(s)" if st.get("quando") else "ainda não coletou"
        cols.append(f'<li><strong>{e(label)}</strong>: {qd}' + (f'<p class="err">{e(st["erro"])}</p>' if st.get("erro") else "") + "</li>")
    erros_col = [json.loads(r["v"]).get("erro", "") for r in q("SELECT v FROM kv WHERE k LIKE 'col:%'")]
    aviso = any("Cookies expiraram" in x for x in erros_col)
    aviso_ig = any(x.startswith(IG_ERR) for x in erros_col)
    body = (('<p class="flash err" role="alert">Cookies vencidos em algum coletor. Veja na lateral e renove em Configurações.</p>' if aviso else "")
            + ('<p class="flash err" role="alert">O login do Instagram precisa de você. Veja o motivo em Coletores, na lateral.</p>' if aviso_ig else "")
            + f'<div class="grid"><section aria-labelledby="t-itens"><div class="head"><h1 id="t-itens">Itens<small>{len(lista)}</small></h1>{busca}</div>{chips}{corpo}</section>'
            + '<aside aria-label="Assuntos e tags">'
            + (f'<h2 class="side">Assuntos</h2><ul class="temas">{lt}</ul>' if lt else "")
            + (f'<h2 class="side">Tags</h2><div class="nuvem">{nuvem}</div>' if nuvem else "")
            + (f'<h2 class="side">Coletores</h2><ul class="cols">{"".join(cols)}</ul>' if cols else "")
            + ("" if lt or nuvem or cols else '<h2 class="side">Assuntos</h2><p class="hint">Aparecem aqui quando o primeiro item ficar pronto.</p>')
            + "</aside></div>")
    return page("/", body, flash)


def campo(k, label, t, hint, v):
    h = f'<p class="hint" id="h-{k}">{e(hint)}</p>' if hint else ""
    desc = f' aria-describedby="h-{k}"' if hint else ""
    if t == "bool":
        return f'<label class="chk"><input type="checkbox" name="{k}" value="1"{" checked" if v == "1" else ""}> {e(label)}</label>{h}'
    if t == "lines":
        ctl = f'<textarea id="{k}" name="{k}" rows="3"{desc}>{e(v)}</textarea>'
    elif t == "text":
        ctl = f'<input id="{k}" name="{k}" value="{e(v)}"{desc}>'
    elif t == "num":
        lo, hi = NUM[k]
        h = f'<p class="hint" id="h-{k}">{e(hint + " " if hint else "")}De {lo} a {hi}.</p>'
        ctl = f'<input id="{k}" name="{k}" type="number" inputmode="numeric" min="{lo}" max="{hi}" step="1" required value="{e(v)}" aria-describedby="h-{k}">'
    elif t == "choice":
        ctl = f'<select id="{k}" name="{k}"{desc}>' + "".join(
            f'<option value="{e(o)}"{" selected" if o == v else ""}>{e(txt)}</option>' for o, txt in CHOICES[k]) + "</select>"
    else:
        ph = (mask(v) or "não configurado") + (" (deixe em branco para manter, - para apagar)" if v else "")
        ctl = (f'<textarea id="{k}" name="{k}" rows="3" placeholder="{e(ph)}"{desc} autocomplete="off"></textarea>' if t == "area"
               else f'<input id="{k}" name="{k}" type="password" placeholder="{e(ph)}"{desc} autocomplete="new-password">')
    return f'<label for="{k}">{e(label)}</label>{h}{ctl}'


def config_page(flash=""):
    c = cfg()
    out, aberto = ['<h1>Configurações</h1><form method="post" action="/config">'], False
    for k, label, t, hint, _ in FIELDS:
        if t == "h":  # seção recolhível; a primeira (Conexões) já abre aberta
            out.append(("</details>" if aberto else "") + f'<details{"" if aberto else " open"}><summary>{e(label)}</summary>'
                       + (f'<p class="hint">{e(hint)}</p>' if hint else ""))
            aberto = True
            continue
        out.append(campo(k, label, t, hint, c.get(k, "")))
        if k == "webhook_token":  # fim de Conexões
            out.append('<p class="hint">Testar usa o que está salvo.</p><div class="row">' + "".join(
                f'<button class="sec" type="submit" formaction="/testar/{n}" formnovalidate>Testar {e(lb)}</button>' for n, (lb, _) in TESTES.items()) + "</div>")
    out.append('</details><p><button type="submit">Salvar</button></p></form>'
               '<details><summary>Manutenção</summary><div class="row">'
               '<form method="post" action="/reprocessar/tudo" onsubmit="return confirm(\'Reprocessar TUDO? Baixa, transcreve e resume de novo cada item (gasta Scribe e IA).\')">'
               '<button class="sec" type="submit">Reprocessar tudo</button></form>'
               '<form method="post" action="/reprocessar/sb" onsubmit="return confirm(\'Refazer no SilverBullet todos os itens (página, vídeo na qualidade atual, quadros, mural)?\')">'
               '<button class="sec" type="submit">Reprocessar só SilverBullet</button></form>'
               '<form method="get" action="/config/export"><button class="sec" type="submit">Exportar configuração (sem segredos)</button></form></div>'
               '<form method="post" action="/config/import"><label for="imp">Importar configuração</label>'
               '<p class="hint" id="h-imp">Cole o JSON exportado. Chaves e cookies não entram; campo inválido fica como está.</p>'
               '<textarea id="imp" name="json" rows="4" required aria-describedby="h-imp"></textarea>'
               '<p><button class="sec" type="submit">Importar</button></p></form></details>')
    return page("/config", '<div class="cfg">' + "".join(out) + "</div>", flash)


def limpar(k, v):
    """Valor de formulário/importação pronto pra salvar. Inválido = ValueError com mensagem."""
    t = TYPE[k]
    if t == "num":
        return valida(k, v)
    if t == "choice":
        if v not in dict(CHOICES[k]):
            raise ValueError(f"{LABEL[k]}: opção inválida")
        return v
    if t == "bool":
        return "1" if v in ("1", "true", "True") else ""
    return str(v).strip() or DEFAULTS[k]


def aplicar(vals):
    """Salva os campos válidos; devolve as mensagens dos inválidos (esses ficam como estavam)."""
    upd, erros = {}, []
    for k, v in vals.items():
        try:
            upd[k] = limpar(k, v)
        except ValueError as ex:
            erros.append(str(ex))
    cfg_save(upd)
    return erros


def save_form(form):
    vals, upd = {}, {}
    for k, _, t, _, _ in FIELDS:
        v = (form.get(k) or [""])[0].strip()
        if t == "h":
            continue
        if t in ("secret", "area"):
            if v == "-":
                upd[k] = ""
            elif v:
                upd[k] = v
        else:
            vals[k] = v
    cfg_save(upd)
    return aplicar(vals)


def importar(texto):
    """JSON exportado -> config. Segredo nunca entra por aqui; chave desconhecida é ignorada."""
    d = json.loads(texto)
    if not isinstance(d, dict):
        raise ValueError("JSON não é um objeto")
    return aplicar({k: ("1" if v else "") if isinstance(v, bool) else str(v) for k, v in d.items() if TYPE.get(k) not in (None, "h", "secret", "area")})


def exportar():
    return {k: v for k, v in cfg().items() if TYPE.get(k) not in (None, "h", "secret", "area") and k != "ig_codigo"}


def modelos_ok(c):
    """Lista do 9Router + confere se cada modelo configurado existe."""
    r = req("GET", c["llm_url"].rstrip("/") + "/models", {"Authorization": "Bearer " + c["llm_key"]}, timeout=20)
    ids = {m.get("id") for m in (r.get("data") or []) if isinstance(m, dict)}
    faltam = sorted({c[k] for k in MODELOS if c.get(k)} - ids) if ids else []
    if faltam:
        raise RuntimeError("modelo não existe no 9Router: " + ", ".join(faltam))


TESTES = {
    "llm": ("9Router", lambda c: modelos_ok(c)),
    "elevenlabs": ("ElevenLabs", lambda c: req("GET", "https://api.elevenlabs.io/v1/models", {"xi-api-key": c["elevenlabs_key"]}, timeout=20)),
    "cobalt": ("Cobalt", lambda c: req("GET", c["cobalt_url"].rstrip("/") + "/", {"Accept": "application/json"}, timeout=20)),
    "karakeep": ("Karakeep", kk_tags),
    "notebook": ("Open Notebook", notebooks),
    "sb": ("SilverBullet", lambda c: sb_get(c, pasta(c, "sb_pagina") + ".md")),
    "instagram": ("login do Instagram", lambda c: (need(c, "ig_user", "ig_pass"), ig_call(c, lambda cl: cl.account_info()))),
    "hiker": ("HikerAPI", lambda c: (need(c, "hiker_key"), req("GET", "https://api.hikerapi.com/sys/balance", {"x-access-key": c["hiker_key"]}, timeout=20))),
}


def testar(nome):
    label, fn = TESTES[nome]
    try:
        fn(cfg())
        return f"{label}: ok"
    except Exception as ex:
        return f"{label}: erro: {scrub(ex)[:200]}"


def reprocessar_item(jid):
    """Erro: retoma da etapa que falhou. Outros: refaz o item do zero."""
    j = job(jid)
    if not j or j["status"] == "erro":
        return retry(jid)
    if j["status"] == "ignorado":  # "Guardar mesmo assim": refaz pulando o filtro
        j["data"] = {"forcar": True}
        j.update(status="fila", error="", after=0)
        save(j)
        WAKE.set()
        return
    zerar(j)
    WAKE.set()


def zerar(j):
    """Refaz do zero mantendo a página (v1.11.3). Item rodando (v1.12.1): só marca; o worker refaz quando terminar a etapa,
    senão o save do worker apagava o reset (ou o reset apagava a página nova) e nascia página duplicada."""
    if j["status"] == "rodando":
        q("INSERT OR REPLACE INTO kv VALUES(?, '1')", (f"refazer:{j['id']}",))
        return
    pag = (j["data"].get("sb") or {}).get("page")
    j["data"] = {"sb": {"page": pag}} if pag else {}
    j.update(status="fila", error="", after=0)
    save(j)


def refazer_pedido(jid):
    if q("SELECT v FROM kv WHERE k=?", (f"refazer:{jid}",)):
        q("DELETE FROM kv WHERE k=?", (f"refazer:{jid}",))
        return True
    return False


def reprocessar(so_sb):
    """Tudo: zera os dados e refaz do zero. Só SilverBullet: refaz página, vídeo (na qualidade atual), quadros e mural."""
    for r in q("SELECT id FROM jobs"):
        j = job(r["id"])
        if so_sb:
            if "analysis" not in j["data"]:
                continue
            j["data"].pop("sb7", None)
            for k in ("midia", "linha", "video"):
                (j["data"].get("sb") or {}).pop(k, None)
        else:
            zerar(j)  # v1.12.1: antes zerava tudo e perdia a página; título novo = página nova e a velha ficava órfã
            continue
        j.update(status="fila", error="", after=0)
        save(j)
    q("INSERT OR REPLACE INTO kv VALUES('mural_sujo','1')")
    WAKE.set()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=(), cache="no-store"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", cache)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def go(self, flash=""):
        self.send(303, "", extra=[("Location", "/?m=" + urllib.request.quote(flash) if flash else "/")])

    def do_GET(self):
        u = urlsplit(self.path)
        m = (parse_qs(u.query).get("m") or [""])[0][:200]
        if u.path == "/":
            self.send(200, queue_page(m, {k: v[0] for k, v in parse_qs(u.query).items()}))
        elif mc := re.fullmatch(r"/capa/(\d+)", u.path):  # capa vem do SilverBullet pelo worker (token fica aqui)
            j = job(int(mc[1]))
            capa = ((j or {}).get("data", {}).get("sb") or {}).get("capa")
            try:
                img = sb_get(cfg(), capa) if capa else None
            except Exception:
                img = None
            self.send(200, img, "image/jpeg", cache="private, max-age=3600") if img else self.send(404, "sem capa", "text/plain")
        elif u.path == "/config":
            self.send(200, config_page(m))
        elif u.path == "/config/export":
            self.send(200, json.dumps(exportar(), ensure_ascii=False, indent=1), "application/json",
                      [("Content-Disposition", 'attachment; filename="video-kb-config.json"')])
        elif u.path == "/health":
            self.send(200, "ok", "text/plain")
        else:
            self.send(404, "não encontrado", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            return self.send(413, "grande demais", "text/plain")
        body = self.rfile.read(n)
        path = self.path.split("?")[0]
        if path == "/webhook/karakeep":
            code, obj = handle_webhook(self.headers, body)
            return self.send(code, json.dumps(obj), "application/json")
        if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):  # CSRF: outro app/site tentando postar
            return self.send(403, "origem bloqueada", "text/plain")
        form = parse_qs(body.decode("utf-8", "replace"))
        if path == "/add":
            url = (form.get("url") or [""])[0].strip()
            if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
                return self.go("Link inválido.")
            self.go("Link adicionado." if add_job(url) else "Esse link já está na fila.")
        elif path == "/config":
            erros = save_form(form)
            sb_refila()
            self.send(200, config_page("Salvo." + (" Não salvei: " + "; ".join(erros) + "." if erros else "")))
        elif path == "/config/import":
            try:
                erros = importar((form.get("json") or [""])[0])
                msg = "Importado." + (" Ignorei: " + "; ".join(erros) + "." if erros else "")
            except ValueError as ex:
                msg = f"Importação falhou: {ex}"
            self.send(200, config_page(msg))
        elif (m := re.fullmatch(r"/testar/(\w+)", path)) and m[1] in TESTES:
            self.send(200, config_page(testar(m[1])))
        elif m := re.fullmatch(r"/reprocessar/(tudo|sb)", path):
            reprocessar(m[1] == "sb")
            self.go("Reprocessando tudo." if m[1] == "tudo" else "Reprocessando o SilverBullet.")
        elif m := re.fullmatch(r"/remover/(\d+)", path):
            j = job(int(m[1]))
            pag = j and (j["data"].get("sb") or {}).get("page")
            if pag and SB_LOCAL and os.path.exists(sb_local(pag + ".md")):  # v1.12.1: apagar aqui tira do Selah também
                os.remove(sb_local(pag + ".md"))
            q("DELETE FROM jobs WHERE id=?", (int(m[1]),))  # só tira da fila do Video KB; `seen` fica, coletor não re-adiciona
            self.go("Removido.")
        elif m := re.fullmatch(r"/item/(\d+)/reprocessar", path):
            reprocessar_item(int(m[1]))
            self.go("Reprocessando.")
        else:
            self.send(404, "não encontrado", "text/plain")


def main():
    init()
    threading.Thread(target=links_teste, daemon=True).start()  # v1.9: teste dos links nos 3 reels
    sb_refila()  # v1.3.3+: não depende do nan clicar Salvar
    threading.Thread(target=sb_orfas_todas, daemon=True).start()  # v1.12.1: limpa duplicadas que já existem
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=collector, daemon=True).start()
    log(f"Video KB na porta {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
