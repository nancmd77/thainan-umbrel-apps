#!/bin/sh
# Subdomínios automáticos: lê os apps instalados no Umbrel e liga
# <app>.$DOMAIN -> $TARGET_HOST:<porta do app>. Atualiza a cada $INTERVAL s.
DOMAIN="${DOMAIN:-nonve.dpdns.org}"
TARGET="${TARGET_HOST:-10.203.0.1}"
EXCL=" ${EXCLUDE:-} "
INTERVAL="${INTERVAL:-30}"
mkdir -p /srv /etc/caddy

field() { awk -v k="$1" -F': *' '$1==k{v=$2;gsub(/["\047\r ]/,"",v);print v;exit}' "$2"; }

gen() {
  rows=""
  {
    printf '{\n\tadmin localhost:2019\n\tauto_https off\n}\n:80 {\nroute {\n'
    for f in /apps/*/umbrel-app.yml; do
      [ -f "$f" ] || continue
      id=$(field id "$f"); port=$(field port "$f")
      [ -n "$id" ] && [ -n "$port" ] || continue
      case "$EXCL" in *" $id "*) continue;; esac
      sub=$(echo "${id#thainan-}" | tr 'A-Z_' 'a-z-')
      m=$(echo "$sub" | tr -c 'a-z0-9\n' '_')
      printf '\t@%s host %s.%s\n\treverse_proxy @%s %s:%s\n' "$m" "$sub" "$DOMAIN" "$m" "$TARGET" "$port"
      rows="$rows<li><a href=\"https://$sub.$DOMAIN\">$sub.$DOMAIN</a> &rarr; porta $port</li>"
    done
    printf '\t@painel not host *.%s\n\thandle @painel {\n\t\troot * /srv\n\t\tfile_server\n\t}\n\trespond 404\n}\n}\n' "$DOMAIN"
  } > /tmp/Caddyfile.new
  printf '<!doctype html><meta charset="utf-8"><title>Subdomínios</title><style>body{font:16px system-ui;max-width:640px;margin:40px auto;padding:0 16px}li{margin:6px 0}</style><h1>Subdomínios automáticos</h1><ul>%s</ul><p>Atualiza a cada %ss.</p>' "$rows" "$INTERVAL" > /srv/index.html
}

gen; cp /tmp/Caddyfile.new /etc/caddy/Caddyfile
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
while sleep "$INTERVAL"; do
  gen
  if ! cmp -s /tmp/Caddyfile.new /etc/caddy/Caddyfile; then
    cp /tmp/Caddyfile.new /etc/caddy/Caddyfile
    caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile && echo "rotas atualizadas"
  fi
done
