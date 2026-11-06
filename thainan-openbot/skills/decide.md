# Skill: decide

Ferramenta CLI de modelo de decisão por passo (`decide`) integrada com Liquid AI SystemOne (`d1-ultra`) e reserva Jev (`jev-latest`) via API oficial da TypeSafe para operação de computer use.

## Fluxo de Decisão do Agente
1. **AT-SPI**: inspecione a árvore de acessibilidade do sistema e da janela aberta.
2. **decide escolhe**: passe o objetivo, o texto do estado atual (árvore AT-SPI) e as opções com `--opcao id='descricao'`.
3. **Clique pela árvore**: execute a ação selecionada referenciando o identificador do elemento acessível na árvore.
4. **md point**: se a interface não expuser rótulo acessível ou for renderizada em canvas/bitmap, utilize mira visual (`md point`).
5. **Modelo grande**: se a confiança for inferior ao limiar (`--limiar`, padrão 0.35), o `decide` imprime `ESCALAR` e sai com código 3. O agente deve escalar para o modelo grande de raciocínio.

## Comandos Principais
```bash
# Decisao de proximo passo entre opcoes
decide --objetivo "Enviar formulario de contato preenchido" \
       --estado "Janela Contato: [btn: Enviar, id=btn_send], [btn: Cancelar, id=btn_cancel]" \
       --opcao btn_send='Clicar no botao Enviar' \
       --opcao btn_cancel='Cancelar operacao'

# Com arquivo de imagem de apoio (apenas se necessario)
decide --objetivo "Selecionar opcao correta no dialogo" \
       --estado "Dialogo de confirmacao sem texto acessivel" \
       --opcao opt_yes='Confirmar gravacao' \
       --opcao opt_no='Rejeitar gravacao' \
       --imagem /tmp/screenshot.png

# Saida estruturada em JSON
decide --objetivo "Selecionar aba" \
       --estado "Abas: [aba: Geral, id=tab_general], [aba: Avancado, id=tab_adv]" \
       --opcao tab_general='Aba Geral' \
       --opcao tab_adv='Aba Avancado' \
       --json

# Ajuste de limiar de confianca
decide --objetivo "Acao critica" \
       --estado "..." \
       --opcao op1='...' \
       --opcao op2='...' \
       --limiar 0.50
```

## Codigos de Saida
- `0`: Sucesso. A melhor opção foi selecionada com confiança `>= limiar`.
- `1`: Erro geral, chave ausente ou parâmetros inválidos.
- `3`: Confiança `< limiar`. Saída: `ESCALAR`. O agente deve recorrer ao modelo grande de raciocínio.
