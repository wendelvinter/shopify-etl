"""
ETL: Shopify Locations → SQL Server
Locations mudam raramente — rodar manualmente ou 1x por semana.

Uso: python etl_locations.py
"""
import logging
import sys

sys.path.append("..")
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
        loader.upsert_locations(locations)
        loader.close()
        log_run(loader, "etl_locations", None, None, "success", len(locations))
        logger.info(f"etl_locations finished — {len(locations)} locations")
    except Exception as e:
        log_run(loader, "etl_locations", None, None, "error", 0, str(e))
        logger.error(f"etl_locations failed: {e}")
        raise


if __name__ == "__main__":
    run()
