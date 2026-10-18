-- AisleMint PostgreSQL Schema
-- Auto-generated from mongo_schema.json
-- All 31 core collections converted to tables

CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT,
    workspace TEXT NOT NULL,
    name TEXT NOT NULL,
    request JSONB NOT NULL,
    result JSONB NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_scenarios_workspace_created
    ON scenarios (workspace, created);

CREATE TABLE IF NOT EXISTS audit (
    id BIGINT,
    event TEXT NOT NULL,
    workspace TEXT NOT NULL,
    payload JSONB NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (workspace, id)
);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT,
    name TEXT NOT NULL,
    quality JSONB NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS demand_datasets (
    id TEXT,
    name TEXT NOT NULL,
    quality JSONB NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS demand_jobs (
    source_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    updated TEXT NOT NULL,
    PRIMARY KEY (source_id)
);

CREATE TABLE IF NOT EXISTS inventory_plans (
    id TEXT,
    source_id TEXT NOT NULL,
    name TEXT NOT NULL,
    request JSONB NOT NULL,
    result JSONB NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_plans_source
    ON inventory_plans (source_id, created);

CREATE TABLE IF NOT EXISTS confirmed_promotions (
    id TEXT,
    workspace TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    request JSONB NOT NULL,
    result JSONB NOT NULL,
    review_token TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created TEXT NOT NULL,
    cancelled TEXT,
    model_id TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_confirmed_promotions_2
    ON confirmed_promotions (workspace, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS promotion_events (
    campaign_id TEXT NOT NULL,
    workspace TEXT NOT NULL,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    period_end TEXT NOT NULL,
    discount DOUBLE PRECISION NOT NULL,
    feature BIGINT NOT NULL,
    display BIGINT NOT NULL,
    active BIGINT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_promotion_period
    ON promotion_events (campaign_id, store_id, product_id, period_end);

CREATE INDEX IF NOT EXISTS idx_promotion_period_lookup
    ON promotion_events (workspace, store_id, product_id, period_end);

CREATE TABLE IF NOT EXISTS promotion_calendar_revisions (
    workspace TEXT,
    revision BIGINT NOT NULL,
    PRIMARY KEY (workspace)
);

CREATE TABLE IF NOT EXISTS pos_stores (
    id TEXT,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    state_code TEXT NOT NULL,
    gstin TEXT NOT NULL,
    gst_registered BIGINT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    demo BIGINT NOT NULL,
    forecast_workspace TEXT,
    forecast_store_id TEXT,
    promo_reference_date TEXT,
    revision BIGINT NOT NULL DEFAULT 1,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS pos_staff (
    id TEXT,
    store_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    salt TEXT NOT NULL,
    pin_hash TEXT NOT NULL,
    active BIGINT NOT NULL DEFAULT 1,
    failed_attempts BIGINT NOT NULL DEFAULT 0,
    locked_until TEXT,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_pos_staff_store
    ON pos_staff (store_id, active);

CREATE TABLE IF NOT EXISTS pos_sessions (
    token_hash TEXT,
    staff_id TEXT NOT NULL,
    expires TEXT NOT NULL,
    PRIMARY KEY (token_hash)
);

CREATE TABLE IF NOT EXISTS pos_products (
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    barcode TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    hsn TEXT NOT NULL,
    unit TEXT NOT NULL,
    price_paise BIGINT NOT NULL CHECK (price_paise >= 1),
    cost_paise BIGINT NOT NULL CHECK (cost_paise >= 0),
    gst_bps BIGINT NOT NULL CHECK (gst_bps >= 0),
    on_hand BIGINT NOT NULL CHECK (on_hand >= 0),
    reorder_level BIGINT NOT NULL,
    active BIGINT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (store_id, product_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_products_2
    ON pos_products (store_id, barcode);

CREATE TABLE IF NOT EXISTS pos_customers (
    id TEXT,
    store_id TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    address TEXT NOT NULL,
    state_code TEXT NOT NULL,
    gstin TEXT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_pos_customer_phone
    ON pos_customers (store_id, phone);

CREATE TABLE IF NOT EXISTS pos_shifts (
    id TEXT,
    store_id TEXT NOT NULL,
    register_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    opening_paise BIGINT NOT NULL,
    opened TEXT NOT NULL,
    closed TEXT,
    counted_paise BIGINT,
    expected_paise BIGINT,
    variance_paise BIGINT,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_open_register
    ON pos_shifts (store_id, register_id) WHERE closed IS NULL;

CREATE TABLE IF NOT EXISTS pos_cash_movements (
    id TEXT,
    store_id TEXT NOT NULL,
    shift_id TEXT NOT NULL,
    amount_paise BIGINT NOT NULL,
    reason TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS pos_counters (
    store_id TEXT NOT NULL,
    financial_year TEXT NOT NULL,
    kind TEXT NOT NULL,
    value BIGINT NOT NULL,
    PRIMARY KEY (store_id, financial_year, kind)
);

CREATE TABLE IF NOT EXISTS pos_sales (
    id TEXT,
    store_id TEXT NOT NULL,
    number TEXT NOT NULL,
    shift_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    customer_id TEXT,
    business_date TEXT NOT NULL,
    created TEXT NOT NULL,
    subtotal_paise BIGINT NOT NULL,
    tax_paise BIGINT NOT NULL,
    total_paise BIGINT NOT NULL,
    discount_paise BIGINT NOT NULL,
    cogs_paise BIGINT NOT NULL,
    cash_net_paise BIGINT NOT NULL,
    request_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_pos_sales_customer
    ON pos_sales (store_id, customer_id, created);

CREATE INDEX IF NOT EXISTS idx_pos_sales_store_date
    ON pos_sales (store_id, business_date, created);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_sales_3
    ON pos_sales (store_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_sales_2
    ON pos_sales (store_id, number);

CREATE TABLE IF NOT EXISTS pos_sale_lines (
    sale_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity BIGINT NOT NULL,
    paid_paise BIGINT NOT NULL,
    net_paise BIGINT NOT NULL,
    tax_paise BIGINT NOT NULL,
    cgst_paise BIGINT NOT NULL,
    sgst_paise BIGINT NOT NULL,
    igst_paise BIGINT NOT NULL,
    cost_paise BIGINT NOT NULL,
    regular_paise BIGINT NOT NULL,
    gst_bps BIGINT NOT NULL,
    returned BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (sale_id, product_id)
);

CREATE TABLE IF NOT EXISTS pos_returns (
    id TEXT,
    store_id TEXT NOT NULL,
    sale_id TEXT NOT NULL,
    number TEXT NOT NULL,
    shift_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    created TEXT NOT NULL,
    net_paise BIGINT NOT NULL,
    tax_paise BIGINT NOT NULL,
    total_paise BIGINT NOT NULL,
    cash_paise BIGINT NOT NULL,
    restock_cost_paise BIGINT NOT NULL,
    discard_cost_paise BIGINT NOT NULL,
    request_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_pos_returns_sale
    ON pos_returns (sale_id);

CREATE INDEX IF NOT EXISTS idx_pos_returns_store_date
    ON pos_returns (store_id, business_date);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_returns_3
    ON pos_returns (store_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_returns_2
    ON pos_returns (store_id, number);

CREATE TABLE IF NOT EXISTS pos_return_lines (
    return_id TEXT NOT NULL,
    line_index BIGINT NOT NULL,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity BIGINT NOT NULL,
    disposition TEXT NOT NULL,
    net_paise BIGINT NOT NULL,
    tax_paise BIGINT NOT NULL,
    cgst_paise BIGINT NOT NULL,
    sgst_paise BIGINT NOT NULL,
    igst_paise BIGINT NOT NULL,
    cost_paise BIGINT NOT NULL,
    PRIMARY KEY (return_id, line_index)
);

CREATE TABLE IF NOT EXISTS pos_sale_offers (
    sale_id TEXT NOT NULL,
    line_index BIGINT NOT NULL,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    saving_paise BIGINT NOT NULL,
    body JSONB NOT NULL,
    PRIMARY KEY (sale_id, line_index)
);

CREATE TABLE IF NOT EXISTS pos_inventory_movements (
    _sequence BIGINT NOT NULL,
    id TEXT,
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity BIGINT NOT NULL,
    balance BIGINT NOT NULL,
    kind TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    cost_paise BIGINT NOT NULL,
    staff_id TEXT NOT NULL,
    created TEXT NOT NULL,
    business_date TEXT NOT NULL,
    idempotency_key TEXT,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_pos_stock_product
    ON pos_inventory_movements (store_id, product_id, created);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_inventory_movements_2
    ON pos_inventory_movements (store_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS pos_carts (
    id TEXT,
    store_id TEXT NOT NULL,
    name TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    body JSONB NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_pos_carts_store
    ON pos_carts (store_id, created);

CREATE TABLE IF NOT EXISTS pos_events (
    sequence BIGINT,
    store_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (store_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_pos_events_store
    ON pos_events (store_id, sequence);

CREATE TABLE IF NOT EXISTS pos_days (
    store_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    closed TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    PRIMARY KEY (store_id, business_date)
);

CREATE TABLE IF NOT EXISTS pos_daily (
    store_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    body JSONB NOT NULL,
    PRIMARY KEY (store_id, product_id, business_date)
);

CREATE TABLE IF NOT EXISTS pos_model_jobs (
    id TEXT,
    store_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    source_id TEXT,
    error TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pos_pending_training
    ON pos_model_jobs (store_id, kind) WHERE status IN ('queued', 'training');

CREATE TABLE IF NOT EXISTS pos_drift_checks (
    id TEXT,
    store_id TEXT NOT NULL,
    created TEXT NOT NULL,
    event_sequence BIGINT NOT NULL,
    result JSONB NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS pos_learning_settings (
    store_id TEXT,
    auto_retrain BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (store_id)
);

CREATE TABLE IF NOT EXISTS pos_holidays (
    store_id TEXT NOT NULL,
    date TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (store_id, date)
);

-- Retail IAM tables (from retail/repository.py)
CREATE TABLE IF NOT EXISTS retailers (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_shops (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_users (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_sessions (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_keys (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_snapshots (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_batches (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_runs (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_ledger_jobs (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_audit (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retail_login_attempts (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Retail IAM indexes

CREATE INDEX IF NOT EXISTS idx_retail_sessions_expires ON retail_sessions ((data->>'expires_at'));
CREATE INDEX IF NOT EXISTS idx_retail_login_attempts_expires ON retail_login_attempts ((data->>'expires_at'));

-- Connected Intelligence tables
CREATE TABLE IF NOT EXISTS connect_sources (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_types (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_type_versions (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_runs (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_batches (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_objects (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_versions (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_quarantine (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_decisions (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_questions (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Data Platform tables
CREATE TABLE IF NOT EXISTS data_products (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS data_jobs (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS data_releases (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS data_chunks (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Infrastructure tables
CREATE TABLE IF NOT EXISTS sequences (
    _id TEXT PRIMARY KEY,
    value BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leases (
    _id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases (expires_at);

CREATE TABLE IF NOT EXISTS assets (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    blob_id TEXT,
    sha256 TEXT,
    size BIGINT,
    compression TEXT,
    created TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_versions (
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    blob_id TEXT,
    sha256 TEXT,
    size BIGINT,
    compression TEXT,
    created TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_versions_key ON asset_versions (key, created DESC);

CREATE TABLE IF NOT EXISTS asset_blobs (
    blob_id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    asset_key TEXT,
    data BYTEA NOT NULL,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS operation_recovery (
    store_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    path TEXT,
    body JSONB,
    idempotency_key TEXT,
    created TIMESTAMPTZ,
    UNIQUE (store_id, staff_id)
);

CREATE TABLE IF NOT EXISTS migration_runs (
    source_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    tables JSONB,
    verified BOOLEAN DEFAULT FALSE,
    created TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_activations (
    kind TEXT NOT NULL,
    previous TEXT,
    model_id TEXT NOT NULL,
    created TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_requests (
    _id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS connect_demo_manifests (_id TEXT PRIMARY KEY, data JSONB NOT NULL);

CREATE TABLE IF NOT EXISTS schema_versions (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS pg_retail_users_email ON retail_users ((data->'email'));
CREATE INDEX IF NOT EXISTS pg_retail_sessions_expires_at ON retail_sessions ((data->'expires_at'));
CREATE INDEX IF NOT EXISTS pg_retail_login_attempts_expires_at ON retail_login_attempts ((data->'expires_at'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_retail_shops_retailer_id_store_code ON retail_shops ((data->'retailer_id'), (data->'store_code'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_retail_shops_pos_store_id ON retail_shops ((data->'pos_store_id')) WHERE jsonb_typeof(data->'pos_store_id')='string';
CREATE UNIQUE INDEX IF NOT EXISTS pg_retail_snapshots_workspace_idempotency_key ON retail_snapshots ((data->'workspace'), (data->'idempotency_key'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_retail_batches_snapshot_id_batch_key ON retail_batches ((data->'snapshot_id'), (data->'batch_key'));
CREATE INDEX IF NOT EXISTS pg_retail_runs_workspace_created ON retail_runs ((data->'workspace'), (data->'created') DESC);
CREATE UNIQUE INDEX IF NOT EXISTS pg_retail_runs_workspace_request_key ON retail_runs ((data->'workspace'), (data->'request_key'));
CREATE INDEX IF NOT EXISTS pg_retail_runs_status_created ON retail_runs ((data->'status'), (data->'created'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_retail_ledger_jobs_workspace_kind ON retail_ledger_jobs ((data->'workspace'), (data->'kind')) WHERE data->>'status' IN ('queued','processing');
CREATE INDEX IF NOT EXISTS pg_retail_ledger_jobs_status_created ON retail_ledger_jobs ((data->'status'), (data->'created'));
CREATE INDEX IF NOT EXISTS pg_retail_audit_workspace_created ON retail_audit ((data->'workspace'), (data->'created') DESC);
CREATE INDEX IF NOT EXISTS pg_connect_sources_workspace_created ON connect_sources ((data->'workspace'), (data->'created') DESC);
CREATE UNIQUE INDEX IF NOT EXISTS pg_connect_types_workspace_name ON connect_types ((data->'workspace'), (data->'name'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_connect_type_versions_workspace_name_revision ON connect_type_versions ((data->'workspace'), (data->'name'), (data->'revision'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_connect_runs_source_id_request_key ON connect_runs ((data->'source_id'), (data->'request_key'));
CREATE INDEX IF NOT EXISTS pg_connect_runs_status_created ON connect_runs ((data->'status'), (data->'created'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_connect_batches_run_id_page ON connect_batches ((data->'run_id'), (data->'page'));
CREATE INDEX IF NOT EXISTS pg_connect_objects_workspace_source_id_type__id ON connect_objects ((data->'workspace'), (data->'source_id'), (data->'type'), (_id));
CREATE UNIQUE INDEX IF NOT EXISTS pg_connect_versions_workspace_object_id_revision ON connect_versions ((data->'workspace'), (data->'object_id'), (data->'revision') DESC);
CREATE INDEX IF NOT EXISTS pg_connect_quarantine_workspace_source_id_created ON connect_quarantine ((data->'workspace'), (data->'source_id'), (data->'created') DESC);
CREATE UNIQUE INDEX IF NOT EXISTS pg_connect_decisions_workspace_request_key ON connect_decisions ((data->'workspace'), (data->'request_key'));
CREATE INDEX IF NOT EXISTS pg_connect_questions_workspace_created ON connect_questions ((data->'workspace'), (data->'created') DESC);
CREATE UNIQUE INDEX IF NOT EXISTS pg_data_jobs_workspace_generation ON data_jobs ((data->'workspace'), (data->'generation'));
CREATE INDEX IF NOT EXISTS pg_data_jobs_status_created ON data_jobs ((data->'status'), (data->'created'));
CREATE UNIQUE INDEX IF NOT EXISTS pg_data_releases_workspace_content_sha256 ON data_releases ((data->'workspace'), (data->'content_sha256'));
CREATE INDEX IF NOT EXISTS pg_data_releases_workspace_created ON data_releases ((data->'workspace'), (data->'created') DESC);
CREATE UNIQUE INDEX IF NOT EXISTS pg_data_chunks_release_id_domain_page ON data_chunks ((data->'release_id'), (data->'domain'), (data->'page'));
CREATE INDEX IF NOT EXISTS pg_inventory_history ON pos_inventory_movements (store_id,product_id,business_date,created DESC,_sequence DESC);
CREATE INDEX IF NOT EXISTS pg_object_properties ON connect_objects USING gin (data jsonb_path_ops);

ALTER TABLE pos_stores ADD COLUMN IF NOT EXISTS retailer_id TEXT;
ALTER TABLE operation_recovery ADD COLUMN IF NOT EXISTS updated TEXT;
