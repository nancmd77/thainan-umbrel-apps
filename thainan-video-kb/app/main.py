"""Video KB worker: link Reels/TikTok -> áudio -> transcrição -> resumo -> Karakeep + Open Notebook.

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
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from contextlib import closing, contextmanager
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit

DATA = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
POLL_SECS = 300
COL_SECS, FIRST_RUN = 1800, 20  # coletores: a cada 30 min; 1ª coleta de cada fonte só pega os 20 mais recentes
COOKIE_ERR = re.compile(r"login|log in|sign in|cookie|authentica|401|403|private|checkpoint", re.I)
SCRIBE_MODEL = "scribe_v2"  # doc POST /v1/speech-to-text (confirmado 2026-10)
SITES = ("instagram.com", "tiktok.com")  # sempre vídeo; outros links: sonda do yt-dlp decide
GRIDS_MAX = 4_000_000  # folhas 3x3 pro Gemini: acima disso redimensiona
MIN_TEXT = 200  # menos que isso não dá resumo honesto
CRAWL_TRIES, CRAWL_WAIT = 3, 120  # Karakeep ainda lendo a página: reagenda até 3x, 2 min
KIND_PT = {"video": "Vídeo", "pagina": "Página", "pdf": "PDF", "texto": "Texto"}

# (chave, rótulo, tipo, dica, padrão)
FIELDS = [
    ("elevenlabs_key", "Chave ElevenLabs", "secret", "Scribe (transcrição).", ""),
    ("llm_url", "URL do 9Router", "text", "OpenAI-compatível (termina em /v1).", "http://thainan-9router_web_1:20128/v1"),
    ("llm_key", "Chave 9Router", "secret", "Resumo com Gemini.", ""),
    ("model", "Modelo", "text", "Nome do modelo no 9Router.", "ag/gemini-3.8-flash-medium"),
    ("cobalt_url", "URL do Cobalt", "text", "Baixa o áudio.", "http://192.168.18.171:9013"),
    ("karakeep_url", "URL do Karakeep", "text", "", "http://192.168.18.171:8620"),
    ("karakeep_key", "API key do Karakeep", "secret", "Karakeep > Configurações > API keys.", ""),
    ("notebook_url", "URL do Open Notebook (API)", "text", "Porta 5055.", "http://thainan-opennotebook_web_1:5055"),
    ("notebook_pass", "Senha do Open Notebook", "secret", "Deixe vazio se não houver senha.", ""),
    ("sb_url", "URL do SilverBullet", "text", "Segundo cérebro (porta 3002). Vazio = desligado.", "http://192.168.18.171:3002"),
    ("sb_token", "Token do SilverBullet", "secret", "Token de API do SilverBullet.", ""),
    ("webhook_token", "Token do webhook", "secret", "Mesmo valor que você põe no webhook do Karakeep. Vazio = webhook desligado.", ""),
    ("max_frames", "Visão: máximo de frames", "text", "Frames que o crv tira do vídeo (viram folhas 3x3 pro Gemini).", "60"),
    ("max_min", "Duração máxima de vídeo (min)", "text", "Vídeo mais longo não é transcrito (custo do Scribe). Vale pra links fora de IG/TikTok.", "60"),
    ("h_col", "Coletores automáticos (a cada 30 min)", "h", "Cada item novo vira bookmark no Karakeep e entra na fila. 1ª coleta: só os 20 mais recentes de cada fonte.", ""),
    ("col_youtube", "YouTube: playlists", "bool", "", ""),
    ("yt_playlists", "Playlists do YouTube", "lines", "Uma por linha: URL da playlist, :ytwatchlater (Assistir mais tarde) ou LL (Curtidos).", ":ytwatchlater\nLL"),
    ("yt_cookies", "Cookies do YouTube", "area", "cookies.txt (Netscape). Obrigatório pra Assistir mais tarde e Curtidos.", ""),
    ("col_instagram", "Instagram: salvos", "bool", "", ""),
    ("ig_user", "Usuário do Instagram", "text", "Sem @.", ""),
    ("ig_cookies", "Cookies do Instagram", "area", "cookies.txt (Netscape). Usado pelo coletor e pelo download dos Reels.", ""),
    ("col_reddit", "Reddit: salvos", "bool", "", ""),
    ("reddit_feed", "Feed RSS dos salvos do Reddit", "secret", "reddit.com/prefs/feeds > linha \"saved\" > RSS. Não precisa de cookie.", ""),
    ("col_tiktok", "TikTok: favoritos (experimental)", "bool", "", ""),
    ("tt_user", "Usuário do TikTok", "text", "Sem @.", ""),
    ("tt_cookies", "Cookies do TikTok", "area", "cookies.txt (Netscape). Usado pelo coletor e pelo download.", ""),
]
SECRETS = {k for k, _, t, _, _ in FIELDS if t in ("secret", "area")}
COOKIES = {"instagram.com": "ig_cookies", "tiktok.com": "tt_cookies", "youtube.com": "yt_cookies", "youtu.be": "yt_cookies"}
DEFAULTS = {k: d for k, _, _, _, d in FIELDS}

KEY = {"tipo": "kind", "visao": "visao", "transcricao": "transcript", "texto": "transcript", "resumo": "analysis", "karakeep": "kk", "notebook": "nb", "silverbullet": "sb7"}
STAGE_PT = {"tipo": "Identificando", "texto": "Lendo texto do Karakeep", "audio": "Baixando vídeo", "visao": "Vendo o vídeo", "transcricao": "Transcrevendo", "resumo": "Resumindo",
            "karakeep": "Gravando no Karakeep", "notebook": "Enviando ao Open Notebook", "silverbullet": "Escrevendo no SilverBullet",
            "novo": "Aguardando", "pronto": "Concluído"}
STATUS_PT = {"fila": "Na fila", "rodando": "Rodando", "erro": "Erro", "pronto": "Pronto"}

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
    return {**DEFAULTS, **saved}


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


def cobalt_audio(c, url, out):
    r = req("POST", c["cobalt_url"].rstrip("/") + "/", {"Accept": "application/json"},
            {"url": url, "downloadMode": "auto", "videoQuality": "480"}, timeout=90)  # vídeo leve: Scribe + visão
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


def ytdlp_audio(c, url, out):
    cmd = [sys.executable, "-m", "yt_dlp", "-f", "b[height<=480]/bv*[height<=480]+ba/b/ba", "--no-playlist", "-o", out + ".%(ext)s", url]
    with cookie_file(site_cookies(c, url)) as ck:
        p = subprocess.run(cmd + (["--cookies", ck] if ck else []), capture_output=True, text=True, timeout=300)
    files = [f for f in glob.glob(out + ".*") if not f.endswith(".part")]
    if p.returncode or not files:
        raise RuntimeError("yt-dlp: " + scrub((p.stderr.strip().splitlines() or ["falhou"])[-1])[:200])
    return files[0]


class Later(Exception):
    """Ainda não dá (Karakeep lendo a página): reagenda em vez de falhar."""


def kk(c):
    return c["karakeep_url"].rstrip("/") + "/api/v1", {"Authorization": "Bearer " + c["karakeep_key"]}


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
    ct = bookmark(c, ensure_bookmark(j, c)).get("content") or {}
    t = ct.get("type")
    if t == "link":
        if not j["orig"]:
            j["orig"] = ct.get("url") or ""
        dur, lim = has_media(ct.get("url") or j["orig"]), int(c.get("max_min") or 60)
        if dur > lim * 60:
            raise RuntimeError(f"Vídeo longo ({dur // 60:.0f} min, limite {lim} min): não transcrevi pra não gastar Scribe. "
                               "Aumente o limite em Configurações e reprocesse.")
        j["data"]["kind"] = "video" if dur else "pagina"
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
    """Página/PDF/texto: usa o que o Karakeep já extraiu. Nada de inventar."""
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
        if kind == "pdf" and not text and j["data"].get("tentativas", 0) < CRAWL_TRIES:
            raise Later("Karakeep ainda extraindo o PDF")
        raise RuntimeError(f"Sem texto suficiente pra resumir ({len(text)} caracteres extraídos pelo Karakeep). "
                           + {"pdf": "PDF escaneado/imagem?", "texto": "Nota curta demais.", "pagina": "Página bloqueada ou só imagem/vídeo?"}[kind])
    j["data"]["transcript"] = text


def step_audio(j, c):
    need(c, "elevenlabs_key")  # sem chave não adianta baixar
    out = os.path.join(DATA, "tmp", str(j["id"]))
    try:
        j["data"]["audio"] = cobalt_audio(c, j["orig"], out)
    except Exception as e1:
        try:
            j["data"]["audio"] = ytdlp_audio(c, j["orig"], out)
        except Exception as e2:
            raise RuntimeError(f"{e1} | {e2}")


def step_transcricao(j, c):
    need(c, "elevenlabs_key")
    path = j["data"]["audio"]
    with open(path, "rb") as f:
        body, ctype = multipart({"model_id": SCRIBE_MODEL, "tag_audio_events": "false", "diarize": "false"},
                                os.path.basename(path), f.read())
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
        run_crv(media, out, int(c.get("max_frames") or 60))
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
            if cfg().get("sb_url") and cfg().get("sb_token") and d.get("kind", "video") == "video":
                os.makedirs(os.path.join(DATA, "sbmedia"), exist_ok=True)  # vai pro SilverBullet na última etapa
                shutil.move(media, os.path.join(DATA, "sbmedia", f"{j['id']}{os.path.splitext(media)[1] or '.mp4'}"))
            else:
                os.remove(media)


PROMPT = (
    "Você analisa conteúdo salvo: transcrição de vídeo curto, página, post ou PDF. Responda SOMENTE um JSON, em português do Brasil, com as chaves: "
    '"titulo" (até 80 caracteres), "gancho" (1 frase de até 100 caracteres dizendo do que se trata, para ler sem abrir), "resumo" (2 a 4 frases), "pontos_chave" (3 a 7 strings), '
    '"tags" (3 a 8, minúsculas, sem #, 1 a 2 palavras), "tema" (1 a 3 palavras, Title Case, genérico para agrupar conteúdos '
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


PROMPT_VISAO = (
    ' Você também recebe folhas 3x3 com frames do vídeo (cada célula tem o nome do frame; os tempos estão na lista "frames") '
    'e a transcrição com tempos [mm:ss]. Inclua a chave "o_que_aparece": lista curta e cronológica de strings "mm:ss: ..." '
    "com o que aparece na tela, texto escrito na tela e ações, alinhado com o que é dito. Só o que dá pra ver, sem inventar."
)
PROMPT_TEXTO = PROMPT.split(', "transcricao_limpa"')[0] + "."  # página/PDF/texto: não reescreve o original


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


def kk_tags(c):
    r = req("GET", c["karakeep_url"].rstrip("/") + "/api/v1/tags",
            {"Authorization": "Bearer " + c["karakeep_key"]}, timeout=20)
    return [t["name"] for t in r.get("tags", [])][:200]  # ponytail: 200 mais usadas; paginar se o prompt ficar curto


def parse_analysis(raw, transcript):
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    a = json.loads(raw)
    tags = [re.sub(r"\s+", " ", str(t)).strip().lstrip("#").lower() for t in a.get("tags") or []]
    return {
        "titulo": str(a.get("titulo") or "Vídeo sem título").strip()[:120],
        "gancho": str(a.get("gancho") or "").strip()[:120],
        "resumo": str(a.get("resumo") or "").strip(),
        "pontos_chave": [str(p).strip() for p in a.get("pontos_chave") or [] if str(p).strip()],
        "tags": list(dict.fromkeys(t for t in tags if t))[:8],
        "tema": re.sub(r"\s+", " ", str(a.get("tema") or "Geral")).strip()[:60] or "Geral",
        "transcricao_limpa": str(a.get("transcricao_limpa") or "").strip() or transcript,
        "o_que_aparece": [str(x).strip() for x in (a.get("o_que_aparece") if isinstance(a.get("o_que_aparece"), list)
                          else [a.get("o_que_aparece")] if a.get("o_que_aparece") else []) if str(x).strip()],
    }


def step_resumo(j, c):
    need(c, "llm_url", "llm_key")
    temas = nomes(lambda: [n["name"] for n in notebooks(c)]) if c.get("notebook_url") else []
    tags = nomes(lambda: kk_tags(c)) if c.get("karakeep_url") and c.get("karakeep_key") else []
    ctx = ("\n\nCadernos (temas) que já existem: " + json.dumps(temas, ensure_ascii=False)
           + ". O tema DEVE ser um desses, escrito igual, quando couber. Só crie tema novo se nenhum servir: nome curto em português do Brasil."
           if temas else "")
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
        return req("POST", c["llm_url"].rstrip("/") + "/chat/completions", {"Authorization": "Bearer " + c["llm_key"]},
                   {"model": c["model"], "stream": False, "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": (PROMPT + (PROMPT_VISAO if com_imagens else "") if video else PROMPT_TEXTO) + ESTILO + ctx},
                                 {"role": "user", "content": user}]}, timeout=300 if com_imagens else 180)

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
        a = parse_analysis(r["choices"][0]["message"]["content"], j["data"]["transcript"])
    except (KeyError, IndexError, ValueError, TypeError) as e:
        raise RuntimeError(f"Resposta do modelo inválida ({type(e).__name__})")
    a["tema"] = pick_tema(a["tema"], temas)
    by = {fold(t): t for t in tags}
    a["tags"] = list(dict.fromkeys(by.get(fold(t), t) for t in a["tags"]))  # grafia da tag existente
    a["estilo"] = 2  # já nasce no ESTILO, com gancho
    j["data"]["analysis"] = a
    if vis.get("dir"):
        shutil.rmtree(vis["dir"], ignore_errors=True)  # folhas já usadas


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


def sb_put(c, path, data, ctype="text/markdown"):
    import urllib.request as ur
    rq = ur.Request(c["sb_url"].rstrip("/") + "/.fs/" + ur.quote(path), data=data if isinstance(data, bytes) else data.encode(),
                    method="PUT", headers={"Authorization": "Bearer " + c["sb_token"], "Content-Type": ctype})
    with ur.urlopen(rq, timeout=60) as r:
        return r.status


def sb_nome(t):
    return re.sub(r"[\\/#\[\]|^:*?\"<>]+", " ", t).strip()[:90] or "Sem título"


def yt_capa(c, url):
    """Capa que o criador escolheu (miniatura do Reel/TikTok no perfil) via yt-dlp, em JPEG."""
    with cookie_file(site_cookies(c, url)) as ck:
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "-j", "--no-playlist", "--no-warnings", url] + (["--cookies", ck] if ck else []),
                           capture_output=True, text=True, timeout=60)
    info = json.loads(p.stdout.splitlines()[0])
    u = info.get("thumbnail") or max(info["thumbnails"], key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))["url"]
    from PIL import Image
    out = io.BytesIO()
    with Image.open(io.BytesIO(req("GET", u, timeout=60, raw=True))) as im:
        im.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


def sb_capa(j, c):
    """Capa do card no SilverBullet: miniatura original (yt-dlp) → imagem que o Karakeep guardou → sem capa."""
    p = f"Reels/capas/{j['id']}.jpg"
    if j["orig"]:
        try:
            sb_put(c, p, yt_capa(c, j["orig"]), "image/jpeg")
            return p
        except Exception as e:
            log(f"job {j['id']} sem capa do yt-dlp: {scrub(e)[:120]}")
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
    import urllib.request as ur
    try:
        with ur.urlopen(ur.Request(c["sb_url"].rstrip("/") + "/.fs/" + ur.quote(path), headers={"Authorization": "Bearer " + c["sb_token"]}), timeout=30) as r:
            return r.read().decode()
    except Exception:
        return None


SB_TEMA = """# {tema}

{sintese}
${{reelCards("{tema}")}}
"""


def sb_sintese(j, c, tema):
    """Síntese do assunto: Gemini junta os resumos de todos os Reels do tema. Regerada a cada Reel novo."""
    itens = [x["analysis"] for x in (json.loads(r["data"] or "{}") for r in q("SELECT data FROM jobs WHERE id<>?", (j["id"],)))
             if x.get("analysis") and fold(x["analysis"].get("tema", "")) == fold(tema)] + [j["data"]["analysis"]]
    lista = "\n\n".join(f"- {x['titulo']}: {x['resumo']}" for x in itens[-40:])
    try:
        r = req("POST", c["llm_url"].rstrip("/") + "/chat/completions", {"Authorization": "Bearer " + c["llm_key"]},
                {"model": c["model"], "stream": False, "messages": [
                    {"role": "system", "content": "Você escreve em português do Brasil, direto, sem floreio e sem cara de texto de IA. "
                     "Recebe resumos de vídeos salvos sobre um mesmo assunto. Escreva em Markdown: um parágrafo curto com o que esses vídeos têm em comum "
                     "e o que dá pra aprender juntando eles; depois '### Ideias que se repetem' com 3 a 6 tópicos curtos; "
                     "se houver, '### Onde eles discordam' com 1 a 3 tópicos. Não invente nada que não esteja nos resumos. Sem título principal."},
                    {"role": "user", "content": f"Assunto: {tema}\n\n{lista}"}]}, timeout=180)
        txt = r["choices"][0]["message"]["content"].strip()
        return f"## Síntese\n_Atualizada pela IA em {time.strftime('%d/%m/%Y')} com {len(itens)} Reels._\n\n{txt}\n\n## Reels\n"
    except Exception as e:
        log(f"job {j['id']} sem síntese: {scrub(e)[:120]}")
        return ""


def sb_reestilo(j, c):
    """Uma vez por Reel antigo: reescreve resumo/pontos/o que aparece no ESTILO (sem tocar fatos, título, tema, tags, transcrição)."""
    a = j["data"]["analysis"]
    if a.get("estilo", 0) >= 2:
        return
    campos = {k: a[k] for k in ("resumo", "pontos_chave", "o_que_aparece") if a.get(k) and not a.get("estilo")}
    campos["gancho"] = a.get("gancho") or ""
    try:
        r = req("POST", c["llm_url"].rstrip("/") + "/chat/completions", {"Authorization": "Bearer " + c["llm_key"]},
                {"model": c["model"], "stream": False, "messages": [
                    {"role": "system", "content": "Reescreva os campos deste JSON em português do Brasil sem mudar fatos, sem acrescentar nem tirar informação. "
                     "Responda SOMENTE o JSON com as mesmas chaves e tipos (resumo string 2 a 4 frases; listas continuam listas de strings). "
                     "Se \"gancho\" vier vazio, escreva 1 frase de até 100 caracteres dizendo do que se trata." + ESTILO},
                    {"role": "user", "content": json.dumps({**campos, "titulo": a["titulo"], "resumo_atual": a["resumo"]}, ensure_ascii=False)}]}, timeout=180)
        txt = r["choices"][0]["message"]["content"].strip()
        novo = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        for k, v in campos.items():
            if type(novo.get(k)) is type(v) and novo[k]:
                a[k] = novo[k]
        a["gancho"] = str(a.get("gancho") or "")[:120]
        a["estilo"] = 2
    except Exception as e:
        log(f"job {j['id']} sem reestilo: {scrub(e)[:120]}")


def sb_midia(j, c):
    """Vídeo no SilverBullet (toca no card) + linha do tempo: quadros do crv com timecode e o que acontece. Uma vez por Reel."""
    d, a = j["data"], j["data"]["analysis"]
    sb = d.setdefault("sb", {})
    if sb.get("midia") or d.get("kind", "video") != "video" or not j["orig"]:
        return
    pasta = os.path.join(DATA, "sbmedia")
    os.makedirs(pasta, exist_ok=True)
    media = next(iter(glob.glob(os.path.join(pasta, f"{j['id']}.*"))), None)
    out = os.path.join(DATA, "tmp", f"tl-{j['id']}")
    try:
        if not media:  # Reel antigo: baixa de novo
            try:
                media = cobalt_audio(c, j["orig"], os.path.join(pasta, str(j["id"])))
            except Exception:
                media = ytdlp_audio(c, j["orig"], os.path.join(pasta, str(j["id"])))
        if media.endswith(".mp3"):
            raise RuntimeError("só áudio, sem vídeo")
        if os.path.getsize(media) <= 60 * 1024 * 1024:
            sb_put(c, f"Reels/videos/{j['id']}.mp4", open(media, "rb").read(), "video/mp4")
            sb["video"] = f"Reels/videos/{j['id']}.mp4"
        run_crv(media, out, 24)
        with open(os.path.join(out, "frames.json")) as f:
            frames = json.load(f)["frames"]
        def seg(x):
            m = re.match(r"\s*(\d+):(\d{2})", x)
            return int(m[1]) * 60 + int(m[2]) if m else None
        eventos = [(seg(x), re.sub(r"^\s*\d+:\d{2}\s*[:\-–]?\s*", "", x)) for x in a.get("o_que_aparece") or []]
        eventos = [e for e in eventos if e[0] is not None][:16] or [(int(fr["timestamp_sec"]), "") for fr in frames[::max(1, len(frames) // 8)]][:8]
        from PIL import Image
        linha = []
        for i, (t, txt) in enumerate(eventos):
            fr = min(frames, key=lambda x: abs(x["timestamp_sec"] - t))
            src = next(iter(glob.glob(os.path.join(out, "**", fr["file"]), recursive=True)), None)
            if not src:
                continue
            p = os.path.join(out, f"q{i}.jpg")
            with Image.open(src) as im:
                k = 360 / max(im.width, 1)
                im.convert("RGB").resize((360, max(1, int(im.height * k)))).save(p, "JPEG", quality=78)
            dest = f"Reels/quadros/{j['id']}/{i:02d}.jpg"
            sb_put(c, dest, open(p, "rb").read(), "image/jpeg")
            linha.append({"t": mmss(t), "img": dest, "txt": txt})
        sb["linha"] = linha
    except Exception as e:
        log(f"job {j['id']} sem mídia no SilverBullet: {scrub(e)[:120]}")
    finally:
        sb["midia"] = True
        shutil.rmtree(out, ignore_errors=True)
        for f in glob.glob(os.path.join(pasta, f"{j['id']}.*")):
            os.remove(f)


def step_silverbullet(j, c):
    need(c, "sb_url", "sb_token")
    sb_reestilo(j, c)
    sb_midia(j, c)
    d, a = j["data"], j["data"]["analysis"]
    page = "Reels/" + sb_nome(a["titulo"])
    sb = d.get("sb") or {}
    capa = sb.get("capa", "") if sb.get("capa_orig") else sb_capa(j, c) or sb.get("capa", "")  # v1.4: troca a capa uma vez
    tema = "Temas/" + sb_nome(a["tema"])
    video = d.get("kind", "video") == "video"
    tags = [re.sub(r"[^\w-]+", "-", t.strip()).strip("-").lower() for t in a["tags"] if t.strip()]
    fm = {"type": "reel", "title": a["titulo"], "tema": a["tema"], "data": time.strftime("%Y-%m-%d", time.localtime(j.get("created") or time.time())),
          "cover": (".fs/" + capa) if capa else "", "link": j["orig"] or "", "gancho": a.get("gancho", ""), "resumo": a["resumo"],
          "video": (".fs/" + sb["video"]) if sb.get("video") else ""}
    linha = sb.get("linha") or []
    md = ("---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in fm.items())
          + "tags:\n" + "".join(f"  - {json.dumps(t, ensure_ascii=False)}\n" for t in tags) + "---\n"
          + (f"![](/{sb['video']})\n\n" if sb.get("video") else f"![](/{capa})\n\n" if capa else "") + f"# {a['titulo']}\n\n"
          + (f"_{fm['gancho']}_\n\n" if fm["gancho"] else "")
          + (f"[▶ Abrir o original]({j['orig']})\n\n" if j["orig"] else "") + f"Assunto: [[{tema}]]\n\n"
          + f"## Resumo\n{a['resumo']}\n\n## Pontos-chave\n" + "\n".join("- " + p for p in a["pontos_chave"]) + "\n\n"
          + ("## Linha do tempo\n" + "\n".join(f"**{x['t']}** {x['txt']}\n![](/{x['img']})\n" for x in linha) + "\n" if linha
             else "## O que aparece\n" + "\n".join("- " + x for x in a["o_que_aparece"]) + "\n\n" if a.get("o_que_aparece") else "")
          + ("## Transcrição\n" + a["transcricao_limpa"] if video else "## Texto\n" + d["transcript"][:20000]) + "\n")
    sb_put(c, page + ".md", md)
    sb_put(c, tema + ".md", SB_TEMA.format(tema=a["tema"].replace('"', ""), sintese=sb_sintese(j, c, a["tema"])))
    d["sb"] = {**sb, "page": page, "capa": capa, "capa_orig": True, "tema": tema}
    d["sb7"] = True


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
           "karakeep": step_karakeep, "notebook": step_notebook, "silverbullet": step_silverbullet}


def done(s, d):
    if s == "audio":
        return "transcript" in d or os.path.exists(d.get("audio", ""))
    return KEY[s] in d


def steps(d):
    if "kind" not in d:
        return ["tipo"]
    return ["tipo", *(["audio", "transcricao", "visao"] if d["kind"] == "video" else ["texto"]), "resumo", "karakeep", "notebook"] + (["silverbullet"] if cfg().get("sb_url") and cfg().get("sb_token") else [])


def run(jid):
    j = job(jid)
    c = cfg()
    while s := next((s for s in steps(j["data"]) if not done(s, j["data"])), None):
        j.update(stage=s, status="rodando", error="")
        save(j)
        try:
            need(c, "llm_url", "llm_key", "karakeep_url", "karakeep_key", "notebook_url")
            STEP_FN[s](j, c)
        except Later as e:  # reagenda; depois de CRAWL_TRIES vira erro com motivo
            n = j["data"].get("tentativas", 0) + 1
            j["data"]["tentativas"] = n
            if n > CRAWL_TRIES:
                j.update(status="erro", error=f"{e} depois de {CRAWL_TRIES} tentativas. Abra o bookmark no Karakeep e reprocesse.")
            else:
                j.update(status="fila", error=f"{e}: tentativa {n}/{CRAWL_TRIES}, de novo em {CRAWL_WAIT // 60} min", after=time.time() + CRAWL_WAIT)
            save(j)
            return
        except Exception as e:  # erro fica no job; reprocessar retoma desta etapa
            j.update(status="erro", error=scrub(e)[:500])
            save(j)
            log(f"job {jid} erro em {s}: {j['error']}")
            return
        save(j)
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
    if not (c["karakeep_url"] and c["karakeep_key"]):
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
        time.sleep(POLL_SECS)


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
    """Itens novos -> bookmark no Karakeep + fila. Dedupe por URL normalizada + tabela seen."""
    seen = {r["url"] for r in q("SELECT url FROM seen WHERE fonte=?", (fonte,))}
    first, novos = not seen, 0
    for i, u in enumerate(dict.fromkeys(urls)):
        n = norm(u)
        if n in seen:
            continue
        if not (first and i >= FIRST_RUN) and not q("SELECT 1 FROM jobs WHERE url=?", (n,)):  # 1ª vez: resto só vira "visto"
            base, h = kk(c)
            novos += add_job(u, req("POST", base + "/bookmarks", h, {"type": "link", "url": u})["id"])
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
            need(c, "karakeep_url", "karakeep_key")
            for fonte, urls in fn(c):
                st["novos"] += ingest(c, f"{name}:{fonte}", urls)
        except Exception as e:
            msg = scrub(e)[:300]
            st["erro"] = ("Cookies expiraram ou não valem mais: exporte de novo e cole em Configurações. " if COOKIE_ERR.search(msg) and not msg.startswith("Configure em") else "") + msg
            log(f"coletor {name}: {st['erro']}")
        q("INSERT OR REPLACE INTO kv VALUES(?,?)", ("col:" + name, json.dumps(st)))


def collector():
    time.sleep(60)  # deixa o servidor subir
    while True:
        try:
            collect_once()
        except Exception as e:
            log("coletores falharam:", scrub(e))
        time.sleep(COL_SECS)


# ---------- UI ----------
e = html.escape
CSS = """
:root{--bg:#f6f5f1;--fg:#1d1c1a;--mut:#5f5c55;--card:#fff;--line:#d9d6cc;--acc:#1f5c4a;--accfg:#fff;--err:#a02a1c;--ok:#1d6b3a;--warn:#7a5a00}
@media(prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--mut:#a5a299;--card:#201f1c;--line:#38362f;--acc:#6fcfae;--accfg:#10201a;--err:#ff8f7e;--ok:#7fd9a0;--warn:#e6c25a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
::selection{background:var(--acc);color:var(--accfg)}
main{max-width:860px;margin:0 auto;padding:16px}
nav{display:flex;gap:8px;margin:0 0 16px}
nav a{padding:10px 16px;border-radius:8px;color:var(--fg);text-decoration:none;border:1px solid var(--line);min-height:44px;display:inline-flex;align-items:center}
nav a[aria-current]{background:var(--acc);color:var(--accfg);border-color:var(--acc)}
h1{font-size:1.4rem;margin:8px 0 16px}
label{display:block;font-weight:600;margin:16px 0 4px}
.hint{color:var(--mut);font-size:.875rem;margin:0 0 4px}
input,textarea{width:100%;min-height:44px;padding:10px 12px;font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px}
button{min-height:44px;padding:10px 18px;font:inherit;font-weight:600;border-radius:8px;border:1px solid var(--acc);background:var(--acc);color:var(--accfg);cursor:pointer}
button.sec{background:transparent;color:var(--fg);border-color:var(--line)}
:focus-visible{outline:3px solid var(--acc);outline-offset:2px}
.add{display:flex;gap:8px;margin-bottom:24px}.add input{flex:1}
.job{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:0 0 8px}
.job h2{font-size:1rem;margin:0 0 4px;overflow-wrap:anywhere}.job a{color:var(--mut);font-size:.875rem;overflow-wrap:anywhere}
.meta{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;margin-top:8px;font-size:.875rem;font-variant-numeric:tabular-nums}
.st{font-weight:700}.st.erro{color:var(--err)}.st.pronto{color:var(--ok)}.st.rodando,.st.fila{color:var(--warn)}
.err{color:var(--err);font-size:.875rem;margin:8px 0 0;overflow-wrap:anywhere}
.empty{color:var(--mut);padding:24px 0}
h2.sec{font-size:1.1rem;margin:32px 0 8px}.cols{padding-left:20px}.cols li{margin:0 0 8px}
.chk{display:flex;gap:8px;align-items:center;font-weight:600;margin:16px 0 4px}.chk input{width:24px;min-height:24px;height:24px}
.flash{padding:10px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);margin-bottom:16px}
@media(max-width:480px){.add{flex-direction:column}}
"""


def page(active, body, flash=""):
    nav = "".join(f'<a href="{h}"{" aria-current=page" if h == active else ""}>{t}</a>' for h, t in (("/", "Fila"), ("/config", "Configurações")))
    fl = f'<p class="flash" role="status">{e(flash)}</p>' if flash else ""
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Video KB</title><style>{CSS}</style></head><body><main><nav aria-label="Principal">{nav}</nav>{fl}{body}</main></body></html>')


def fmt_time(t):  # Brasil sem horário de verão: UTC-3 fixo
    return time.strftime("%d/%m %H:%M", time.gmtime(t - 3 * 3600))


def queue_page(flash=""):
    jobs = q("SELECT * FROM jobs ORDER BY id DESC LIMIT 100")
    rows = []
    for r in jobs:
        d = json.loads(r["data"] or "{}")
        title = (d.get("analysis") or {}).get("titulo") or r["orig"] or f"Bookmark {r['bookmark_id']}"
        retry_btn = (f'<form method="post" action="/retry/{r["id"]}"><button class="sec" type="submit">Reprocessar</button></form>'
                     if r["status"] == "erro" else "")
        retry_btn += (f'<form method="post" action="/remover/{r["id"]}" onsubmit="return confirm(\'Remover da fila?\')">'
                      f'<button class="sec" type="submit">Remover</button></form>')
        err = (f'<p class="err">{e(r["error"])}</p>' if r["error"] else "") + (f'<p class="hint">{e(d["aviso"])}</p>' if d.get("aviso") else "")
        rows.append(
            f'<article class="job"><h2>{e(title)}</h2><a href="{e(r["orig"])}" rel="noreferrer">{e(r["orig"])}</a>'
            f'<div class="meta"><span class="st {r["status"]}">{STATUS_PT.get(r["status"], r["status"])}</span>'
            f'<span>{KIND_PT.get(r["kind"], "Tipo ?")}</span><span>{STAGE_PT.get(r["stage"], r["stage"])}</span><span>{fmt_time(r["created"])}</span>{retry_btn}</div>{err}</article>')
    c = cfg()
    cols = []
    for name, (label, _) in COLLECTORS.items():
        if c.get("col_" + name) != "1":
            continue
        st = json.loads((q("SELECT v FROM kv WHERE k=?", ("col:" + name,)) or [{"v": "{}"}])[0]["v"])
        quando = f"última coleta {fmt_time(st['quando'])}, {st['novos']} novo(s)" if st.get("quando") else "ainda não coletou"
        err = f'<p class="err">{e(st["erro"])}</p>' if st.get("erro") else ""
        cols.append(f'<li><strong>{e(label)}</strong>: {quando}{err}</li>')
    aviso = any("Cookies expiraram" in json.loads(r["v"]).get("erro", "") for r in q("SELECT v FROM kv WHERE k LIKE 'col:%'"))
    body = (('<p class="flash err" role="alert">Cookies vencidos em algum coletor. Veja abaixo e renove em Configurações.</p>' if aviso else "")
            + '<h1>Fila</h1><form class="add" method="post" action="/add"><label for="u" hidden>Link</label>'
            '<input id="u" name="url" type="url" required placeholder="Cole um link (Reels, TikTok, Reddit, página, PDF)" autocomplete="off">'
            '<button type="submit">Adicionar</button></form>'
            + ("".join(rows) or '<p class="empty">Nenhum item ainda. Cole um link acima ou salve no Karakeep.</p>')
            + '<p><a href="/">Atualizar</a></p>'
            + (f'<h2 class="sec">Coletores</h2><ul class="cols">{"".join(cols)}</ul>' if cols else ""))
    return page("/", body, flash)


def config_page(flash=""):
    c = cfg()
    out = ['<h1>Configurações</h1><form method="post" action="/config">']
    for k, label, t, hint, _ in FIELDS:
        v = c.get(k, "")
        h = f'<p class="hint" id="h-{k}">{e(hint)}</p>' if hint else ""
        desc = f' aria-describedby="h-{k}"' if hint else ""
        if t == "h":
            out.append(f'<h2 class="sec">{e(label)}</h2>{h}')
            continue
        if t == "bool":
            out.append(f'<label class="chk"><input type="checkbox" name="{k}" value="1"{" checked" if v == "1" else ""}> {e(label)}</label>')
            continue
        if t == "lines":
            out.append(f'<label for="{k}">{e(label)}</label>{h}<textarea id="{k}" name="{k}" rows="3"{desc}>{e(v)}</textarea>')
            continue
        if t == "text":
            ctl = f'<input id="{k}" name="{k}" value="{e(v)}"{desc}>'
        else:
            ph = (mask(v) or "não configurado") + (" (deixe em branco para manter, - para apagar)" if v else "")
            tag = (f'<textarea id="{k}" name="{k}" rows="3" placeholder="{e(ph)}"{desc} autocomplete="off"></textarea>' if t == "area"
                   else f'<input id="{k}" name="{k}" type="password" placeholder="{e(ph)}"{desc} autocomplete="new-password">')
            ctl = tag
        out.append(f'<label for="{k}">{e(label)}</label>{h}{ctl}')
    out.append('<p><button type="submit">Salvar</button></p></form>')
    return page("/config", "".join(out), flash)


def save_form(form):
    upd = {}
    for k, _, t, _, _ in FIELDS:
        v = (form.get(k) or [""])[0].strip()
        if t == "h":
            continue
        if t == "bool":
            upd[k] = "1" if v == "1" else ""
        elif t in ("text", "lines"):
            upd[k] = v or DEFAULTS[k]
        elif v == "-":
            upd[k] = ""
        elif v:
            upd[k] = v
    cfg_save(upd)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
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
            self.send(200, queue_page(m))
        elif u.path == "/config":
            self.send(200, config_page(m))
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
            save_form(form)
            if cfg().get("sb_url") and cfg().get("sb_token"):  # SilverBullet ligado: prontos antigos ganham a página
                q("UPDATE jobs SET status='fila' WHERE status='pronto' AND data NOT LIKE '%\"sb7\"%'")
                WAKE.set()
            self.send(200, config_page("Salvo."))
        elif m := re.fullmatch(r"/remover/(\d+)", path):
            q("DELETE FROM jobs WHERE id=?", (int(m[1]),))  # só tira da fila do Video KB; `seen` fica, coletor não re-adiciona
            self.go("Removido.")
        elif m := re.fullmatch(r"/retry/(\d+)", path):
            retry(int(m[1]))
            self.go("Reprocessando.")
        else:
            self.send(404, "não encontrado", "text/plain")


def main():
    init()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=collector, daemon=True).start()
    log(f"Video KB na porta {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
