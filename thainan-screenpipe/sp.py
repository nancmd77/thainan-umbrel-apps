#!/usr/bin/env python3
# Screenpipe Trabalho -> Nonve: copia continua (prints, textos, cliques, transcricoes) + visualizador.
import json, os, sqlite3, threading, time, urllib.request, urllib.parse, secrets, subprocess, glob, html
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

D = os.environ.get("DATA", "/data")
BASE = os.environ.get("SP_BASE", "https://screenpipe-trabalho.nonve.dpdns.org")
CFG = f"{D}/config.json"; DB = f"{D}/sp.db"
os.makedirs(f"{D}/frames", exist_ok=True); os.makedirs(f"{D}/video", exist_ok=True)
BRT = timezone(timedelta(hours=-3))
STATE = {"ok": None, "err": "", "last": None}

def cfg():
    try: return json.load(open(CFG))
    except Exception: return {}
def save_cfg(c):
    json.dump(c, open(CFG + ".tmp", "w")); os.replace(CFG + ".tmp", CFG)
c0 = cfg()
if "setup" not in c0: c0["setup"] = secrets.token_urlsafe(18); save_cfg(c0)
print("SETUP_TOKEN", c0["setup"], flush=True)

def db():
    x = sqlite3.connect(DB, timeout=30); x.row_factory = sqlite3.Row; return x
with db() as x:
    x.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS frames(id INTEGER PRIMARY KEY, ts TEXT, app TEXT, win TEXT, url TEXT, text TEXT, src TEXT, img TEXT);
    CREATE TABLE IF NOT EXISTS audio(k TEXT PRIMARY KEY, chunk INTEGER, ts TEXT, s REAL, e REAL, text TEXT, device TEXT, speaker TEXT);
    CREATE TABLE IF NOT EXISTS inputs(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, app TEXT, win TEXT, url TEXT, text TEXT, x INTEGER, y INTEGER, key INTEGER, el TEXT, frame INTEGER, raw TEXT);
    CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
    CREATE INDEX IF NOT EXISTS fts ON frames(ts); CREATE INDEX IF NOT EXISTS ats ON audio(ts); CREATE INDEX IF NOT EXISTS its ON inputs(ts);
    """)

def req(path, raw=False, timeout=40):
    c = cfg()
    if not c.get("gate") or not c.get("key"): raise RuntimeError("faltam chaves (abra Configurar)")
    r = urllib.request.Request(BASE + path, headers={"x-nonve-gate": c["gate"], "Authorization": "Bearer " + c["key"],
        "X-Screenpipe-Client": "api", "User-Agent": "nonve-screenpipe"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        b = f.read()
    return b if raw else json.loads(b)

def iso(ts):
    try: return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except Exception: return ts

def kv(k, v=None):
    with db() as x:
        if v is None:
            r = x.execute("select v from kv where k=?", (k,)).fetchone(); return r[0] if r else None
        x.execute("insert or replace into kv values(?,?)", (k, v))

def pull(ctype, since):
    out, off = [], 0
    while off < 20000:
        q = urllib.parse.urlencode({"content_type": ctype, "limit": 20, "offset": off, "start_time": since, "end_time": "now"})
        d = req("/search?" + q).get("data", [])
        out += d; off += 20
        if len(d) < 20: break
    return out

def get_img(fid, ts):
    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(BRT)
    rel = f"frames/{t:%Y-%m-%d}/{t:%H}/{fid}.jpg"; p = f"{D}/{rel}"
    if os.path.exists(p): return rel
    os.makedirs(os.path.dirname(p), exist_ok=True)
    b = req(f"/frames/{fid}", raw=True, timeout=60)
    open(p + ".tmp", "wb").write(b); os.replace(p + ".tmp", p); return rel

def cycle():
    since = kv("since") or (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    newest = since
    with db() as x:
        for r in pull("ocr", since):
            c = r["content"]; fid = int(c["frame_id"]); ts = iso(c["timestamp"]); newest = max(newest, ts)
            if x.execute("select img from frames where id=? and img is not null", (fid,)).fetchone(): continue
            try: img = get_img(fid, ts)
            except Exception: img = None
            x.execute("insert or replace into frames values(?,?,?,?,?,?,?,?)", (fid, ts, c.get("app_name"), c.get("window_name"),
                      c.get("browser_url"), c.get("text"), c.get("text_source"), img)); x.commit()
        for r in pull("audio", since):
            c = r["content"]; ts = iso(c["timestamp"]); newest = max(newest, ts)
            k = f'{c["chunk_id"]}:{c.get("start_time")}'
            sp = c.get("speaker"); sp = sp.get("name") if isinstance(sp, dict) else sp
            x.execute("insert or replace into audio values(?,?,?,?,?,?,?,?)", (k, c["chunk_id"], ts, c.get("start_time"), c.get("end_time"),
                      c.get("transcription"), c.get("device_name"), sp))
        for r in pull("input", since):
            c = r["content"]; ts = iso(c["timestamp"]); newest = max(newest, ts)
            x.execute("insert or replace into inputs values(?,?,?,?,?,?,?,?,?,?,?,?,?)", (int(c["id"]), ts, c.get("event_type"), c.get("app_name"),
                      c.get("window_title"), c.get("browser_url"), c.get("text_content"), c.get("x"), c.get("y"), c.get("key_code"),
                      c.get("element_name"), c.get("frame_id"), json.dumps(c)))
    # volta 2 min para pegar transcricoes que chegam atrasadas
    nt = (datetime.fromisoformat(newest.replace("Z", "+00:00")) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if nt > since: kv("since", nt)

def poller():
    while True:
        try: cycle(); STATE.update(ok=True, err="", last=datetime.now(BRT).strftime("%d/%m %H:%M:%S"))
        except Exception as e: STATE.update(ok=False, err=str(e)[:200])
        time.sleep(5)

def videos():
    while True:
        now = datetime.now(BRT)
        for h in sorted(glob.glob(f"{D}/frames/*/*")):
            day, hr = h.split("/")[-2:]
            if f"{day} {hr}" >= now.strftime("%Y-%m-%d %H"): continue
            out = f"{D}/video/{day}/{hr}.mp4"
            if os.path.exists(out): continue
            with db() as x:
                rows = x.execute("select ts,img from frames where img like ? order by ts", (f"frames/{day}/{hr}/%",)).fetchall()
            if not rows: continue
            os.makedirs(os.path.dirname(out), exist_ok=True); lst = out + ".txt"
            with open(lst, "w") as f:
                for i, r in enumerate(rows):
                    a = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
                    b = datetime.fromisoformat(rows[i + 1]["ts"].replace("Z", "+00:00")) if i + 1 < len(rows) else a + timedelta(seconds=2)
                    f.write(f"file '{D}/{r['img']}'\nduration {max(0.04, min(600, (b - a).total_seconds())):.3f}\n")
                f.write(f"file '{D}/{rows[-1]['img']}'\n")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                            "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-vsync", "vfr", out + ".tmp.mp4"])
            if os.path.exists(out + ".tmp.mp4"): os.replace(out + ".tmp.mp4", out)
            os.remove(lst)
        time.sleep(300)

CSS = "body{font-family:system-ui;margin:0;background:#0f1115;color:#e7e7ea}a{color:#8ab4ff}header{padding:12px 16px;background:#171a21;display:flex;gap:12px;flex-wrap:wrap;align-items:center}input,select,button{background:#222632;color:#eee;border:1px solid #333;border-radius:6px;padding:6px 8px}main{padding:12px 16px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}.c{background:#171a21;border-radius:8px;overflow:hidden;font-size:12px}.c img{width:100%;display:block}.c div{padding:6px}.a{background:#171a21;border-radius:8px;padding:8px;margin:6px 0}.m{color:#9aa}.ok{color:#5c5}.bad{color:#e66}"

def page(body, title="Screenpipe Trabalho"):
    s = STATE; st = f"<span class={'ok' if s['ok'] else 'bad'}>{'copiando' if s['ok'] else 'parado: ' + html.escape(s['err'] or 'iniciando')}</span> · ultima copia {s['last'] or '-'}"
    return f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>{title}</title><style>{CSS}</style><header><b><a href=./ style='color:#fff;text-decoration:none'>Screenpipe Trabalho</a></b><a href=./audio>Transcricoes</a><a href=./videos>Videos</a><a href=./cliques>Cliques</a><a href=./config>Configurar</a><span class=m>{st}</span></header><main>{body}</main>"

def local(ts):
    try: return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(BRT).strftime("%d/%m %H:%M:%S")
    except Exception: return ts

def rng(q):
    day = q.get("dia", [datetime.now(BRT).strftime("%Y-%m-%d")])[0]; hr = q.get("hora", [""])[0]
    a = datetime.fromisoformat(day + (f"T{int(hr):02d}:00" if hr else "T00:00")).replace(tzinfo=BRT)
    b = a + (timedelta(hours=1) if hr else timedelta(days=1))
    f = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return day, hr, f(a), f(b)

def form(day, hr, extra=""):
    hs = "".join(f"<option {'selected' if hr == str(h) else ''} value={h}>{h:02d}h</option>" for h in range(24))
    return f"<form><input type=date name=dia value={day}> <select name=hora><option value=''>dia todo</option>{hs}</select> {extra}<button>ver</button></form><br>"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ct="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def file(self, p, ct):
        if not os.path.realpath(p).startswith(D + "/") or not os.path.exists(p): return self.send(404, "nao achei")
        self.send(200, open(p, "rb").read(), ct)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); p = u.path
        if p.startswith("/frames/"): return self.file(D + urllib.parse.unquote(p), "image/jpeg")
        if p.startswith("/video/"): return self.file(D + urllib.parse.unquote(p), "video/mp4")
        if p == "/status": return self.send(200, json.dumps(STATE), "application/json")
        if p == "/config":
            c = cfg(); return self.send(200, page(f"<h3>Configurar</h3><p class=m>Chave do gate: {'salva' if c.get('gate') else 'falta'} · Chave do Screenpipe: {'salva' if c.get('key') else 'falta'}</p><form method=post><p>Chave da API do Screenpipe (no notebook: Settings, API):<br><input type=password name=key size=60></p><button>Salvar</button></form>"))
        day, hr, a, b = rng(q)
        with db() as x:
            if p == "/audio":
                rows = x.execute("select * from audio where ts>=? and ts<? order by ts,s", (a, b)).fetchall()
                body = form(day, hr) + "".join(f"<div class=a><span class=m>{local(r['ts'])} · {html.escape(r['device'] or '')}</span><br>{html.escape(r['text'] or '')}</div>" for r in rows) or "nada"
                return self.send(200, page(body + (f"<p class=m>{len(rows)} trechos</p>")))
            if p == "/cliques":
                rows = x.execute("select * from inputs where ts>=? and ts<? order by ts desc limit 2000", (a, b)).fetchall()
                body = form(day, hr) + "<table style='font-size:12px'>" + "".join(f"<tr><td class=m>{local(r['ts'])}</td><td>{html.escape(r['kind'] or '')}</td><td>{html.escape(r['app'] or '')}</td><td>{html.escape(r['win'] or '')}</td><td>{html.escape(str(r['text'] or r['el'] or ''))}</td></tr>" for r in rows) + "</table>"
                return self.send(200, page(body))
            if p == "/videos":
                vs = sorted(glob.glob(f"{D}/video/*/*.mp4"), reverse=True)[:200]
                body = "<p class=m>Um video por hora, montado com os prints (fica pronto quando a hora fecha).</p>" + "".join(
                    f"<div class=a><a href=./video/{v.split('/')[-2]}/{v.split('/')[-1]}>{v.split('/')[-2]} {v.split('/')[-1][:2]}h</a> <span class=m>{os.path.getsize(v)//1048576} MB</span></div>" for v in vs)
                return self.send(200, page(body or "ainda nenhum"))
            busca = q.get("q", [""])[0]
            if busca: rows = x.execute("select * from frames where text like ? or win like ? order by ts desc limit 300", (f"%{busca}%", f"%{busca}%")).fetchall()
            else: rows = x.execute("select * from frames where ts>=? and ts<? order by ts desc limit 600", (a, b)).fetchall()
            n = x.execute("select (select count(*) from frames),(select count(*) from audio),(select count(*) from inputs)").fetchone()
        body = form(day, hr, f"<input name=q placeholder='buscar texto' value='{html.escape(busca)}'>") + f"<p class=m>Guardado: {n[0]} prints · {n[1]} trechos de audio · {n[2]} cliques/teclas</p><div class=g>" + "".join(
            f"<div class=c><a href=./{r['img']} target=_blank><img loading=lazy src=./{r['img']}></a><div><b>{local(r['ts'])}</b> · {html.escape(r['app'] or '')}<br><span class=m>{html.escape((r['win'] or '')[:80])}</span></div></div>" for r in rows if r["img"]) + "</div>"
        self.send(200, page(body))
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); f = urllib.parse.parse_qs(self.rfile.read(n).decode())
        c = cfg()
        if self.server.server_port == 8542:  # porta de setup: so com token
            if f.get("setup", [""])[0] != c["setup"]: return self.send(403, "nao")
            if f.get("gate"): c["gate"] = f["gate"][0].strip()
            if f.get("key"): c["key"] = f["key"][0].strip()
            save_cfg(c); return self.send(200, "ok")
        if f.get("key"): c["key"] = f["key"][0].strip(); save_cfg(c)
        self.send_response(303); self.send_header("Location", "./config"); self.end_headers()

class S(BaseHTTPRequestHandler):
    do_POST = H.do_POST; send = H.send
    def log_message(self, *a): pass
    def do_GET(self): self.send(404, "")

threading.Thread(target=poller, daemon=True).start()
threading.Thread(target=videos, daemon=True).start()
threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", 8542), S).serve_forever, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", 8541), H).serve_forever()
