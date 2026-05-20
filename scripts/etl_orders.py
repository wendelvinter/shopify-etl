"""
ETL: Shopify Orders → SQL Server
Modo histórico:   python etl_orders.py --start-date 2024-01-01 --end-date 2024-12-31
Modo incremental: python etl_orders.py  (padrão: ontem)
Modo por ID:      python etl_orders.py --order-id 6995328762108
"""
import logging
import os
import sys
from argparse import ArgumentParser
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.constants import TABLE_ORDERS
from extractors.shopify_api_extractor import ShopifyAPIExtractor
from loaders.sqlserver_loader import SQLServerLoader
from utils.logger import setup_logger
from utils.run_log import log_run

setup_logger()
logger = logging.getLogger(__name__)


def run(start_date: str, end_date: str, order_id: str = None):
    label = f"order {order_id}" if order_id else f"{start_date} → {end_date}"
    logger.info(f"Starting etl_orders | {label}")
    extractor = ShopifyAPIExtractor()
    loader = SQLServerLoader()
    total = inserts = updates = 0

    try:
        orders = extractor.get_order_by_id(order_id) if order_id else extractor.get_orders(start_date, end_date)
        inserts, updates = loader.upsert_orders(orders)
        total = len(orders)
        log_run(loader, "etl_orders", start_date, end_date, "success", total,
                inserts=inserts, updates=updates, pid=os.getpid())
        loader.close()
        logger.info(f"etl_orders finished — {total} records ({inserts} ins, {updates} upd)")
    except Exception as e:
        log_run(loader, "etl_orders", start_date, end_date, "error", total, str(e),
                inserts=inserts, updates=updates, pid=os.getpid())
        logger.error(f"etl_orders failed: {e}")
        raise


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--start-date", default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--end-date",   default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--order-id",   default=None)
    args = parser.parse_args()
    run(args.start_date, args.end_date, args.order_id)
