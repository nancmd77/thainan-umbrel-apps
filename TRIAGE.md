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

## SKIP

| App | Reason |
|---|---|
| Proton Pass | Proprietary cloud-only server; clients are OSS but there is no self-hostable Pass server. Do not substitute Vaultwarden unless asked. |
| Anytype (any-sync) | Self-host is a multi-node sync network (no browser UI). Clients need client.yml; too complex / wrong UX for Umbrel Open button. |
| Readest | Official Docker needs full Supabase stack (Postgres, Kong, GoTrue, PostgREST, MinIO) — too heavy/fragile for simplest Umbrel packaging. |
| Novu | Self-hostable but heavy multi-service stack (Mongo, Redis, workers, etc.); skipped for simplest packaging. |
| Rakazo | Compose requires docker.sock, local image builds, and sandbox supervisor — not suitable as a normal Umbrel community app. |
| treg | No published Docker image; Python/uv install only. |
| Billmora | Laravel hosting-billing platform; no simple published Docker image found (release/PHP install path). |
