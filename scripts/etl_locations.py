"""
ETL: Shopify Locations → SQL Server
Locations mudam raramente — rodar manualmente ou 1x por semana.
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractors.shopify_api_extractor import ShopifyAPIExtractor
from loaders.sqlserver_loader import SQLServerLoader
from utils.logger import setup_logger
from utils.run_log import log_run

setup_logger()
logger = logging.getLogger(__name__)


def run():
    logger.info("Starting etl_locations")
    extractor = ShopifyAPIExtractor()
    loader = SQLServerLoader()
    inserts = updates = 0
    total = 0
    status = "error"

    try:
        locations = extractor.get_locations()
        inserts, updates = loader.upsert_locations(locations)
        total = len(locations)
        loader.commit()
        status = "success"
        logger.info(f"etl_locations finished — {total} locations ({inserts} ins, {updates} upd)")
    except Exception as e:
        loader.rollback()
        logger.error(f"etl_locations failed: {e}")
        log_run(loader, "etl_locations", None, None, "error", total, str(e),
                inserts=inserts, updates=updates, pid=os.getpid())
        raise
    else:
        log_run(loader, "etl_locations", None, None, status, total,
                inserts=inserts, updates=updates, pid=os.getpid())
    finally:
        loader.close(commit=False)


if __name__ == "__main__":
    run()
