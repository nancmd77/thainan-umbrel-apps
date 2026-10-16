"""Video KB worker: link Reels/TikTok -> áudio -> transcrição -> resumo -> Karakeep + Open Notebook.

Só stdlib (+ yt-dlp opcional, fallback do Cobalt). Tudo num arquivo de propósito.
"""
import glob
import html
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

DATA = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
POLL_SECS = 300
SCRIBE_MODEL = "scribe_v2"  # doc POST /v1/speech-to-text (confirmado 2026-10)
SITES = ("instagram.com", "tiktok.com")

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
    ("webhook_token", "Token do webhook", "secret", "Mesmo valor que você põe no webhook do Karakeep. Vazio = webhook desligado.", ""),
    ("ig_cookies", "Cookies do Instagram (opcional)", "area", "Formato Netscape (cookies.txt). Só usado pelo yt-dlp.", ""),
]
SECRETS = {k for k, _, t, _, _ in FIELDS if t in ("secret", "area")}
DEFAULTS = {k: d for k, _, _, _, d in FIELDS}

STEPS = ["audio", "transcricao", "resumo", "karakeep", "notebook"]
KEY = {"transcricao": "transcript", "resumo": "analysis", "karakeep": "kk", "notebook": "nb"}
STAGE_PT = {"audio": "Baixando áudio", "transcricao": "Transcrevendo", "resumo": "Resumindo",
            "karakeep": "Gravando no Karakeep", "notebook": "Enviando ao Open Notebook",
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
    q("UPDATE jobs SET status='fila' WHERE status='rodando'")  # crash no meio -> retoma


def norm(url):
    u = urlsplit(url.strip())
    host = re.sub(r"^(www|m)\.", "", (u.hostname or "").lower())
    return f"https://{host}{u.path.rstrip('/')}"  # ponytail: ignora query; short links (vm.tiktok) não deduplicam


def is_target(url):
    h = (urlsplit(url).hostname or "").lower()
    return any(h == s or h.endswith("." + s) for s in SITES)


def add_job(url, bookmark_id=None):
    """Idempotente por URL normalizada. Retorna True se criou."""
    n = norm(url)
    now = time.time()
    if q("SELECT id FROM jobs WHERE url=?", (n,)):
        if bookmark_id:
            q("UPDATE jobs SET bookmark_id=? WHERE url=? AND bookmark_id IS NULL", (bookmark_id, n))
        return False
    q("INSERT INTO jobs(url,orig,bookmark_id,created,updated) VALUES(?,?,?,?,?)",
      (n, url.strip(), bookmark_id, now, now))
    WAKE.set()
    return True


def job(jid):
    r = q("SELECT * FROM jobs WHERE id=?", (jid,))
    if r:
        r[0]["data"] = json.loads(r[0]["data"] or "{}")
    return r[0] if r else None


def save(j):
    q("UPDATE jobs SET bookmark_id=?,stage=?,status=?,error=?,data=?,updated=? WHERE id=?",
      (j["bookmark_id"], j["stage"], j["status"], j["error"], json.dumps(j["data"]), time.time(), j["id"]))


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
            {"url": url, "downloadMode": "audio"}, timeout=90)
    link = r.get("url") if r.get("status") in ("tunnel", "redirect") else (r.get("audio") if r.get("status") == "picker" else None)
    if not link:
        raise RuntimeError("Cobalt: " + str((r.get("error") or {}).get("code") or r.get("status") or r)[:150])
    if r["status"] == "tunnel":  # Cobalt devolve o próprio host (ex.: nonve.local), que o container pode não resolver
        link = urlsplit(link)._replace(scheme=urlsplit(c["cobalt_url"]).scheme, netloc=urlsplit(c["cobalt_url"]).netloc).geturl()
    path = out + (os.path.splitext(r.get("filename") or "")[1] or ".mp3")
    with open(path, "wb") as f:
        f.write(req("GET", link, timeout=180, raw=True))
    return path


def ytdlp_audio(c, url, out):
    cmd = [sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "--no-playlist", "-o", out + ".%(ext)s", url]
    ck = None
    if c.get("ig_cookies"):
        ck = out + ".cookies"
        fd = os.open(ck, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(c["ig_cookies"])
        cmd += ["--cookies", ck]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    finally:
        if ck and os.path.exists(ck):
            os.remove(ck)
    files = [f for f in glob.glob(out + ".*") if not f.endswith(".part")]
    if p.returncode or not files:
        raise RuntimeError("yt-dlp: " + scrub((p.stderr.strip().splitlines() or ["falhou"])[-1])[:200])
    return files[0]


def step_audio(j, c):
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
    os.remove(path)
    del j["data"]["audio"]


PROMPT = (
    "Você analisa transcrições de vídeos curtos (Reels/TikTok). Responda SOMENTE um JSON, em português do Brasil, com as chaves: "
    '"titulo" (até 80 caracteres), "resumo" (2 a 4 frases), "pontos_chave" (3 a 7 strings), '
    '"tags" (3 a 8, minúsculas, sem #, 1 a 2 palavras), "tema" (1 a 3 palavras, Title Case, genérico para agrupar vídeos '
    'parecidos, ex.: "Marketing", "Saúde", "Finanças"), '
    '"transcricao_limpa" (a transcrição com pontuação e erros de reconhecimento corrigidos, sem inventar conteúdo).'
)


def parse_analysis(raw, transcript):
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    a = json.loads(raw)
    tags = [re.sub(r"\s+", " ", str(t)).strip().lstrip("#").lower() for t in a.get("tags") or []]
    return {
        "titulo": str(a.get("titulo") or "Vídeo sem título").strip()[:120],
        "resumo": str(a.get("resumo") or "").strip(),
        "pontos_chave": [str(p).strip() for p in a.get("pontos_chave") or [] if str(p).strip()],
        "tags": list(dict.fromkeys(t for t in tags if t))[:8],
        "tema": re.sub(r"\s+", " ", str(a.get("tema") or "Geral")).strip()[:60] or "Geral",
        "transcricao_limpa": str(a.get("transcricao_limpa") or "").strip() or transcript,
    }


def step_resumo(j, c):
    need(c, "llm_url", "llm_key")
    r = req("POST", c["llm_url"].rstrip("/") + "/chat/completions",
            {"Authorization": "Bearer " + c["llm_key"]},
            {"model": c["model"], "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": PROMPT},
                          {"role": "user", "content": j["data"]["transcript"][:30000]}]}, timeout=180)
    try:
        j["data"]["analysis"] = parse_analysis(r["choices"][0]["message"]["content"], j["data"]["transcript"])
    except (KeyError, IndexError, ValueError, TypeError) as e:
        raise RuntimeError(f"Resposta do modelo inválida ({type(e).__name__})")


def step_karakeep(j, c):
    need(c, "karakeep_url", "karakeep_key")
    base, h, a = c["karakeep_url"].rstrip("/") + "/api/v1", {"Authorization": "Bearer " + c["karakeep_key"]}, j["data"]["analysis"]
    if not j["bookmark_id"]:  # link colado à mão: cria o bookmark (Karakeep devolve o existente se a URL já está lá)
        j["bookmark_id"] = req("POST", base + "/bookmarks", h, {"type": "link", "url": j["orig"]})["id"]
        save(j)
    bid = j["bookmark_id"]
    note = "Pontos-chave:\n" + "\n".join("- " + p for p in a["pontos_chave"])
    req("PATCH", f"{base}/bookmarks/{bid}", h, {"summary": a["resumo"], "note": note})
    if a["tags"]:
        req("POST", f"{base}/bookmarks/{bid}/tags", h, {"tags": [{"tagName": t, "attachedBy": "human"} for t in a["tags"]]})
    lists = req("GET", base + "/lists", h).get("lists", [])
    lid = next((x["id"] for x in lists if x["name"].lower() == a["tema"].lower()), None)
    if not lid:
        lid = req("POST", base + "/lists", h, {"name": a["tema"], "icon": "📚"})["id"]
    req("PUT", f"{base}/lists/{lid}/bookmarks/{bid}", h)
    j["data"]["kk"] = True


def step_notebook(j, c):
    need(c, "notebook_url")
    base = c["notebook_url"].rstrip("/") + "/api"
    h = {"Authorization": "Bearer " + c["notebook_pass"]} if c.get("notebook_pass") else {}
    a = j["data"]["analysis"]
    nbs = req("GET", base + "/notebooks", h)
    nid = next((n["id"] for n in nbs if n["name"].lower() == a["tema"].lower()), None)
    if not nid:
        nid = req("POST", base + "/notebooks", h, {"name": a["tema"], "description": "Criado pelo Video KB"})["id"]
    content = (f"# {a['titulo']}\n\nLink: {j['orig']}\n\n## Resumo\n{a['resumo']}\n\n## Pontos-chave\n"
               + "\n".join("- " + p for p in a["pontos_chave"])
               + f"\n\nTags: {', '.join(a['tags'])}\n\n## Transcrição\n{a['transcricao_limpa']}\n")
    s = req("POST", base + "/sources/json", h, {"type": "text", "notebooks": [nid], "title": a["titulo"],
                                                "content": content, "embed": True, "async_processing": True}, timeout=120)
    j["data"]["nb"] = s.get("id") or True


STEP_FN = {"audio": step_audio, "transcricao": step_transcricao, "resumo": step_resumo,
           "karakeep": step_karakeep, "notebook": step_notebook}


def done(s, d):
    if s == "audio":
        return "transcript" in d or os.path.exists(d.get("audio", ""))
    return KEY[s] in d


def run(jid):
    j = job(jid)
    c = cfg()
    for s in STEPS:
        if done(s, j["data"]):
            continue
        j.update(stage=s, status="rodando", error="")
        save(j)
        try:
            need(c, "elevenlabs_key", "llm_url", "llm_key", "karakeep_url", "karakeep_key", "notebook_url")
            STEP_FN[s](j, c)
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
    q("UPDATE jobs SET status='fila', error='' WHERE id=? AND status='erro'", (jid,))
    WAKE.set()


def worker():
    while True:
        r = q("SELECT id FROM jobs WHERE status='fila' ORDER BY id LIMIT 1")
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
    if op != "created" or not url or not is_target(url):
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
        if ts >= since and url and is_target(url):  # >= : add_job é idempotente
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
        title = (d.get("analysis") or {}).get("titulo") or r["orig"]
        retry_btn = (f'<form method="post" action="/retry/{r["id"]}"><button class="sec" type="submit">Reprocessar</button></form>'
                     if r["status"] == "erro" else "")
        err = f'<p class="err">{e(r["error"])}</p>' if r["error"] else ""
        rows.append(
            f'<article class="job"><h2>{e(title)}</h2><a href="{e(r["orig"])}" rel="noreferrer">{e(r["orig"])}</a>'
            f'<div class="meta"><span class="st {r["status"]}">{STATUS_PT.get(r["status"], r["status"])}</span>'
            f'<span>{STAGE_PT.get(r["stage"], r["stage"])}</span><span>{fmt_time(r["created"])}</span>{retry_btn}</div>{err}</article>')
    body = ('<h1>Fila</h1><form class="add" method="post" action="/add"><label for="u" hidden>Link</label>'
            '<input id="u" name="url" type="url" required placeholder="Cole o link do Reels ou TikTok" autocomplete="off">'
            '<button type="submit">Adicionar</button></form>'
            + ("".join(rows) or '<p class="empty">Nenhum link ainda. Cole um acima ou compartilhe no Karakeep.</p>')
            + '<p><a href="/">Atualizar</a></p>')
    return page("/", body, flash)


def config_page(flash=""):
    c = cfg()
    out = ['<h1>Configurações</h1><form method="post" action="/config">']
    for k, label, t, hint, _ in FIELDS:
        v = c.get(k, "")
        h = f'<p class="hint" id="h-{k}">{e(hint)}</p>' if hint else ""
        desc = f' aria-describedby="h-{k}"' if hint else ""
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
        if t == "text":
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
            self.send(200, config_page("Salvo."))
        elif m := re.fullmatch(r"/retry/(\d+)", path):
            retry(int(m[1]))
            self.go("Reprocessando.")
        else:
            self.send(404, "não encontrado", "text/plain")


def main():
    init()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=poller, daemon=True).start()
    log(f"Video KB na porta {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
