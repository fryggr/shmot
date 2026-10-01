#!/usr/bin/env bash
# Обновление продакшена: подтянуть ветку, пересобрать, перезапустить. Запуск из корня репозитория.
#   ./infra/server/deploy.sh [ветка]
set -euo pipefail
cd "$(dirname "$0")/../.."

BRANCH=${1:-$(git rev-parse --abbrev-ref HEAD)}
COMPOSE=(docker compose --env-file .env.prod -f infra/compose.prod.yaml)

[ -f .env.prod ] || { echo "нет .env.prod (см. .env.prod.example)"; exit 1; }

PREV=$(git rev-parse HEAD)
git fetch origin "$BRANCH"
git checkout -q "$BRANCH"
git merge --ff-only "origin/$BRANCH"
echo "деплой $(git rev-parse --short HEAD) (было ${PREV:0:7})"

# Бэкап базы перед обновлением (миграции применяются автоматически сервисом init)
if "${COMPOSE[@]}" ps --status running postgres | grep -q postgres; then
  ./infra/server/backup.sh
fi

"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d --remove-orphans
"${COMPOSE[@]}" ps

for i in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready')" 2>/dev/null; then
    echo "API ready"; exit 0
  fi
  sleep 5
done
echo "API не стал ready за 150 с. Логи: ${COMPOSE[*]} logs --tail=100 api init"
echo "Откат кода: git checkout $PREV && ./infra/server/deploy.sh (миграции не откатываются — см. docs/deploy.md)"
exit 1
