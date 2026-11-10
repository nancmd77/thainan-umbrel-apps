# Diretrizes para Agentes OpenBot (Computer Use)

## Fluxo de Decisao e Acoes do Agente (Display :1)

O agente de uso de computador (CUA) segue estritamente este pipeline por passo:

1. **AT-SPI (Leitura de Estado)**:
   - Inspecione a arvore de acessibilidade com `agent-desktop snapshot --app <nome> -i --compact` (skill agent-desktop). A leitura semantica e imediata, confiavel e sem custo de inferencia.

2. **decide escolhe (Decisao por Passo)**:
   - Execute a CLI `decide --objetivo '<meta>' --estado '<lista AT-SPI>' --opcao id='descricao' ...`.
   - O modelo leve calibrado (Liquid AI `d1-ultra` com reserva Jev `jev-latest` via API oficial da TypeSafe) seleciona a melhor opcao com confianca e custo minimo.

3. **Clique pela arvore**:
   - Apos a escolha do `decide`, clique pelo ref: `agent-desktop click <ref>` (ou `type`, `set-value`), e confira com novo snapshot.

4. **md point se nao houver rotulo (Mira Visual)**:
   - Se o elemento nao possuir rotulo acessivel, estiver em canvas WebGL, bitmaps, jogos ou a arvore falhar, utilize `md point '<alvo>' --click` para mira visual com glide e pulso Hark.

5. **Escalonamento (exit 3)**:
   - Se o `decide` deu ESCALAR (exit 3, confiança < 0,35), leve a decisão difícil de desfazer (arquitetura, escolha de serviço, produção, apagar dados) ao conselho (máx. 1 vez por tarefa; só o Chefe chama; subagente devolve a decisão ao Chefe) e a decisão do dia a dia (inclui cliques de computer use, md/moondream) ao modelo grande de raciocínio com contexto completo; só pergunte ao Nan se o conselho também ficar com confiança baixa ou se a ação for destrutiva (que já exige OK do Nan).

## Ferramenta CLI `decide`

- **Decisao rapida entre opcoes**:
  `decide --objetivo '<meta>' --estado '<estado/arvore>' --opcao id='descricao' (repetivel)`
- **Com imagem de apoio opcional**:
  `decide --objetivo '<meta>' --estado '<estado>' --opcao id='descricao' --imagem arq.png`
- **Ajuste de limiar e saida JSON**:
  `decide --objetivo '<meta>' --estado '<estado>' --opcao id='descricao' --limiar 0.40 --json`
- **Chaves de API**:
  - Liquid AI: `LIQUID_API_KEY` ou `/config/liquid.key` (perm 600).
  - TypeSafe (Jev reserva): `TYPESAFE_API_KEY` ou `/config/typesafe.key` (perm 600).
  - Configuraveis no painel web na porta `8592` (rotas `/config` e `/moondream`).

## Ferramenta CLI `conselho`

- **Consulta a 3 modelos com síntese**: `conselho "<pergunta>" [--contexto arquivo|-] [--modelos a,b,c] [--json]`
- Só para decisões difíceis (ver skill `conselho`). Máximo 1 por tarefa; só o Chefe chama.
- Chave: `OPENBOT_9ROUTER_KEY` ou `/data/9router_key`. Nunca imprimir a chave.

## Ferramenta CLI `md`

- **Regra de ouro**: `md point`, `md detect` e cliques são para **mirar e clicar** (Moondream). Para **descrever a tela**, use `md ask` (Qwen via Cerebras rápido, com reserva Gemini via 9Router). As coordenadas do ask não servem pra clicar (use `md point`).
- **Código 4 = tela vazia**: se a miniatura tiver 2 cores ou menos (display sem janela visível mapeada), o `md` não chama nenhuma API, imprime "tela vazia: display <X> sem janela visivel" e sai com **código 4**. **Não insista**: avise que não há janela visível.
- **Mira e Clique (Moondream)**:
  `md point '<descricao do alvo>' --click`
  Localiza o elemento visual, move o cursor com glide suave estilo Hark (~12 passos, ~180 ms, com easing) e exibe um anel de pulso visual de 400 ms ao clicar.
- **Clique Duplo**:
  `md point '<alvo>' --double`
- **Clique com Botao Direito**:
  `md point '<alvo>' --right`
- **Modo Rapido (sem animacao)**:
  `md point '<alvo>' --click --sem-cursor`
- **Deteccao de Caixas Delimitadoras (Moondream)**:
  `md detect '<alvo>'`
- **Descricao e Perguntas Visuais (Qwen via Cerebras com reserva Gemini via 9Router)**:
  `md ask '<pergunta>' [--modelo <modelo>]`
  Modelo padrão: rápido `cerebras/qwen-3.8-27b`, com reserva `ag/gemini-3.8-flash-high` e fallback Moondream query com aviso explícito no stderr. Coordenadas do ask são aproximadas e não servem pra clicar.

Display padrao: `:1` (pode ser sobrescrito com `--display :0` ou variavel `DISPLAY`).
Chaves: 9Router em `OPENBOT_9ROUTER_KEY` ou `/data/9router_key`; Moondream em `MOONDREAM_API_KEY` ou `/config/moondream.key` (painel web na porta 8592 rota `/config` ou `/moondream`). Nunca imprima chaves.
