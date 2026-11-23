"""Partida do app: baixa/atualiza o agy (último release Linux x64 do GitHub) e sobe a ponte."""
import json, os, pwd, shutil, subprocess, tarfile, urllib.request

BIN = "/data/bin"; os.makedirs(BIN, exist_ok=True)
AGY = BIN + "/agy"; VER = BIN + "/VERSION"
PIN = os.environ.get("AGY_VERSION", "latest").strip()

def get(url, t=60):
    r = urllib.request.Request(url, headers={"User-Agent": "thainan-agy-ponte"})
    return urllib.request.urlopen(r, timeout=t)

try:
    if PIN == "latest":
        tag = json.load(get("https://api.github.com/repos/google-antigravity/antigravity-cli/releases/latest"))["tag_name"]
    else:
        tag = PIN
    cur = open(VER).read().strip() if os.path.exists(VER) else ""
    if tag != cur or not os.path.exists(AGY):
        url = f"https://github.com/google-antigravity/antigravity-cli/releases/download/{tag}/agy_cli_linux_x64.tar.gz"
        print("baixando agy", tag, flush=True)
        tmp = BIN + "/dl.tgz"
        with get(url, 600) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        with tarfile.open(tmp) as t:
            m = [x for x in t.getmembers() if x.isfile() and os.path.basename(x.name) in ("antigravity", "agy")][0]
            src = t.extractfile(m)
            with open(AGY + ".new", "wb") as f:
                shutil.copyfileobj(src, f)
        os.chmod(AGY + ".new", 0o755); os.replace(AGY + ".new", AGY); os.remove(tmp)
        open(VER, "w").write(tag)
    print("agy versão", open(VER).read().strip(), flush=True)
except Exception as e:
    print("não consegui atualizar o agy:", e, "(usando o que houver)", flush=True)

# usuário sem privilégio para rodar o agy
try:
    pwd.getpwnam("agy")
except KeyError:
    subprocess.run(["useradd", "-u", "1000", "-m", "-d", "/home/agy", "-s", "/bin/sh", "agy"], check=False)
for d in ("/data/home", "/tmp/agy-req"):
    os.makedirs(d, exist_ok=True)
    subprocess.run(["chown", "-R", "agy:agy", d], check=False)
os.chmod("/data/logs", 0o755) if os.path.exists("/data/logs") else None
os.execvp("python", ["python", "-u", "server.py"])
