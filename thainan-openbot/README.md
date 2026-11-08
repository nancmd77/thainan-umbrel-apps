# OpenBot (thainan-openbot)

Servidor headless do [OpenBot](https://github.com/nightly-labs/openbot) rodando no hardware do Nonve (Umbrel, amd64).

- **Imagem**: `ghcr.io/nancmd77/thainan-openbot:0.62.0-r2` (imagem derivada enxuta baseada na upstream oficial `ghcr.io/nightly-labs/openbot:v0.62.0@sha256:e0673c02cf6a9e576a613f2fe2220d76fda64cacc2c563385e460450419dadac`, com build definido em `thainan-openbot/image/Dockerfile`).
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
- **Mira Visual e Moondream Cloud (r8)**:
  - Ferramenta de linha de comando `md` (`python3`, urllib) instalada no container com subcomandos `point`, `detect` e `ask`.
  - Captura direta da tela no display dos agentes (`:1`), com redução proporcional para largura <=1280px em JPEG q85 para máxima velocidade e menor latência de rede.
  - **Ordem obrigatória para agentes**: (1) AT-SPI primeiro via árvore de acessibilidade; (2) `md point` como fallback para interfaces sem suporte a a11y (canvas, jogos, apps legados).
  - **Cursor estilo Hark**: movimentação com glide suave via `xdotool` em 12 passos (~180 ms, smoothstep easing) e anel de pulso visual ciano de 400 ms ao clicar (override-redirect sem roubar foco). Suporte à flag `--sem-cursor` para automação sem animação.
  - **Gestão de Chave**: lida da variável de ambiente `MOONDREAM_API_KEY` ou de `${APP_DATA_DIR}/config/moondream.key` (permissão 600). Página de configuração unificada na porta 8592 (rotas `/config` e `/moondream`) com máscara segura (nunca exibe a chave).
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
3. A action compila a imagem só com a tag `candidate-<run>`, roda o smoke (`thainan-openbot/smoke-custom.sh`) e valida o `docker-compose.yml` (`.github/scripts/openbot-robo.sh compose-check`). Só se tudo passar ela dá ao mesmo digest as tags finais `ghcr.io/nancmd77/thainan-openbot:<VERSION>` (por exemplo `0.62.0-r2`, lido de `thainan-openbot/image/VERSION`) e `latest`. Se algo falhar, nenhuma tag final muda e o run abre uma issue com o label `openbot-manual` (ou comenta na aberta de mesmo título). A tag `candidate-*` sai do GHCR no fim de qualquer run.
4. Ao concluir, pegue o digest gerado na saída da action (ou via `docker pull ghcr.io/nancmd77/thainan-openbot:0.62.0-r2 && docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/nancmd77/thainan-openbot:0.62.0-r2`).
5. Atualize o campo `image` em `thainan-openbot/docker-compose.yml` para fixar pelo digest:
   ```yaml
   image: ghcr.io/nancmd77/thainan-openbot:0.62.0-r2@sha256:<DIGEST>
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

## 5. Aceleração por GPU, Suporte a CUDA e Compartilhamento com o Host (r7)

A partir da versão `0.61.0-r7`, o OpenBot conta com suporte automático e resiliente à aceleração gráfica e CUDA no hardware do Nonve (RTX 2050 dedicada e Intel DRI integrada), sem depender de `nvidia-container-toolkit` e sem risco de quebra na inicialização em hosts sem GPU.

### 5.1 Arquitetura de Isolamento e Segurança

1. **Montagens Read-Only e Preservação do Container**:
   - O diretório `/dev` do host é montado como `/host/dev:ro`. Isso evita sobrepor o `/dev` interno do container, preservando intactos o `/dev/shm` (com `shm_size: 1g`), `/dev/pts` e `/dev/mqueue`, além de impedir qualquer alteração nas permissões reais dos nós do host (`chmod`).
   - Diretórios userspace do host são montados em modo somente leitura (`:ro`):
     - `/usr/lib/x86_64-linux-gnu` -> `/host/lib:ro`
     - `/usr/bin` -> `/host/bin:ro`
     - `/usr/share` -> `/host/share:ro`
   - Todos esses caminhos existem nativamente no Debian/Ubuntu do host, eliminando falhas de montagem na criação do container.

2. **Regras de Cgroup Seguras**:
   - O container define exclusivamente a regra `device_cgroup_rules`: `'c *:* rmw'`, liberando todos os nós de caractere do host (NVIDIA, DRI e UVM) sem necessidade de regras de major dinâmico fixo (como o antigo 510).
   - Dispositivos de bloco (`'b'`, discos e partições de armazenamento) permanecem totalmente isolados fora do container (aprovado por nan em 2026-11-05 22:06), preservando o isolamento e execução sob UID 1000 com seccomp.
### 5.2 Detecção e Injeção Userspace em Tempo de Execução

Ao iniciar o container:
1. **Com GPU NVIDIA presente (`/host/dev/nvidia0`)**:
   - Recria os nós `/dev/nvidia*` localmente no container com permissão `666` via `mknod` seguro (consultando major e minor do host), permitindo acesso irrestrito pelo UID 1000 sem alterar arquivos do host.
   - Cria `/usr/local/nvidia/lib` com symlinks dedicados das bibliotecas do driver do host: `libnvidia*`, `libcuda*`, `libEGL_nvidia*`, `libGLX_nvidia*`, `libnvidia-egl*` e `vdpau/nvidia`.
   - Configura o GLVND vendor EGL em `/usr/share/glvnd/egl_vendor.d/10_nvidia.json` e o Vulkan ICD em `/usr/share/vulkan/icd.d/nvidia_icd.json`.
   - Cria symlink para `nvidia-smi` em `/usr/local/bin/nvidia-smi` caso presente no host.
   - Registra o caminho no linker (`/etc/ld.so.conf.d/00-nvidia.conf`, `ldconfig`), exporta `LD_LIBRARY_PATH` e define `__GLX_VENDOR_LIBRARY_NAME=nvidia`.
2. **Sem GPU NVIDIA (ou em hosts sem placa dedicada como VMs)**:
   - O container detecta a ausência de `/host/dev/nvidia0` e opera transparentemente em modo CPU/DRI sem injetar bibliotecas ou alterar o GLVND/LD_LIBRARY_PATH, garantindo total compatibilidade.

### 5.3 Processos GL, Suporte a CUDA Automático e nvidia-uvm

- **Processos com GL vs Electron**: O processo gráfico principal do Electron upstream sobe com `--use-gl=disabled`. Portanto, a disponibilização dos nós `/dev/nvidia*` e das bibliotecas userspace atende prioritariamente a subprocessos dos agentes, navegadores externos (Chromium/Chrome) e ferramentas que executem pipelines gráficos ou multimídia (Mesa/GLX/Vulkan).
- **Dispositivos necessários**: Para operações gráficas e GLX/EGL, os dispositivos `nvidia0`, `nvidiactl` e `nvidia-modeset` (major 195) são provisionados em `/dev`.
- **CUDA automático sem dependência de major dinâmico fixo**: Os dispositivos `/dev/nvidia-uvm` e `/dev/nvidia-uvm-tools` são utilizados por aplicações CUDA/GPGPU. No kernel Linux, seus majors são alocados dinamicamente:
  - A regra cgroup atual é exclusivamente `'c *:* rmw'`, e não regras pontuais de major fixo (como 510). O acesso cgroup a qualquer major de caractere é liberado sem necessidade de intervenção manual.
  - O entrypoint recria automaticamente `/dev/nvidia-uvm` e `/dev/nvidia-uvm-tools` lendo dinamicamente o major e minor atuais de `/host/dev` via `stat`.
  - O entrypoint executa `nvidia-smi -L` na inicialização para reportar o diagnóstico das GPUs detectadas nos logs.
---

## 6. Mira Visual com Moondream Cloud (r8)

A partir da versão `0.61.0-r8`, o OpenBot integra a Moondream Cloud API como ferramenta de mira visual para agentes CUA (Computer Use Agents) no display `:1`.

### 6.1 Ordem de Precedência para Agentes
1. **AT-SPI primeiro**: Sempre que o elemento existir na árvore de acessibilidade do sistema ou aplicativo, interaja diretamente pela semântica do AT-SPI. É determinístico, rápido e sem custo de inferência visual.
2. **Moondream Cloud (`md point`) como fallback**: Utilize quando o aplicativo não tiver suporte a acessibilidade (canvas WebGL, interfaces sem bridge a11y, menus renderizados em bitmap, jogos) ou quando a busca semântica não localizar o elemento.

### 6.2 Comandos da CLI `md`
- **Regra de ouro**: `md point`, `md detect` e cliques continuam no Moondream Cloud para mirar e clicar. Para **descrever a tela**, use `md ask` (agora Gemini via 9Router).
- **Código 4 = tela vazia**: checagem obrigatória em todos os subcomandos (`point`, `detect`, `ask`). Se a miniatura 64x64 da captura tiver 2 cores ou menos (Xvfb sem janela mapeada), o `md` não chama nenhuma API, imprime `tela vazia: display <X> sem janela visivel` e sai com **código 4**. Os agentes são instruídos a não insistir e avisar que não há janela visível.

```bash
# Mira e clique no elemento com glide e pulso Hark (Moondream)
md point "Send button" --click

# Duplo clique ou clique com botão direito
md point "File icon" --double
md point "Table row" --right

# Clique rápido sem animações
md point "Confirm" --click --sem-cursor

# Detecção de caixas delimitadoras de múltiplos objetos (Moondream)
md detect "text input"

# Descrever a tela ou perguntas visuais (Gemini via 9Router)
md ask "Descreva a tela e liste as janelas e botões visíveis"
md ask "Qual formulário está aberto na tela?"

# Troca de modelo via flag
md ask "O que há na tela?" --modelo ag/gemini-3.8-flash-high
```

### 6.3 Configuração da Chave
- **Moondream Cloud**: lida de `MOONDREAM_API_KEY` ou de `/config/moondream.key` (painel web na porta 8592 rota `/config` ou `/moondream`).
- **9Router (para `md ask`)**: reutiliza o mesmo endpoint e chave do CLI `conselho` (`OPENBOT_9ROUTER_KEY` ou `/data/9router_key`, com endpoint em `CONSELHO_URL` ou padrão `http://192.168.18.171:20128/v1/chat/completions`). Nunca imprime a chave.
- Se o 9Router falhar, o `md ask` faz fallback para o Moondream query com o aviso explícito `(resposta do Moondream, menos confiável)`.
- **Códigos de Saída do `md`**: `0` sucesso; `1` erro geral / chave ausente; `2` nenhum alvo encontrado (`point`); `4` tela vazia (<= 2 cores, sem janela visível).
---

## 7. Modelo de Decisão por Passo com Liquid AI e Jev (r10)

A partir da versão `0.61.0-r9`, o OpenBot integra a CLI `decide` para que agentes de computer use tomem decisões determinísticas, rápidas e calibradas por passo sem overhead de geração textual. Na versão `0.61.0-r10`, a reserva Jev migra para a API oficial da TypeSafe.

### 7.1 Pipeline de Decisão CUA
1. **AT-SPI**: Leitura semântica imediata da árvore de acessibilidade da janela ativa.
2. **decide escolhe**: Seleção da melhor opção com a CLI `decide`, calibrada com probabilidades reais.
3. **Clique pela árvore**: Disparo da ação diretamente no nó acessível escolhido.
4. **md point**: Mira visual quando o alvo não possuir rótulo acessível ou estiver em canvas/bitmaps.
5. **Escalonamento (exit 3)**: Se o `decide` deu ESCALAR (exit 3, confiança < 0,35), leve a decisão difícil de desfazer (arquitetura, escolha de serviço, produção, apagar dados) ao conselho (máx. 1 vez por tarefa; só o Chefe chama; subagente devolve a decisão ao Chefe) e a decisão do dia a dia (inclui cliques de computer use, md/moondream) ao modelo grande de raciocínio com contexto completo; só pergunte ao Nan se o conselho também ficar com confiança baixa ou se a ação for destrutiva (que já exige OK do Nan).

### 7.2 Comandos da CLI `decide`
```bash
# Decisão entre opções semânticas
decide --objetivo "Confirmar encerramento da sessão" \
       --estado "Janela de diálogo: [btn: 'Sim, sair', id=btn_ok], [btn: 'Cancelar', id=btn_cancel]" \
       --opcao btn_ok='Confirmar saída da conta' \
       --opcao btn_cancel='Cancelar e continuar na sessão'

# Saída com JSON estruturado
decide --objetivo "Escolher aba ativa" \
       --estado "Abas: [id=tab_1, Perfil], [id=tab_2, Segurança]" \
       --opcao tab_1='Aba Perfil' \
       --opcao tab_2='Aba Segurança' \
       --json

# Com imagem de apoio opcional e limiar customizado
decide --objetivo "Clicar no ícone de salvar" \
       --estado "Toolbar de botões sem texto" \
       --opcao save='Botão com disquete' \
       --opcao discard='Botão com lixeira' \
       --imagem /tmp/toolbar.png \
       --limiar 0.40
```

### 7.3 Arquitetura de Provedores e Fallback Resiliente
1. **Liquid AI (Principal)**:
   - Endpoint: `POST https://api.liquid.ai/decisions/v1/systemone`
   - Modelo: `d1-ultra`
   - Chave: lida de `LIQUID_API_KEY` ou `/config/liquid.key` (permissão `600`).
2. **Jev via API oficial TypeSafe (Reserva)**:
   - Endpoint: `POST https://api.typesafe.ai/v1/systemone`
   - Modelo: `jev-latest` (mesmo schema da API Liquid)
   - Header: `Authorization: Bearer <chave>`
   - Header de imagem: `TypeSafe-Danger-Zone-Images: true` quando `--imagem` for fornecida.
   - Chave: lida de `TYPESAFE_API_KEY` ou `/config/typesafe.key` (permissão `600`).
   - Acionado automaticamente quando o Liquid AI falhar (erro HTTP ou timeout de 5 segundos).
3. **Gestão de Chaves na Web**:
   - As chaves podem ser configuradas no painel web unificado na porta `8592` (rotas `/config` e `/moondream`), gravando com permissão `600` e nunca criando portas adicionais.

### 7.4 Conselho de Modelos (`conselho`)
- Só em 4 casos: decisão difícil de desfazer (arquitetura, escolha de serviço, produção, apagar dados); dois nichados discordam, ou plano e revisão discordam; `decide` com confiança abaixo de 0,35 numa decisão difícil de desfazer (passo 5 de 7.1); o Nan pede "pergunta ao conselho". Nunca no dia a dia; no máximo 1 vez por tarefa; só o Chefe chama e o subagente devolve a decisão ao Chefe.
- Três modelos respondem em paralelo; um presidente recebe as respostas anônimas (A, B, C) e devolve recomendação, confiança, pontos de concordância e divergência.
- Padrão: `ag/gemini-3.8-flash-high` (presidente, primeiro da lista), `cerebras/gpt-oss-120b` e `cerebras/qwen-3.8-27b`; nenhum depende de assinatura Claude. Troca por `--modelos` ou `CONSELHO_MODELOS`; presidente por `CONSELHO_PRESIDENTE`; timeout por chamada por `CONSELHO_TIMEOUT` (padrão 90 s).
- Mínimo 2 respostas para haver síntese. Se o presidente falhar, o próximo candidato assume na mesma rodada.
- Registro em `/data/conselho.log`, sem chave e com pergunta resumida (até 200 caracteres).
```bash
conselho "Apagar a tabela antiga?" --contexto /tmp/plano.md
conselho "Escolher a rota de deploy?" --modelos ag/gemini-3.8-flash-high,cerebras/gpt-oss-120b --json
```
- Códigos de saída: `0` ok; `1` erro de uso, chave ausente ou nenhum modelo respondeu; `2` nenhum presidente conseguiu sintetizar (respostas brutas na saída).

### 7.5 Onde os agentes leem as skills (0.64.0-r4)
- Os agentes leem `~/.agents/skills/<nome>/SKILL.md` (`/data/.agents/skills/`). O init instala lá `conselho`, `decide` e `moondream`: front matter (`name`, `description`) mais o mesmo texto de `/data/skills/<nome>.md`. A cópia antiga em `/data/.config/opencode/skills/` continua.
- O init só grava `SKILL.md` se o arquivo não existir (edição do Nan é preservada). A cópia de fábrica anterior de um texto de fábrica (`AGENTS.md`, `skills/*.md`, `SKILL.md`, `bin/conselho`) só é trocada se o sha256 bater (`troca_fabrica`); release que mudar um deles soma o sha256 da versão anterior ali.
- O bloco `jeito-hark` do `AGENTS.md` de cada agente é recriado a cada início (o init remove o bloco antigo e injeta o novo), então quem já tinha a versão anterior recebe a linha nova do conselho.

### 7.6 Regra única do ESCALAR (0.64.0-r5)
- Antes, `AGENTS.md` e a skill `moondream` mandavam o exit 3 ao modelo grande, e a skill `decide` e o `jeito-hark` mandavam perguntar ao Nan. Agora a regra é uma só, com a mesma frase em todos os textos (passo 5 de 7.1): decisão difícil de desfazer vai ao conselho, decisão do dia a dia vai ao modelo grande de raciocínio com contexto completo, e o Nan só é perguntado se o conselho também ficar com confiança baixa ou se a ação for destrutiva.
- Textos com a regra: `/data/AGENTS.md`, `/data/skills/{conselho,decide,moondream}.md` (e a cópia em `/data/.config/opencode/skills/`), as `SKILL.md` de `conselho`, `decide`, `moondream`, `jeito-hark` e `cursor-hark` em `/data/.agents/skills/` e o bloco `jeito-hark` do `AGENTS.md` de cada agente.
- Quem já tem a r4: o bloco dos agentes e a `jeito-hark` são recriados a cada início; `/data/AGENTS.md`, `skills/*.md` e as `SKILL.md` de `conselho`, `decide`, `moondream` e `cursor-hark` só são trocados se forem a cópia de fábrica de uma versão anterior (sha256 igual). Arquivo editado pelo Nan fica como está.
### 7.7 md mais confiável (0.64.0-r6)
- **Separação de papéis**: `md point`/`detect` continuam no Moondream para mirar e clicar com precisão; `md ask` passa a usar Gemini via 9Router (`ag/gemini-3.8-flash-high`) para descrever a tela objetivamente sem inventar, listando janelas e botões com posições aproximadas.
- **Tela vazia (código 4)**: contagem de cores em miniatura 64x64 em todos os subcomandos; se `<= 2` cores (Xvfb sem janela mapeada), sai imediatamente com código 4 sem chamada de rede.
- **Fallback resiliente**: se o 9Router falhar no `md ask`, cai no Moondream query com aviso explícito `(resposta do Moondream, menos confiável)`.
- **Preservação de edições**: `troca_fabrica` atualizado com o sha256 das versões r5 dos arquivos alterados (`/data/AGENTS.md`, `skills/moondream.md`, `cursor-hark/SKILL.md`, `moondream/SKILL.md` e `bin/md`). Edições manuais do Nan continuam preservadas.
