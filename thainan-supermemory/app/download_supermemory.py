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
DOWNLOAD_URL = "https://github.com/supermemoryai/supermemory/releases/download/server-v0.0.9/supermemory-server-linux-x64"
DEFAULT_BIN_PATH = "/data/bin/supermemory-server"

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

    if check_sha256(bin_path):
        os.chmod(bin_path, 0o755)
        print(f"[download_supermemory] Binário oficial supermemory-server já presente e verificado em {bin_path}.", flush=True)
        return

    print(f"[download_supermemory] Baixando binário oficial Linux x64 de {DOWNLOAD_URL}...", flush=True)
    tmp_path = bin_path + ".tmp"
    h = hashlib.sha256()
    total_bytes = 0

    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "umbrel-thainan-supermemory/1.0.0"})
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

if __name__ == "__main__":
    main()
