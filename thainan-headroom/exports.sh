# Umbrel carrega antes do compose up. Token nasce do APP_SEED (único por máquina, nunca no repo).
# O thainan-litellm deriva o mesmo identificador para chamar o /v1/compress.
export APP_THAINAN_HEADROOM_TOKEN="$(derive_entropy "env-thainan-headroom-proxy_token")"
