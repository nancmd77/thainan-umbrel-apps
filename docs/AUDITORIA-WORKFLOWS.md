# Push direto na main

Auditoria da base `6a2fbfe29c2ddfd794928ffa2879465e8d0ebe7a`. Nenhum dos arquivos auditados foi alterado.

| Arquivo | Push direto na main? | Como |
| --- | --- | --- |
| `.github/workflows/auto-update.yml` | Sim, nas execuções agendadas com mudanças e main como ramo padrão. | `contents: write`, checkout do ramo padrão, commit do bump e `git push` sem ref explícita (linha 35). Em disparo manual, depende do ramo selecionado. |
| `.github/workflows/openbot-upstream.yml` | Sim, explicitamente. | Checkout `ref: main`, commit, `git pull --rebase origin main`, tag anotada local e `git push --atomic origin HEAD:refs/heads/main "refs/tags/thainan-openbot-v$REV"` (linha 143), com `GITHUB_TOKEN`. Antes desse push, já promove as tags finais no GHCR. |
| `.github/workflows/thainan-openbot.yml` | Não. | Escuta push na main ou disparo manual; publica imagens/tags no GHCR, não commits ou tags Git. Usa `contents: read`. |
| `.github/workflows/umbrel-app-store.yml` | Não existe nesta base. | O arquivo raiz `umbrel-app-store.yml` é somente o manifesto `id`/`name` da loja, não um workflow. |

O `auto-update.yml` da raiz também é configuração dos apps para o bump, não um workflow.

## Efeito da proteção

Exigir PR na main sem exceções de bypass bloqueará os pushes de `auto-update.yml` e `openbot-upstream.yml`. No segundo, o push atômico também impede a publicação da tag Git, mas a promoção no GHCR já pode ter ocorrido: a falha deixa imagem e loja fora de sincronia.

Antes de ativar essa exigência sem quebrar os robôs, migre ambos para enviar ramos candidatos e encerrar. O Hark abre o PR e faz merge após o OK do nan. A tag anotada só vem depois, no commit de merge; a promoção final da imagem deve acompanhar essa ordem. Esta auditoria não migra os robôs, não cria bypass e não altera a proteção da main.
