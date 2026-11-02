"""Anamnese: base de conhecimento pessoal do nan (método Saraev: bruto -> segmentos -> lembranças, FTS5/BM25 + recência).

Só stdlib. Duas portas:
  UI_PORT (8590): telas + /api de leitura e escrita, sem token; só o app_proxy do Umbrel (com login) chega nela.
  API_PORT (8591): /api e /mcp com Bearer token (VM Roberta, Hark). Token conferido por sha256 (TOKEN_SHA256).
IA: 9Router e ElevenLabs lidos da config do Video KB (montada só leitura em /vkb).
"""
import hashlib, hmac, json, math, os, re, sqlite3, threading, time, traceback, unicodedata, uuid
import urllib.request
from contextlib import closing, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

DATA = os.environ.get("DATA_DIR", "/data")
WEB = os.environ.get("WEB_DIR", "/app/web")
VKB = os.environ.get("VKB_DIR", "/vkb")
DB = os.path.join(DATA, "anamnese.db")
TOKEN_SHA = os.environ.get("TOKEN_SHA256", "")
TEMAS = ["Lume e Roberta", "Homelab", "Fé e leitura", "Trabalho", "Saúde e casa", "Ideias"]
FONTES = {"sessoes": "Sessões dos agentes", "videokb": "Video KB", "audio": "Áudios", "manual": "Manual", "mcp": "MCP"}
LOCK = threading.Lock()
WAKE = threading.Event()
VERSAO = "0.3.1"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn").strip()


# ---------- banco ----------
def q(sql, args=()):
    # leitura não pega o LOCK: o WAL deixa vários leitores em paralelo com 1 escritor; só escrita é serializada
    leitura = sql.lstrip()[:6].upper() in ("SELECT", "PRAGMA")
    with (nullcontext() if leitura else LOCK), closing(sqlite3.connect(DB, timeout=30)) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        c.commit()
        return rows if rows or not sql.lstrip().upper().startswith("INSERT") else [{"id": cur.lastrowid}]


def init():
    os.makedirs(DATA, exist_ok=True)
    with closing(sqlite3.connect(DB)) as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS bruto(id INTEGER PRIMARY KEY, fonte TEXT, ext_id TEXT, titulo TEXT, texto TEXT,
          criado REAL, meta TEXT DEFAULT '{}', estado TEXT DEFAULT 'fila', erro TEXT DEFAULT '', UNIQUE(fonte, ext_id));
        CREATE TABLE IF NOT EXISTS segmento(id INTEGER PRIMARY KEY, bruto_id INTEGER, ordem INTEGER, texto TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS segmento_fts USING fts5(texto, content='segmento', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE IF NOT EXISTS lembranca(id INTEGER PRIMARY KEY, bruto_id INTEGER, texto TEXT, tema TEXT, pessoas TEXT DEFAULT '[]',
          quando TEXT DEFAULT '', criado REAL, substituida_por INTEGER, arquivada INTEGER DEFAULT 0);
        CREATE VIRTUAL TABLE IF NOT EXISTS lembranca_fts USING fts5(texto, content='lembranca', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE IF NOT EXISTS ligacao(a INTEGER, b INTEGER, motivo TEXT, PRIMARY KEY(a, b));
        CREATE TABLE IF NOT EXISTS wiki(tema TEXT PRIMARY KEY, md TEXT, compilado REAL, n INTEGER, ate INTEGER);
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        CREATE INDEX IF NOT EXISTS l_tema ON lembranca(tema, criado);
        CREATE INDEX IF NOT EXISTS b_estado ON bruto(estado);
        CREATE INDEX IF NOT EXISTS lig_b ON ligacao(b);
        CREATE INDEX IF NOT EXISTS l_bruto ON lembranca(bruto_id);
        CREATE INDEX IF NOT EXISTS l_subst ON lembranca(substituida_por);
        CREATE INDEX IF NOT EXISTS s_bruto ON segmento(bruto_id);
        """)
    cols = {r["name"] for r in q("PRAGMA table_info(lembranca)")}
    for col, tipo in (("reel", "TEXT"), ("t_ini", "REAL"), ("t_fim", "REAL")):
        if col not in cols:
            q(f"ALTER TABLE lembranca ADD COLUMN {col} {tipo}")
    q("CREATE INDEX IF NOT EXISTS l_reel ON lembranca(reel, t_ini)")
    q("UPDATE bruto SET estado='fila' WHERE estado IN ('rodando', 'erro')")
    if kv("vkb_formato") != "2":  # Selah fase 1: vídeos com id do reel e tempos; refaz tudo o que veio do Video KB
        for r in q("SELECT id FROM bruto WHERE fonte='videokb'"):
            limpar_bruto(r["id"])
            q("DELETE FROM bruto WHERE id=?", (r["id"],))
        kv("vkb_desde", 0); kv("vkb_formato", "2")


def kv(k, v=None):
    if v is None:
        r = q("SELECT v FROM kv WHERE k=?", (k,))
        return r[0]["v"] if r else None
    q("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, str(v)))


def fts_query(texto, modo=" "):
    toks = [t for t in re.findall(r"\w+", fold(texto)) if len(t) > 1][:12]
    return modo.join(f'"{t}"*' for t in toks)


# ---------- IA ----------
def vkb_cfg():
    try:
        with open(os.path.join(VKB, "config.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def http(method, url, headers, body=None, timeout=180):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise RuntimeError("resposta não-JSON de " + url.split("/")[2] + ": " + raw[:200].decode("utf-8", "replace"))


def llm_json(sistema, usuario, timeout=240):
    c = vkb_cfg()
    if not c.get("llm_key"):
        raise RuntimeError("sem chave do 9Router na config do Video KB")
    url = (c.get("llm_url") or "http://thainan-9router_web_1:20128/v1").rstrip("/") + "/chat/completions"
    body = {"model": c.get("model") or "ag/gemini-3.8-flash-high", "stream": False, "temperature": 0.2, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]}
    r = http("POST", url, {"Authorization": "Bearer " + c["llm_key"], "Content-Type": "application/json"}, body, timeout)
    txt = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not txt.strip():
        raise RuntimeError("IA respondeu vazio: " + json.dumps(r)[:200])
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0) if m else txt)


def transcrever(audio, ctype):
    c = vkb_cfg()
    if not c.get("elevenlabs_key"):
        raise RuntimeError("sem chave ElevenLabs na config do Video KB")
    b = uuid.uuid4().hex
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}.get(ctype.split(";")[0], "webm")
    parts = [(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode() for k, v in
             (("model_id", "scribe_v1"), ("language_code", "por"), ("tag_audio_events", "false"))]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="gravacao.{ext}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + audio + b"\r\n")
    body = b"".join(parts) + f"--{b}--\r\n".encode()
    r = http("POST", "https://api.elevenlabs.io/v1/speech-to-text",
             {"xi-api-key": c["elevenlabs_key"], "Content-Type": f"multipart/form-data; boundary={b}"}, body, 300)
    return (r.get("text") or "").strip()


# ---------- Decider (igual ao Video KB): escolha fechada, sem texto, via 9Router /v1/systemone ----------
# Ordem: pplx-decider → jev → microsoft. Abaixo de 85% = incerto. Falhou tudo = None (vale o Gemini).
DECIDER_MODELOS = ("openrouter/~perplexity/pplx-decider-latest", "openrouter/~typesafe/jev-latest", "openrouter/microsoft/microsoft-decision-1")
DECIDER_LIMIAR = 0.85
DECIDER_FORA = {"ate": 0.0}  # os 3 modelos caíram: pula o Decider por 2 min em vez de gastar 3 x 40 s por lembrança


def decider_ligado(c=None):
    c = c if c is not None else vkb_cfg()
    return str(c.get("decider_on", "1")) != "0" and bool(c.get("llm_key"))


def decidir(nome, state, pergunta):
    """pergunta: {"type": "noul"|"choice", "instructions", "criteria"}. Volta {"valor", "prob", "sim"} ou None (fora do ar/desligado)."""
    c = vkb_cfg()
    if not decider_ligado(c) or time.time() < DECIDER_FORA["ate"]:
        return None
    url = (c.get("llm_url") or "http://thainan-9router_web_1:20128/v1").rstrip("/") + "/systemone"
    h = {"Authorization": "Bearer " + c["llm_key"], "Content-Type": "application/json"}
    for m in DECIDER_MODELOS:
        try:
            r = http("POST", url, h, {"model": m, "state": state, "questions": {"q": pergunta}}, timeout=40)
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
            log(f"decider {nome} falhou com {m.split('/')[-1]}: {str(e)[:150]}")
    DECIDER_FORA["ate"] = time.time() + 120
    return None


def decider_sim(nome, state, instrucao, sim, nao):
    """True = Decider confirma (>=85%); False = nega ou está incerto; None = fora do ar (vale o Gemini)."""
    d = decidir(nome, state, {"type": "noul", "instructions": instrucao, "criteria": {"true": sim, "false": nao}})
    return None if d is None else d["sim"] >= DECIDER_LIMIAR


def decider_tema(texto, tema_gemini):
    d = decidir(f"tema #{texto[:40]}", {"lembranca": texto, "tema_sugerido": tema_gemini},
                {"type": "choice", "instructions": "Em qual tema fixo da base pessoal do nan esta lembrança se encaixa melhor?",
                 "criteria": {t: t for t in TEMAS}})
    if d and d["valor"] != tema_gemini and d["prob"] >= DECIDER_LIMIAR:
        log(f"decider trocou tema: {tema_gemini} -> {d['valor']}")
        return d["valor"]
    return tema_gemini


SIS_EXTRAI = (
    "Você mantém a base de conhecimento pessoal do nan (Thainan). Do texto recebido, extraia LEMBRANÇAS: fatos curtos, "
    "autossuficientes e duráveis (decisões, preferências, estado de projetos, quem é quem, pendências, aprendizados). "
    "Ignore conversa fiada, passos técnicos triviais e o que só valeu no momento. Cada lembrança tem 1 frase de até 200 caracteres, "
    "em português do Brasil, com nomes próprios explícitos (nunca 'ele', 'isso'). "
    "Responda SOMENTE JSON: {\"lembrancas\": [{\"texto\": str, \"tema\": um de TEMAS, \"pessoas\": [nomes], "
    "\"quando\": \"AAAA-MM-DD\" ou \"\", \"substitui\": [ids das lembranças EXISTENTES que esta torna falsas ou desatualizadas]}]}. "
    "No máximo 12 lembranças; nenhuma se não houver nada durável. O nan (Thainan) é o dono da base: nunca o coloque em pessoas "
    "e não comece frases com 'Thainan salvou', 'Vídeo salvo por Thainan' ou parecido; escreva o conhecimento direto (de quem é a ideia, o que diz). "
    "Quando o texto for um vídeo com marcas [mm:ss] na transcrição, cada lembrança leva também \"t\": a marca de onde ela saiu, "
    "em segundos (inteiro). Se saiu do resumo e não de um trecho, use a marca do trecho que melhor a sustenta."
)


def segmentar(texto, alvo=1400):
    blocos, atual = [], ""
    for p in re.split(r"\n\s*\n", texto):
        p = p.strip()
        while len(p) > alvo * 2:
            blocos.append(p[:alvo]); p = p[alvo:]
        if len(atual) + len(p) > alvo and atual:
            blocos.append(atual); atual = ""
        atual = (atual + "\n\n" + p).strip()
    if atual:
        blocos.append(atual)
    return blocos


def processar(b):
    bid = b["id"]
    q("UPDATE bruto SET estado='rodando' WHERE id=?", (bid,))
    q("DELETE FROM segmento_fts WHERE rowid IN (SELECT id FROM segmento WHERE bruto_id=?)", (bid,))  # reprocesso (reinício/erro) não duplica
    q("DELETE FROM segmento WHERE bruto_id=?", (bid,))
    for i, s in enumerate(segmentar(b["texto"])):
        sid = q("INSERT INTO segmento(bruto_id, ordem, texto) VALUES(?,?,?)", (bid, i, s))[0]["id"]
        q("INSERT INTO segmento_fts(rowid, texto) VALUES(?,?)", (sid, s))
    texto = b["texto"][:14000]
    viz = buscar_lembrancas(b["titulo"] + " " + texto[:600], 10, modo=" OR ")
    usuario = (f"TEMAS: {json.dumps(TEMAS, ensure_ascii=False)}\nFONTE: {FONTES.get(b['fonte'], b['fonte'])}\n"
               f"DATA DO TEXTO: {time.strftime('%Y-%m-%d', time.localtime(b['criado']))}\n"
               f"LEMBRANÇAS EXISTENTES PARECIDAS: {json.dumps([{'id': v['id'], 'texto': v['texto']} for v in viz], ensure_ascii=False)}\n"
               f"TÍTULO: {b['titulo']}\nTEXTO:\n{texto}")
    out = llm_json(SIS_EXTRAI, usuario).get("lembrancas") or []
    try:
        meta = json.loads(b.get("meta") or "{}")
    except (ValueError, TypeError):
        meta = {}
    ids_viz = {v["id"] for v in viz}
    for l in out[:12]:
        t = str(l.get("texto") or "").strip()[:300]
        if not t:
            continue
        tema = decider_tema(t, next((x for x in TEMAS if fold(x) == fold(l.get("tema"))), "Ideias"))
        pessoas = [str(p).strip()[:60] for p in (l.get("pessoas") or []) if str(p).strip() and fold(str(p)) not in ("thainan", "nan", "thainan caldeira", "thainan dos santos caldeira")][:8]
        reel = meta.get("reel") if b["fonte"] == "videokb" else None
        t_ini, t_fim = trecho(meta, l.get("t")) if reel else (None, None)
        lid = q("INSERT INTO lembranca(bruto_id, texto, tema, pessoas, quando, criado, reel, t_ini, t_fim) VALUES(?,?,?,?,?,?,?,?,?)",
                (bid, t, tema, json.dumps(pessoas, ensure_ascii=False), str(l.get("quando") or "")[:10], b["criado"], reel, t_ini, t_fim))[0]["id"]
        q("INSERT INTO lembranca_fts(rowid, texto) VALUES(?,?)", (lid, t))
        for old in l.get("substitui") or []:
            if isinstance(old, int) and old in ids_viz:
                velha = next(v["texto"] for v in viz if v["id"] == old)
                ok = decider_sim(f"substitui {lid}->{old}", {"nova": t, "antiga": velha},
                                 "A lembrança nova torna a antiga falsa ou desatualizada?",
                                 "Sim: a nova contradiz ou atualiza a antiga.", "Não: as duas continuam valendo.")
                if ok is False:
                    log(f"decider barrou substituição {lid}->{old}")
                    continue
                q("UPDATE lembranca SET substituida_por=? WHERE id=? AND substituida_por IS NULL", (lid, old))
                q("INSERT OR IGNORE INTO ligacao VALUES(?,?,?)", (lid, old, "substitui"))
        for p in pessoas:
            for o in q("SELECT l.id FROM lembranca l, json_each(l.pessoas) j WHERE j.value=? AND l.id<>? ORDER BY l.criado DESC LIMIT 4", (p, lid)):
                q("INSERT OR IGNORE INTO ligacao VALUES(?,?,?)", (lid, o["id"], "pessoa:" + p))
    sib = [r["id"] for r in q("SELECT id FROM lembranca WHERE bruto_id=? ORDER BY id", (bid,))]
    for x, y in zip(sib, sib[1:]):
        q("INSERT OR IGNORE INTO ligacao VALUES(?,?,?)", (y, x, "mesma fonte"))
    q("UPDATE bruto SET estado='pronto', erro='' WHERE id=?", (bid,))
    log("processado", bid, b["fonte"], len(out), "lembranças")


def limpar_bruto(bid):
    """Refazer de verdade: apaga o que esse bruto gerou (lembranças, ligações, segmentos) e põe na fila."""
    ids = [r["id"] for r in q("SELECT id FROM lembranca WHERE bruto_id=?", (bid,))]
    refazer_fts = False
    for lid in ids:
        try:
            q("DELETE FROM lembranca_fts WHERE rowid=?", (lid,))
        except sqlite3.DatabaseError:  # linha fora do índice: reconstrói o índice inteiro no fim
            refazer_fts = True
        q("DELETE FROM ligacao WHERE a=? OR b=?", (lid, lid))
        q("UPDATE lembranca SET substituida_por=NULL WHERE substituida_por=?", (lid,))
    q("DELETE FROM lembranca WHERE bruto_id=?", (bid,))
    try:
        q("DELETE FROM segmento_fts WHERE rowid IN (SELECT id FROM segmento WHERE bruto_id=?)", (bid,))
    except sqlite3.DatabaseError:
        refazer_fts = True
    q("DELETE FROM segmento WHERE bruto_id=?", (bid,))
    q("UPDATE bruto SET estado='fila', erro='' WHERE id=?", (bid,))
    if refazer_fts:
        q("INSERT INTO lembranca_fts(lembranca_fts) VALUES('rebuild')")
        q("INSERT INTO segmento_fts(segmento_fts) VALUES('rebuild')")


def entrar(fonte, ext_id, titulo, texto, criado=None, meta=None):
    """Grava o bruto (idempotente por fonte+ext_id). Texto igual ao já gravado = nada muda."""
    if not isinstance(texto, str):
        raise ValueError("texto deve ser string")
    texto = texto.strip()
    if len(texto) < 20:
        raise ValueError("texto curto demais")
    if titulo is not None and not isinstance(titulo, str):
        raise ValueError("titulo deve ser string")
    if ext_id is not None and (isinstance(ext_id, bool) or not isinstance(ext_id, (str, int))):
        raise ValueError("ext_id deve ser string ou inteiro")
    if meta is not None and not isinstance(meta, dict):
        raise ValueError("meta deve ser objeto")
    try:
        criado = float(criado) if criado else time.time()
    except (TypeError, ValueError):
        raise ValueError("criado inválido")
    if not math.isfinite(criado):
        raise ValueError("criado inválido")
    if criado > 1e11:  # Date.now() do JS vem em milissegundos
        criado /= 1000
    criado = min(criado, time.time()) if criado > 0 else time.time()
    titulo = (titulo or "")[:200]
    ext_id = str(ext_id or hashlib.sha1(texto.encode()).hexdigest())
    old = q("SELECT id, texto FROM bruto WHERE fonte=? AND ext_id=?", (fonte, ext_id))
    if old and old[0]["texto"] == texto:
        return old[0]["id"], False
    if old:  # texto mudou (sessão continuou): refaz segmentos; lembranças antigas ficam, a IA substitui o que mudou
        q("DELETE FROM segmento_fts WHERE rowid IN (SELECT id FROM segmento WHERE bruto_id=?)", (old[0]["id"],))
        q("DELETE FROM segmento WHERE bruto_id=?", (old[0]["id"],))
        q("UPDATE bruto SET texto=?, titulo=?, estado='fila' WHERE id=?", (texto, titulo, old[0]["id"]))
        WAKE.set()
        return old[0]["id"], True
    bid = q("INSERT INTO bruto(fonte, ext_id, titulo, texto, criado, meta) VALUES(?,?,?,?,?,?)",
            (fonte, ext_id, titulo, texto, criado, json.dumps(meta or {}, ensure_ascii=False)))[0]["id"]
    WAKE.set()
    return bid, True


# ---------- Video KB (só leitura) ----------
def mmss_s(m):
    a, b = m.split(":")
    return int(a) * 60 + int(b)


def trecho(meta, t):
    """Volta (t_ini, t_fim) alinhado às marcas da transcrição; fim = marca seguinte (ou +30 s)."""
    marcas = sorted(meta.get("marcas") or [])
    if t is None or not isinstance(t, (int, float)):
        return None, None
    t = max(0, int(t))
    ini = max([m for m in marcas if m <= t], default=t)
    prox = [m for m in marcas if m > ini]
    fim = prox[0] if prox else (meta.get("dur") or ini + 30)
    return float(ini), float(max(fim, ini + 1))


VKB_SIG = {}


def importar_videokb():
    p = os.path.join(VKB, "kb.db")
    if not os.path.exists(p):
        return 0
    desde = float(kv("vkb_desde") or 0)
    sig = tuple((st.st_size, st.st_mtime_ns) if (st := os.stat(p + s) if os.path.exists(p + s) else None) else None for s in ("", "-wal"))
    if VKB_SIG.get("v") == sig:  # nada mudou desde a última cópia: não regrava o arquivo inteiro a cada 10 min
        return 0
    VKB_SIG["v"] = sig
    import shutil  # o volume do Video KB é só leitura e o banco é WAL: lê uma cópia (na pasta de dados: /tmp/vkb era compartilhado entre instâncias)
    cp = os.path.join(DATA, "vkb-copia")
    os.makedirs(cp, exist_ok=True)
    for suf in ("", "-wal"):
        if os.path.exists(p + suf):
            shutil.copyfile(p + suf, os.path.join(cp, "kb.db" + suf))
        elif os.path.exists(os.path.join(cp, "kb.db" + suf)):
            os.remove(os.path.join(cp, "kb.db" + suf))
    if os.path.exists(os.path.join(cp, "kb.db-shm")):  # índice do WAL antigo não vale para a cópia nova
        os.remove(os.path.join(cp, "kb.db-shm"))
    with closing(sqlite3.connect(os.path.join(cp, "kb.db"), timeout=30)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT id, url, data, updated FROM jobs WHERE status='pronto' AND updated>? ORDER BY updated LIMIT 200", (desde,)).fetchall()
    n = 0
    for r in rows:
        try:
            novo = linha_videokb(r)
        except Exception as e:  # linha ruim não pode travar todas as seguintes a cada ciclo
            log("video kb: linha", r["id"], "ignorada:", str(e)[:150])
            novo = 0
        if novo is None:
            continue
        n += novo
        kv("vkb_desde", r["updated"])
    if n:
        log("video kb:", n, "novos")
    return n


def linha_videokb(r):
    """Volta 1 (novo/mudou), 0 (igual/ruim) ou None (sem análise ainda: não avança o marcador)."""
    d = json.loads(r["data"] or "{}")
    if not isinstance(d, dict):
        return 0
    a = d.get("analysis")
    if not a:
        return None
    if not isinstance(a, dict):
        return 0
    trans = d.get("transcript_t") or d.get("transcript") or ""
    trans = trans if isinstance(trans, str) else ""
    pontos = a.get("pontos_chave")
    pontos = pontos if isinstance(pontos, list) else []
    titulo = a.get("titulo") if isinstance(a.get("titulo"), str) else ""
    resumo = a.get("resumo") if isinstance(a.get("resumo"), str) else ""
    texto = "\n\n".join(x for x in [titulo, resumo, "\n".join("- " + str(p) for p in pontos),
                                      "TRANSCRIÇÃO:\n" + trans[:12000] if trans else ""] if x)
    marcas = [mmss_s(m) for m in re.findall(r"^\[(\d+:\d\d)\]", trans, re.M)]
    dur = d.get("duration") or d.get("duracao") or (d.get("meta") or {}).get("duration")
    meta = {"url": r["url"], "reel": str(r["id"]), "tema_vkb": str(a.get("tema") or ""), "marcas": marcas[:400],
            "dur": float(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) else None}
    try:
        return int(entrar("videokb", r["url"], titulo or r["url"], texto, r["updated"], meta)[1])
    except ValueError:
        return 0


def worker():
    ultimo_vkb = 0
    while True:
        WAKE.clear()  # antes de olhar a fila: um set() durante o processamento não se perde
        try:
            if time.time() - ultimo_vkb > 600:
                ultimo_vkb = time.time()
                try:
                    importar_videokb()
                except Exception:
                    traceback.print_exc()
            for b in q("SELECT * FROM bruto WHERE estado='fila' ORDER BY criado DESC LIMIT 3"):
                try:
                    processar(b)
                except Exception as e:
                    log("erro", b["id"], e)
                    q("UPDATE bruto SET estado='erro', erro=? WHERE id=?", (str(e)[:400], b["id"]))
            espera = 1 if q("SELECT 1 FROM bruto WHERE estado='fila' LIMIT 1") else 20
        except Exception:
            traceback.print_exc()
            espera = 5  # banco travado: não morre nem gira em falso
        WAKE.wait(espera)


# ---------- leitura ----------
def lembs(rows):
    """Anexa fonte/origem/url de cada lembrança com UMA consulta (antes: 1 conexão por linha)."""
    rows = [dict(r) for r in rows]
    ids = sorted({r["bruto_id"] for r in rows if r.get("bruto_id") is not None})
    bs = {}
    if ids:
        bs = {b["id"]: b for b in q(f"SELECT id, fonte, titulo, CASE WHEN json_valid(meta) THEN json_extract(meta, '$.url') END AS url FROM bruto "
                                     f"WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))}
    for r in rows:
        r["pessoas"] = json.loads(r.get("pessoas") or "[]")
        b = bs.get(r.get("bruto_id"))
        if b:
            r["fonte"], r["fonte_nome"], r["origem"] = b["fonte"], FONTES.get(b["fonte"], b["fonte"]), b["titulo"]
            r["url"] = b["url"] or ""
    return rows


def lemb(r):
    return lembs([r])[0]


def inteiro(v, padrao, lo, hi):
    try:
        n = int(v) if v not in (None, "") else padrao
    except (TypeError, ValueError):
        raise ValueError("número inválido: " + str(v)[:20])
    return max(lo, min(n, hi))


def recencia(criado):
    idade = max(0.0, time.time() - (criado or 0))  # data futura no banco não pode estourar o exp()
    return 1 + 0.6 * math.exp(-idade / 86400 / 60)


def buscar_lembrancas(texto, n=10, modo=" ", incluir_antigas=False):
    fq = fts_query(texto, modo)
    if not fq:
        return []
    extra = "" if incluir_antigas else "AND l.substituida_por IS NULL"
    rows = q(f"SELECT l.*, bm25(lembranca_fts) AS s FROM lembranca_fts JOIN lembranca l ON l.id=lembranca_fts.rowid "
             f"WHERE lembranca_fts MATCH ? AND l.arquivada=0 {extra} ORDER BY s LIMIT ?", (fq, n * 4))
    rows.sort(key=lambda r: r["s"] / recencia(r["criado"]))  # bm25 é negativo: menor = melhor
    return rows[:n]


def buscar(texto, n=10):
    ls = buscar_lembrancas(texto, n) or buscar_lembrancas(texto, n, " OR ")
    fq = fts_query(texto, " OR ")
    segs = q("SELECT s.id, s.bruto_id, snippet(segmento_fts, 0, '«', '»', ' … ', 24) AS trecho, bm25(segmento_fts) AS s, b.titulo, b.fonte, b.criado "
             "FROM segmento_fts JOIN segmento s ON s.id=segmento_fts.rowid JOIN bruto b ON b.id=s.bruto_id "
             "WHERE segmento_fts MATCH ? ORDER BY s LIMIT 6", (fq,)) if fq else []
    return {"lembrancas": lembs(ls), "trechos": segs}


def status():
    f = {r["fonte"]: r for r in q("SELECT fonte, COUNT(*) n, MAX(criado) ultimo FROM bruto GROUP BY fonte")}
    est = {r["estado"]: r["n"] for r in q("SELECT estado, COUNT(*) n FROM bruto GROUP BY estado")}
    return {"versao": VERSAO, "lembrancas": q("SELECT COUNT(*) n FROM lembranca WHERE substituida_por IS NULL AND arquivada=0")[0]["n"],
            "substituidas": q("SELECT COUNT(*) n FROM lembranca WHERE substituida_por IS NOT NULL")[0]["n"],
            "arquivadas": q("SELECT COUNT(*) n FROM lembranca WHERE arquivada=1")[0]["n"],
            "brutos": est, "ia": bool(vkb_cfg().get("llm_key")), "decider": decider_ligado(), "transcricao": bool(vkb_cfg().get("elevenlabs_key")),
            "fontes": [{"id": k, "nome": v, "brutos": f.get(k, {}).get("n", 0), "ultimo": f.get(k, {}).get("ultimo")} for k, v in FONTES.items()],
            "temas": temas(), "ultima_gravacao": (q("SELECT MAX(criado) m FROM bruto WHERE fonte='audio'")[0]["m"])}


def temas():
    c = {r["tema"]: r for r in q("SELECT tema, COUNT(*) n, MAX(criado) ultimo FROM lembranca WHERE substituida_por IS NULL AND arquivada=0 GROUP BY tema")}
    return [{"tema": t, "n": c.get(t, {}).get("n", 0), "ultimo": c.get(t, {}).get("ultimo")} for t in TEMAS]


def lista(qs):
    w, a = ["arquivada=?"], [1 if qs.get("arquivadas") == "1" else 0]
    if qs.get("tema"):
        w.append("tema=?"); a.append(qs["tema"])
    if qs.get("antes"):
        w.append("criado<?"); a.append(float(qs["antes"]))
    if qs.get("depois"):
        w.append("criado>?"); a.append(float(qs["depois"]))
    if qs.get("antigas") != "1":
        w.append("substituida_por IS NULL")
    n = inteiro(qs.get("n"), 50, 1, 500)
    rows = q(f"SELECT * FROM lembranca WHERE {' AND '.join(w)} ORDER BY criado DESC, id DESC LIMIT ?", (*a, n))
    return {"total": q(f"SELECT COUNT(*) n FROM lembranca WHERE {' AND '.join(w)}", tuple(a))[0]["n"], "itens": lembs(rows)}


def uma(lid):
    r = q("SELECT * FROM lembranca WHERE id=?", (lid,))
    if not r:
        return None
    x = lemb(r[0])
    lg = q("SELECT l.*, g.motivo FROM ligacao g JOIN lembranca l ON l.id = CASE WHEN g.a=? THEN g.b ELSE g.a END WHERE g.a=? OR g.b=? LIMIT 20", (lid, lid, lid))
    x["ligacoes"] = lembs(lg)
    x["substitui"] = lembs(q("SELECT * FROM lembranca WHERE substituida_por=?", (lid,)))
    return x


def grafico(n=20):
    nos = q("SELECT l.*, COALESCE(g.n, 0) AS grau FROM lembranca l LEFT JOIN "
            "(SELECT id, COUNT(*) n FROM (SELECT a AS id FROM ligacao UNION ALL SELECT b FROM ligacao) GROUP BY id) g ON g.id=l.id "
            "WHERE l.substituida_por IS NULL AND l.arquivada=0 ORDER BY grau DESC, l.criado DESC LIMIT ?", (n,))
    ids = [r["id"] for r in nos]
    marcas = ",".join("?" * len(ids))
    arestas = q(f"SELECT a, b, motivo FROM ligacao WHERE a IN ({marcas}) AND b IN ({marcas})", tuple(ids) * 2) if ids else []
    total = q("SELECT COUNT(*) n FROM lembranca WHERE substituida_por IS NULL AND arquivada=0")[0]["n"]
    return {"total": total, "nos": lembs(nos), "arestas": arestas}


SIS_WIKI = (
    "Você compila a página de wiki de um tema da base pessoal do nan a partir das lembranças dadas (cada uma com id). "
    "Escreva em português do Brasil, frases curtas e diretas, sem inventar nada além das lembranças. "
    "Responda SOMENTE JSON: {\"resumo\": \"1 a 2 frases\", \"secoes\": [{\"titulo\": \"Estado atual\"|\"Decisões\"|\"Quem está envolvido\"|\"Pendências\", "
    "\"afirmacoes\": [{\"texto\": str, \"fontes\": [ids]}]}]}. Seção sem afirmação fica de fora."
)


def compilar(tema):
    ls = q("SELECT id, texto, quando, criado FROM lembranca WHERE tema=? AND substituida_por IS NULL AND arquivada=0 ORDER BY criado DESC LIMIT 200", (tema,))
    if not ls:
        raise ValueError("tema sem lembranças")
    j = llm_json(SIS_WIKI, f"TEMA: {tema}\nLEMBRANÇAS: {json.dumps(ls, ensure_ascii=False)}")
    mx = max(l["id"] for l in ls)
    q("INSERT OR REPLACE INTO wiki VALUES(?,?,?,?,?)", (tema, json.dumps(j, ensure_ascii=False), time.time(), len(ls), mx))
    return wiki(tema)


def wiki(tema):
    w = q("SELECT * FROM wiki WHERE tema=?", (tema,))
    ate = w[0]["ate"] if w else 0
    novas = q("SELECT COUNT(*) n FROM lembranca WHERE tema=? AND id>? AND substituida_por IS NULL", (tema, ate))[0]["n"]
    pag = json.loads(w[0]["md"]) if w else None
    riscadas = lembs(q("SELECT * FROM lembranca WHERE tema=? AND substituida_por IS NOT NULL ORDER BY criado DESC LIMIT 20", (tema,)))
    return {"tema": tema, "pagina": pag, "compilado": w[0]["compilado"] if w else None, "n": w[0]["n"] if w else 0,
            "novas_para_revisar": novas, "substituidas": riscadas}


# ---------- MCP ----------
FERRAMENTAS = [
    {"name": "buscar", "description": "Busca na Anamnese, a base de conhecimento pessoal do nan (lembranças + trechos das fontes, BM25 com recência).",
     "inputSchema": {"type": "object", "properties": {"consulta": {"type": "string"}, "n": {"type": "integer", "default": 10}}, "required": ["consulta"]}},
    {"name": "gravar", "description": "Grava um texto na Anamnese; a IA extrai as lembranças duráveis (e substitui as desatualizadas).",
     "inputSchema": {"type": "object", "properties": {"texto": {"type": "string"}, "titulo": {"type": "string"}, "fonte": {"type": "string", "default": "mcp"}}, "required": ["texto"]}},
    {"name": "status", "description": "Números da Anamnese: lembranças, fontes, fila de processamento e temas.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def mcp(msg):
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "requisição inválida"}}
    m, i, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if i is None:
        return None
    if not isinstance(p, dict):
        return {"jsonrpc": "2.0", "id": i, "error": {"code": -32602, "message": "params deve ser objeto"}}
    ok = lambda r: {"jsonrpc": "2.0", "id": i, "result": r}
    if m == "initialize":
        return ok({"protocolVersion": p.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                   "serverInfo": {"name": "anamnese", "version": VERSAO}})
    if m == "ping":
        return ok({})
    if m == "tools/list":
        return ok({"tools": FERRAMENTAS})
    if m == "tools/call":
        a = p.get("arguments") or {}
        if not isinstance(a, dict):
            return {"jsonrpc": "2.0", "id": i, "error": {"code": -32602, "message": "arguments deve ser objeto"}}
        try:
            if p.get("name") == "buscar":
                r = buscar(str(a.get("consulta") or ""), inteiro(a.get("n"), 10, 1, 30))
            elif p.get("name") == "gravar":
                fonte = a.get("fonte") if a.get("fonte") in FONTES else "mcp"
                bid, novo = entrar(fonte, None, a.get("titulo") or "", a.get("texto") or "")
                r = {"bruto": bid, "novo": novo, "obs": "a IA extrai as lembranças em segundos"}
            elif p.get("name") == "status":
                r = status()
            else:
                return {"jsonrpc": "2.0", "id": i, "error": {"code": -32602, "message": "ferramenta desconhecida"}}
            return ok({"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]})
        except Exception as e:
            return ok({"content": [{"type": "text", "text": "erro: " + str(e)}], "isError": True})
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "método desconhecido"}}


# ---------- HTTP ----------
def handler(com_token):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = float(os.environ.get("HTTP_TIMEOUT", 60))  # conexão keep-alive parada ou corpo que não chega não prende a thread para sempre

        def log_message(self, *a):
            pass

        def manda(self, code, obj, ctype="application/json; charset=utf-8"):
            b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b)

        def autorizado(self):
            if not com_token:
                return True
            if not TOKEN_SHA:
                return False
            # X-Anamnese-Token (sem prefixo) existe porque o Hark injeta outro Bearer no host do Funnel; basta um bater
            ts = [(self.headers.get("Authorization") or "").removeprefix("Bearer ").strip(), (self.headers.get("X-Anamnese-Token") or "").strip()]
            return any(t and hmac.compare_digest(hashlib.sha256(t.encode()).hexdigest().encode(), TOKEN_SHA.encode()) for t in ts)

        def corpo(self):
            if self._lido is None:
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    self.close_connection = True  # não lemos chunked: o resto viraria o próximo pedido
                    raise ValueError("Transfer-Encoding chunked não é aceito; mande Content-Length")
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > 60_000_000:
                    self.close_connection = True
                    raise ValueError("Content-Length inválido ou corpo grande demais")
                self._lido = self.rfile.read(n) if n else b""
                if len(self._lido) < n:
                    self.close_connection = True
                    raise ValueError("corpo incompleto")
            return self._lido

        def objeto(self):
            j = json.loads(self.corpo() or b"{}")
            if not isinstance(j, dict):
                raise ValueError("corpo deve ser um objeto JSON")
            return j

        def do_GET(self):
            self.rota("GET")

        def do_POST(self):
            self.rota("POST")

        def do_DELETE(self):
            self.rota("DELETE")

        def rota(self, metodo):
            self._lido = None
            u = urlsplit(self.path)
            path, qs = unquote(u.path), {k: v[-1] for k, v in parse_qs(u.query).items()}
            try:
                if path == "/healthz":
                    return self.manda(200, {"ok": True})
                if not path.startswith(("/api/", "/mcp")):
                    if com_token:
                        return self.manda(404, {"erro": "telas só pela porta do app"})
                    return self.estatico(path)
                if not self.autorizado():
                    return self.manda(401, {"erro": "token inválido"})
                if path == "/mcp":
                    if metodo != "POST":
                        return self.manda(405, {"erro": "use POST"})
                    msg = json.loads(self.corpo() or b"{}")
                    out = [x for x in map(mcp, msg) if x] if isinstance(msg, list) else mcp(msg)
                    if not out:
                        self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers(); return
                    return self.manda(200, out)
                return self.api(metodo, path, qs)
            except ValueError as e:
                return self.manda(400, {"erro": str(e)})
            except Exception as e:
                traceback.print_exc()
                return self.manda(500, {"erro": str(e)})
            finally:
                try:  # keep-alive do Funnel: corpo pequeno não lido vira lixo no próximo pedido ('{}POST'); corpo grande: fecha, não lê
                    if self._lido is None and int(self.headers.get("Content-Length") or 0) > 65536:
                        self.close_connection = True
                    else:
                        self.corpo()
                except Exception:
                    self.close_connection = True

        def api(self, metodo, path, qs):
            p = path[5:].strip("/").split("/")
            if metodo == "GET":
                if p == ["status"]:
                    return self.manda(200, status())
                if p == ["buscar"]:
                    return self.manda(200, buscar(qs.get("q", ""), inteiro(qs.get("n"), 10, 1, 50)))
                if p == ["lembrancas"]:
                    return self.manda(200, lista(qs))
                if p[0] == "lembranca" and len(p) == 2:
                    x = uma(int(p[1]))
                    return self.manda(200 if x else 404, x or {"erro": "não achei"})
                if p[0] == "de_reel" and len(p) == 2:
                    rows = q("SELECT * FROM lembranca WHERE reel=? AND arquivada=0 ORDER BY t_ini IS NULL, t_ini, id", (p[1],))
                    return self.manda(200, {"reel": p[1], "total": len(rows), "itens": [lemb(r) for r in rows]})
                if p == ["temas"]:
                    return self.manda(200, temas())
                if p == ["grafico"]:
                    return self.manda(200, grafico(inteiro(qs.get("n"), 20, 1, 200)))
                if p[0] == "wiki" and len(p) == 2:
                    return self.manda(200, wiki(p[1]))
                if p == ["fila"]:
                    return self.manda(200, q("SELECT id, fonte, titulo, estado, erro, criado FROM bruto WHERE estado<>'pronto' ORDER BY criado DESC LIMIT 50"))
            if metodo == "POST":
                if p == ["gravar"] or p == ["ingest"]:
                    j = self.objeto()
                    fonte = j.get("fonte") if isinstance(j.get("fonte"), str) and j.get("fonte") in FONTES else ("manual" if p == ["gravar"] else None)
                    if not fonte:
                        raise ValueError("fonte inválida: " + ", ".join(FONTES))
                    bid, novo = entrar(fonte, j.get("ext_id"), j.get("titulo"), j.get("texto"), j.get("criado"), j.get("meta"))
                    return self.manda(200, {"bruto": bid, "novo": novo})
                if p == ["audio"]:
                    audio = self.corpo()
                    if len(audio) < 1000:
                        raise ValueError("áudio vazio")
                    texto = transcrever(audio, self.headers.get("Content-Type") or "audio/webm")
                    if len(texto) < 20:
                        return self.manda(200, {"texto": texto, "bruto": None, "obs": "fala curta demais, nada salvo"})
                    bid, _ = entrar("audio", None, qs.get("titulo") or "Gravação " + time.strftime("%d/%m %H:%M"), texto)
                    return self.manda(200, {"texto": texto, "bruto": bid})
                if p[0] == "wiki" and len(p) == 3 and p[2] == "compilar":
                    return self.manda(200, compilar(p[1]))
                if p[0] == "lembranca" and len(p) == 3 and p[2] in ("arquivar", "desarquivar"):
                    q("UPDATE lembranca SET arquivada=? WHERE id=?", (1 if p[2] == "arquivar" else 0, int(p[1])))
                    return self.manda(200, {"ok": True})
                if p[0] == "bruto" and len(p) == 3 and p[2] == "refazer":
                    limpar_bruto(int(p[1])); WAKE.set()
                    return self.manda(200, {"ok": True})
            return self.manda(404, {"erro": "rota não existe"})

        def estatico(self, path):
            nome = "anamnese-inicio.html" if path in ("/", "") else os.path.basename(path)
            f = os.path.join(WEB, nome)
            if not os.path.isfile(f):
                return self.manda(404, b"nao achei", "text/plain")
            ct = "text/html; charset=utf-8" if f.endswith(".html") else "application/javascript" if f.endswith(".js") else "application/octet-stream"
            with open(f, "rb") as fh:
                return self.manda(200, fh.read(), ct)
    return H


if __name__ == "__main__":
    init()
    threading.Thread(target=worker, daemon=True).start()
    ui = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("UI_PORT", "8590"))), handler(False))
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    log("Anamnese", VERSAO, "telas :", ui.server_port, "| api+mcp com token :", os.environ.get("API_PORT", "8591"))
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("API_PORT", "8591"))), handler(True)).serve_forever()
