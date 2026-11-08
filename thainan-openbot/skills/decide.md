# Skill: decide

Ferramenta CLI de modelo de decisão por passo (`decide`) integrada com Liquid AI SystemOne (`d1-ultra`) e reserva Jev (`jev-latest`) via API oficial da TypeSafe para operação de computer use.

## Fluxo de Decisão do Agente
1. **AT-SPI**: inspecione a árvore de acessibilidade do sistema e da janela aberta.
2. **decide escolhe**: passe o objetivo, o texto do estado atual (árvore AT-SPI) e as opções com `--opcao id='descricao'`.
3. **Clique pela árvore**: execute a ação selecionada referenciando o identificador do elemento acessível na árvore.
4. **md point**: se a interface não expuser rótulo acessível ou for renderizada em canvas/bitmap, utilize mira visual (`md point`).
5. **Escalonamento (exit 3)**: Se o `decide` deu ESCALAR (exit 3, confiança < 0,35), leve a decisão difícil de desfazer (arquitetura, escolha de serviço, produção, apagar dados) ao conselho (máx. 1 vez por tarefa; só o Chefe chama; subagente devolve a decisão ao Chefe) e a decisão do dia a dia (inclui cliques de computer use, md/moondream) ao modelo grande de raciocínio com contexto completo; só pergunte ao Nan se o conselho também ficar com confiança baixa ou se a ação for destrutiva (que já exige OK do Nan).

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
- `3`: Confiança `< limiar`. Aplique o passo 5 do fluxo.
