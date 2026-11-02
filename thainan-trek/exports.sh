# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_TREK_ENCRYPTION_KEY="$(printf '%s' "$(derive_entropy "env-thainan-trek-encryption_key")" | cut -c1-64)"
