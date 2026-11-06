# Skill: moondream

Ferramenta CLI de mira visual (`md`) integrada com Moondream Cloud para operacao de computer use no display `:1`.

## Regra de Precedencia
- **AT-SPI primeiro**: Sempre verifique a arvore de acessibilidade antes de acionar visao computacional.
- **Moondream fallback**: Acione `md point` quando a janela nao expuser objetos acessiveis ou falhar a localizacao semantica.

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
