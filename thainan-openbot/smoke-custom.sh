#!/usr/bin/env bash
# Smoke das customizações do thainan-openbot numa imagem já buildada, antes da tag final.
# Uso: smoke-custom.sh [IMAGEM]   (padrão: ghcr.io/nancmd77/thainan-openbot:0.63.0-r1)
# Usa `docker`; se o daemon recusar o usuário atual, tenta `sudo -n docker`.
#
# 1. Imagem: o que ela de fato contém (uid 1000, md, decide, tini, openbox/xdotool/scrot, sudo, AT-SPI).
# 2. Customizações: roda o próprio serviço `init` do docker-compose.yml com APP_DATA_DIR num diretório
#    temporário e valida /data/.agents (inclusive conselho, decide e moondream = front matter + skills/*.md),
#    o bloco Jeito Hark no AGENTS.md de agente novo e de agente com bloco antigo, a regra única do ESCALAR
#    em todos os textos dos agentes, /data/bin/sm e a chave.
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
mkdir -p "$WORK/app/data/OpenBot/Agents/smoke-agente" "$WORK/app/data/OpenBot/Agents/smoke-antigo" \
  "$WORK/app/run" "$WORK/app/config" "$WORK/thainan-video-kb/data" "$WORK/thainan-supermemory/data"
printf '# Agente de smoke\n' > "$WORK/app/data/OpenBot/Agents/smoke-agente/AGENTS.md"
# Agente que já tem o bloco gerenciado de uma versão antiga: o init troca o bloco e não mexe no texto do dono.
printf '# Agente antigo\nnota do dono\n<!-- jeito-hark:inicio (bloco antigo) -->\n- regra-antiga-do-bloco\n<!-- jeito-hark:fim -->\n' \
  > "$WORK/app/data/OpenBot/Agents/smoke-antigo/AGENTS.md"
# Chave falsa só para testar o caminho de cópia; nunca é impressa.
printf 'chave-falsa-de-smoke\n' > "$WORK/thainan-supermemory/data/local_key"
printf 'APP_DATA_DIR=%s\n' "$WORK/app" > "$WORK/compose.env"
# O app_proxy do compose commitado não tem image nem build (o Umbrel injeta o serviço), e o compose
# recusa o projeto inteiro. O override, só do teste, dá uma imagem a ele; o arquivo real não muda.
printf 'services:\n  app_proxy:\n    image: alpine:3.21\n' > "$WORK/override.yml"
COMPOSE=("${DOCKER[@]}" compose --env-file "$WORK/compose.env" -p "$PROJ" -f "$COMPOSE_FILE" -f "$WORK/override.yml")

echo "== docker compose config (com override do app_proxy)"
"${COMPOSE[@]}" config -q

echo "== pull $IMAGE"
"${DOCKER[@]}" pull "$IMAGE" >/dev/null 2>&1 || "${DOCKER[@]}" image inspect "$IMAGE" >/dev/null

echo "== init (serviço real do docker-compose.yml)"
if ! "${COMPOSE[@]}" run -T --rm init >"$WORK/init.log" 2>&1; then
  tail -n 40 "$WORK/init.log" >&2
  echo "FALHA: serviço init terminou com erro" >&2
  exit 1
fi

echo "== checagens dentro da imagem (uid do usuário da imagem)"
set +e
"${DOCKER[@]}" run --rm -i \
  -v "$WORK/app/data:/data:ro" -v "$WORK/app/config:/config:ro" -v "$HERE/skills:/skills:ro" \
  --entrypoint /bin/bash "$IMAGE" -s <<'CHECKS'
fails=0
check() { local nome=$1; shift; if "$@" >/dev/null 2>&1; then echo "OK    $nome"; else echo "FALHA $nome"; fails=$((fails+1)); fi; }
bins() { for b in "$@"; do command -v "$b" >/dev/null || return 1; done; }
skill() { test -s "/data/.agents/skills/$1/SKILL.md" && grep -qx "name: $1" "/data/.agents/skills/$1/SKILL.md"; }
fonte() { tail -n +6 "/data/.agents/skills/$1/SKILL.md" | cmp -s - "/skills/$1.md"; }  # 5 linhas de front matter + o texto de skills/<nome>.md
dono() { test "$(stat -c %u:%g "$1")" = 1000:1000; }
bloco_novo() { local f=/data/OpenBot/Agents/smoke-agente/AGENTS.md; test "$(grep -c 'jeito-hark:inicio' "$f")" = 1 && grep -qF 'dois nichados discordam, ou plano e revisão discordam' "$f"; }
bloco_trocado() { local f=/data/OpenBot/Agents/smoke-antigo/AGENTS.md; test "$(grep -c 'jeito-hark:inicio' "$f")" = 1 && ! grep -q 'regra-antiga-do-bloco' "$f" && grep -q 'nota do dono' "$f" && grep -qF 'dois nichados discordam, ou plano e revisão discordam' "$f"; }
sm_ok() { python3 /data/bin/sm --help | grep -q 'sm buscar'; }
conselho_ok() { python3 /data/bin/conselho --help | grep -q 'conselho'; }
chave_ok() { test -s /config/supermemory.key && test "$(stat -c %a /config/supermemory.key)" = 600; }
ad_versao() { agent-desktop version | grep -q '"ok":true'; }
# /data é só leitura aqui: o estado do agent-desktop vai pra /tmp. Sessão igual à do app (Xvfb :1, dbus de sessão, at-spi-bus-launcher, openbox); zenity com botão "Sim"; clica pelo ref e exige saída 0
ad_clique() {
  local s ref
  Xvfb :1 -screen 0 1280x800x24 -nolisten tcp >/dev/null 2>&1 &
  sleep 1.5
  export DISPLAY=:1
  eval "$(dbus-launch --sh-syntax)"
  ( /usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 & )
  sleep 1.5
  openbox >/dev/null 2>&1 &
  ( zenity --question --title=smoke-ad --text=Fechar --ok-label=Sim --cancel-label=Nao >/dev/null 2>&1; echo $? > /tmp/zenity.rc ) &
  sleep 3
  s=$(AGENT_DESKTOP_HOME=/tmp/adh agent-desktop snapshot --app zenity -i --compact) || return 1
  ref=$(printf '%s' "$s" | python3 -c 'import sys,json
def anda(n):
    if n.get("name")=="Sim" and n.get("ref_id"): print(n["ref_id"]); raise SystemExit
    for c in n.get("children",[]): anda(c)
anda(json.load(sys.stdin)["data"]["tree"])')
  [ -n "$ref" ] || return 1
  AGENT_DESKTOP_HOME=/tmp/adh agent-desktop click "$ref" | grep -q '"ok":true' || return 1
  sleep 1
  test "$(cat /tmp/zenity.rc 2>/dev/null)" = 0
}
no_at() { test -z "${NO_AT_BRIDGE+x}"; }
# regra do ESCALAR: todo texto dos agentes cita ESCALAR e a frase que começa em "Se o `decide` deu ESCALAR" é a mesma em todos
TEXTOS_ESCALAR=(/data/AGENTS.md /data/skills/{conselho,decide,moondream}.md /data/.agents/skills/{conselho,decide,moondream,jeito-hark,cursor-hark}/SKILL.md /data/OpenBot/Agents/smoke-{agente,antigo}/AGENTS.md)
escalar_igual() {
  local f
  for f in "${TEXTOS_ESCALAR[@]}"; do grep -q ESCALAR "$f" || return 1; done
  test "$(grep -h ESCALAR "${TEXTOS_ESCALAR[@]}" | sed 's/^.*\(Se o `decide` deu ESCALAR\)/\1/' | sort -u | wc -l)" = 1
}

# imagem
check "uid 1000" test "$(id -u)" = 1000
check "openbot-container" test -x /opt/OpenBot/app/resources/hosting/openbot-container
check "tini" test -x /usr/bin/tini
check "md" test -x /usr/local/bin/md
check "decide" test -x /usr/local/bin/decide
check "openbox/xdotool/scrot" bins openbox xdotool scrot
check "sudo sem senha" sudo -n true
check "agent-desktop version" ad_versao
check "agent-desktop: snapshot de janela GTK no :1 e clique por ref" ad_clique
check "at-spi-bus-launcher instalado" test -x /usr/libexec/at-spi-bus-launcher -o -x /usr/lib/at-spi2-core/at-spi-bus-launcher
check "NO_AT_BRIDGE não definido no shell" no_at

# customizações geradas pelo init
for s in jeito-hark cursor-hark openui-hark supermemory conselho decide moondream agent-desktop; do check "skill $s" skill "$s"; done
for s in conselho decide moondream agent-desktop; do
  check "skill $s = front matter + skills/$s.md" fonte "$s"
  check "skill $s com dono 1000:1000" dono "/data/.agents/skills/$s/SKILL.md"
done
check "cursor-hark cursor.js" test -s /data/.agents/skills/cursor-hark/cursor.js
check "openui-hark montar.py" test -s /data/.agents/skills/openui-hark/montar.py
check "AGENTS.md do agente novo: um só bloco, com os 4 casos do conselho" bloco_novo
check "AGENTS.md do agente antigo: bloco trocado, texto do dono preservado" bloco_trocado
check "ESCALAR: mesma regra no AGENTS.md, nas skills, nas SKILL.md e no bloco dos agentes" escalar_igual
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
