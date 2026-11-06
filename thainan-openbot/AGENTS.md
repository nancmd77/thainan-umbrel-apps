# Diretrizes para Agentes OpenBot (Computer Use)

## Ordem de Acoes para Interacao com a Interface (Display :1)

1. **AT-SPI primeiro (Acessibilidade)**:
   - Se o elemento existir na arvore de acessibilidade do sistema ou do aplicativo, utilize AT-SPI diretamente.
   - A leitura semantica via AT-SPI e a mais rapida, deterministica e nao depende de inferencia visual.

2. **Moondream Cloud (`md`) como fallback**:
   - Utilize a ferramenta de linha de comando `md` quando a aplicacao nao tiver suporte a acessibilidade (ex.: elementos renderizados em canvas HTML5, jogos, janelas legadas, bibliotecas graficas sem bridge AT-SPI ou quando a busca semantica falhar).
   - O comando `md point` captura a tela do display `:1`, redimensiona para <=1280px em JPEG q85, envia para Moondream Cloud e converte as coordenadas para pixels reais da tela.

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
Chave da API: configurada via variavel de ambiente `MOONDREAM_API_KEY` ou arquivo `/config/moondream.key` (pelo painel web na porta 8593 ou `/moondream`).
