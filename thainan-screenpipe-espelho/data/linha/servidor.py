#!/usr/bin/env python3
"""Linha do tempo do Screenpipe Espelho (só leitura).

Lê a réplica Litestream (/replica/db.sqlite) e a mídia enviada pelo notebook (/midia).
Nunca escreve na réplica: abre sempre com mode=ro&immutable=1, uma conexão por pedido.
"""
import calendar, json, os, re, sqlite3, subprocess, threading, time, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

DB = os.environ.get("SP_DB", "/replica/db.sqlite")
MIDIA = os.environ.get("SP_MIDIA", "/midia")
AQUI = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("SP_CACHE", "/cache")
os.makedirs(CACHE, exist_ok=True)

_idx = {"t": 0, "map": {}}
_lock = threading.Lock()


def indice():
    """nome do arquivo -> caminho em /midia (reconstruído a cada 60 s)."""
    with _lock:
        if time.time() - _idx["t"] > 60:
            m = {}
            for raiz, _, arqs in os.walk(MIDIA):
                for a in arqs:
                    m[a] = os.path.join(raiz, a)
            _idx.update(t=time.time(), map=m)
        return _idx["map"]


def achar(caminho):
    if not caminho:
        return None
    nome = re.split(r"[\\/]", caminho)[-1]
    p = indice().get(nome)
    if p is None:
        _idx["t"] = 0
        p = indice().get(nome)
    return p


def con():
    c = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def colunas(c, tabela):
    try:
        return {r[1] for r in c.execute(f"pragma table_info({tabela})")}
    except sqlite3.Error:
        return set()


def dia_bounds(dia, tz_min):
    # dia local (YYYY-MM-DD) -> intervalo UTC em texto ISO (Screenpipe grava timestamp em UTC)
    t0 = calendar.timegm(time.strptime(dia, "%Y-%m-%d")) + tz_min * 60
    f = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))
    return f(t0), f(t0 + 86400)


def frames_do_dia(dia, tz_min):
    a, b = dia_bounds(dia, tz_min)
    with con() as c:
        fc = colunas(c, "frames")
        app = "f.app_name" if "app_name" in fc else "o.app_name"
        win = "f.window_name" if "window_name" in fc else "o.window_name"
        sql = f"""select f.id, f.timestamp, {app} as app, {win} as win
                  from frames f left join ocr_text o on o.frame_id = f.id
                  where julianday(f.timestamp) >= julianday(?) and julianday(f.timestamp) < julianday(?) group by f.id order by f.timestamp"""
        return [dict(r) for r in c.execute(sql, (a, b))]


def frame_info(fid):
    with con() as c:
        fc = colunas(c, "frames")
        cols = ["f.id", "f.timestamp", "f.offset_index", "v.file_path"]
        for k in ("app_name", "window_name", "browser_url", "snapshot_path"):
            if k in fc:
                cols.append(f"f.{k}")
        r = c.execute(f"select {', '.join(cols)} from frames f left join video_chunks v on v.id = f.video_chunk_id where f.id = ?", (fid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        t = c.execute("select text, app_name, window_name from ocr_text where frame_id = ?", (fid,)).fetchone()
        if t:
            d["texto"] = t["text"]
            d.setdefault("app_name", t["app_name"])
            d.setdefault("window_name", t["window_name"])
        if "accessibility_text" in fc or "full_text" in fc:
            k = "accessibility_text" if "accessibility_text" in fc else "full_text"
            x = c.execute(f"select {k} from frames where id = ?", (fid,)).fetchone()[0]
            if x and not d.get("texto"):
                d["texto"] = x
        return d


def imagem(fid):
    destino = os.path.join(CACHE, f"{fid}.jpg")
    if os.path.exists(destino):
        return destino
    d = frame_info(fid)
    if not d:
        return None
    snap = achar(d.get("snapshot_path"))
    if snap:
        return snap
    video = achar(d.get("file_path"))
    if not video:
        return None
    n = int(d.get("offset_index") or 0)
    tmp = destino + ".tmp.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-q:v", "3", tmp],
                   timeout=60, check=False)
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, destino)
        return destino
    return None


def busca(q, limite=150):
    like = f"%{q}%"
    out = []
    with con() as c:
        for r in c.execute("""select f.id, f.timestamp, o.app_name as app, o.window_name as win, substr(o.text, max(1, instr(lower(o.text), lower(?)) - 80), 220) as trecho
                              from ocr_text o join frames f on f.id = o.frame_id where o.text like ? order by f.timestamp desc limit ?""", (q, like, limite)):
            out.append(dict(r, tipo="tela"))
        if colunas(c, "audio_transcriptions"):
            for r in c.execute("""select a.timestamp, a.transcription as trecho from audio_transcriptions a where a.transcription like ? order by a.timestamp desc limit ?""", (like, limite // 2)):
                d = dict(r, tipo="audio")
                f = c.execute("select id from frames where julianday(timestamp) <= julianday(?) order by julianday(timestamp) desc limit 1", (d["timestamp"],)).fetchone()
                d["id"] = f[0] if f else None
                out.append(d)
    out.sort(key=lambda d: d["timestamp"] or "", reverse=True)
    return out


def audio_do_dia(dia, tz_min):
    a, b = dia_bounds(dia, tz_min)
    with con() as c:
        if not colunas(c, "audio_transcriptions"):
            return []
        return [dict(r) for r in c.execute("select timestamp, transcription as texto from audio_transcriptions where julianday(timestamp) >= julianday(?) and julianday(timestamp) < julianday(?) order by timestamp", (a, b))]


def estado():
    try:
        with con() as c:
            r = c.execute("select max(timestamp) from frames").fetchone()[0]
        return {"ok": True, "ultimo_frame": r, "midia_arquivos": len(indice())}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def env(self, code, body, tipo="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store" if "json" in tipo else "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        tz = int(q.get("tz", "180"))
        try:
            if u.path in ("/", "/index.html"):
                return self.env(200, open(os.path.join(AQUI, "index.html"), "rb").read(), "text/html; charset=utf-8")
            if u.path == "/api/estado":
                return self.env(200, estado())
            if u.path == "/api/dia":
                return self.env(200, {"frames": frames_do_dia(q["dia"], tz), "audio": audio_do_dia(q["dia"], tz)})
            if u.path == "/api/busca":
                return self.env(200, busca(q.get("q", "").strip()) if len(q.get("q", "").strip()) >= 2 else [])
            m = re.fullmatch(r"/api/frame/(\d+)", u.path)
            if m:
                d = frame_info(int(m.group(1)))
                return self.env(200 if d else 404, d or {"erro": "frame não encontrado"})
            m = re.fullmatch(r"/img/(\d+)\.jpg", u.path)
            if m:
                p = imagem(int(m.group(1)))
                if not p:
                    return self.env(404, {"erro": "imagem ainda não chegou"})
                return self.env(200, open(p, "rb").read(), "image/jpeg" if p.lower().endswith((".jpg", ".jpeg")) else "image/png")
            return self.env(404, {"erro": "não encontrado"})
        except sqlite3.Error as e:
            return self.env(503, {"erro": f"réplica indisponível: {e}"})
        except Exception as e:
            return self.env(500, {"erro": str(e)})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8090"))), H).serve_forever()
