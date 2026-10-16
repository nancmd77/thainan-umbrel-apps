# Video KB Worker (app Umbrel)

Link de Reels/TikTok no Karakeep → áudio (Cobalt, fallback yt-dlp) → ElevenLabs Scribe (`scribe_v2`) → 9Router do Nonve, OpenAI-compatível (`http://thainan-9router_web_1:20128/v1`, modelo `ag/gemini-3.8-flash-medium`, ambos editáveis; JSON `titulo/resumo/pontos_chave/tags/tema/transcricao_limpa`) → Karakeep (summary, nota, tags, lista do tema) → Open Notebook (caderno do tema, fonte texto).

Python 3.12, só stdlib. Único pacote externo: `yt-dlp==2026.8.19`, instalado no start em `/data/pylib`. Sem ffmpeg, sem build de imagem.

## Arquivos
- `docker-compose.yml`, `umbrel-app.yml`, `icon.png`: porta Umbrel **8519**, container 8080, `APP_HOST thainan-video-kb_web_1`, `/webhook/*` fora do login Umbrel.
- `app/main.py`: servidor + pipeline (SQLite em `/data/kb.db`, config em `/data/config.json`, chmod 600).
- `app/test_main.py`: testes com mocks (sem rede).

Código: o compose monta `${APP_DATA_DIR}/app:/app:ro`. Se `/app/main.py` não existir, baixa de `raw.githubusercontent.com/nancmd77/thainan-umbrel-apps/${REF}/thainan-video-kb/app/main.py` (`REF: video-kb-v1` no compose; crie essa tag ao publicar).

## Uso
1. Instale, abra, **Configurações**: chave ElevenLabs, URL + chave 9Router, Karakeep (URL + API key), Open Notebook (URL `http://thainan-opennotebook_web_1:5055` + senha, se houver), token do webhook. Chaves nunca voltam inteiras na tela (só `••••últimos4`); campo vazio mantém, `-` apaga.
2. Webhook no Karakeep: URL `http://<IP-Nonve>:8519/webhook/karakeep`, evento `created`, mesmo token (vai como `Authorization: Bearer`). O Karakeep bloqueia IP privado em chamadas de worker: se o webhook não chegar, ponha o host em `CRAWLER_ALLOWED_INTERNAL_HOSTNAMES` no Karakeep.
3. Sem webhook, o polling a cada 5 min pega os últimos 50 bookmarks criados depois do primeiro polling (histórico não é importado). Link avulso: campo "colar link" na Fila.
4. **Fila**: etapa, erro e **Reprocessar** (retoma da etapa que falhou; transcrição/resumo já feitos não são refeitos).

## Comportamento
- Tema/tags: antes do Gemini lê `GET /api/notebooks` (Open Notebook) e `GET /api/v1/tags` (Karakeep, 200 mais usadas) e manda no prompt pra reaproveitar. Se a leitura falhar, segue sem lista. Tema normalizado (trim, 1ª maiúscula) e casado sem diferenciar maiúscula/acento com caderno e lista existentes.
- Idempotente por URL normalizada (sem `www/m`, sem query/fragmento, sem `/` final). Short links (`vm.tiktok.com`) não deduplicam com o link longo.
- Karakeep: `summary` = resumo; `note` é **substituída** pelos pontos-chave. Link colado à mão cria o bookmark.
- Open Notebook: `POST /api/sources/json` `{type:text, notebooks:[id], embed:true, async_processing:true}`; embeddings dependem do provedor configurado lá.

## Testes
```
cd app && python3 -m unittest -v
```
Cobre pipeline feliz, idempotência, reprocessar após erro (sem refazer etapas), parse/auth do webhook, polling, máscara de chaves.

## Testado (2026-10-16, a partir da VM, sem chaves)
- `GET http://192.168.18.171:8620/api/health` → `200 {"status":"ok","message":"Web app is working"}`.
- Cobalt `http://192.168.18.171:9013` (v11.5) responde. **TikTok: falhou** em 4 links públicos (`error.api.fetch.fail`, HTTP 400); Instagram `error.api.fetch.empty`. Provável bloqueio/quebra do extrator do TikTok nesse Cobalt (ou vídeos removidos), não do worker. O fallback yt-dlp existe para isso, mas não pôde ser testado aqui (sem pip/Docker).
- Cobalt com YouTube (`downloadMode: audio`): `status: tunnel`, e `cobalt_audio()` baixou 305 KB de MP3 válido. O tunnel vem com host `nonve.local:9013`; o worker reescreve para o host de `cobalt_url`.
- Servidor local: `/health`, Fila, Configurações (máscara `••••5678`), CSRF (`Sec-Fetch-Site: same-site` → 403), webhook sem token válido → 401, webhook válido → job na fila, `config.json` com modo 600, nenhuma chave no log.
- Não testado (sem chaves/rede): ElevenLabs, 9Router (responde 401 sem chave, ok), escrita no Karakeep, Open Notebook, yt-dlp, boot do compose no Umbrel.
