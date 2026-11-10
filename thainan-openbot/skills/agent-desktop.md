# Skill: agent-desktop

Programa de desktop na área remota (:1): leia e aja pela árvore de acessibilidade com o `agent-desktop`, sem print e sem chute de pixel. É o primeiro recurso; o Moondream fica de reserva.

## Ordem
1. Ache o app: `agent-desktop list-apps`. Abrir um: `agent-desktop launch <comando> --activate`.
2. Leia: `agent-desktop snapshot --app <nome> -i --compact`. Cada elemento acionável vem com um `ref_id` (ex. `@s1abc:e2`). O ref só vale pro snapshot que o gerou.
3. Aja pelo ref: `click`, `type <ref> "texto"`, `set-value`, `select`, `check`, `toggle`, `press return`. Pra achar um elemento sem ler a árvore toda: `agent-desktop find --app <nome> --name "Salvar"`.
4. Confira antes de dizer que terminou: `snapshot` de novo ou `agent-desktop wait --window "<título>" --app <nome>`.
5. Escolha difícil entre refs: `decide` com as opções do snapshot.
6. App fora da árvore (erro `APP_NOT_FOUND ... not on the accessibility bus`), canvas, jogo, imagem ou elemento sem nome: `md point '<alvo>' --click` (skill moondream).
7. Página web: navegador interno do OpenBot, não o agent-desktop. Chromium aberto à mão precisa de `--force-renderer-accessibility`; app Qt, de `QT_ACCESSIBILITY=1`.

## Notas
- O comando já carrega o barramento de acessibilidade e usa DISPLAY=:1 quando nada foi definido. Outra tela: `DISPLAY=:0 agent-desktop ...`.
- A saída é JSON. Com `ok:false`, leia `error.suggestion` antes de tentar de novo; não repita o mesmo comando mais de 2 vezes.
- Detalhes da versão instalada: `agent-desktop <comando> --help` e `agent-desktop skills get platform`.
- Mostre o que faz (skill cursor-hark): avise "vou fazer na área remota" antes de começar.
