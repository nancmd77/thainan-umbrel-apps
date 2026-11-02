# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_EZBOOKKEEPING_EBK_SECURITY_SECRET_KEY="$(printf '%s' "$(derive_entropy "env-thainan-ezbookkeeping-ebk_security_secret_key")" | cut -c1-64)"
