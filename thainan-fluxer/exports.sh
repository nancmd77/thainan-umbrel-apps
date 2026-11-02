# Umbrel sources this before compose up
export FLUXER_DOMAIN="${DEVICE_DOMAIN_NAME}"
export FLUXER_PUBLIC_SCHEME="http"
export FLUXER_PUBLIC_PORT="3005"
export FLUXER_PUBLIC_ORIGIN="http://${DEVICE_DOMAIN_NAME}:3005"
export FLUXER_EDGE_SITE_ADDRESS=":8080"
export FLUXER_PASSKEY_RP_ID="${DEVICE_DOMAIN_NAME}"

# --- Segredos: nascem na instalação a partir do APP_SEED do Umbrel (únicos por máquina, nunca no repo) ---
_fx() { derive_entropy "env-thainan-fluxer-$1"; }
_hex2b64() { printf "$(printf '%s' "$1" | sed 's/../\\x&/g')" | base64 | tr -d '\n'; }
_b64url() { tr '+/' '-_' | tr -d '=\n'; }

export POSTGRES_PASSWORD="$(_fx postgres-password | cut -c1-48)"
export MEILI_MASTER_KEY="$(_fx meili-master-key)"
export FLUXER_S3_SECRET_KEY="$(_fx s3-secret-key)"
export FLUXER_SUDO_MODE_SECRET="$(_fx sudo-mode-secret)"
export FLUXER_CONNECTION_INITIATION_SECRET="$(_fx connection-initiation-secret)"
export FLUXER_PROFILE_PSEUDONYM_SECRET="$(_fx profile-pseudonym-secret)"
export FLUXER_GATEWAY_RPC_AUTH_TOKEN="$(_fx gateway-rpc-auth-token)"
export FLUXER_ERLANG_COOKIE="$(_fx erlang-cookie | cut -c1-32)"
export FLUXER_MEDIA_PROXY_SECRET_KEY="$(_fx media-proxy-secret-key)"
export FLUXER_MEDIA_PROXY_UPLOAD_RELAY_SECRET_BASE64="$(_hex2b64 "$(_fx media-proxy-upload-relay)")"
export FLUXER_ADMIN_SECRET_KEY_BASE="$(_fx admin-secret-key-base-1)$(_fx admin-secret-key-base-2)"
export FLUXER_ADMIN_OAUTH_CLIENT_SECRET="$(_fx admin-oauth-client-secret)"
export LIVEKIT_API_SECRET="$(_fx livekit-api-secret)"

# VAPID (push no navegador): par P-256 fixo derivado do seed. Sem openssl, fica vazio e o push do navegador desliga.
_vapid_priv_hex="$(_fx vapid-private-key)"
if command -v openssl >/dev/null 2>&1; then
  _vapid_pub_b64="$(printf "$(printf '%s' "30310201010420${_vapid_priv_hex}a00a06082a8648ce3d030107" | sed 's/../\\x&/g')" \
    | openssl ec -inform DER -pubout -outform DER 2>/dev/null | tail -c 65 | base64 | tr -d '\n')"
  if [ -n "$_vapid_pub_b64" ]; then
    export FLUXER_VAPID_PRIVATE_KEY="$(_hex2b64 "$_vapid_priv_hex" | _b64url)"
    export FLUXER_VAPID_PUBLIC_KEY="$(printf '%s' "$_vapid_pub_b64" | _b64url)"
  fi
fi
