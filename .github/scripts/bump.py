#!/usr/bin/env python3
"""Auto-atualização da Thainan Appstore.
Para cada app em auto-update.yml:
- modo "tag": acha a maior versão estável do mesmo formato no registry e troca a tag.
- modo "digest": imagem :latest fica presa no digest atual (name:latest@sha256:...); quando o digest muda, troca.
Mudou imagem -> sobe "version" do umbrel-app.yml (tag: vira a própria versão; digest: +1 no patch) e escreve releaseNotes.
Umbrel vê a versão nova e mostra Atualizar."""
import json, re, sys, urllib.request, os, datetime

ACCEPT = ",".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                   "application/vnd.docker.distribution.manifest.v2+json", "application/vnd.oci.image.manifest.v1+json"])


def get(url, headers=None, method="GET"):
    rq = urllib.request.Request(url, headers=headers or {}, method=method)
    with urllib.request.urlopen(rq, timeout=30) as r:
        return r.headers, r.read()


def split(ref):
    ref = ref.split("@")[0]
    name, tag = (ref.rsplit(":", 1) + ["latest"])[:2] if ":" in ref.split("/")[-1] else (ref, "latest")
    parts = name.split("/")
    if "." in parts[0] or ":" in parts[0]:
        reg, repo = parts[0], "/".join(parts[1:])
    else:
        reg, repo = "registry-1.docker.io", name if "/" in name else "library/" + name
    return reg, repo, tag


def token(reg, repo):
    if reg == "registry-1.docker.io":
        u = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    elif reg == "ghcr.io":
        u = f"https://ghcr.io/token?scope=repository:{repo}:pull"
    elif reg == "quay.io":
        return None
    else:
        return None
    return json.loads(get(u)[1])["token"]


def auth(reg, repo):
    t = token(reg, repo)
    return {"Authorization": "Bearer " + t} if t else {}


def tags(reg, repo):
    h, out, url = auth(reg, repo), [], f"https://{reg}/v2/{repo}/tags/list?n=1000"
    while url:
        hd, body = get(url, h)
        out += json.loads(body).get("tags") or []
        m = re.search(r'<([^>]+)>;\s*rel="next"', hd.get("Link", "") or "")
        url = (f"https://{reg}" + m[1] if m and m[1].startswith("/") else m[1]) if m else None
    return out


def digest(reg, repo, tag):
    hd, _ = get(f"https://{reg}/v2/{repo}/manifests/{tag}", {**auth(reg, repo), "Accept": ACCEPT}, "HEAD")
    return hd.get("Docker-Content-Digest")


def ver(t):
    m = re.fullmatch(r"(v?)(\d+)\.(\d+)\.(\d+)", t)
    return (m[1], tuple(int(x) for x in m.groups()[1:])) if m else None


def newest(cur, all_tags):
    c = ver(cur)
    if not c:
        return None
    cands = [t for t in all_tags if (v := ver(t)) and v[0] == c[0]]
    best = max(cands, key=lambda t: ver(t)[1], default=None)
    return best if best and ver(best)[1] > c[1] else None


def load_cfg(path):
    apps, cur = {}, None
    for line in open(path):
        line = line.split("#")[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            cur = line[:-1].strip(); apps[cur] = {}
        elif cur and ":" in line:
            k, v = line.strip().split(":", 1); apps[cur][k.strip()] = v.strip().strip('"')
    return apps


def bump_patch(v):
    m = re.fullmatch(r"(.*?)(\d+)$", v)
    return f"{m[1]}{int(m[2]) + 1}" if m else v + ".1"


def main():
    changed = []
    for app, c in load_cfg("auto-update.yml").items():
        comp_p, meta_p = f"{app}/docker-compose.yml", f"{app}/umbrel-app.yml"
        if not os.path.exists(comp_p):
            continue
        comp, meta = open(comp_p).read(), open(meta_p).read()
        image = c["image"]
        m = re.search(r"image:\s*(" + re.escape(image) + r"(?::[\w.\-]+)?(?:@sha256:[0-9a-f]+)?)\s*$", comp, re.M)
        if not m:
            print(f"{app}: imagem {image} não achada"); continue
        ref = m[1]
        reg, repo, tag = split(ref)
        try:
            if c.get("mode", "tag") == "tag":
                novo_tag = newest(tag, tags(reg, repo))
                if not novo_tag:
                    print(f"{app}: {tag} já é a mais nova"); continue
                novo_ref, nova_ver = f"{image}:{novo_tag}", novo_tag.lstrip("v")
                nota = f"Atualizado automaticamente para {novo_tag}."
            else:
                d = digest(reg, repo, tag)
                if not d or ref.endswith(d):
                    print(f"{app}: {tag} sem mudança"); continue
                novo_ref = f"{image}:{tag}@{d}"
                mv = re.search(r'^version:\s*"?([^"\n]+)"?', meta, re.M)
                nova_ver = bump_patch(mv[1]) if "@sha256:" in ref else bump_patch(mv[1])
                nota = f"Nova imagem {tag} publicada pelo projeto ({d[:19]})."
        except Exception as e:
            print(f"{app}: erro {e}"); continue
        comp = comp.replace(ref, novo_ref)
        meta = re.sub(r'^version:.*$', f'version: "{nova_ver}"', meta, count=1, flags=re.M)
        nota += f" ({datetime.date.today().isoformat()})"
        if re.search(r"^releaseNotes:", meta, re.M):
            meta = re.sub(r"^releaseNotes:.*?(?=^\S|\Z)", f'releaseNotes: >-\n  {nota}\n', meta, count=1, flags=re.M | re.S)
        else:
            meta = meta.rstrip("\n") + f'\nreleaseNotes: >-\n  {nota}\n'
        open(comp_p, "w").write(comp); open(meta_p, "w").write(meta)
        changed.append(f"{app} -> {nova_ver}")
        print(f"{app}: {ref} -> {novo_ref} (versão {nova_ver})")
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write("changed=" + "; ".join(changed) + "\n")


if __name__ == "__main__":
    main()
