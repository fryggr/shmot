# Эксплуатация (этап 1)

Все команды worker: `fashion-worker <команда>` (в compose: `docker compose -f infra/compose.yaml exec worker fashion-worker <команда>`).

| Задача | Команда |
|---|---|
| Миграции | `fashion-worker migrate` |
| Демо-источники и справочник категорий | `fashion-worker seed-demo` |
| Импорт источника | `fashion-worker import demo-shop-a [--force]` |
| Аудит run | `fashion-worker audit <run_id>` |
| Подтвердить удержанный (held) run | `fashion-worker publish <run_id> --approve` |
| Восстановить каталог источника из последнего принятого снимка | `fashion-worker restore demo-shop-a` |
| Обработать outbox | `fashion-worker outbox` |
| Сверка индекса с каталогом | `fashion-worker reconcile` |
| Полная перестройка индекса | `fashion-worker rebuild-index` |
| Очистка по срокам хранения | `fashion-worker cleanup` |
| Цикл worker (очередь Redis + outbox) | `fashion-worker run` |

Через API (нужен `Authorization: Bearer $INTERNAL_API_TOKEN`):

```bash
curl -X POST localhost:8000/internal/imports -H "Authorization: Bearer $T" \
     -H 'content-type: application/json' -d '{"source":"demo-shop-a"}'
curl localhost:8000/internal/imports/<run_id> -H "Authorization: Bearer $T"
curl -X POST localhost:8000/internal/imports/<run_id>/publish -H "Authorization: Bearer $T" \
     -H 'content-type: application/json' -d '{"approve":true}'
```

## Статусы import_runs

`queued → running → staging → validated → published`; побочные исходы:

* `unchanged` — checksum совпал с последним опубликованным снимком; обновлена только проверка свежести
  (`offers.last_seen_at`, `feed_sources.last_checked_at`), повторной обработки нет.
* `held` — публикация остановлена защитным порогом (`held_reason`): доля карантина > `max_error_ratio`
  (по умолчанию 5%), падение числа валидных записей > `max_drop_ratio` (30%) относительно последнего
  полного снимка, пустой полный снимок. Каталог не изменён; staging сохранён до решения.
  Порог настраивается на источник (`feed_sources.max_error_ratio`, `max_drop_ratio`).
* `failed` — ошибка скачивания/разбора/публикации (`errors[].code`). Публикация — одна транзакция,
  поэтому каталог остаётся в последнем корректном состоянии.

Удержанный run нельзя опубликовать, если после него уже опубликован более новый снимок.

## Свежесть

`stale_after` (по умолчанию 2 дня = два интервала обновления). Offer с `last_seen_at` старше порога:
не попадает в выдачу в режиме «в наличии», в карточке — предупреждение. Повторный успешный импорт
(в т.ч. `unchanged`) снимает флаг. `imported_at` — время получения, а не время изменения у магазина.

## Метрики для мониторинга (SQL на этапе 1)

* свежесть: `SELECT slug, last_checked_at FROM feed_sources`;
* импорт: `import_runs` — `status`, `audit->'error_ratio'`, `audit->'seconds'`;
* outbox lag: `fashion-worker reconcile` → `pending`, `oldest_pending_seconds` (оповещение при > 900 с);
* ошибки поиска и p95: JSON-логи API (`path`, `status`, `ms`).

## Сроки хранения

Снимки — 14 дней (кроме нужных для восстановления: удаляйте только после следующего принятого),
`import_runs`/аудит — 90 дней, `search_sessions`/`events` — 30 дней. `fashion-worker cleanup`.
IP-адреса в событиях не хранятся; путь запроса логируется без query string.
