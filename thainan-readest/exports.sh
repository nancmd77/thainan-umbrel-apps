# Umbrel carrega antes do compose up.

# Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo).
export APP_THAINAN_READEST_PGPASSWORD="$(printf '%s' "$(derive_entropy "env-thainan-readest-pgpassword")" | cut -c1-32)"
export APP_THAINAN_READEST_JWT_SECRET="$(printf '%s' "$(derive_entropy "env-thainan-readest-jwt_secret-0")$(derive_entropy "env-thainan-readest-jwt_secret-1")" | cut -c1-80)"
export APP_THAINAN_READEST_MINIO_ROOT_PASSWORD="$(printf '%s' "$(derive_entropy "env-thainan-readest-minio_root_password")" | cut -c1-32)"
# Chaves do Supabase (JWT HS256 sem validade) assinadas com o segredo acima.
_rd_b64url() { base64 | tr -d '=\n' | tr '+/' '-_'; }
_rd_jwt() {
  _h="$(printf '%s' '{"alg":"HS256","typ":"JWT"}' | _rd_b64url)"
  _p="$(printf '%s' "{\"role\":\"$1\",\"iss\":\"supabase\"}" | _rd_b64url)"
  _s="$(printf '%s' "$_h.$_p" | openssl dgst -sha256 -hmac "$APP_THAINAN_READEST_JWT_SECRET" -binary | _rd_b64url)"
  printf '%s' "$_h.$_p.$_s"
}
export APP_THAINAN_READEST_ANON_KEY="$(_rd_jwt anon)"
export APP_THAINAN_READEST_SERVICE_KEY="$(_rd_jwt service_role)"
