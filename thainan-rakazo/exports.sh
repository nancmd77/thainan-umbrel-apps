# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_RAKAZO_POSTGRES_PASSWORD="$(printf '%s' "$(derive_entropy "env-thainan-rakazo-postgres_password")" | cut -c1-32)"
export APP_THAINAN_RAKAZO_SANDBOX_SUPERVISOR_TOKEN="$(printf '%s' "$(derive_entropy "env-thainan-rakazo-sandbox_supervisor_token")" | cut -c1-64)"
export APP_THAINAN_RAKAZO_BETTER_AUTH_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-rakazo-better_auth_secret")" | cut -c1-64)"
export APP_THAINAN_RAKAZO_ENCRYPTION_KEY="$(printf '%s' "$(derive_entropy "env-thainan-rakazo-encryption_key")" | cut -c1-64)"
export APP_THAINAN_RAKAZO_SCREEN_PROXY_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-rakazo-screen_proxy_secret")" | cut -c1-64)"
