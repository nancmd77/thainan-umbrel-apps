#!/usr/bin/env bash
# Trava 1: os arquivos upstream que os patches do thainan-openbot dependem não podem mudar.
# Compara o sha256 de cada arquivo na tag nova com thainan-openbot/upstream-pins.sha256 (valores da
# tag fixada). Só baixa do upstream, nunca dos arquivos locais.
# Uso: check-upstream-pins.sh <repo> <tag-fixada> <tag-nova> <arquivo-pins> <arquivo-issue>
# Saída: 0 = todos iguais; 1 = algum hash mudou (corpo da issue em <arquivo-issue>); 2 = erro (download, pins inválido).
set -euo pipefail

repo=$1 old=$2 new=$3 pins=$4 issue=$5
base="https://raw.githubusercontent.com/$repo"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

[ -s "$pins" ] || { echo "pins vazio ou ausente: $pins" >&2; exit 2; }

fetch() { # tag caminho destino: falha se vazio ou erro HTTP
  mkdir -p "$(dirname "$3")"
  curl -sSfL --retry 3 --max-time 60 -o "$3" "$base/$1/$2" || return 1
  test -s "$3" || return 1
}

changed=()
while read -r want path; do
  [ -n "${want:-}" ] || continue
  [[ "$want" =~ ^[0-9a-f]{64}$ ]] || { echo "pin inválido: $want $path" >&2; exit 2; }
  fetch "$new" "$path" "$work/new/$path" || { echo "falha ao baixar $new:$path" >&2; exit 2; }
  got=$(sha256sum "$work/new/$path" | cut -d' ' -f1)
  [ "$got" = "$want" ] || changed+=("$path")
done < "$pins"

if [ "${#changed[@]}" -eq 0 ]; then
  echo "pins OK em $new"
  exit 0
fi

{
  echo "Arquivos upstream que os patches do thainan-openbot dependem mudaram: \`$old\` → \`$new\`."
  echo
  echo "O robô **não publicou** nada. Revise o diff, ajuste os patches e só depois atualize \`thainan-openbot/upstream-pins.sha256\`."
  for path in "${changed[@]}"; do
    fetch "$old" "$path" "$work/old/$path" || { echo "falha ao baixar $old:$path" >&2; exit 2; }
    echo
    echo "### \`$path\`"
    echo '```diff'
    diff -u "$work/old/$path" "$work/new/$path" | head -n 120 || true
    echo '```'
  done
} > "$issue"
echo "pins mudaram: ${changed[*]}" >&2
exit 1
