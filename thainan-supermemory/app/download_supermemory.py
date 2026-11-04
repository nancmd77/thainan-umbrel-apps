#!/usr/bin/env python3
"""
download_supermemory.py - Garante a presença e integridade do binário oficial
supermemory-server (Linux x64) da release server-v0.0.9.
"""

import hashlib
import os
import sys
import urllib.request

EXPECTED_SHA256 = "d7987ecbf2b024c6de9d167becbcfcaabce447169b41d6f56db86f87d5187984"
PATCHED_SHA256 = "f34ef6a99d263a9a11135b3ac2a0da99cc2ded929ab0cbbd2eb73168cbd14271"
DOWNLOAD_URL = "https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.9/supermemory-server-linux-x64"
DEFAULT_BIN_PATH = "/data/bin/supermemory-server"

ORIGINAL_TARGET = b"noSleep:x.boolean().default(!1)"
PATCHED_TARGET = b"noSleep:x.boolean().default(!0)"

def patch_binary(bin_path: str) -> bool:
    """
    Aplica patch de mesmo tamanho no binário trocando ORIGINAL_TARGET por PATCHED_TARGET (uma vez).
    Garante idempotência se o binário já estiver patchado.
    Escreve via arquivo temporário + os.replace e mantém chmod 755.
    Emite aviso alto se o alvo não for encontrado nem a versão já patchada.
    """
    if not os.path.exists(bin_path):
        print(f"[download_supermemory] AVISO: Arquivo {bin_path} não encontrado para aplicação de patch.", file=sys.stderr, flush=True)
        return False

    with open(bin_path, "rb") as f:
        content = f.read()

    if ORIGINAL_TARGET in content:
        patched_content = content.replace(ORIGINAL_TARGET, PATCHED_TARGET, 1)
        if len(patched_content) != len(content):
            raise RuntimeError(f"Tamanho inconsistente após patch: original {len(content)}, patch {len(patched_content)}")
        tmp_path = bin_path + ".patch.tmp"
        with open(tmp_path, "wb") as f:
            f.write(patched_content)
        os.replace(tmp_path, bin_path)
        os.chmod(bin_path, 0o755)
        print(
            f"[download_supermemory] Patch aplicado com sucesso em {bin_path}: "
            f"trocado '{ORIGINAL_TARGET.decode('latin1', errors='replace')}' por '{PATCHED_TARGET.decode('latin1', errors='replace')}'.",
            flush=True,
        )
        return True
    elif PATCHED_TARGET in content:
        os.chmod(bin_path, 0o755)
        print(
            f"[download_supermemory] Binário em {bin_path} já está com o patch aplicado.",
            flush=True,
        )
        return True
    else:
        print(
            f"[download_supermemory] AVISO: Alvo do patch {ORIGINAL_TARGET!r} não encontrado "
            f"e versão já patchada {PATCHED_TARGET!r} também não encontrada em {bin_path}!",
            file=sys.stderr,
            flush=True,
        )
        return False

def check_cache_and_patch(bin_path: str) -> bool:
    """
    Verifica se o binário já existe em cache e é válido.
    Aplica patch caso esteja no formato original não patchado.
    Retorna True se o binário estiver pronto para uso em cache.
    """
    if not os.path.exists(bin_path) or os.path.getsize(bin_path) == 0:
        return False

    h = hashlib.sha256()
    with open(bin_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    digest = h.hexdigest()

    if digest == EXPECTED_SHA256:
        print(f"[download_supermemory] Binário oficial supermemory-server já presente em cache em {bin_path}.", flush=True)
        return patch_binary(bin_path)

    if digest == PATCHED_SHA256:
        os.chmod(bin_path, 0o755)
        print(f"[download_supermemory] Binário supermemory-server patchado já presente e verificado em {bin_path}.", flush=True)
        return True

    try:
        with open(bin_path, "rb") as f:
            content = f.read()
        if ORIGINAL_TARGET in content or PATCHED_TARGET in content:
            print(f"[download_supermemory] Binário supermemory-server detectado em {bin_path}. Verificando patch...", flush=True)
            return patch_binary(bin_path)
    except Exception:
        pass

    return False
def check_sha256(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest() == EXPECTED_SHA256

def main():
    bin_path = os.environ.get("SUPERMEMORY_BIN_PATH", DEFAULT_BIN_PATH)
    bin_dir = os.path.dirname(bin_path)
    os.makedirs(bin_dir, exist_ok=True)

    if check_cache_and_patch(bin_path):
        return
    print(f"[download_supermemory] Baixando binário oficial Linux x64 de {DOWNLOAD_URL}...", flush=True)
    tmp_path = bin_path + ".tmp"
    h = hashlib.sha256()
    total_bytes = 0

    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "umbrel-thainan-supermemory/1.0.3"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp_path, "wb") as f:
        while chunk := resp.read(65536):
            h.update(chunk)
            f.write(chunk)
            total_bytes += len(chunk)

    digest = h.hexdigest()
    if digest != EXPECTED_SHA256:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError(f"Checksum mismatch! Esperado {EXPECTED_SHA256}, obtido {digest}")

    os.replace(tmp_path, bin_path)
    os.chmod(bin_path, 0o755)
    print(f"[download_supermemory] Download concluído com sucesso ({total_bytes} bytes). SHA256 verificado: {digest}", flush=True)

    patch_binary(bin_path)

if __name__ == "__main__":
    main()
