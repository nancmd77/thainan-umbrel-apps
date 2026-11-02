# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_INKVOICE_JWT_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-inkvoice-jwt_secret")" | cut -c1-64)"
