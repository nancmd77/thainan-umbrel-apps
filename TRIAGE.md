# Triage — Thainan Umbrel Community App Store

## INCLUDE

| App | ID | Port | Secrets / notes |
|---|---|---|---|
| FreeLLMAPI | `thainan-freellmapi` | 3001 | ENCRYPTION_KEY (baked; rotate if exposing beyond LAN) |
| 9Router | `thainan-9router` | 20128 | Optional: INITIAL_PASSWORD, JWT_SECRET |
| openGym | `thainan-opengym` | 8088 | RP_ID / ORIGIN for HTTPS passkeys; Passkeys need HTTPS domain |
| SparkyFitness | `thainan-sparkyfitness` | 3004 | SPARKY_FITNESS_DB_PASSWORD, SPARKY_FITNESS_API_ENCRYPTION_KEY, BETTER_AUTH_SECRET (baked) |
| Hindsight | `thainan-hindsight` | 9999 | HINDSIGHT_API_LLM_API_KEY (set before real use) |
| OpenHuman | `thainan-openhuman` | 7788 | OPENHUMAN_CORE_TOKEN (baked) |
| Yuvomi | `thainan-yuvomi` | 3100 | SESSION_SECRET, DB_ENCRYPTION_KEY (baked; keep stable) |
| Redlib | `thainan-redlib` | 8081 | — |
| SilverBullet | `thainan-silverbullet` | 3002 | Optional SB_USER=username:password |
| TREK | `thainan-trek` | 3010 | ENCRYPTION_KEY (baked); optional ADMIN_EMAIL/ADMIN_PASSWORD |
| Langflow | `thainan-langflow` | 7860 | Optional LANGFLOW_SUPERUSER / LANGFLOW_SUPERUSER_PASSWORD |
| Crawl4AI | `thainan-crawl4ai` | 11235 | CRAWL4AI_API_TOKEN (baked) |
| Wallos | `thainan-wallos` | 8282 | — |
| Inkvoice | `thainan-inkvoice` | 3020 | JWT_SECRET (baked); optional ADMIN_USER/ADMIN_PASS |
| ezBookkeeping | `thainan-ezbookkeeping` | 8085 | — |
| Bugsink | `thainan-bugsink` | 8000 | SECRET_KEY, DB password, CREATE_SUPERUSER (baked) |
| InvoiceShelf | `thainan-invoiceshelf` | 8090 | None for SQLite; set APP_URL to match how you browse; APP_URL must match browser URL |
| Spliit | `thainan-spliit` | 3003 | — |
| Readest | `thainan-readest` | 3050 | Heavy Supabase+MinIO stack behind nginx; baked JWT/DB/MinIO secrets; set public URLs if not using DEVICE_DOMAIN_NAME |
| Rakazo | `thainan-rakazo` | 5173 | Published GHCR images; **docker.sock** on supervisor (privileged); baked secrets; optional OPENROUTER_API_KEY |
| Chatwoot | `thainan-chatwoot` | 3000 | SECRET_KEY_BASE + Postgres/Redis passwords baked; first boot runs db:chatwoot_prepare |
| Fluxer | `thainan-fluxer` | 3005 | Heavy official stack (proxy mode :8080); baked .env secrets; exports.sh sets DOMAIN from DEVICE_DOMAIN_NAME; needs several GB RAM |
| Chatto | `thainan-chatto` | 4000 | Single image + embedded NATS; LiveKit/SMTP off; `chatto init` on first start |

## SKIP

| App | Reason |
|---|---|
| Proton Pass | Proprietary cloud-only server; clients are OSS but there is no self-hostable Pass server. Do not substitute Vaultwarden unless asked. |
| Anytype (any-sync) | Self-host is a multi-node sync network (no browser UI). Clients need client.yml; too complex / wrong UX for Umbrel Open button. |
| Novu | Self-hostable but heavy multi-service stack (Mongo, Redis, workers, etc.); skipped for simplest packaging. |
| treg | No published Docker image; Python/uv install only. |
| Billmora | Laravel hosting-billing platform; no simple published Docker image found (release/PHP install path). |
