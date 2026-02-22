-- TimescaleDB is optional in Termux bare metal environments.
-- If it's not installed, keep the schema usable as plain PostgreSQL tables.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
  ELSE
    RAISE NOTICE 'timescaledb extension not available; skipping';
  END IF;
END $$;

-- Enums
CREATE TYPE platform_enum AS ENUM ('JD', 'TB', 'PDD', 'XIANYU');
CREATE TYPE category_enum AS ENUM ('GPU', 'CPU', 'SSD', 'RAM');
CREATE TYPE condition_enum AS ENUM ('NEW', 'OPEN_BOX', 'MINING', 'JUNK');
CREATE TYPE snipe_status_enum AS ENUM ('PENDING', 'NOTIFIED', 'IGNORED');

-- Layer 1: Raw lake (write-optimized)
CREATE TABLE raw_listings (
  id BIGSERIAL PRIMARY KEY,
  platform platform_enum NOT NULL,
  external_id TEXT NOT NULL,
  raw_title TEXT NOT NULL,
  raw_price NUMERIC(12,2) NOT NULL,
  seller_info JSONB,
  raw_html_snapshot TEXT,
  crawled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT raw_listings_external_id_uniq UNIQUE (external_id)
);

-- Layer 2: Canonical SKUs
CREATE TABLE standard_skus (
  id BIGSERIAL PRIMARY KEY,
  category category_enum NOT NULL,
  brand TEXT NOT NULL,
  model_name TEXT NOT NULL,
  key_specs JSONB NOT NULL DEFAULT '{}'::jsonb,
  release_price NUMERIC(12,2),
  CONSTRAINT standard_skus_brand_model_uniq UNIQUE (brand, model_name)
);
CREATE INDEX standard_skus_category_idx ON standard_skus(category);

-- Layer 3: Price history (TimescaleDB hypertable)
CREATE TABLE price_history (
  time TIMESTAMPTZ NOT NULL,
  sku_id BIGINT NOT NULL REFERENCES standard_skus(id),
  listing_id BIGINT NOT NULL REFERENCES raw_listings(id),
  price NUMERIC(12,2) NOT NULL,
  condition condition_enum NOT NULL,
  is_valid BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY (time, sku_id, listing_id)
);
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'create_hypertable') THEN
    PERFORM create_hypertable('price_history', 'time', if_not_exists => TRUE);
  ELSE
    RAISE NOTICE 'create_hypertable() not available; using plain table';
  END IF;
END $$;
CREATE INDEX price_history_sku_time_idx ON price_history (sku_id, time DESC);
CREATE INDEX price_history_listing_idx ON price_history (listing_id);

-- ROI metrics
CREATE TABLE pcdn_roi_metrics (
  sku_id BIGINT PRIMARY KEY REFERENCES standard_skus(id) ON DELETE CASCADE,
  daily_revenue_avg NUMERIC(12,2) NOT NULL,
  power_draw_watts INTEGER NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Xianyu snipes
CREATE TABLE xianyu_snipes (
  id BIGSERIAL PRIMARY KEY,
  listing_id BIGINT NOT NULL REFERENCES raw_listings(id) ON DELETE CASCADE,
  sniped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  confidence_score INTEGER NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
  reasoning TEXT NOT NULL,
  status snipe_status_enum NOT NULL DEFAULT 'PENDING',
  CONSTRAINT xianyu_snipes_listing_uniq UNIQUE (listing_id)
);
CREATE INDEX xianyu_snipes_status_idx ON xianyu_snipes(status);
