#!/usr/bin/env bash
# Проверка восстановления: разворачивает последний бэкап во временную базу и считает строки.
# Боевую базу не трогает. ./infra/server/restore-check.sh [файл.dump]
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR=${BACKUP_DIR:-/var/backups/fashion}
FILE=${1:-$(ls -1t "$DIR"/fashion-*.dump | head -1)}
C=(docker compose --env-file .env.prod -f infra/compose.prod.yaml exec -T postgres)

"${C[@]}" psql -U fashion -d postgres -qc "DROP DATABASE IF EXISTS restore_check" -c "CREATE DATABASE restore_check"
"${C[@]}" pg_restore -U fashion -d restore_check --no-owner < "$FILE"
"${C[@]}" psql -U fashion -d restore_check -c \
  "SELECT (SELECT count(*) FROM products) products, (SELECT count(*) FROM offers WHERE active) active_offers,
          (SELECT max(completed_at) FROM import_runs WHERE status='published') last_publish"
"${C[@]}" psql -U fashion -d postgres -qc "DROP DATABASE restore_check"
echo "restore OK: $FILE"
