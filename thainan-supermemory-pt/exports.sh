# Umbrel carrega antes do compose up. Chaves derivadas do APP_SEED com os mesmos identificadores dos apps de origem (nada no repo).
export APP_THAINAN_SUPERMEMORY_PT_CLIPROXY_KEY="$(derive_entropy "env-thainan-cliproxyapi-api_key")"
export APP_THAINAN_SUPERMEMORY_PT_LITELLM_KEY="sk-$(printf '%s' "$(derive_entropy "env-thainan-litellm-master_key")" | cut -c1-40)"
