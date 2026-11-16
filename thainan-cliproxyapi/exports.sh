# Umbrel carrega antes do compose up. Chave da API nasce do APP_SEED (única por máquina, nunca no repo).
export APP_THAINAN_CLIPROXYAPI_API_KEY="$(derive_entropy "env-thainan-cliproxyapi-api_key")"
