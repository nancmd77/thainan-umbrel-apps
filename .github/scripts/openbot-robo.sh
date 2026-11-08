#!/usr/bin/env bash
# Utilitários dos workflows do thainan-openbot: o robô diário (.github/workflows/openbot-upstream.yml) e o
# build manual ou por push (.github/workflows/thainan-openbot.yml). Os dois seguem a mesma ordem: build só
# com a tag candidate-<run>, travas, promoção das tags finais e limpeza das candidatas no fim.
#
# Uso:
#   openbot-robo.sh compose-check <docker-compose.yml>
#       Valida o compose com `docker compose config`. O app_proxy não tem image/build porque o Umbrel
#       o injeta; um override temporário, só para o teste, dá uma imagem a ele. O arquivo real não muda.
#   openbot-robo.sh promote <imagem> <digest> <tag>...
#       Dá as tags ao digest da candidata, sem rebuild (docker buildx imagetools create), e confere que
#       cada uma resolve para esse digest. <imagem> é o repositório sem tag (ghcr.io/nancmd77/thainan-openbot).
#   openbot-robo.sh issue-fail <título> <arquivo-corpo>
#       Se já há issue aberta com o label e o mesmo título, comenta nela; senão abre uma.
#   openbot-robo.sh issue-ok <url-do-run>
#       Comenta e fecha as issues abertas com o label.
#   openbot-robo.sh clean-candidates <owner> <pacote>
#       Remove do GHCR as tags candidate-<número> do pacote. Versão só com tags candidate-* é apagada;
#       versão que também tem tag final (promovida) só perde a tag candidata: a tag é movida para uma
#       imagem vazia descartável, e essa é que é apagada. Nunca apaga versão com outra tag.
#
# Variáveis: DRY_RUN=1 só imprime o que mudaria (gh/docker de escrita); ROBO_LABEL (padrão openbot-robo) e
# ROBO_LABEL_DESC (descrição do label ao criá-lo) separam as issues de cada workflow: issue-ok só fecha as
# do label dele; MAX_DELETE (padrão 20) limita quantas versões um run apaga; REGISTRY (padrão ghcr.io) só
# para teste local.
set -euo pipefail

LABEL=${ROBO_LABEL:-openbot-robo}
LABEL_DESC=${ROBO_LABEL_DESC:-Falha do robô diário do thainan-openbot}
MAX_DELETE=${MAX_DELETE:-20}
CAND_RE='^candidate-[0-9]+$'

run() { # executa comando de escrita, ou só imprime (stderr) em DRY_RUN
  if [ "${DRY_RUN:-0}" = 1 ]; then { printf 'DRY_RUN:'; printf ' %q' "$@"; echo; } >&2; else "$@"; fi
}

compose_check() {
  local file=$1 tmp rc=0
  tmp=$(mktemp -d)
  printf 'services:\n  app_proxy:\n    image: alpine:3.21\n' > "$tmp/override.yml"
  printf 'APP_DATA_DIR=%s\n' "$tmp/app" > "$tmp/compose.env"
  docker compose --env-file "$tmp/compose.env" -p compose-check \
    -f "$file" -f "$tmp/override.yml" config -q || rc=$?
  rm -rf "$tmp"
  [ "$rc" -eq 0 ] && echo "compose OK: $file"
  return "$rc"
}

promote() { # imagem digest tag...: dá as tags ao digest da candidata (sem rebuild) e confere cada uma
  local img=$1 digest=$2 tags=() t final
  shift 2
  [ "$#" -gt 0 ] || { echo "promote: informe ao menos uma tag" >&2; return 2; }
  for t in "$@"; do tags+=(-t "$img:$t"); done
  run docker buildx imagetools create "${tags[@]}" "$img@$digest"
  if [ "${DRY_RUN:-0}" = 1 ]; then return 0; fi
  for t in "$@"; do
    final=$(docker buildx imagetools inspect "$img:$t" --format '{{.Manifest.Digest}}')
    [ "$final" = "$digest" ] || { echo "digest de $img:$t ($final) difere do candidato $digest" >&2; return 1; }
  done
  echo "promovido: $img@$digest -> $*"
}

issue_fail() {
  local title=$1 body=$2 num
  run gh label create "$LABEL" --color B60205 --description "$LABEL_DESC" --force >/dev/null || true
  num=$(gh issue list --state open --label "$LABEL" --limit 100 --json number,title \
    | jq -r --arg t "$title" 'map(select(.title == $t)) | .[0].number // empty')
  if [ -n "$num" ]; then
    echo "issue #$num já aberta com o mesmo título; comentando"
    run gh issue comment "$num" --body-file "$body"
  else
    run gh issue create --title "$title" --label "$LABEL" --body-file "$body"
  fi
}

issue_ok() {
  local url=$1 num
  for num in $(gh issue list --state open --label "$LABEL" --limit 100 --json number --jq '.[].number'); do
    run gh issue close "$num" --comment "Run seguinte passou: $url"
  done
}

versions_tsv() { # id \t digest \t tags (vírgula) das versões com alguma tag candidate-*
  gh api --paginate "/users/$1/packages/container/$2/versions?per_page=100" \
    --jq '.[] | select(any(.metadata.container.tags[]?; startswith("candidate-")))
          | [.id, .name, (.metadata.container.tags | join(","))] | @tsv'
}

only_candidates() { # true se a lista (vírgula) não é vazia e todas as tags são candidate-<número>
  local t
  [ -n "$1" ] || return 1
  for t in ${1//,/ }; do [[ $t =~ $CAND_RE ]] || return 1; done
}

deleted=0
delete_version() { # owner pacote id: confere de novo as tags imediatamente antes de apagar
  local tags
  [ "$deleted" -lt "$MAX_DELETE" ] || { echo "limite MAX_DELETE=$MAX_DELETE atingido; resto fica para o próximo run"; return 1; }
  tags=$(gh api "/users/$1/packages/container/$2/versions/$3" --jq '.metadata.container.tags | join(",")')
  only_candidates "$tags" || { echo "recusado: versão $3 tem tags [$tags]" >&2; return 1; }
  # a função roda dentro de `||`, onde o set -e não vale: a falha do DELETE (403, 404) tem de sair explícita
  run gh api -X DELETE "/users/$1/packages/container/$2/versions/$3" >/dev/null || return 1
  deleted=$((deleted + 1))
  echo "apagada versão $3 [$tags]"
}

untag() { # owner pacote tag: move a tag para uma imagem vazia única e apaga essa imagem
  local owner=$1 pkg=$2 tag=$3 img dir digest id="" i
  img="${REGISTRY:-ghcr.io}/$owner/$pkg"
  dir=$(mktemp -d)
  printf 'FROM scratch\nLABEL org.thainan.untag=%s\n' "$tag-$(date +%s)" > "$dir/Dockerfile"
  if [ "${DRY_RUN:-0}" = 1 ]; then
    run docker buildx build --provenance=false --sbom=false --push -t "$img:$tag" "$dir"
    rm -rf "$dir"
    echo "DRY_RUN: apagaria a versão vazia que recebeu $tag"
    return 0
  fi
  docker buildx build -q --provenance=false --sbom=false --push -t "$img:$tag" \
    --metadata-file "$dir/meta.json" "$dir" >/dev/null
  digest=$(jq -r '."containerimage.digest"' "$dir/meta.json")
  rm -rf "$dir"
  for i in 1 2 3 4 5 6; do
    id=$(versions_tsv "$owner" "$pkg" | awk -F'\t' -v d="$digest" '$2 == d {print $1}')
    [ -n "$id" ] && break
    sleep $((i * 5))
  done
  [ -n "$id" ] || { echo "versão vazia $digest não apareceu na API" >&2; return 1; }
  delete_version "$owner" "$pkg" "$id"
}

clean_candidates() {
  local owner=$1 pkg=$2 id digest tags t rc=0 list
  list=$(versions_tsv "$owner" "$pkg")
  while IFS=$'\t' read -r id digest tags; do
    [ -n "$id" ] || continue
    if only_candidates "$tags"; then
      delete_version "$owner" "$pkg" "$id" </dev/null || rc=1
    else
      # promovida: mantém a versão e suas tags finais, tira só as candidate-*
      for t in ${tags//,/ }; do
        [[ $t =~ $CAND_RE ]] || continue
        echo "versão $id ($digest) tem tag final; retirando só $t"
        untag "$owner" "$pkg" "$t" </dev/null || rc=1
      done
    fi
  done <<< "$list"
  echo "versões apagadas: $deleted"
  return "$rc"
}

cmd=${1:-}
shift || true
case "$cmd" in
  compose-check) compose_check "$@" ;;
  promote) promote "$@" ;;
  issue-fail) issue_fail "$@" ;;
  issue-ok) issue_ok "$@" ;;
  clean-candidates) clean_candidates "$@" ;;
  *) sed -n '2,25p' "$0" >&2; exit 2 ;;
esac
