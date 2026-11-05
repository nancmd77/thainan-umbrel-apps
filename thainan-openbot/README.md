# OpenBot (thainan-openbot)

Servidor headless do [OpenBot](https://github.com/nightly-labs/openbot) rodando no hardware do Nonve (Umbrel, amd64).

- **Imagem oficial**: `ghcr.io/nightly-labs/openbot:v0.61.0@sha256:7eae38c2354f770bbf924657a908576e23c9bd3387efbf8e7fd52c059fe51a42`
- **Segurança**: perfil seccomp dedicado (`seccomp.json`), `no-new-privileges: true`, `shm_size: 1g`, `stop_grace_period: 60s`.
- **Interface web**: O servidor expõe uma interface web mínima na porta 8592 (serviço sidecar leve conectado ao `/run/openbot/control.sock`). Permite verificar o status da conexão, solicitar código de acesso por e-mail para `thainancaldeira75@gmail.com`, confirmar o código numérico recebido e exibir o código QR para pareamento com o aplicativo móvel (Mobile Connect). O acesso após login também funciona pelo app desktop (macOS/Windows/Linux) ou em [https://openbot.run/app](https://openbot.run/app).

---

## 1. Login e Vinculação da Conta

Após iniciar o container no Umbrel, conecte o servidor à sua conta OpenBot.

### Login pela Web (Recomendado)
Acesse a interface do OpenBot na porta 8592 (pelo painel do Umbrel ou diretamente pelo navegador):
1. Clique em **Enviar código** para receber o código de verificação no e-mail `thainancaldeira75@gmail.com`.
2. Digite o código recebido no campo **Código de verificação** e clique em **Entrar**.
3. O status mudará para **Conectado** e o servidor aparecerá na sua conta no app desktop e em [openbot.run/app](https://openbot.run/app).
4. Para conectar o aplicativo móvel (iPhone), clique em **Conectar celular (código QR)** na página web e aponte a câmera do aplicativo.

### Login pelo Terminal
Se preferir autenticar via terminal no Nonve:
```bash
docker exec -it thainan-openbot_openbot_1 openbot login thainancaldeira75@gmail.com
```
O OpenBot enviará um código de verificação para o seu e-mail e solicitará a digitação do código no terminal (`Code: `).

### Conectar o Aplicativo de iPhone (Mobile Connect)
Para parear o app do iPhone sem precisar digitar senha:
```bash
docker exec -it thainan-openbot_openbot_1 openbot mobile-connect
```
Escaneie o QR Code exibido no terminal com o app OpenBot no iPhone.

### Login Não-Interativo
O comando `openbot login` exige um terminal (`[ -t 0 ]`). Para fluxos não interativos:

1. **Via socket Unix do container** (`/run/openbot/control.sock`):
   ```bash
   # 1. Inicia o desafio e obtém o challenge ID:
   CHALLENGE=$(docker exec thainan-openbot_openbot_1 curl -s --unix-socket /run/openbot/control.sock -X POST -d "email=thainancaldeira75@gmail.com" http://openbot/v1/login/start | sed -n 's/^challenge=//p')

   # 2. Envia o código recebido por email via stdin (sem expor em argumentos de processo):
   printf 'challenge=%s&code=%s' "$CHALLENGE" "<CODIGO>" | docker exec -i thainan-openbot_openbot_1 curl -s --unix-socket /run/openbot/control.sock -X POST -H "content-type: application/x-www-form-urlencoded" --data-binary @- http://openbot/v1/login/verify
   ```

2. **Via Server Claim (Operador)**:
   Se gerado um claim token pela API administrativa do OpenBot:
   ```bash
   echo "<CLAIM_TOKEN>" | docker exec -i thainan-openbot_openbot_1 openbot login --claim
   ```

---

## 2. Configuração do 9Router (Endpoint OpenAI-compatível)

O 9Router do Nonve roda em `http://192.168.18.171:20128/v1`.

### Opção A: Importação Automática do Video KB (Padrão)
O app monta automaticamente a pasta de dados do Video KB (`${APP_DATA_DIR}/../thainan-video-kb/data:/vkb:ro`) e extrai a chave `llm_key` de `/vkb/config.json`, gravando em `${APP_DATA_DIR}/data/9router_key` com permissão 600 e uid 1000 se o arquivo ainda não existir.

O arquivo `${APP_DATA_DIR}/data/.config/opencode/opencode.json` é gerado automaticamente apontando para `http://192.168.18.171:20128/v1` com `{env:OPENBOT_9ROUTER_KEY}`.

### Opção B: Manual via arquivo 9router_key
Caso queira definir manualmente uma chave diferente:
```bash
printf '%s' '<SUA_CHAVE_9ROUTER>' > ~/umbrel/app-data/thainan-openbot/data/9router_key
chmod 600 ~/umbrel/app-data/thainan-openbot/data/9router_key
chown 1000:1000 ~/umbrel/app-data/thainan-openbot/data/9router_key
```
O entrypoint do container lê `/data/9router_key` no início da execução e exporta como `OPENBOT_9ROUTER_KEY`.
3. No app OpenBot (desktop ou web), abra **Configurações do servidor** > **Provedores**, localize a linha **OpenCode** e clique em **Download** para baixar o runtime do OpenCode, que carregará o `opencode.json` com os modelos do 9Router.

Modelos pré-configurados:
- `ag/gemini-3.8-flash-high`
- `ag/gemini-4-argon-high`
- `cl/deepseek/deepseek-v4.1-flash-1017`

### Opção B: Pela Interface do OpenBot (Desktop ou openbot.run/app)
Como o OpenBot criptografa credenciais de provedores customizados via `safeStorage` (keyring do container em `/data`), você também pode cadastrar diretamente pela UI:

1. Acesse o OpenBot pelo aplicativo desktop ou [https://openbot.run/app](https://openbot.run/app).
2. Selecione o servidor do Nonve na lista de servidores.
3. Abra **Configurações do servidor** > **Provedores**.
4. Clique em **Adicionar um provedor personalizado** (predefinição **Endpoint compatível com OpenAI**).
5. Preencha os campos:
   - **ID do provedor**: `9router`
   - **Nome de exibição**: `9Router`
   - **Executa em**: `OpenCode`
   - **URL base**: `http://192.168.18.171:20128/v1`
   - **Chave de API**: Cole a sua chave do 9Router.
   - **Modelos**: Clique em **Buscar modelos** ou adicione manualmente (ex.: `ag/gemini-3.8-flash-high`).
6. Clique em **Enviar**. O OpenBot persiste em `/data/.config/OpenBot/openbot-custom-providers-v1.json` criptografado e ativa o provedor imediatamente.

---

## 3. Comandos Úteis

```bash
# Ver status do servidor, versão, memória e conta vinculada
docker exec -it thainan-openbot_openbot_1 openbot status

# Ver logs do OpenBot
docker logs -f thainan-openbot_openbot_1

# Desconectar conta
docker exec -it thainan-openbot_openbot_1 openbot logout
```
