"""
ETL: Shopify Returns -> SQL Server
Modo historico:   python etl_returns.py --start-date 2024-01-01 --end-date 2024-12-31
Modo incremental: python etl_returns.py  (padrao: ontem)
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
    logger.info(f"Starting etl_returns | {start_date} -> {end_date}")
    extractor = ShopifyAPIExtractor()
    loader = SQLServerLoader()
    total_r = ins_r = upd_r = total_li = 0
    status = "error"

    try:
        orders = extractor.get_orders(start_date, end_date)
        logger.info(f"{len(orders)} orders found — fetching returns...")

        if orders:
            loader.upsert_orders(orders, include_embedded_fulfillments=False)
            loader.commit()

        for order in orders:
            order_id = order.get("id")
            if not order_id:
                logger.warning("Order sem id — ignorando")
                continue
            returns = extractor.get_returns(order_id)
            if returns:
                i, u, li = loader.upsert_returns(returns, order_id)
                ins_r += i
                upd_r += u
                total_r += len(returns)
                total_li += li
            loader.commit()

        status = "success"
        logger.info(
            f"etl_returns finished — {total_r} returns "
            f"({ins_r} ins, {upd_r} upd), {total_li} line items"
        )
    except Exception as e:
        loader.rollback()
        logger.error(f"etl_returns failed: {e}")
        log_run(loader, "etl_returns", start_date, end_date, "error", total_r, str(e),
                inserts=ins_r, updates=upd_r, pid=os.getpid())
        raise
    else:
        log_run(loader, "etl_returns", start_date, end_date, status, total_r,
                inserts=ins_r, updates=upd_r, pid=os.getpid())
    finally:
        loader.close(commit=False)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--start-date", default=(datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--end-date",   default=datetime.today().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    run(args.start_date, args.end_date)
