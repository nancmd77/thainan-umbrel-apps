# Umbrel carrega antes do compose up. Segredos nascem do APP_SEED (únicos por máquina, nunca no repo).
export APP_THAINAN_TREG_FERNET_SEED="$(derive_entropy "env-thainan-treg-fernet")"
export APP_THAINAN_TREG_SESSION_SECRET="$(derive_entropy "env-thainan-treg-session")"
