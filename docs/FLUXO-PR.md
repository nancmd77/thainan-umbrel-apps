# Publicação da loja por PR

1. O omp faz fetch de `origin` e cria ramo e worktree próprios a partir de `origin/main`. Não altera o checkout base `~/trabalhos/loja`.
2. O omp implementa, testa e cria commits no próprio ramo. Faz somente `git push github-loja <ramo>` e para: nunca push na main, force push, merge local na main ou abertura de PR.
3. Depois do OK do nan, o Hark abre o PR e faz o merge. Comentários do PR-Agent não substituem a autorização humana.
4. Só depois do merge o Hark cria a tag anotada, apontando explicitamente para o commit de merge, não para o último commit do ramo. Não move tags existentes.
5. Antes do update, o Hark garante backup consistente de `/data`; aplica a versão e verifica o app no Umbrel. Veja [backup e reversão](REVERTER.md).

Estas regras substituem a publicação direta anterior. Também foram aplicadas em `~/sollum/omp-regras.md` e no trecho de release da memória da loja em `~/.omp/agent/memories/--home-roberta-trabalhos-loja--/MEMORY.md`. Ambos são locais, não versionados; os originais foram preservados ao lado com sufixo `.bak-1108pr` antes da alteração.

## Revisão automática

O [workflow](../.github/workflows/pr-agent.yml) usa a [imagem oficial do PR-Agent v0.50.0](https://github.com/The-PR-Agent/pr-agent/releases/tag/v0.50.0), variante Docker `0.50.0-github_action`, fixada por digest SHA256. O [wrapper oficial](https://github.com/The-PR-Agent/pr-agent/blob/v0.50.0/action.yaml) usa uma imagem móvel; por isso a imagem é referenciada diretamente.

O modelo é `inception/mercury-3`, sem modelos de fallback. A rota [nativa Inception do LiteLLM](https://docs.litellm.ai/docs/providers/inception) usa exclusivamente o secret `INCEPTION_API_KEY` como variável de mesmo nome; não é necessário configurar outro provedor. Sem esse secret, o job do agente é ignorado sem falha. Respostas e instruções são pt-BR. A disponibilidade real de Mercury 3 depende da conta Inception e só é comprovada por uma chamada autenticada.

PRs não rascunho do próprio repositório recebem descrição, revisão e sugestões ao abrir, reabrir, ficar prontos e sincronizar commits. Para evitar token de escrita ausente, os eventos automáticos de forks são ignorados. Em comentários de PR, somente proprietário, membros e colaboradores podem enviar `/review`, `/improve` ou `/describe` como texto exato, sem argumentos ou overrides de modelo. Comentários de bots ou issues comuns são ignorados. O workflow não faz checkout nem executa código do PR. O evento `issue_comment` só utiliza o workflow depois que ele existe no ramo padrão.

## Antes de proteger a main

A [auditoria dos workflows](AUDITORIA-WORKFLOWS.md) identifica dois robôs que ainda fazem push direto. Não ative exigência de PR sem migrá-los antes; esta alteração não modifica os robôs nem as configurações do GitHub.

## Verificação local

```sh
actionlint .github/workflows/pr-agent.yml
shellcheck scripts/backup-antes-update.sh
bash -n scripts/backup-antes-update.sh
```

Para um smoke de backup sem tocar no Umbrel, crie `/tmp/umbrel-teste/app-data/thainan-openbot/data`, grave ali um arquivo de teste e rode `UMBREL_ROOT=/tmp/umbrel-teste ./scripts/backup-antes-update.sh thainan-openbot`. Confira o conteúdo com `tar -tzf <caminho-impresso>`. Uma segunda execução no mesmo minuto deve falhar sem sobrescrever o primeiro arquivo.
