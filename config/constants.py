from pathlib import Path
from dotenv import load_dotenv
from os import getenv

_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT / ".env"

load_dotenv(_ENV_PATH)


def _int_env(name: str, default: str) -> int:
    raw = getenv(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def reload_config():
    """Recarrega variáveis do .env (após alteração pela UI)."""
    load_dotenv(_ENV_PATH, override=True)
    global SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN, SHOPIFY_API_VERSION
    global SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET
    global SQL_SERVER_HOST, SQL_SERVER_PORT, SQL_SERVER_DATABASE
    global SQL_SERVER_USER, SQL_SERVER_PASSWORD, ETL_BATCH_SIZE, ETL_LOG_LEVEL

    SHOPIFY_STORE_URL = getenv("SHOPIFY_STORE_URL")
    SHOPIFY_ACCESS_TOKEN = getenv("SHOPIFY_ACCESS_TOKEN")
    SHOPIFY_API_VERSION = getenv("SHOPIFY_API_VERSION", "2024-01")
    SHOPIFY_CLIENT_ID = getenv("SHOPIFY_CLIENT_ID")
    SHOPIFY_CLIENT_SECRET = getenv("SHOPIFY_CLIENT_SECRET")
    SQL_SERVER_HOST = getenv("SQL_SERVER_HOST", "localhost")
    SQL_SERVER_PORT = getenv("SQL_SERVER_PORT", "1433")
    SQL_SERVER_DATABASE = getenv("SQL_SERVER_DATABASE")
    SQL_SERVER_USER = getenv("SQL_SERVER_USER")
    SQL_SERVER_PASSWORD = getenv("SQL_SERVER_PASSWORD")
    ETL_BATCH_SIZE = _int_env("ETL_BATCH_SIZE", "250")
    ETL_LOG_LEVEL = getenv("ETL_LOG_LEVEL", "INFO")


# Shopify
SHOPIFY_STORE_URL = getenv("SHOPIFY_STORE_URL")
SHOPIFY_ACCESS_TOKEN = getenv("SHOPIFY_ACCESS_TOKEN")
SHOPIFY_API_VERSION = getenv("SHOPIFY_API_VERSION", "2024-01")
SHOPIFY_CLIENT_ID = getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = getenv("SHOPIFY_CLIENT_SECRET")

# SQL Server
SQL_SERVER_HOST = getenv("SQL_SERVER_HOST", "localhost")
SQL_SERVER_PORT = getenv("SQL_SERVER_PORT", "1433")
SQL_SERVER_DATABASE = getenv("SQL_SERVER_DATABASE")
SQL_SERVER_USER = getenv("SQL_SERVER_USER")
SQL_SERVER_PASSWORD = getenv("SQL_SERVER_PASSWORD")

# ETL
ETL_BATCH_SIZE = _int_env("ETL_BATCH_SIZE", "250")
ETL_LOG_LEVEL = getenv("ETL_LOG_LEVEL", "INFO")

# Tabelas no SQL Server
TABLE_ORDERS = "shopify_orders"
TABLE_FULFILLMENTS = "shopify_fulfillments"
TABLE_FULFILLMENT_EVENTS = "shopify_fulfillment_events"
TABLE_LOCATIONS = "shopify_locations"
