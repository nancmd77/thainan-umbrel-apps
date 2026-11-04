# Thainan Supermemory Local

Motor de memória de longo prazo e *continual learning* para agentes de IA rodando no host Nonve (Umbrel).

- **appId:** `thainan-supermemory`
- **Versão:** `1.0.0`
- **Porta:** `8567` (mapeada para `6767` no container via `app_proxy`)
- **Acesso:** `http://10.203.0.1:8567`, LAN ou NetBird

---

## Componentes

1. **supermemory-server (v0.0.9):**
   - Binário oficial Linux x64 da release `server-v0.0.9` (`supermemoryai/supermemory`).
   - Checksum SHA256 verificado na partida: `d7987ecbf2b024c6de9d167becbcfcaabce447169b41d6f56db86f87d5187984`.
   - Dados e banco vetorial armazenados em `${APP_DATA_DIR}/data`.
   - Telemetria desativada (`SUPERMEMORY_DISABLE_TELEMETRY=1`, `NO_UPDATE_CHECK=1`, `NO_OPEN=1`).
   - Embeddings vetoriais locais rápidos com ONNX (`Xenova/bge-base-en-v1.5`).

2. **llmproxy (127.0.0.1:20129):**
   - Proxy local OpenAI-compatível entre o `supermemory-server` e o 9Router (`http://thainan-9router_web_1:20128/v1` ou `http://192.168.18.171:20128/v1`).
   - Modelo: `ag/gemini-3.8-flash-high`.
   - Agrega chunks SSE em respostas JSON estruturadas exigidas para reescrita de buscas e perfil do usuário.

3. **filter_stream & Segurança da Chave:**
   - O `supermemory-server` gera uma chave única `sm_...` no primeiro boot.
   - O `filter_stream.py` intercepta a saída em tempo real, grava a chave em `${APP_DATA_DIR}/data/local_key` com permissão restrita `600` e redige a chave no stdout (`sm_***REDACTED***`), impedindo vazamento nos logs do Docker e do painel Umbrel.

---

## Configuração da Chave do 9Router

A chave do 9Router nunca deve ser commitada no repositório. Para configurá-la no app:

1. **Via arquivo niner_key (recomendado):**
   Grave a chave no volume de dados do app com permissão 600:
   ```bash
   # No host Nonve ou via app-data:
   printf '%s' "sua-chave-do-9router" > /home/umbrel/umbrel/app-data/thainan-supermemory/data/niner_key
   chmod 600 /home/umbrel/umbrel/app-data/thainan-supermemory/data/niner_key
   ```
   *(Na VM Roberta, a chave de origem fica em `~/.sollum/llm.env` sob `NINER_KEY`).*

2. **Fallback automático:**
   Se o app `thainan-video-kb` estiver instalado no mesmo Umbrel e configurado com a chave do 9Router, o `thainan-supermemory` importa automaticamente a chave de `/vkb/config.json`.

---

## Leitura Segura da Chave Local

Para consultar ou utilizar a chave gerada pelo Supermemory sem exibi-la na tela ou em históricos:

```bash
# Verificar status da chave (sem imprimir):
bash app/read_key.sh status

# Exportar para a sessão atual:
eval $(bash app/read_key.sh export)

# Gravar em um arquivo .env com permissão 600:
bash app/read_key.sh to-env ~/.config/supermemory/local.env
```
