"""
ETL: Shopify Fulfillments + Events → SQL Server
Modo histórico:   python etl_fulfillments.py --start-date 2024-01-01 --end-date 2024-12-31
Modo incremental: python etl_fulfillments.py  (padrão: ontem)
"""
import logging
import os
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
    total_f = ins_f = upd_f = total_e = 0
    status = "error"

    try:
        orders = extractor.get_orders(start_date, end_date)
        logger.info(f"{len(orders)} orders found — fetching fulfillments...")

        if orders:
            loader.upsert_orders(orders, include_embedded_fulfillments=False)
            loader.commit()

        for order in orders:
            order_id = order.get("id")
            if not order_id:
                logger.warning("Order sem id — ignorando")
                continue
            fulfillments = extractor.get_fulfillments(order_id)
            if fulfillments:
                i, u = loader.upsert_fulfillments(fulfillments)
                ins_f += i
                upd_f += u
                total_f += len(fulfillments)
                for f in fulfillments:
                    events = extractor.get_fulfillment_events(order_id, f["id"])
                    if events:
                        loader.upsert_fulfillment_events(events)
                        total_e += len(events)
            loader.commit()

        status = "success"
        logger.info(
            f"etl_fulfillments finished — {total_f} fulfillments "
            f"({ins_f} ins, {upd_f} upd), {total_e} events"
        )
    except Exception as e:
        loader.rollback()
        logger.error(f"etl_fulfillments failed: {e}")
        log_run(loader, "etl_fulfillments", start_date, end_date, "error", total_f, str(e),
                inserts=ins_f, updates=upd_f, pid=os.getpid())
        raise
    else:
        log_run(loader, "etl_fulfillments", start_date, end_date, status, total_f,
                inserts=ins_f, updates=upd_f, pid=os.getpid())
    finally:
        loader.close(commit=False)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--start-date", default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--end-date",   default=datetime.today().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    run(args.start_date, args.end_date)
