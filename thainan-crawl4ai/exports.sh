# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_CRAWL4AI_CRAWL4AI_API_TOKEN="$(printf '%s' "$(derive_entropy "env-thainan-crawl4ai-crawl4ai_api_token")" | cut -c1-64)"
