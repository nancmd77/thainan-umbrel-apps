# Umbrel carrega antes do compose up.
# Segredo da sessão do painel: nasce do APP_SEED do Umbrel (único por máquina, nunca no repo).
export APP_THAINAN_9ROUTER_JWT_SECRET="$(derive_entropy "env-thainan-9router-jwt_secret")"
