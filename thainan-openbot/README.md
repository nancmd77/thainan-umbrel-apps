# OpenBot (thainan-openbot)

Servidor headless do [OpenBot](https://github.com/nightly-labs/openbot) rodando no hardware do Nonve (Umbrel, amd64).

- **Imagem**: `ghcr.io/nancmd77/thainan-openbot:0.61.0-r4` (imagem derivada enxuta baseada na upstream oficial `ghcr.io/nightly-labs/openbot:v0.61.0@sha256:7eae38c2354f770bbf924657a908576e23c9bd3387efbf8e7fd52c059fe51a42`, com build definido em `thainan-openbot/image/Dockerfile`).
- **Segurança e Privilégios**:
  - `seccomp=${APP_DATA_DIR}/seccomp.json`: perfil seccomp dedicado do upstream oficial permitindo `clone` e `unshare` para o sandbox do Electron.
  - `apparmor=unconfined`: contorna a restrição de criação de user namespace (`CLONE_NEWUSER`) imposta pelo perfil `docker-default` do AppArmor em kernels modernos. **Documentação de risco**: com `apparmor=unconfined`, perde-se o confinamento de regras do perfil AppArmor `docker-default`; continuam ativos o perfil seccomp do upstream, execução como usuário UID 1000 e o isolamento interno do próprio Electron sandbox.
  - `sudo` sem senha: o usuário `openbot` (UID 1000) possui permissão NOPASSWD configurada em `/etc/sudoers.d/openbot` (permissão `0440`). Para viabilizar a transição de privilégios do binário setuid do sudo, o atributo `no-new-privileges` foi removido.
  - `shm_size: 1g`, `stop_grace_period: 60s`.
- **Gerenciador de Janelas (WM)**:
  - O gerenciador leve `openbox` é iniciado automaticamente em loop de supervisão no display dos agentes (`:1`, `OPENBOT_AGENT_DISPLAY`) assim que o Xvfb correspondente entra em operação, garantindo suporte a manipulação e posicionamento de janelas pelos agentes CUA.
- **Acessibilidade e AT-SPI**:
  - A imagem embute o pacote oficial `at-spi2-core` nativamente via apt.
  - O entrypoint inicializa o barramento D-Bus de acessibilidade com `/usr/share/defaults/at-spi2/accessibility.conf`, exporta `AT_SPI_BUS_ADDRESS`, propaga a propriedade X11 `AT_SPI_BUS` nas telas `:1` e `:0` via `xprop` e executa o `at-spi2-registryd` no display dos agentes (`:1`).
  - Mitigação preventiva para `NO_AT_BRIDGE`: configurado `ENV BASH_ENV=/etc/profile.d/a11y.sh` e inclusão em `/etc/bash.bashrc` (`unset NO_AT_BRIDGE`, `ACCESSIBILITY_ENABLED=1`, `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`). Em runtime, processos vivos inspecionados (`cua-driver`) não apresentaram a variável e processos Chromium usam `PR_SET_DUMPABLE=0`, restando confirmação em turno real.
  - **Pendência técnica (Split de barramento AT-SPI)**: com `at-spi2-core` em `/usr`, o session bus ativa `org.a11y.Bus` via `at-spi-bus-launcher` gerando um segundo barramento em `unix:path=/run/openbot/at-spi/bus` (ao qual o `cua-driver` se conecta), enquanto `at-spi2-registryd` roda no barramento manual (`/tmp/dbus-...` via `xprop AT_SPI_BUS`). A unificação dos dois barramentos fica mapeada para a próxima etapa.
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

---

## 4. Publicação da Imagem (GitHub Actions)

A imagem personalizada com `sudo`, `at-spi2-core`, `openbox` e neutralização do `NO_AT_BRIDGE` é compilada a partir de `thainan-openbot/image/Dockerfile`.

### Publicando pelo GitHub Actions

O repositório possui o workflow `.github/workflows/thainan-openbot.yml` que compila e publica automaticamente a imagem no GitHub Container Registry (GHCR):
1. Acesse a aba **Actions** no repositório GitHub e selecione o workflow **Build and Publish thainan-openbot Image**.
2. Clique em **Run workflow** (ou faça merge na branch `main` com alterações em `thainan-openbot/image/**`).
3. A action publicará a tag `ghcr.io/nancmd77/thainan-openbot:0.61.0-r4`.
4. Ao concluir, pegue o digest gerado na saída da action (ou via `docker pull ghcr.io/nancmd77/thainan-openbot:0.61.0-r4 && docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/nancmd77/thainan-openbot:0.61.0-r4`).
5. Atualize o campo `image` em `thainan-openbot/docker-compose.yml` para fixar pelo digest:
   ```yaml
   image: ghcr.io/nancmd77/thainan-openbot:0.61.0-r4@sha256:<DIGEST>
   ```

### Build e Teste Local

Para construir e validar a imagem localmente antes da publicação:
```bash
# Build local:
sudo -n docker build -t thainan-openbot:r4-teste thainan-openbot/image

# Override para teste local no docker-compose.override.yml:
# services:
#   openbot:
#     image: thainan-openbot:r4-teste
```

---

## 5. Aceleração por GPU e Compartilhamento com o Host (r5-gpu)

Esta branch (`feat/openbot-r5-gpu`) habilita o suporte à aceleração gráfica para o OpenBot no hardware do Nonve (notebook Acer Nitro 5 com processador Intel Core i5-12450H e placa de vídeo dedicada NVIDIA GeForce RTX 2050).

### 5.1 Aceleração DRI Resiliente (`/dev/dri`)

O `docker-compose.yml` monta nativamente o subsistema Direct Rendering Infrastructure do host:
```yaml
    devices:
      - /dev/dri:/dev/dri
    device_cgroup_rules:
      - 'c 226:* rmw'
      - 'c 195:* rmw'
      - 'c 509:* rmw'
      - 'c 511:* rmw'
```
- **Intel Iris Xe / UHD Graphics**: Sempre presente no kernel Linux (`/dev/dri/card0` e `/dev/dri/renderD128`). Provê aceleração por hardware (OpenGL, WebGL, renderização no display `:1`) aos navegadores e ferramentas dos agentes sem risco de falha na inicialização do container.
- **Permissões automáticas**: O entrypoint do OpenBot detecta `/dev/dri` e ajusta permissões (`chmod 666`) via sudo NOPASSWD para acesso irrestrito pelo usuário `openbot` (UID 1000).

### 5.2 NVIDIA RTX 2050 e Diagnóstico do Host

A habilitação da GPU dedicada NVIDIA para o OpenBot depende das informações expostas pelo diagnóstico do aplicativo **Economia** (`thainan-economia` v1.0.6) na porta 8535 (HTML ou endpoint JSON `/status.json`).

Campos reportados pelo bloco `gpu` do diagnóstico:
1. `lspci_01_00`: Confirma se a RTX 2050 (`01:00.0` VGA e `01:00.1` Audio) está conectada ao barramento PCI do host (acionada via `RTX=on` e `rescan`).
2. `lsmod_nvidia`: Informa se os módulos de kernel proprietários da NVIDIA (`nvidia`, `nvidia_modeset`, `nvidia_uvm`, `nvidia_drm`) estão carregados no host.
3. `dev_nvidia`: Lista os nós de dispositivo criados pelo driver (`/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`, `/dev/nvidia-modeset`).
4. `driver_version`: Versão do driver instalada em `/proc/driver/nvidia/version`.
5. `nvidia_smi`: Presença do binário `nvidia-smi` no host (verificado via nsenter).
6. `docker_daemon_json` e `nvidia_container_runtime`: Indica se o Docker do host possui configuração de runtime NVIDIA em `/etc/docker/daemon.json` e se o binário `nvidia-container-runtime` existe no PATH do host.

Com base no diagnóstico obtido, escolha uma das duas abordagens:

#### Opção 1: NVIDIA Container Toolkit (`deploy.resources.reservations.devices`)

**Quando usar**: Quando o diagnóstico indicar que o Docker do host possui runtime NVIDIA ativo (`docker_has_nvidia_runtime: true` e `nvidia_container_runtime: presente`).

Descomente o bloco `deploy` no serviço `openbot` em `docker-compose.yml`:
```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```
*Funcionamento*: O runtime do Docker injeta automaticamente os drivers de usuário, bibliotecas CUDA e utilitários NVIDIA dentro do container sem necessidade de mapear nós manualmente.

#### Opção 2: Bind Direto de Dispositivos (`/dev/nvidia*`)

**Quando usar**: Quando o diagnóstico indicar que os módulos de kernel NVIDIA estão carregados e os nós `/dev/nvidia*` existem no host, mas o Docker daemon do host ainda **não** possui o `nvidia-container-runtime` configurado em `/etc/docker/daemon.json`.

Descomente os nós correspondentes na lista `devices:` do serviço `openbot`:
```yaml
    devices:
      - /dev/dri:/dev/dri
      - /dev/nvidia0:/dev/nvidia0
      - /dev/nvidiactl:/dev/nvidiactl
      - /dev/nvidia-uvm:/dev/nvidia-uvm
      - /dev/nvidia-modeset:/dev/nvidia-modeset
    device_cgroup_rules:
      - 'c 226:* rmw'
      - 'c 195:* rmw'
      - 'c 509:* rmw'
      - 'c 511:* rmw'
```
*Funcionamento*: Os nós de hardware ficam disponíveis diretamente no container sob permissões de cgroup. O entrypoint ajusta automaticamente as permissões para o usuário `openbot`.

#### O que falta se o diagnóstico indicar ausência de driver ou runtime
- Se `lspci_01_00` mostrar a placa mas `lsmod_nvidia` estiver vazio: os drivers NVIDIA do kernel precisam ser instalados no Debian/Umbrel do host (`apt install nvidia-driver`).
- Se os nós `/dev/nvidia*` existirem mas o Docker não tiver runtime: instale o pacote `nvidia-container-toolkit` no host e execute `nvidia-ctk runtime configure --runtime=docker && systemctl restart docker`.
