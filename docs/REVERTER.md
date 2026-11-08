# Como reverter

## Um PR

No PR mesclado, use o botão **Revert** do GitHub. O Hark abre o PR de reversão e faz o merge depois do OK do nan.

Alternativa, em ramo e worktree próprios a partir da main atualizada:

```sh
git revert -m 1 <commit-de-merge>
git push github-loja <ramo-de-reversao>
```

O Hark abre o PR desse ramo. Não reescreva o histórico: nada de reset da main ou force push. `-m 1` se aplica a um commit de merge com a main como primeiro pai; em merge por squash, reverta o commit sem `-m 1`.

## Uma versão no Umbrel

Consulte o [histórico](../CHANGELOG.md) e volte à tag anterior do app. O Umbrel não tem um seletor genérico de tags: em um ramo da loja, recupere da tag anterior os arquivos do app (incluindo manifesto, compose, referências de código e digest da imagem). Envie o ramo; o Hark abre o PR e faz o merge após o OK do nan. Depois, o Hark aplica essa revisão no Umbrel e confere a versão em execução. Não mova nem recrie tags existentes. Não desinstale o app para reverter.

## Os dados de /data

Antes do update, pare o app pelo Umbrel para obter uma cópia consistente e execute no host Umbrel, com um usuário que possa ler os dados:

```sh
./scripts/backup-antes-update.sh thainan-openbot
```

O script só lê `/home/umbrel/umbrel/app-data/<id>/data` e imprime o arquivo em `/home/umbrel/umbrel/home/Documents/Backups`. Mantém os cinco backups mais recentes por app e recusa sobrescrever um backup no mesmo minuto. Configure `UMBREL_ROOT`, `UMBREL_APP_DATA_DIR` (diretório que contém os apps) e/ou `BACKUP_DIR` se sua instalação usa outros caminhos. O script não para nem reinicia apps. Arquivos em uso podem produzir uma cópia inconsistente ou interromper o tar.

Para restaurar, mantenha o app parado e preserve os dados atuais fora de `data`. Restaure o tar.gz feito **antes** do update em um diretório `data` vazio do mesmo app: `tar -xzf <backup.tar.gz> -C <diretorio-data-vazio>`. O arquivo contém os conteúdos de `data`, inclusive os ocultos, não uma pasta `data` externa. Confira permissões/dono e a versão compatível do app antes de iniciá-lo. Voltar a tag não desfaz migrações de dados.

O caminho padrão de umbrelOS 1.x é definido no [serviço oficial](https://github.com/getumbrel/umbrel/blob/master/packages/os/overlay/etc/systemd/system/umbrel.service); cada app usa [app-data/<id>](https://github.com/getumbrel/umbrel/blob/master/packages/umbreld/source/modules/apps/app.ts).
