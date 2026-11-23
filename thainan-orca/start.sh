#!/bin/sh
# Partida do thainan-orca: baixa/atualiza o servidor do Orca (orca-server-linux-x64 do GitHub),
# instala os agentes (Claude Code, Codex, OpenCode, agy) e sobe `orca-ide serve` + a página do app.
set -u
D=/data
H=$D/home
mkdir -p $D/orca $D/logs $H/.local/bin $H/.npm-global
chown -R 1000:1000 $D 2>/dev/null || true
# identidade do git (o Orca recusa criar projeto sem ela)
if [ ! -f $H/.gitconfig ]; then
  printf '[user]\n\tname = %s\n\temail = %s\n[init]\n\tdefaultBranch = main\n' "${GIT_NAME:-Thainan Caldeira}" "${GIT_EMAIL:-thainancaldeira75@gmail.com}" > $H/.gitconfig
  chown 1000:1000 $H/.gitconfig
fi

get() { curl -fsSL --retry 3 -A thainan-orca "$@"; }

# login do agy: na primeira partida, copia o login já feito na ponte do Antigravity (thainan-agy-ponte)
if [ ! -f $H/.agy-login-da-ponte ] && [ -d /ponte-home ] && [ -n "$(ls -A /ponte-home 2>/dev/null)" ]; then
  cp -an /ponte-home/. $H/ 2>/dev/null && rm -rf $H/.cache/agy-req 2>/dev/null
  touch $H/.agy-login-da-ponte && chown -R 1000:1000 $H && echo "login do agy copiado da ponte"
fi

# --- servidor do Orca -------------------------------------------------------
PIN=${ORCA_VERSION:-latest}
if [ "$PIN" = latest ]; then
  TAG=$(get https://api.github.com/repos/stablyai/orca/releases/latest | python3 -c 'import sys,json;print(json.load(sys.stdin)["tag_name"])' 2>/dev/null || true)
else
  TAG=$PIN
fi
CUR=$(cat $D/orca/TAG 2>/dev/null || true)
if [ -n "$TAG" ] && { [ "$TAG" != "$CUR" ] || [ ! -x $D/orca/current/bin/orca-ide ]; }; then
  echo "baixando servidor do Orca $TAG"
  B=https://github.com/stablyai/orca/releases/download/$TAG
  if get -o /tmp/os.tgz $B/orca-server-linux-x64.tar.gz && get -o /tmp/sums $B/orca-server-sha256sums.txt; then
    WANT=$(grep 'orca-server-linux-x64.tar.gz' /tmp/sums | cut -d' ' -f1)
    GOT=$(sha256sum /tmp/os.tgz | cut -d' ' -f1)
    if [ -n "$WANT" ] && [ "$WANT" = "$GOT" ]; then
      rm -rf $D/orca/new && mkdir -p $D/orca/new && tar xzf /tmp/os.tgz -C $D/orca/new --strip-components=1 \
        && rm -rf $D/orca/old && { [ -d $D/orca/current ] && mv $D/orca/current $D/orca/old || true; } \
        && mv $D/orca/new $D/orca/current && echo "$TAG" > $D/orca/TAG && rm -rf $D/orca/old
    else
      echo "sha256 não confere ($GOT != $WANT), mantendo a versão atual"
    fi
  else
    echo "não consegui baixar o Orca $TAG, mantendo a versão atual"
  fi
  rm -f /tmp/os.tgz /tmp/sums
fi
chown -R 1000:1000 $D/orca
echo "Orca $(cat $D/orca/current/VERSION 2>/dev/null)"
ln -sf $D/orca/current/bin/orca-ide /usr/local/bin/orca-ide
ln -sf $D/orca/current/bin/orca-ide /usr/local/bin/orca

# --- agentes (em segundo plano, para não atrasar o servidor) ----------------
(
  export HOME=$H NPM_CONFIG_PREFIX=$H/.npm-global
  for p in $AGENTS_NPM; do
    runuser -u node -- env HOME=$H NPM_CONFIG_PREFIX=$H/.npm-global npm i -g --no-fund --no-audit "$p" >> $D/logs/agentes.log 2>&1 \
      && echo "agente ok: $p" || echo "agente falhou: $p (ver logs/agentes.log)"
  done
  ATAG=$(get https://api.github.com/repos/google-antigravity/antigravity-cli/releases/latest | python3 -c 'import sys,json;print(json.load(sys.stdin)["tag_name"])' 2>/dev/null || true)
  if [ -n "$ATAG" ] && [ "$ATAG" != "$(cat $H/.local/bin/AGY_TAG 2>/dev/null)" ]; then
    get -o /tmp/agy.tgz https://github.com/google-antigravity/antigravity-cli/releases/download/$ATAG/agy_cli_linux_x64.tar.gz \
      && mkdir -p /tmp/agyx && tar xzf /tmp/agy.tgz -C /tmp/agyx \
      && f=$(find /tmp/agyx -type f \( -name agy -o -name antigravity \) | head -1) && [ -n "$f" ] \
      && install -m 755 "$f" $H/.local/bin/agy && echo "$ATAG" > $H/.local/bin/AGY_TAG && echo "agy $ATAG"
    rm -rf /tmp/agy.tgz /tmp/agyx
  fi
  chown -R 1000:1000 $H/.local $H/.npm-global
) &

# --- página do app (links de pareamento) ------------------------------------
runuser -u node -- env HOME=$H PATH="$PATH" python3 -u /app/page.py >> $D/logs/page.log 2>&1 &

# --- servidor ----------------------------------------------------------------
export HOME=$H
while true; do
  runuser -u node -- env HOME=$H PATH="$H/.local/bin:$H/.npm-global/bin:$PATH" \
    orca-ide serve --port 6768 --bind 0.0.0.0 --pairing-address "$PAIRING_ADDRESS" 2>&1 | tee -a $D/logs/serve.log
  rc=$?
  echo "orca serve saiu ($rc); tentando liberar trava antiga e subir de novo em 10 s"
  runuser -u node -- env HOME=$H orca-ide serve --reclaim-stale-lock --yes >/dev/null 2>&1 || true
  sleep 10
done
