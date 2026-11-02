# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_OPENHUMAN_OPENHUMAN_CORE_TOKEN="$(printf '%s' "$(derive_entropy "env-thainan-openhuman-openhuman_core_token")" | cut -c1-24)"
