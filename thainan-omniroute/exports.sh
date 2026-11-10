# Umbrel carrega antes do compose up. Segredos nascem do APP_SEED (únicos por máquina, nunca no repo).
export APP_THAINAN_OMNIROUTE_JWT_SECRET="$(derive_entropy "env-thainan-omniroute-jwt_secret")"
export APP_THAINAN_OMNIROUTE_API_KEY_SECRET="$(derive_entropy "env-thainan-omniroute-api_key_secret")"
