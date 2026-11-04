#!/usr/bin/env bash
# read_key.sh - Leitura e gerenciamento seguro da chave gerada pelo Supermemory Local
# Sem expor a chave em stdout aberto ou logs acidentalmente.
set -euo pipefail

# Localiza arquivo de chave local
KEY_FILE="${SUPERMEMORY_KEY_FILE:-}"
if [ -z "$KEY_FILE" ]; then
    for candidate in \
        "/data/local_key" \
        "./data/local_key" \
        "/home/umbrel/umbrel/app-data/thainan-supermemory/data/local_key" \
        "$HOME/.config/supermemory/local_key"; do
        if [ -f "$candidate" ]; then
            KEY_FILE="$candidate"
            break
        fi
    done
fi

action="${1:-status}"

case "$action" in
    status)
        echo "=== Status da Chave Supermemory Local ==="
        if [ -n "$KEY_FILE" ] && [ -s "$KEY_FILE" ]; then
            perms=$(stat -c '%a' "$KEY_FILE" 2>/dev/null || stat -f '%Op' "$KEY_FILE" 2>/dev/null || echo "600")
            size=$(wc -c < "$KEY_FILE" | tr -d ' ')
            echo "Arquivo: $KEY_FILE"
            echo "Status: Presente ($size bytes, permissão $perms)"
            echo
            echo "Para usar em scripts sem imprimir na tela:"
            echo "  eval \$(./read_key.sh export)"
            echo "Para gravar em um arquivo .env com permissão 600:"
            echo "  ./read_key.sh to-env /caminho/para/meu.env"
        else
            echo "Arquivo de chave ainda não encontrado ou vazio."
            echo "O Supermemory Local gera a chave no primeiro boot."
            exit 1
        fi
        ;;

    export)
        if [ -n "$KEY_FILE" ] && [ -s "$KEY_FILE" ]; then
            key=$(head -n 1 "$KEY_FILE" | tr -d '\r\n')
            printf "export SUPERMEMORY_API_KEY='%s'\n" "$key"
        else
            echo "echo 'ERROR: local_key nao encontrado' >&2; return 1"
            exit 1
        fi
        ;;

    to-env)
        target="${2:-}"
        if [ -z "$target" ]; then
            echo "Uso: $0 to-env /caminho/destino.env" >&2
            exit 1
        fi
        if [ -n "$KEY_FILE" ] && [ -s "$KEY_FILE" ]; then
            key=$(head -n 1 "$KEY_FILE" | tr -d '\r\n')
            umask 077
            mkdir -p "$(dirname "$target")"
            if [ -f "$target" ]; then
                grep -v "^SUPERMEMORY_API_KEY=" "$target" > "${target}.tmp" 2>/dev/null || true
                mv "${target}.tmp" "$target"
            fi
            printf "SUPERMEMORY_API_KEY=%s\n" "$key" >> "$target"
            chmod 600 "$target"
            echo "Chave gravada em $target (modo 600) com sucesso."
        else
            echo "ERROR: local_key não encontrado em $KEY_FILE" >&2
            exit 1
        fi
        ;;

    show)
        if [ "${2:-}" = "--force" ] || [ -t 1 ]; then
            if [ -n "$KEY_FILE" ] && [ -s "$KEY_FILE" ]; then
                cat "$KEY_FILE"
            else
                echo "ERROR: local_key não encontrado." >&2
                exit 1
            fi
        else
            echo "ERROR: 'show' rejeitado porque o stdout não é um terminal tty interativo (evita vazamento em logs)." >&2
            echo "Use './read_key.sh to-env <arquivo>' ou './read_key.sh show --force' conscientemente." >&2
            exit 1
        fi
        ;;

    *)
        echo "Uso: $0 {status|export|to-env <arquivo>|show}"
        exit 1
        ;;
esac
