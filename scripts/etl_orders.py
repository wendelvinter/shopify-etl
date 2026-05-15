"""
ETL: Shopify Orders → SQL Server
Modo histórico:  python etl_orders.py --start-date 2024-01-01 --end-date 2024-12-31
Modo incremental: python etl_orders.py  (padrão: ontem)
"""
import logging
import sys
from argparse import ArgumentParser
from datetime import datetime, timedelta

sys.path.append("..")
from config.constants import TABLE_ORDERS
from extractors.shopify_api_extractor import ShopifyAPIExtractor
from loaders.sqlserver_loader import SQLServerLoader
from utils.logger import setup_logger
from utils.run_log import log_run

setup_logger()
logger = logging.getLogger(__name__)


def run(start_date: str, end_date: str):
    logger.info(f"Starting etl_orders | {start_date} → {end_date}")
    extractor = ShopifyAPIExtractor()
    loader = SQLServerLoader()
    total = 0

    try:
        orders = extractor.get_orders(start_date, end_date)
        loader.upsert_orders(orders)
        total = len(orders)
        loader.close()
        log_run(loader, "etl_orders", start_date, end_date, "success", total)
        logger.info(f"etl_orders finished — {total} records")
    except Exception as e:
        log_run(loader, "etl_orders", start_date, end_date, "error", total, str(e))
        logger.error(f"etl_orders failed: {e}")
        raise


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--start-date", default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--end-date", default=datetime.today().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    run(args.start_date, args.end_date)
