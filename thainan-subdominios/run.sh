#!/bin/sh
# Acesso remoto ao dashboard do Umbrel: Caddy na frente do painel (porta 80 do host).
# Injeta /__nonve-tiles.js no index para os tiles abrirem os endereços públicos
# (NetBird) em vez de <host>:<porta>, que só funciona em casa.
UP="${UPSTREAM:-10.203.0.1:80}"
REF="${REF:-main}"
RAW="https://raw.githubusercontent.com/nancmd77/thainan-umbrel-apps/$REF/thainan-subdominios"
mkdir -p /srv /etc/caddy

mapa() {  # "porta=host porta=host" -> {"porta":"host",...}
  printf '{'; s=""
  for par in $TILES; do printf '%s"%s":"%s"' "$s" "${par%%=*}" "${par#*=}"; s=","; done
  printf '}'
}

atualiza() {
  wget -qO /tmp/tiles.js "$RAW/tiles.js" || [ -f /srv/__nonve-tiles.js ] || return
  [ -s /tmp/tiles.js ] && sed "s|__MAPA__|$(mapa)|" /tmp/tiles.js > /srv/__nonve-tiles.js
  if wget -qO /tmp/index.html "http://$UP/" && grep -q '</head>' /tmp/index.html; then
    sed 's|</head>|<script src="/__nonve-tiles.js"></script></head>|' /tmp/index.html > /srv/index.html
  fi
}

atualiza
cat > /etc/caddy/Caddyfile <<CF
{
	admin off
	auto_https off
}
:80 {
	header /__nonve-tiles.js Cache-Control "no-cache"
	handle /__nonve-tiles.js {
		root * /srv
		file_server
	}
	@pagina {
		header Sec-Fetch-Dest document
		not path /api/* /trpc/* /manager-api/* /files-api/*
		file {
			root /srv
			try_files /index.html
		}
	}
	handle @pagina {
		header Cache-Control "no-cache"
		root * /srv
		rewrite * /index.html
		file_server
	}
	handle {
		reverse_proxy $UP
	}
}
CF
# 2.0.2: atalhos diretos (sem o login extra do Umbrel) pros apps cujo tile cai no app-auth :2000
for par in $DIRETOS; do
  printf '\n:%s {\n\treverse_proxy %s\n}\n' "${par%%=*}" "${par#*=}" >> /etc/caddy/Caddyfile
done
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
while sleep "${INTERVAL:-300}"; do atualiza; done
