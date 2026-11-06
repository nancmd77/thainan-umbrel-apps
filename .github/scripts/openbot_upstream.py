#!/usr/bin/env python3
"""Detecta e aplica nova versão upstream do OpenBot (nightly-labs/openbot) no thainan-openbot.

Subcomandos:
  detect  [--root .] [--repo nightly-labs/openbot] [--current v0.62.0]
          Consulta a última release do GitHub e compara com a versão do FROM.
          Se for maior, resolve o digest do manifest list no GHCR (anônimo).
  apply   --root . --version 0.63.0 --digest sha256:... [--revision 1]
          [--release-url URL] [--notes TEXTO]
          Reescreve FROM, image/VERSION, docker-compose.yml (tag sem digest),
          umbrel-app.yml (version e releaseNotes). Não faz commit.
  pin     --root . --digest sha256:...
          Fixa o digest da imagem construída no docker-compose.yml.

Saída do detect: pares chave=valor em stdout e em $GITHUB_OUTPUT (se existir).
Token: opcional, lido da variável de ambiente GITHUB_TOKEN; nunca impresso.
Só biblioteca padrão do Python.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_DEFAULT = "nightly-labs/openbot"
APP_DIR = "thainan-openbot"
DOCKERFILE = f"{APP_DIR}/image/Dockerfile"
VERSION_FILE = f"{APP_DIR}/image/VERSION"
COMPOSE = f"{APP_DIR}/docker-compose.yml"
MANIFEST = f"{APP_DIR}/umbrel-app.yml"
IMAGE_REPO = "ghcr.io/nancmd77/thainan-openbot"
UA = "thainan-umbrel-apps-openbot-upstream"
MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

FROM_RE = re.compile(
    r"^FROM ghcr\.io/nightly-labs/openbot:(v\d+\.\d+\.\d+)@(sha256:[0-9a-f]{64})[ \t]*$",
    re.M,
)
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
COMPOSE_IMAGE_RE = re.compile(
    r"^(\s*image: ghcr\.io/nancmd77/thainan-openbot:)([^\s@]+)(?:@sha256:[0-9a-f]{64})?[ \t]*$",
    re.M,
)
VERSION_LINE_RE = re.compile(r'^version: "[^"]+"[ \t]*$', re.M)
NOTES_RE = re.compile(r"^releaseNotes: >-\n(?:[ \t].*\n|\n)*", re.M)


def parse_version(tag: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag.strip())
    if not m:
        raise SystemExit(f"versão não reconhecida: {tag!r}")
    return int(m[1]), int(m[2]), int(m[3])


def http_json(url: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.load(r)


def ghcr_digest(repo: str, tag: str) -> str:
    """Digest do manifest (ou index) publicado no GHCR, via token anônimo."""
    scope = urllib.parse.urlencode({"scope": f"repository:{repo}:pull"})
    with urllib.request.urlopen(f"https://ghcr.io/token?{scope}", timeout=30) as r:
        token = json.load(r)["token"]
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{repo}/manifests/{tag}",
        method="HEAD",
        headers={"Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT, "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        digest = r.headers.get("Docker-Content-Digest", "")
    if not DIGEST_RE.fullmatch(digest):
        raise SystemExit(f"digest ausente ou inválido para {repo}:{tag}")
    return digest


def current_from_dockerfile(root: Path) -> str:
    m = FROM_RE.search((root / DOCKERFILE).read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"FROM do OpenBot não encontrado em {DOCKERFILE}")
    return m[1]


def emit(values: dict[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")
    out_file = os.environ.get("GITHUB_OUTPUT")
    if out_file:
        with open(out_file, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")


def cmd_detect(a: argparse.Namespace) -> None:
    root = Path(a.root)
    current = a.current or current_from_dockerfile(root)
    rel = http_json(f"https://api.github.com/repos/{a.repo}/releases/latest")
    latest = rel["tag_name"]
    out = {"current": current, "latest": latest, "changed": "false", "release_url": rel["html_url"]}
    if parse_version(latest) > parse_version(current):
        out["changed"] = "true"
        out["new_version"] = latest.lstrip("v")
        out["digest"] = ghcr_digest(a.repo, latest)
    emit(out)


def replace_once(text: str, pattern: re.Pattern, repl, label: str) -> str:
    new, n = pattern.subn(repl, text)
    if n != 1:
        raise SystemExit(f"{label}: esperado 1 ocorrência, achei {n}")
    return new


def cmd_apply(a: argparse.Namespace) -> None:
    root = Path(a.root)
    nv = a.version.lstrip("v")
    parse_version(nv)
    revision = f"{nv}-r{a.revision}"
    if not DIGEST_RE.fullmatch(a.digest):
        raise SystemExit("digest inválido")

    df = root / DOCKERFILE
    df.write_text(
        replace_once(
            df.read_text(encoding="utf-8"),
            FROM_RE,
            lambda m: f"FROM ghcr.io/nightly-labs/openbot:v{nv}@{a.digest}",
            DOCKERFILE,
        ),
        encoding="utf-8",
    )

    (root / VERSION_FILE).write_text(revision + "\n", encoding="utf-8")

    compose = root / COMPOSE
    compose.write_text(
        replace_once(
            compose.read_text(encoding="utf-8"),
            COMPOSE_IMAGE_RE,
            lambda m: f"{m[1]}{revision}",
            COMPOSE,
        ),
        encoding="utf-8",
    )

    notes = a.notes or (
        f"Versão {revision}: base OpenBot atualizada para v{nv} (upstream nightly-labs). "
        f"Notas completas da release: {a.release_url or 'ver GitHub nightly-labs/openbot'}."
    )
    manifest = root / MANIFEST
    text = replace_once(
        manifest.read_text(encoding="utf-8"),
        VERSION_LINE_RE,
        f'version: "{revision}"',
        MANIFEST,
    )
    text = replace_once(
        text,
        NOTES_RE,
        f"releaseNotes: >-\n  {notes}\n",
        f"{MANIFEST} releaseNotes",
    )
    manifest.write_text(text, encoding="utf-8")
    print(f"aplicado: {revision} (FROM v{nv})")


def cmd_pin(a: argparse.Namespace) -> None:
    root = Path(a.root)
    if not DIGEST_RE.fullmatch(a.digest):
        raise SystemExit("digest inválido")
    revision = (root / VERSION_FILE).read_text(encoding="utf-8").strip()
    compose = root / COMPOSE
    compose.write_text(
        replace_once(
            compose.read_text(encoding="utf-8"),
            COMPOSE_IMAGE_RE,
            lambda m: f"{m[1]}{revision}@{a.digest}",
            COMPOSE,
        ),
        encoding="utf-8",
    )
    print(f"fixado: {IMAGE_REPO}:{revision}@{a.digest}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect")
    d.add_argument("--root", default=".")
    d.add_argument("--repo", default=REPO_DEFAULT)
    d.add_argument("--current", help="versão atual (ex.: v0.62.0); padrão: lê o FROM do Dockerfile")
    d.set_defaults(func=cmd_detect)

    ap = sub.add_parser("apply")
    ap.add_argument("--root", default=".")
    ap.add_argument("--version", required=True)
    ap.add_argument("--digest", required=True)
    ap.add_argument("--revision", type=int, default=1)
    ap.add_argument("--release-url", default="")
    ap.add_argument("--notes", default="")
    ap.set_defaults(func=cmd_apply)

    pi = sub.add_parser("pin")
    pi.add_argument("--root", default=".")
    pi.add_argument("--digest", required=True)
    pi.set_defaults(func=cmd_pin)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, OSError) as exc:
        print(f"erro de rede/arquivo: {exc}", file=sys.stderr)
        sys.exit(1)
