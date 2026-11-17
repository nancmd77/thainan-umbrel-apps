# Umbrel carrega antes do compose up. Chave do LiteLLM derivada do APP_SEED com o mesmo identificador do app thainan-litellm (nada no repo).
export APP_THAINAN_FASTAGENT_LITELLM_KEY="sk-$(printf '%s' "$(derive_entropy "env-thainan-litellm-master_key")" | cut -c1-40)"
