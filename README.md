# Fashion Search POC — этап 1 (основа)

Поисковик одежды, обуви и аксессуаров из нескольких интернет-магазинов с переходом к продавцу.
Этот репозиторий содержит **этап 1** из ТЗ: monorepo, импорт фидов с защитой каталога, модель данных
с размерными вариантами, базовый API, worker и минимальный frontend.

> **Только демонстрационные данные.** Подключены два вымышленных магазина (Demo Shop A и Demo Shop B,
> домены `.example`) с 30 моделями. Реальные магазины не подключались, affiliate-доступ не имитируется.
> Это не работающий поиск по реальному рынку.

## Запуск

```bash
docker compose -f infra/compose.yaml up --build
```

* Web: <http://localhost:3000>
* API и Swagger: <http://localhost:8000/docs>
* Сервис `init` один раз применяет миграции, заводит демо-источники и импортирует демо-фиды
  (отключить импорт: `DEMO_IMPORT_ON_START=0`).
* PostgreSQL стенда доступен с хоста на `localhost:55432` (не 5432 — чтобы не конфликтовать с локальным
  Postgres). Если заняты 8000/3000: `API_HOST_PORT=8001 WEB_HOST_PORT=3001 docker compose ...`.
* Свои значения переменных (токены, URL фидов): `docker compose --env-file .env -f infra/compose.yaml up --build`.
* OpenSearch нужен с этапа 3: `docker compose -f infra/compose.yaml --profile search up`.

Сервисы: PostgreSQL 16 + pgvector, Redis (очередь заданий), SeaweedFS (локальное S3-совместимое
хранилище закрытых снимков фидов, порт наружу не публикуется), API (FastAPI), worker, web (Next.js).

### Без Docker (разработка)

Нужны Python 3.11, Node 22, PostgreSQL 16 с расширением pgvector, Redis.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock -r requirements-dev.lock
pip install -e packages/domain -e services/api -e services/worker
cp .env.example .env && set -a && . ./.env && set +a

fashion-worker migrate          # миграции db/migrations/*.sql
fashion-worker seed-demo        # демо-продавцы, источники, справочник категорий
fashion-worker import demo-shop-a
fashion-worker import demo-shop-b
uvicorn fashion_api.main:app --reload --port 8000
fashion-worker run              # очередь заданий + обработка outbox

cd apps/web && npm ci && API_INTERNAL_URL=http://localhost:8000 npm run dev
```

Обновление демо-фидов: `python tests/fixtures/generate_demo_feeds.py`. Второй снимок магазина A
(`demo_shop_a_v2.yml`: пропала рубашка, подешевел тренч, закончился размер кроссовок):
`FEED_URL_DEMO_SHOP_A=fixture://demo_shop_a_v2.yml fashion-worker import demo-shop-a`.

Импорт, восстановление, held-публикации, перестройка индекса — [docs/operations.md](docs/operations.md).

## Тесты

Интеграционные тесты работают с настоящим PostgreSQL (нужен pgvector) — для каждого прогона
создаётся и удаляется отдельная база.

```bash
TEST_DATABASE_URL=postgresql://fashion:fashion@localhost:5432/postgres pytest
cd apps/web && npm run typecheck && npm run build
```

Что проверяется (`tests/`):

| Область | Тесты |
|---|---|
| Импорт | полный снимок, аудит и raw provenance; повторный импорт (unchanged и force) ничего не добавляет; delta удаляет только по явному `deleted="true"`; пропавший offer становится inactive; изменение/удаление вариантов; duplicate SKU, неверная цена, чужой домен, нет фото, валюта вне allowlist → карантин; некорректная old_price не становится скидкой |
| Защита каталога | обрезанный XML, XXE, billion laughs, выход за каталог фикстур, нет секрета → `failed`, каталог не изменён; пустой фид, падение объёма >30%, ошибки >5% → `held`; ручное подтверждение; held не перекрывает более новый снимок; падение внутри публикации откатывает всё; параллельный импорт источника блокируется |
| Поиск | размер и цена — в одном варианте; размер магазина A и цена магазина B не складываются; цена «от» пересчитывается после фильтра; RU/EU не смешиваются; `unknown` ≠ в наличии; устаревший источник; снятый товар; материал — только подтверждённый (экокожа ≠ кожа); фасеты; подсказки при нуле результатов |
| API | валидация (q ≤ 500, limit 1–48, размер только с системой, цена ≥ 0, валюта), курсор: mismatch / подделка / истечение; outbound только на разрешённые домены (+ affiliate только с явным tracking-доменом); internal auth; rate limit; секреты не утекают |
| Дедуп | одна модель+колорвей в двух магазинах — одна карточка с объяснением; другой цвет и одинаковое название разных моделей не сливаются; конфликт категорий блокирует слияние |
| Сквозной | fixture → import → индекс → запрос с размером → карточка → разрешённый outbound |

## Устройство

```text
apps/web/                 Next.js: /, /search, /product/[id], /brand/[slug]
services/api/fashion_api/ FastAPI: routers/, schemas.py, services/ (search, products)
services/worker/fashion_worker/
  adapters/               yml.py (потоковый безопасный разбор), download.py (секреты, allowlist, лимиты)
  jobs/                   import_job.py, validate.py (нормализация + карантин), publish.py
  indexing/outbox.py      outbox → индекс, сверка, перестройка
packages/domain/          общий пакет: настройки, БД/миграции, правила нормализации, хранилище снимков
packages/contracts/openapi.json
db/migrations/            SQL-миграции
config/                   taxonomy.yaml, color_aliases.yaml, material_aliases.yaml
tests/                    fixtures/, integration/, search/
infra/                    compose.yaml, Dockerfile'ы, init.sh
docs/                     source-audit.md, operations.md
```

Python-пакеты названы `fashion_api` / `fashion_worker` / `fashion_domain` вместо `app` из ТЗ, чтобы API,
worker и тесты можно было ставить в одно окружение без конфликта имён. Нормализация лежит в общем
пакете `fashion_domain`, т.к. её правила нужны и API (словари фильтров), и worker.

### Поток импорта

`секрет → download (fixture:// или http(s) с allowlist хостов и запретом приватных IP, лимиты размера/времени)
→ закрытый снимок + checksum → (тот же checksum → unchanged) → потоковый разбор YML в staging батчами
→ проверка/нормализация → аудит → защитные пороги → публикация одной транзакцией + outbox → индекс`.

* **Модель данных.** `products` — модель в одном цвете; `offers` — предложение магазина
  (`UNIQUE(source_id, external_id)`, где external_id = `group_id|код цвета`); `offer_variants` —
  размерный вариант со своей ценой и наличием. Цены — целые копейки.
* **Нормализация.** Только детерминированные правила по явным полям: категория по пути категории
  источника, цвет/материал по словарям, размер — система из `unit` либо однозначной метки
  (XS…XXL → INT), иначе `UNKNOWN`. Без конвертаций RU↔EU, без выдуманных размеров, наличие без
  атрибута — `unknown`. Конфликтующие значения разных продавцов хранятся отдельно (`product_attributes`).
* **Дедупликация.** По GTIN или бренд + код модели + код цвета при совпадении категории и цвета;
  каждое решение записано в `dedup_links` с evidence. Иначе — отдельная карточка.
* **Индекс этапа 1** — PostgreSQL FTS (`search_documents`), обновляется из `index_outbox` идемпотентно
  по `entity_version`. OpenSearch и pgvector подключаются к тому же outbox на этапах 3–4.

### Поиск этапа 1 (baseline, режим A)

Слова запроса ищутся лексически (русский стеммер, OR + `ts_rank_cd`), фильтры передаются явно.
Товар попадает в выдачу, только если **один вариант одного предложения** одновременно подходит по
размеру, наличию (и свежести), цене, валюте и магазину; цена в карточке — цена этого варианта
(`matched_offer_id`). Окно выдачи — первые 500 результатов, курсор подписан и привязан к запросу,
фильтрам, сортировке, режиму и версии индекса.

## Чего нет на этапе 1 (следующие итерации)

* разбор запроса (правила + LLM), `disabled_inferred_filters` принимаются, но пока ни на что не влияют;
  `parsed.parser_version = "none"`, поэтому «до 30 тысяч» в тексте запроса не превращается в фильтр;
* embeddings, semantic retrieval, fusion, режим B и A/B-распределение — этап 4; benchmark — `evaluation/`;
* OpenSearch-индексатор — этап 3 (сервис в compose уже есть под профилем `search`);
* клиентские события аналитики (`POST /api/v1/events`) и полноценные состояния UI — этап 5;
  сейчас сервер пишет только факт отданного редиректа;
* AI enrichment, CSV/API-адаптеры, реальные источники (см. [docs/source-audit.md](docs/source-audit.md));
* стабильный снимок результатов между страницами (PIT/список ID): сейчас при изменении индекса курсор
  возвращает `cursor_expired` и UI предлагает начать заново;
* rate limit — в памяти процесса API (для нескольких реплик перенести в Redis).
