# OpenBot (thainan-openbot)

OpenBot original, espelho do app `openbot-server` da loja oficial
[nightly-labs/openbot-umbrel-store](https://github.com/nightly-labs/openbot-umbrel-store), sem patches
(desde 0.74.0, 20/11/2026).

- Imagem oficial `ghcr.io/nightly-labs/openbot`, presa por digest; atualiza pelo `auto-update.yml` (mode tag).
- Página de login oficial (`OPENBOT_SETUP_PAGE_PORT=8080`), aberta pelo Umbrel na porta 8592.
- Única diferença: `apparmor=unconfined` (o AppArmor do host bloqueia user namespaces do Electron).
- Dados em `${APP_DATA_DIR}/data` (/data), os mesmos de antes.
