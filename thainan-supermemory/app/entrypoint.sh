#!/usr/bin/env bash
set -eo pipefail

echo "[entrypoint] Iniciando Thainan Supermemory Local..."

APP_DIR="${APP_DIR:-/data/app}"
if [ ! -f "$APP_DIR/llmproxy.py" ] && [ -f "/app/llmproxy.py" ]; then
    APP_DIR="/app"
fi

DATA_DIR="${SUPERMEMORY_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR/bin" "$DATA_DIR/models" "$DATA_DIR/cache"

# Limpa logs antigos para manter tamanho sob controle se necessário
[ -f "$DATA_DIR/llmproxy.log" ] && tail -n 500 "$DATA_DIR/llmproxy.log" > "$DATA_DIR/llmproxy.log.tmp" && mv "$DATA_DIR/llmproxy.log.tmp" "$DATA_DIR/llmproxy.log" 2>/dev/null || true

# 1. Carrega chave do 9Router (OPENAI_API_KEY) de forma segura
if [ -z "${OPENAI_API_KEY:-}" ]; then
    for kf in "$DATA_DIR/niner_key" "$DATA_DIR/openai_api_key" "$DATA_DIR/llm.env"; do
        if [ -s "$kf" ]; then
            # Lê chave sem ecoar em logs
            if grep -q '^NINER_KEY=' "$kf" 2>/dev/null; then
                OPENAI_API_KEY=$(grep '^NINER_KEY=' "$kf" | cut -d= -f2- | tr -d '\r\n ')
            elif grep -q '^OPENAI_API_KEY=' "$kf" 2>/dev/null; then
                OPENAI_API_KEY=$(grep '^OPENAI_API_KEY=' "$kf" | cut -d= -f2- | tr -d '\r\n ')
            else
                OPENAI_API_KEY=$(head -n 1 "$kf" | tr -d '\r\n ')
            fi
            export OPENAI_API_KEY
            echo "[entrypoint] Chave do 9Router carregada de $kf com sucesso."
            break
        fi
    done
    if [ -z "${OPENAI_API_KEY:-}" ] && [ -s "/vkb/config.json" ]; then
        OPENAI_API_KEY=$(python3 -c "import json; print(json.load(open('/vkb/config.json')).get('llm_key', ''))" 2>/dev/null || true)
        if [ -n "$OPENAI_API_KEY" ]; then
            export OPENAI_API_KEY
            echo "[entrypoint] Chave do 9Router importada de /vkb/config.json com sucesso."
        fi
    fi
fi
if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "[entrypoint] AVISO: Chave do 9Router não configurada."
    echo "[entrypoint] Crie ${DATA_DIR}/niner_key contendo a chave do 9Router para habilitar reescrita e resumos por IA."
fi

# 2. Configura variáveis de ambiente do supermemory-server e llmproxy
export SUPERMEMORY_DISABLE_TELEMETRY=1
export SUPERMEMORY_NO_UPDATE_CHECK=1
export NO_UPDATE_CHECK=1
export SUPERMEMORY_NO_OPEN=1
export NO_OPEN=1
export SUPERMEMORY_HOST=0.0.0.0
export HOST=0.0.0.0
export PORT=6767
export SUPERMEMORY_DATA_DIR="$DATA_DIR"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:20129/v1}"
export OPENAI_MODEL="${OPENAI_MODEL:-ag/gemini-3.8-flash-high}"
export TARGET_BASE_URL="${TARGET_BASE_URL:-http://192.168.18.171:20128}"
export SUPERMEMORY_KEY_FILE="$DATA_DIR/local_key"

# 4. Inicia llmproxy em background
echo "[entrypoint] Iniciando llmproxy (porta 20129)..."
python3 -u "$APP_DIR/llmproxy.py" >> "$DATA_DIR/llmproxy.log" 2>&1 &
PROXY_PID=$!

# Aguarda llmproxy responder
for i in $(seq 1 15); do
    if python3 -c "import socket; s = socket.socket(); s.connect(('127.0.0.1', 20129)); s.close()" 2>/dev/null; then
        echo "[entrypoint] llmproxy pronto e ouvindo em 127.0.0.1:20129."
        break
    fi
    sleep 0.5
done

# 5. Baixa / verifica binário oficial do supermemory-server
echo "[entrypoint] Verificando binário do supermemory-server..."
SUPERMEMORY_BIN_PATH="$DATA_DIR/bin/supermemory-server" python3 "$APP_DIR/download_supermemory.py"

# 6. Prepara finalização graciosa (trap SIGTERM / SIGINT)
cleanup() {
    echo "[entrypoint] Recebido sinal de parada. Encerrando serviços..."
    if [ -n "${SERVER_PID:-}" ]; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
    fi
    if [ -n "${PROXY_PID:-}" ]; then
        kill -TERM "$PROXY_PID" 2>/dev/null || true
    fi
    if [ -n "${FILTER_PID:-}" ]; then
        kill -TERM "$FILTER_PID" 2>/dev/null || true
    fi
    wait "${SERVER_PID:-}" 2>/dev/null || true
    wait "${PROXY_PID:-}" 2>/dev/null || true
    echo "[entrypoint] Serviços finalizados."
    exit 0
}
trap cleanup SIGTERM SIGINT SIGQUIT

# 7. Inicia supermemory-server com saída filtrada para redação da chave
FIFO=/tmp/sm_stdout.fifo
rm -f "$FIFO"
mkfifo "$FIFO"

python3 -u "$APP_DIR/filter_stream.py" < "$FIFO" &
FILTER_PID=$!

echo "[entrypoint] Iniciando supermemory-server na porta 6767..."
stdbuf -oL -eL "$DATA_DIR/bin/supermemory-server" > "$FIFO" 2>&1 &
SERVER_PID=$!

# Aguarda supermemory-server finalizar
wait "$SERVER_PID"
