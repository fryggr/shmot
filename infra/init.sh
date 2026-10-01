#!/bin/sh
# Одноразовая инициализация: миграции, демо-источники и (по умолчанию) импорт демо-фидов.
set -eu
fashion-worker migrate
fashion-worker seed-demo
if [ "${DEMO_IMPORT_ON_START:-1}" = "1" ]; then
  fashion-worker import demo-shop-a
  fashion-worker import demo-shop-b
fi
