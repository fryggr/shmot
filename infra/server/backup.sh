#!/usr/bin/env bash
# Логический бэкап PostgreSQL. Ежедневно через cron (см. docs/deploy.md); хранит 14 последних.
set -euo pipefail
cd "$(dirname "$0")/../.."

DIR=${BACKUP_DIR:-/var/backups/fashion}
KEEP=${BACKUP_KEEP:-14}
mkdir -p "$DIR"
FILE="$DIR/fashion-$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose --env-file .env.prod -f infra/compose.prod.yaml exec -T postgres \
  pg_dump -U fashion -d fashion --format=custom --no-owner > "$FILE.part"
mv "$FILE.part" "$FILE"
echo "backup: $FILE ($(du -h "$FILE" | cut -f1))"

ls -1t "$DIR"/fashion-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm --
