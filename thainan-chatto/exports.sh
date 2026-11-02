# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_CHATTO_CHATTO_WEBSERVER_COOKIE_SIGNING_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-chatto-chatto_webserver_cookie_signing_secret")" | cut -c1-64)"
export APP_THAINAN_CHATTO_CHATTO_WEBSERVER_COOKIE_ENCRYPTION_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-chatto-chatto_webserver_cookie_encryption_secret")" | cut -c1-64)"
export APP_THAINAN_CHATTO_CHATTO_CORE_SECRET_KEY="$(printf '%s' "$(derive_entropy "env-thainan-chatto-chatto_core_secret_key")" | cut -c1-64)"
export APP_THAINAN_CHATTO_CHATTO_CORE_ASSETS_SIGNING_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-chatto-chatto_core_assets_signing_secret")" | cut -c1-64)"
