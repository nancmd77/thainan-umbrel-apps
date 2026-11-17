# Umbrel carrega antes do compose up. Segredos nascem do APP_SEED (únicos por máquina, nunca no repo).
# As chaves do projeto (pk/sk) usam os mesmos identificadores no app thainan-litellm, que manda os traces pra cá.
_lf() { printf '%s' "$(derive_entropy "env-thainan-langfuse-$1")" | cut -c1-$2; }
export APP_THAINAN_LANGFUSE_PG_PASSWORD="$(_lf pg_password 24)"
export APP_THAINAN_LANGFUSE_CH_PASSWORD="$(_lf ch_password 24)"
export APP_THAINAN_LANGFUSE_REDIS_PASSWORD="$(_lf redis_password 24)"
export APP_THAINAN_LANGFUSE_MINIO_PASSWORD="$(_lf minio_password 24)"
export APP_THAINAN_LANGFUSE_SALT="$(_lf salt 32)"
export APP_THAINAN_LANGFUSE_ENCRYPTION_KEY="$(_lf encryption_key 64)"
export APP_THAINAN_LANGFUSE_NEXTAUTH_SECRET="$(_lf nextauth_secret 48)"
export APP_THAINAN_LANGFUSE_PUBLIC_KEY="pk-lf-$(_lf public_key 32)"
export APP_THAINAN_LANGFUSE_SECRET_KEY="sk-lf-$(_lf secret_key 32)"
