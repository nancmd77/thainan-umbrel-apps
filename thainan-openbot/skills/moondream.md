# Skill: moondream

Ferramenta CLI de mira visual (`md`) integrada com Moondream Cloud para operacao de computer use no display `:1`.

## Fluxo de Precedencia CUA
1. **AT-SPI primeiro**: Inspecione a arvore de acessibilidade da janela.
2. **decide escolhe**: Escolha o proximo passo com a CLI `decide`.
3. **Clique pela arvore**: Interaja diretamente com os nos acessiveis identificados na arvore.
4. **md point como mira visual**: Quando o elemento nao tiver rotulo acessivel, estiver em canvas/jogos/bitmaps ou a localizacao semantica falhar, acione `md point`.
5. **Escalonamento (exit 3)**: Se o `decide` deu ESCALAR (exit 3, confiança < 0,35), leve a decisão difícil de desfazer (arquitetura, escolha de serviço, produção, apagar dados) ao conselho (máx. 1 vez por tarefa; só o Chefe chama; subagente devolve a decisão ao Chefe) e a decisão do dia a dia (inclui cliques de computer use, md/moondream) ao modelo grande de raciocínio com contexto completo; só pergunte ao Nan se o conselho também ficar com confiança baixa ou se a ação for destrutiva (que já exige OK do Nan).
## Comandos Principais
```bash
# Localizar e clicar no botao
md point "Send button" --click

# Duplo clique
md point "Document icon" --double

# Clique com botao direito
md point "Context menu target" --right

# Clique rapido sem animacao Hark
md point "Submit" --click --sem-cursor

# Detectar todos os objetos da classe
md detect "button"

# Perguntar o estado visual da tela
md ask "Qual mensagem de erro esta em destaque?"
```
