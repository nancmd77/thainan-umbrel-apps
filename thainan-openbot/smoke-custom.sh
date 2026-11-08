#!/usr/bin/env bash
# Smoke das customizações do thainan-openbot numa imagem já buildada, antes da tag final.
# Uso: smoke-custom.sh [IMAGEM]   (padrão: ghcr.io/nancmd77/thainan-openbot:0.63.0-r1)
# Usa `docker`; se o daemon recusar o usuário atual, tenta `sudo -n docker`.
#
# 1. Imagem: o que ela de fato contém (uid 1000, md, decide, tini, openbox/xdotool/scrot, sudo, AT-SPI).
# 2. Customizações: roda o próprio serviço `init` do docker-compose.yml com APP_DATA_DIR num diretório
#    temporário e valida /data/.agents, o bloco Jeito Hark no AGENTS.md de um agente, /data/bin/sm e a chave.
set -euo pipefail

IMAGE=${1:-ghcr.io/nancmd77/thainan-openbot:0.63.0-r1}
HERE=$(cd "$(dirname "$0")" && pwd)
COMPOSE_FILE="$HERE/docker-compose.yml"

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker)
else
  echo "docker indisponível para o usuário atual" >&2
  exit 2
fi

WORK=$(mktemp -d)
PROJ="smoke-custom-$$"
cleanup() {
  # Arquivos criados pelo init (uid 1000/root) são removidos por um container, sem sudo.
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  "${DOCKER[@]}" run --rm -v "$WORK:/w" alpine:3.21 sh -c 'rm -rf /w/* /w/.[!.]*' >/dev/null 2>&1 || true
  rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT

# Estrutura que o compose espera ao redor de APP_DATA_DIR ($WORK/app).
mkdir -p "$WORK/app/data/OpenBot/Agents/smoke-agente" "$WORK/app/run" "$WORK/app/config" \
  "$WORK/thainan-video-kb/data" "$WORK/thainan-supermemory/data"
printf '# Agente de smoke\n' > "$WORK/app/data/OpenBot/Agents/smoke-agente/AGENTS.md"
# Chave falsa só para testar o caminho de cópia; nunca é impressa.
printf 'chave-falsa-de-smoke\n' > "$WORK/thainan-supermemory/data/local_key"
printf 'APP_DATA_DIR=%s\n' "$WORK/app" > "$WORK/compose.env"
# O app_proxy do compose commitado não tem image nem build, e o compose recusa o projeto inteiro.
# O override só dá uma imagem a ele, para validar; o serviço init segue o arquivo real.
printf 'services:\n  app_proxy:\n    image: alpine:3.21\n' > "$WORK/override.yml"
COMPOSE=("${DOCKER[@]}" compose --env-file "$WORK/compose.env" -p "$PROJ" -f "$COMPOSE_FILE" -f "$WORK/override.yml")

echo "== pull $IMAGE"
"${DOCKER[@]}" pull "$IMAGE" >/dev/null

echo "== init (serviço real do docker-compose.yml)"
if ! "${COMPOSE[@]}" run -T --rm init >"$WORK/init.log" 2>&1; then
  tail -n 40 "$WORK/init.log" >&2
  echo "FALHA: serviço init terminou com erro" >&2
  exit 1
fi

echo "== checagens dentro da imagem (uid do usuário da imagem)"
set +e
"${DOCKER[@]}" run --rm -i \
  -v "$WORK/app/data:/data:ro" -v "$WORK/app/config:/config:ro" \
  --entrypoint /bin/bash "$IMAGE" -s <<'CHECKS'
fails=0
check() { local nome=$1; shift; if "$@" >/dev/null 2>&1; then echo "OK    $nome"; else echo "FALHA $nome"; fails=$((fails+1)); fi; }
bins() { for b in "$@"; do command -v "$b" >/dev/null || return 1; done; }
skill() { test -s "/data/.agents/skills/$1/SKILL.md" && grep -qx "name: $1" "/data/.agents/skills/$1/SKILL.md"; }
sm_ok() { python3 /data/bin/sm --help | grep -q 'sm buscar'; }
conselho_ok() { python3 /data/bin/conselho --help | grep -q 'conselho'; }
chave_ok() { test -s /config/supermemory.key && test "$(stat -c %a /config/supermemory.key)" = 600; }
no_at() { test -z "${NO_AT_BRIDGE+x}"; }

# imagem
check "uid 1000" test "$(id -u)" = 1000
check "openbot-container" test -x /opt/OpenBot/app/resources/hosting/openbot-container
check "tini" test -x /usr/bin/tini
check "md" test -x /usr/local/bin/md
check "decide" test -x /usr/local/bin/decide
check "openbox/xdotool/scrot" bins openbox xdotool scrot
check "sudo sem senha" sudo -n true
check "at-spi-bus-launcher instalado" test -x /usr/libexec/at-spi-bus-launcher -o -x /usr/lib/at-spi2-core/at-spi-bus-launcher
check "NO_AT_BRIDGE não definido no shell" no_at

# customizações geradas pelo init
for s in jeito-hark cursor-hark openui-hark supermemory; do check "skill $s" skill "$s"; done
check "cursor-hark cursor.js" test -s /data/.agents/skills/cursor-hark/cursor.js
check "openui-hark montar.py" test -s /data/.agents/skills/openui-hark/montar.py
check "bloco Jeito Hark no AGENTS.md do agente" grep -q 'jeito-hark:inicio' /data/OpenBot/Agents/smoke-agente/AGENTS.md
check "/data/AGENTS.md" test -s /data/AGENTS.md
check "/data/bin/sm executável" test -x /data/bin/sm
check "/data/bin/conselho executável" test -x /data/bin/conselho
check "CLI sm --help" sm_ok
check "CLI conselho --help" conselho_ok
check "chave copiada com modo 600" chave_ok

echo "falhas: $fails"
exit $fails
CHECKS
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  echo "FALHA: $rc checagem(ns) na imagem $IMAGE" >&2
  exit 1
fi
echo "smoke OK: $IMAGE"
