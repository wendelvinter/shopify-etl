"""
ETL: Shopify Fulfillments + Events → SQL Server
Busca fulfillments e eventos de rastreamento para cada pedido do período.

Modo histórico:   python etl_fulfillments.py --start-date 2024-01-01 --end-date 2024-12-31
Modo incremental: python etl_fulfillments.py  (padrão: ontem)
"""
import logging
import sys
from argparse import ArgumentParser
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extractors.shopify_api_extractor import ShopifyAPIExtractor
from loaders.sqlserver_loader import SQLServerLoader
from utils.logger import setup_logger
from utils.run_log import log_run

setup_logger()
logger = logging.getLogger(__name__)


def run(start_date: str, end_date: str):
    logger.info(f"Starting etl_fulfillments | {start_date} → {end_date}")
    extractor = ShopifyAPIExtractor()
    loader = SQLServerLoader()
    total_fulfillments = 0
    total_events = 0

    try:
        orders = extractor.get_orders(start_date, end_date)
        logger.info(f"{len(orders)} orders found — fetching fulfillments...")

        for order in orders:
            order_id = order["id"]
            fulfillments = extractor.get_fulfillments(order_id)

            if fulfillments:
                loader.upsert_fulfillments(fulfillments)
                total_fulfillments += len(fulfillments)

                for f in fulfillments:
                    events = extractor.get_fulfillment_events(order_id, f["id"])
                    if events:
                        loader.upsert_fulfillment_events(events)
                        total_events += len(events)

        log_run(loader, "etl_fulfillments", start_date, end_date, "success", total_fulfillments)
        loader.close()
        logger.info(f"etl_fulfillments finished — {total_fulfillments} fulfillments, {total_events} events")

    except Exception as e:
        log_run(loader, "etl_fulfillments", start_date, end_date, "error", total_fulfillments, str(e))
        logger.error(f"etl_fulfillments failed: {e}")
        raise


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--start-date", default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--end-date", default=datetime.today().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    run(args.start_date, args.end_date)
