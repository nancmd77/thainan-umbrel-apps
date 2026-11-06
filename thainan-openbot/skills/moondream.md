# Skill: moondream

Ferramenta CLI de mira visual (`md`) integrada com Moondream Cloud para operacao de computer use no display `:1`.

## Fluxo de Precedencia CUA
1. **AT-SPI primeiro**: Inspecione a arvore de acessibilidade da janela.
2. **decide escolhe**: Escolha o proximo passo com a CLI `decide`.
3. **Clique pela arvore**: Interaja diretamente com os nos acessiveis identificados na arvore.
4. **md point como mira visual**: Quando o elemento nao tiver rotulo acessivel, estiver em canvas/jogos/bitmaps ou a localizacao semantica falhar, acione `md point`.
5. **Modelo grande se exit 3**: Se `decide` retornar exit code 3 (`ESCALAR`), delegue a decisao ao modelo de raciocinio grande.
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
