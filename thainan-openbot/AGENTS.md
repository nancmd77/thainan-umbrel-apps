# Diretrizes para Agentes OpenBot (Computer Use)

## Fluxo de Decisao e Acoes do Agente (Display :1)

O agente de uso de computador (CUA) segue estritamente este pipeline por passo:

1. **AT-SPI (Leitura de Estado)**:
   - Inspecione a arvore de acessibilidade da janela ativa via AT-SPI. A leitura semantica e imediata, confiavel e sem custo de inferencia.

2. **decide escolhe (Decisao por Passo)**:
   - Execute a CLI `decide --objetivo '<meta>' --estado '<lista AT-SPI>' --opcao id='descricao' ...`.
   - O modelo leve calibrado (Liquid AI `d1-ultra` com reserva Jev `jev-latest` via API oficial da TypeSafe) seleciona a melhor opcao com confianca e custo minimo.

3. **Clique pela arvore**:
   - Apos a escolha do `decide`, clique diretamente no no da arvore AT-SPI correspondente ao elemento.

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

- **Mira e Clique**:
  `md point '<descricao do alvo>' --click`
  Localiza o elemento visual, move o cursor com glide suave estilo Hark (~12 passos, ~180 ms, com easing) e exibe um anel de pulso visual de 400 ms ao clicar.
- **Clique Duplo**:
  `md point '<alvo>' --double`
- **Clique com Botao Direito**:
  `md point '<alvo>' --right`
- **Modo Rapido (sem animacao)**:
  `md point '<alvo>' --click --sem-cursor`
- **Deteccao de Caixas Delimitadoras**:
  `md detect '<alvo>'`
- **Perguntas Visuais sobre a Tela (VQA)**:
  `md ask '<pergunta>'`

Display padrao: `:1` (pode ser sobrescrito com `--display :0` ou variavel `DISPLAY`).
Chave da API Moondream: `MOONDREAM_API_KEY` ou arquivo `/config/moondream.key` (painel web na porta 8592 rota `/config` ou `/moondream`).
