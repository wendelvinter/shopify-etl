# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Shopify ETL pipeline that extracts data from the Shopify REST API and loads it into SQL Server for real-time logistics visibility. Includes a FastAPI web UI for manual ETL runs, scheduling, and configuration management.

## Stack

- **Python 3.10+** with `requests`, `pyodbc`, `pandas`, `FastAPI`, `uvicorn`, `APScheduler`
- **SQL Server** (ODBC Driver 17 or 18, with `TrustServerCertificate=yes`)
- **Linux** target production (development works on Windows/Linux/macOS)
- No tests or test runners currently configured

## Commands

```bash
# Install (Linux — system Python, no venv)
pip install -r requirements.txt
cp .env.example .env          # then edit with real credentials

# Install (Windows — optional venv for local dev)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

# Start the web UI (primary interface)
cd ui && uvicorn main:app --host 0.0.0.0 --port 8500

# Or use the helper script (Linux)
bash fix_port.sh              # kills anything on port 8500, then starts

# Run ETL scripts directly (default: yesterday → today incremental)
python scripts/etl_orders.py
python scripts/etl_orders.py --start-date 2024-01-01 --end-date 2024-12-31
python scripts/etl_orders.py --order-id 6995328762108       # single order by ID
python scripts/etl_fulfillments.py
python scripts/etl_locations.py                              # no date args — full load
python scripts/etl_returns.py
```

## Architecture

### Module layout

```
shopify-etl/
├── config/constants.py        # All env vars loaded via python-dotenv; reload_config()
├── extractors/shopify_api_extractor.py  # Shopify REST client with pagination
├── loaders/sqlserver_loader.py         # MERGE upserts into SQL Server via pyodbc
├── scripts/etl_*.py           # Four runnable ETL entry points
├── ui/main.py                 # FastAPI app: dashboard, setup page, OAuth, scheduler
├── utils/
│   ├── logger.py              # setup_logger() — file (logs/YYYY-MM-DD.log) + stdout
│   ├── run_log.py             # log_run() — writes outcome row to etl_run_log table
│   └── db_migrations.py       # Idempotent migration runner with schema_migrations tracking
├── sql/                       # DDL: create_tables.sql + migrate_v2–v4.sql
└── config/schedules.json      # Per-script schedule config (auto-created, not in repo)
```

### Data flow

1. **Script** (`scripts/etl_*.py`) calls `ShopifyAPIExtractor` to pull data from Shopify REST API
2. Extractor handles cursor-based pagination via `Link` header, rate limiting (429 → Retry-After), and retries (3 attempts)
3. **Script** passes raw dicts to `SQLServerLoader` which performs `MERGE` with `OUTPUT $action` for accurate insert/update counts
4. Loader auto-runs pending migrations on init; opens a single connection for the entire run
5. Results are logged to `etl_run_log` table via `log_run()`

### Key design decisions

- **Orders are the hub**: `upsert_orders()` cascades to order_line_items, shipping_lines, discount_codes, and optionally embedded fulfillments. The fulfillments ETL re-upserts orders without embedded fulfillments then fetches fulfillments separately per order.
- **Returns ETL** follows the same pattern: first upserts orders (without fulfillments), then iterates orders to fetch and upsert returns + return_line_items.
- **Deduplication**: rows are deduped by primary key before MERGE to avoid duplicate key errors within a batch.
- **Orphan cleanup**: child tables (line items, shipping lines, discount codes, fulfillment line items) have stale rows deleted when the parent order/fulfillment no longer references them.
- **Migrations**: versioned SQL files in `sql/` run via `db_migrations.py` on every `SQLServerLoader` init. Applied versions are tracked in a `schema_migrations` table. Migrations 002–004 add columns and tables incrementally. All migration SQL blocks use `IF NOT EXISTS` guards.

### API extractor

- `ShopifyAPIExtractor._get()` is the core — it handles pagination transparently, following `rel="next"` Link headers until exhausted.
- Date-range queries use `updated_at_min`/`updated_at_max` with `status=any`.
- `get_order_by_id()` is a one-off lookup (no pagination) that raises `OrderNotFoundError` if the order is missing or the response contains errors.

### SQL Server loader

- Uses raw `MERGE` SQL with `OUTPUT $action AS _act` to count inserts vs. updates row-by-row.
- `ALLOWED_TABLES` whitelist prevents SQL injection through table name interpolation.
- Connection string uses `DRIVER={ODBC Driver 18 for SQL Server}` with `TrustServerCertificate=yes`.
- Helper converters: `_to_int`, `_to_decimal`, `_to_bool` sanitize Shopify's mixed-type fields.

### UI (FastAPI)

- **Dashboard** (`/`): table stats, run history with filtering, manual ETL trigger (by date range or order ID), processed volume chart (Chart.js), today's log tail. Scheduler pause/resume toggle.
- **Setup** (`/setup`): OAuth authorization flow, Shopify API credentials, SQL Server credentials with test buttons, per-script scheduling cards, config change audit log.
- **i18n**: English (`en`) and Portuguese (`pt`) via `TRANSLATIONS` dict; user preference stored in `lang` cookie.
- **Scheduling**: APScheduler `BackgroundScheduler` with cron/interval triggers. Schedules are saved to `config/schedules.json`. Supports daily, weekdays, weekly, every-N-minutes/hours/days frequencies.
- **OAuth**: Saves `SHOPIFY_CLIENT_ID`/`SHOPIFY_CLIENT_SECRET` to `.env`, redirects to Shopify's OAuth authorize page, handles callback with HMAC validation and token exchange. Access token is written directly to `.env`.
- **Config changes**: Every `.env` save via the Setup page is logged to `config/config_changes.json` (timestamp + keys changed).

### Configuration

All config lives in `.env` and is loaded by `config/constants.py`. The `reload_config()` function re-reads `.env` with `override=True` so the UI can update credentials at runtime. Key variables:

| Variable | Purpose |
|---|---|
| `SHOPIFY_STORE_URL` | `https://store.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | Admin API access token (or obtained via OAuth) |
| `SHOPIFY_API_VERSION` | API version, default `2024-01` |
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` | OAuth app credentials |
| `SQL_SERVER_HOST` / `SQL_SERVER_PORT` | SQL Server connection |
| `SQL_SERVER_DATABASE` / `SQL_SERVER_USER` / `SQL_SERVER_PASSWORD` | Auth |
| `ETL_BATCH_SIZE` | Page size for Shopify API (default 250) |
| `ETL_LOG_LEVEL` | Python logging level (default INFO) |
