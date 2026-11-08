# Skill: moondream

Ferramenta CLI `md`: mira e clique com Moondream Cloud (`point`, `detect`, `click`) e descrição/perguntas sobre a tela com Qwen via Cerebras e reserva Gemini via 9Router (`ask`).

- `md point`, `md detect` e cliques: use para mirar e clicar quando o alvo não tem rótulo acessível (canvas, jogo, imagem) ou a árvore AT-SPI falha.
- `md ask`: use para descrever a tela ou fazer perguntas visuais (usa Qwen via Cerebras `cerebras/qwen-3.8-27b` rápido, com reserva Gemini `ag/gemini-3.8-flash-high`; coordenadas do ask são aproximadas e não servem pra clicar; fallback Moondream query com aviso se ambos falharem).
- Código 4 = tela vazia: se a captura tiver 2 cores ou menos (display sem janela visível mapeada), o CLI não chama nenhuma API e sai com código 4. Não insista: avise que não há janela visível.

## Fluxo de Precedencia CUA
1. **AT-SPI primeiro**: Inspecione a arvore de acessibilidade da janela.
2. **decide escolhe**: Escolha o proximo passo com a CLI `decide`.
3. **Clique pela arvore**: Interaja diretamente com os nos acessiveis identificados na arvore.
4. **md point como mira visual**: Quando o elemento nao tiver rotulo acessivel, estiver em canvas/jogos/bitmaps ou a localizacao semantica falhar, acione `md point`.
5. **Escalonamento (exit 3)**: Se o `decide` deu ESCALAR (exit 3, confiança < 0,35), leve a decisão difícil de desfazer (arquitetura, escolha de serviço, produção, apagar dados) ao conselho (máx. 1 vez por tarefa; só o Chefe chama; subagente devolve a decisão ao Chefe) e a decisão do dia a dia (inclui cliques de computer use, md/moondream) ao modelo grande de raciocínio com contexto completo; só pergunte ao Nan se o conselho também ficar com confiança baixa ou se a ação for destrutiva (que já exige OK do Nan).

## Comandos Principais
```bash
# Localizar e clicar no botao (Moondream)
md point "Send button" --click

# Duplo clique
md point "Document icon" --double

# Clique com botao direito
md point "Context menu target" --right

# Clique rapido sem animacao Hark
md point "Submit" --click --sem-cursor

# Detectar todos os objetos da classe
md detect "button"

# Descrever ou perguntar o estado da tela (Qwen via Cerebras / 9Router)
md ask "Descreva a tela e liste os elementos visiveis"
md ask "Qual mensagem de erro esta em destaque?"
# Trocar modelo do ask (força modelo único sem cadeia)
md ask "O que ha na tela?" --modelo ag/gemini-3.8-flash-high
```
