# Padrão derivado do APP_SEED; o valor real usado pelo notebook entra por env custom (SP_WEBDAV_PASS).
export APP_THAINAN_SP_ESPELHO_PASS="$(derive_entropy "env-thainan-screenpipe-espelho-webdav")"
