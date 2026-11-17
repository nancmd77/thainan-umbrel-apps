# Umbrel carrega antes do compose up. Segredos nascem do APP_SEED (únicos por máquina, nunca no repo).
export APP_THAINAN_LITELLM_MASTER_KEY="sk-$(printf '%s' "$(derive_entropy "env-thainan-litellm-master_key")" | cut -c1-40)"
export APP_THAINAN_LITELLM_SALT_KEY="sk-$(printf '%s' "$(derive_entropy "env-thainan-litellm-salt_key")" | cut -c1-40)"
export APP_THAINAN_LITELLM_PG_PASSWORD="$(printf '%s' "$(derive_entropy "env-thainan-litellm-pg_password")" | cut -c1-24)"
# Mesmos identificadores dos apps thainan-cliproxyapi e thainan-langfuse: dá as chaves deles sem copiar nada.
export APP_THAINAN_LITELLM_CLIPROXY_KEY="$(derive_entropy "env-thainan-cliproxyapi-api_key")"
export APP_THAINAN_LITELLM_LANGFUSE_PK="pk-lf-$(printf '%s' "$(derive_entropy "env-thainan-langfuse-public_key")" | cut -c1-32)"
export APP_THAINAN_LITELLM_LANGFUSE_SK="sk-lf-$(printf '%s' "$(derive_entropy "env-thainan-langfuse-secret_key")" | cut -c1-32)"
export APP_THAINAN_LITELLM_HEADROOM_TOKEN="$(derive_entropy "env-thainan-headroom-proxy_token")"
export APP_THAINAN_LITELLM_REDIS_PASSWORD="$(printf '%s' "$(derive_entropy "env-thainan-litellm-redis_password")" | cut -c1-32)"
