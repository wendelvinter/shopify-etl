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

    try:
        locations = extractor.get_locations()
        inserts, updates = loader.upsert_locations(locations)
        log_run(loader, "etl_locations", None, None, "success", len(locations),
                inserts=inserts, updates=updates, pid=os.getpid())
        loader.close()
        logger.info(f"etl_locations finished — {len(locations)} locations ({inserts} ins, {updates} upd)")
    except Exception as e:
        log_run(loader, "etl_locations", None, None, "error", 0, str(e), pid=os.getpid())
        logger.error(f"etl_locations failed: {e}")
        raise


if __name__ == "__main__":
    run()
