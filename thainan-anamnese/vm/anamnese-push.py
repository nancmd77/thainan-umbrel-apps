#!/usr/bin/env python3
"""VM Roberta -> Anamnese: empurra as sessões encerradas dos agentes (Claude Code e omp) como texto bruto.
Roda pelo timer systemd --user a cada 15 min. Sessão parada há 10 min conta como encerrada; se continuar depois, sobe de novo
(a Anamnese refaz os segmentos e a IA substitui o que mudou). Token em ~/.sollum/anamnese.env (ANAMNESE_TOKEN=...)."""
import glob, json, os, time, urllib.request

URL = os.environ.get("ANAMNESE_URL", "http://10.203.0.1:8591/api/ingest")
H = os.path.expanduser("~")
ESTADO = os.path.join(H, ".sollum", "anamnese-push.json")
PASTAS = [H + "/.claude/projects/*/*.jsonl", H + "/.omp/agent/sessions/*/*.jsonl"]
PULA = ("claude-mem-observer", "__advisor")


def token():
    for l in open(os.path.join(H, ".sollum", "anamnese.env")):
        if l.startswith("ANAMNESE_TOKEN="):
            return l.split("=", 1)[1].strip()


def textos(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return [c]
    return [p.get("text", "") for p in c or [] if isinstance(p, dict) and p.get("type") == "text"]


def transcrever(path):
    linhas, primeira, ultima = [], None, None
    for l in open(path, errors="ignore"):
        try:
            j = json.loads(l)
        except ValueError:
            continue
        m = j.get("message") if isinstance(j.get("message"), dict) else j
        papel = m.get("role") or j.get("type")
        if papel not in ("user", "assistant"):
            continue
        t = "\n".join(x.strip() for x in textos(m) if x and x.strip())
        if not t or t.startswith(("<command-", "<local-command", "<system-reminder", "Caveat:")):
            continue
        ts = j.get("timestamp")
        primeira = primeira or ts
        ultima = ts or ultima
        linhas.append(("nan: " if papel == "user" else "agente: ") + t[:4000])
    return linhas, primeira


def main():
    est = json.load(open(ESTADO)) if os.path.exists(ESTADO) else {}
    tok, agora, n = token(), time.time(), 0
    for path in sorted(set(f for p in PASTAS for f in glob.glob(p))):
        if any(x in path for x in PULA):
            continue
        mt = os.path.getmtime(path)
        if agora - mt < 600 or est.get(path) == mt:
            continue
        linhas, ini = transcrever(path)
        texto = "\n\n".join(linhas)
        if sum(1 for x in linhas if x.startswith("nan: ")) < 2 or len(texto) < 400:
            est[path] = mt
            continue
        agente = "Claude Code" if "/.claude/" in path else "omp"
        projeto = os.path.basename(os.path.dirname(path)).replace("-home-roberta", "~").strip("-") or "~"
        corpo = {"fonte": "sessoes", "ext_id": path.replace(H, "~"), "titulo": f"{agente} · {projeto}",
                 "texto": texto[-60000:], "criado": mt, "meta": {"agente": agente, "projeto": projeto, "inicio": ini}}
        r = urllib.request.Request(URL, data=json.dumps(corpo).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            urllib.request.urlopen(r, timeout=60).read()
            est[path] = mt
            n += 1
        except Exception as e:
            print("falhou", path, e)
            break
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    json.dump(est, open(ESTADO, "w"))
    print("enviadas", n)


if __name__ == "__main__":
    main()
