#!/usr/bin/env python3
"""
filter_stream.py - Filtra o stdout/stderr do supermemory-server em tempo real:
1. Salva a chave 'sm_...' gerada no boot em /data/local_key (modo 600, umask 077) se não existir.
2. Substitui qualquer ocorrência de sm_... por sm_***REDACTED*** na saída para não vazar nos logs do container.
"""

import sys
import os
import re

KEY_FILE = os.environ.get("SUPERMEMORY_KEY_FILE", "/data/local_key")
KEY_REGEX = re.compile(r"sm_[A-Za-z0-9_-]{20,}")

def save_key(key: str) -> None:
    target_dir = os.path.dirname(KEY_FILE)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    if not os.path.exists(KEY_FILE) or os.path.getsize(KEY_FILE) == 0:
        try:
            old_umask = os.umask(0o077)
            with open(KEY_FILE, "w", encoding="utf-8") as f:
                f.write(key.strip() + "\n")
            os.umask(old_umask)
            os.chmod(KEY_FILE, 0o600)
            sys.stderr.write(f"[filter_stream] Chave local salva com sucesso em {KEY_FILE} (modo 600).\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"[filter_stream] Erro ao salvar chave em {KEY_FILE}: {e}\n")
            sys.stderr.flush()

def main():
    try:
        for line in sys.stdin:
            matches = KEY_REGEX.findall(line)
            if matches:
                save_key(matches[0])
                line = KEY_REGEX.sub("sm_***REDACTED***", line)
            sys.stdout.write(line)
            sys.stdout.flush()
    except (KeyboardInterrupt, BrokenPipeError):
        pass

if __name__ == "__main__":
    main()
