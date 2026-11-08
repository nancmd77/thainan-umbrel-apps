#!/usr/bin/env bash
set -euo pipefail
umask 077
export LC_ALL=C

if [[ $# != 1 || ! $1 =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  printf 'Uso: %s <id-do-app> (ex.: thainan-openbot)\n' "$0" >&2
  exit 2
fi
app_id=$1
umbrel_root=${UMBREL_ROOT:-/home/umbrel/umbrel}
app_data_root=${UMBREL_APP_DATA_DIR:-$umbrel_root/app-data}
backup_dir=${BACKUP_DIR:-$umbrel_root/home/Documents/Backups}
data_dir=$(realpath -e -- "$app_data_root/$app_id/data")
if [[ ! -d $data_dir ]]; then
  printf 'Diretório de dados inexistente: %s\n' "$data_dir" >&2
  exit 1
fi
backup_dir=$(realpath -m -- "$backup_dir")
case "$backup_dir/" in
  "$data_dir/"*)
    printf 'O destino do backup não pode ficar dentro dos dados do app.\n' >&2
    exit 1
    ;;
esac

mkdir -p -- "$backup_dir"
archive="$backup_dir/$app_id-$(date +%Y%m%d-%H%M).tar.gz"
if [[ -e $archive || -L $archive ]]; then
  printf 'Backup deste minuto já existe; nada sobrescrito: %s\n' "$archive" >&2
  exit 1
fi
temporary=$(mktemp "$backup_dir/.$app_id.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Não segue links internos nem altera permissões/dono dos dados de origem.
tar -czf "$temporary" -C "$data_dir" .
# Publicação atômica sem sobrescrever, mesmo em execuções simultâneas.
if ! ln -- "$temporary" "$archive"; then
  printf 'Backup deste minuto já existe; nada sobrescrito: %s\n' "$archive" >&2
  exit 1
fi

# O nome ordena cronologicamente; só arquivos deste app entram na retenção.
shopt -s nullglob
backups=()
for file in "$backup_dir/$app_id"-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9].tar.gz; do
  if [[ -f $file && ! -L $file ]]; then
    backups+=("$file")
  fi
done
for ((i = 0; i < ${#backups[@]} - 5; i++)); do
  rm -f -- "${backups[i]}"
done
printf '%s\n' "$archive"
