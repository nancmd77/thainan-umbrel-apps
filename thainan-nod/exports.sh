# Umbrel carrega antes do compose up. Segredos nascem do APP_SEED (únicos por máquina, nunca no repo).
export APP_THAINAN_NOD_POSTGRES_PASSWORD="$(derive_entropy "env-thainan-nod-postgres_password")"
__nod_vault_hex="$(derive_entropy "env-thainan-nod-vault_key")"
# NOD_VAULT_KEY: 32 bytes em base64 (o hex de 64 caracteres vira 32 bytes)
export APP_THAINAN_NOD_VAULT_KEY="$(printf "$(printf '%s' "${__nod_vault_hex}" | sed 's/../\\x&/g')" | base64 | tr -d '\n')"
unset __nod_vault_hex
