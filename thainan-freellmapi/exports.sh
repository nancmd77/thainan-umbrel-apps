# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_FREELLMAPI_ENCRYPTION_KEY="$(printf '%s' "$(derive_entropy "env-thainan-freellmapi-encryption_key")" | cut -c1-64)"
