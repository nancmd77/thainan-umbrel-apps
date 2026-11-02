# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_BUGSINK_POSTGRES_PASSWORD="$(printf '%s' "$(derive_entropy "env-thainan-bugsink-postgres_password")" | cut -c1-16)"
export APP_THAINAN_BUGSINK_SECRET_KEY="$(printf '%s' "$(derive_entropy "env-thainan-bugsink-secret_key")" | cut -c1-64)"
