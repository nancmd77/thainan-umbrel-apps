# Umbrel carrega antes do compose up. Segredos nascem do APP_SEED (únicos por máquina, nunca no repo).
export APP_THAINAN_YUVOMI_SESSION_SECRET="$(derive_entropy "env-thainan-yuvomi-session_secret")"
export APP_THAINAN_YUVOMI_DB_KEY="$(derive_entropy "env-thainan-yuvomi-db_encryption_key")"
