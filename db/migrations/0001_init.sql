-- Fashion Search POC — базовая схема (этап 1).
-- Все времена хранятся как timestamptz (UTC). Цены — целые числа в минорных единицах (копейки).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- источники
CREATE TABLE merchants (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    slug            text NOT NULL UNIQUE,
    allowed_domains text[] NOT NULL DEFAULT '{}',
    -- tracking-домены для affiliate URL разрешаются отдельно и явно
    affiliate_domains text[] NOT NULL DEFAULT '{}',
    is_demo         boolean NOT NULL DEFAULT false,
    enabled         boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feed_sources (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id       uuid NOT NULL REFERENCES merchants(id),
    slug              text NOT NULL UNIQUE,
    format            text NOT NULL CHECK (format IN ('yml')),
    -- ссылка на секрет (например, env:FEED_URL_DEMO_A); сам URL в базе не хранится
    secret_ref        text NOT NULL,
    mapping_version   text NOT NULL DEFAULT 'yml-v1',
    refresh_interval  interval NOT NULL DEFAULT interval '1 day',
    stale_after       interval NOT NULL DEFAULT interval '2 days',
    is_full_snapshot  boolean NOT NULL DEFAULT true,
    max_drop_ratio    numeric(4,3) NOT NULL DEFAULT 0.300,
    max_error_ratio   numeric(4,3) NOT NULL DEFAULT 0.050,
    enabled           boolean NOT NULL DEFAULT true,
    last_checked_at   timestamptz,
    last_published_run_id uuid,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE import_runs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id        uuid NOT NULL REFERENCES feed_sources(id),
    status           text NOT NULL CHECK (status IN
                        ('queued','running','staging','validated','held','published','unchanged','failed')),
    is_full_snapshot boolean NOT NULL,
    mapping_version  text NOT NULL,
    snapshot_uri     text,
    checksum         text,
    snapshot_bytes   bigint,
    triggered_by     text NOT NULL DEFAULT 'cli',
    held_reason      text,
    counts           jsonb NOT NULL DEFAULT '{}'::jsonb,
    audit            jsonb NOT NULL DEFAULT '{}'::jsonb,
    errors           jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    completed_at     timestamptz
);
CREATE INDEX import_runs_source_idx ON import_runs (source_id, created_at DESC);

ALTER TABLE feed_sources
    ADD CONSTRAINT feed_sources_last_run_fk FOREIGN KEY (last_published_run_id) REFERENCES import_runs(id);

-- Разобранные записи снимка до проверки и публикации.
CREATE TABLE staging_records (
    run_id        uuid NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    seq           integer NOT NULL,
    external_id   text,
    group_key     text,
    payload_json  jsonb NOT NULL,
    normalized    jsonb,
    content_hash  text NOT NULL,
    status        text NOT NULL CHECK (status IN ('pending','valid','quarantined','deleted')),
    error_codes   text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX staging_records_run_status_idx ON staging_records (run_id, status);

-- Последняя версия сырой записи + ссылка на снимок, из которого она пришла.
CREATE TABLE raw_records (
    source_id     uuid NOT NULL REFERENCES feed_sources(id),
    external_id   text NOT NULL,
    run_id        uuid NOT NULL REFERENCES import_runs(id),
    payload_json  jsonb NOT NULL,
    content_hash  text NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, external_id)
);

-- ------------------------------------------------------------ справочники
CREATE TABLE brands (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    slug       text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE brand_aliases (
    alias      text PRIMARY KEY,           -- нормализованная (lower/trim) форма
    brand_id   uuid NOT NULL REFERENCES brands(id)
);

CREATE TABLE categories (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id uuid REFERENCES categories(id),
    code      text NOT NULL UNIQUE,
    title     text NOT NULL
);

CREATE TABLE category_mappings (
    source_id          uuid NOT NULL REFERENCES feed_sources(id),
    source_category_id text NOT NULL,
    source_path        text NOT NULL,
    category_code      text REFERENCES categories(code),
    method             text NOT NULL,           -- rule | manual | unmapped
    updated_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, source_category_id)
);

-- ---------------------------------------------------------------- каталог
CREATE TABLE products (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id      uuid REFERENCES brands(id),
    category_id   uuid REFERENCES categories(id),
    title         text NOT NULL,
    description   text,
    gender        text NOT NULL DEFAULT 'unknown'
                  CHECK (gender IN ('women','men','unisex','kids','unknown')),
    color         text,                    -- нормализованный базовый цвет
    model_code    text,
    colorway_code text,
    is_demo       boolean NOT NULL DEFAULT false,
    -- Источник, создавший карточку: только он обновляет её основные поля
    origin_source_id   uuid,
    origin_external_id text,
    version       bigint NOT NULL DEFAULT 1,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX products_brand_category_idx ON products (brand_id, category_id);
CREATE INDEX products_model_idx ON products (brand_id, model_code, colorway_code);

CREATE TABLE product_attributes (
    id          bigserial PRIMARY KEY,
    product_id  uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    key         text NOT NULL,
    value_json  jsonb NOT NULL,
    provenance  text NOT NULL CHECK (provenance IN ('raw','merchant','rule','model','manual')),
    confidence  numeric(4,3) NOT NULL DEFAULT 1.0,
    source_id   uuid REFERENCES feed_sources(id),
    version     integer NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- Конфликтующие значения разных источников/провенансов сохраняются отдельными строками.
CREATE UNIQUE INDEX product_attributes_uniq
    ON product_attributes (product_id, key, provenance, coalesce(source_id, '00000000-0000-0000-0000-000000000000'::uuid));

CREATE TABLE offers (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id        uuid NOT NULL REFERENCES products(id),
    merchant_id       uuid NOT NULL REFERENCES merchants(id),
    source_id         uuid NOT NULL REFERENCES feed_sources(id),
    external_id       text NOT NULL,
    title             text NOT NULL,
    product_url       text NOT NULL,
    affiliate_url     text,
    currency          text NOT NULL,
    price_minor       bigint NOT NULL CHECK (price_minor > 0),
    old_price_minor   bigint CHECK (old_price_minor IS NULL OR old_price_minor > price_minor),
    availability      text NOT NULL CHECK (availability IN ('in_stock','out_of_stock','unknown')),
    content_hash      text NOT NULL,
    last_seen_at      timestamptz NOT NULL,
    source_updated_at timestamptz,
    imported_at       timestamptz NOT NULL,
    active            boolean NOT NULL DEFAULT true,
    last_run_id       uuid REFERENCES import_runs(id),
    UNIQUE (source_id, external_id)
);
CREATE INDEX offers_product_active_idx ON offers (product_id, active);
CREATE INDEX offers_last_seen_idx ON offers (last_seen_at);

CREATE TABLE offer_variants (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    offer_id            uuid NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    external_variant_id text NOT NULL,
    size_raw            text,
    size_system         text,             -- RU | EU | US | UK | INT | ONE | UNKNOWN; NULL = размер не передан
    size_label          text,
    size_kind           text,             -- apparel | shoes | accessory | unknown
    price_minor         bigint CHECK (price_minor IS NULL OR price_minor > 0),
    old_price_minor     bigint,
    availability        text NOT NULL CHECK (availability IN ('in_stock','out_of_stock','unknown')),
    gtin                text,
    UNIQUE (offer_id, external_variant_id)
);
CREATE INDEX offer_variants_offer_idx ON offer_variants (offer_id);
CREATE INDEX offer_variants_size_idx ON offer_variants (size_system, size_label, availability);

CREATE TABLE product_images (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    offer_id   uuid REFERENCES offers(id) ON DELETE CASCADE,
    url        text NOT NULL,
    position   integer NOT NULL,
    source_id  uuid NOT NULL REFERENCES feed_sources(id),
    UNIQUE (offer_id, position)
);

CREATE TABLE product_embeddings (
    product_id   uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    model_id     text NOT NULL,
    dimension    integer NOT NULL,
    content_hash text NOT NULL,
    vector       vector,
    status       text NOT NULL DEFAULT 'pending',
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, model_id)
);

CREATE TABLE dedup_links (
    offer_id      uuid PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE,
    product_id    uuid NOT NULL REFERENCES products(id),
    method        text NOT NULL,        -- source_group | gtin | brand_model_colorway | manual
    evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence    numeric(4,3) NOT NULL DEFAULT 1.0,
    reviewed_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- -------------------------------------------------------------- индексация
CREATE TABLE index_outbox (
    id             bigserial PRIMARY KEY,
    entity_type    text NOT NULL DEFAULT 'product',
    entity_id      uuid NOT NULL,
    entity_version bigint NOT NULL,
    action         text NOT NULL CHECK (action IN ('upsert','delete')),
    status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed')),
    attempts       integer NOT NULL DEFAULT 0,
    last_error     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    processed_at   timestamptz
);
CREATE INDEX index_outbox_status_idx ON index_outbox (status, id);

-- Лексический индекс этапа 1 (PostgreSQL FTS). OpenSearch подключается на этапе 3
-- тем же outbox-потоком.
CREATE TABLE search_documents (
    product_id     uuid PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    entity_version bigint NOT NULL,
    tsv            tsvector NOT NULL,
    indexed_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX search_documents_tsv_idx ON search_documents USING gin (tsv);

-- -------------------------------------------------------------- аналитика
CREATE TABLE search_sessions (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    anonymous_session_id text,
    experiment_arm       text NOT NULL,
    query                text NOT NULL,
    parsed_json          jsonb NOT NULL DEFAULT '{}'::jsonb,
    ranking_version      text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX search_sessions_created_idx ON search_sessions (created_at);

CREATE TABLE events (
    id         bigserial PRIMARY KEY,
    search_id  uuid,
    event_type text NOT NULL,
    product_id uuid,
    offer_id   uuid,
    rank       integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    dedup_key  text NOT NULL UNIQUE
);
CREATE INDEX events_created_idx ON events (created_at);
