import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from config.constants import (
    SHOPIFY_STORE_URL,
    SHOPIFY_ACCESS_TOKEN,
    SHOPIFY_API_VERSION,
    ETL_BATCH_SIZE,
)

logger = logging.getLogger(__name__)


class ShopifyAPIExtractor:
    """
    Responsável por consumir a API REST do Shopify.
    Suporta paginação automática via link header (cursor-based).
    """

    def __init__(self):
        self.base_url = f"{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}"
        self.headers = {
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json",
        }
        self.batch_size = ETL_BATCH_SIZE

    def _get(self, endpoint: str, params: dict = {}) -> list:
        """Executa GET com paginação automática via cursor."""
        results = []
        url = f"{self.base_url}/{endpoint}.json"
        params["limit"] = self.batch_size

        while url:
            logger.info(f"Fetching: {url} | params: {params}")
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()

            data = response.json()
            key = list(data.keys())[0]
            page_data = data[key]
            results.extend(page_data)
            logger.info(f"Fetched {len(page_data)} records (total: {len(results)})")

            # Paginação via Link header
            url = self._get_next_page(response.headers.get("Link", ""))
            params = {}  # próxima página já vem no cursor

        return results

    def _get_next_page(self, link_header: str) -> Optional[str]:
        """Extrai URL da próxima página do header Link."""
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                return part.split(";")[0].strip().strip("<>")
        return None

    # ------------------------------------------------------------------ #
    # Métodos públicos — um por entidade                                  #
    # ------------------------------------------------------------------ #

    def get_orders(self, start_date: str, end_date: str) -> list:
        """Extrai pedidos criados no intervalo de datas."""
        logger.info(f"Extracting orders from {start_date} to {end_date}")
        return self._get("orders", {
            "status": "any",
            "created_at_min": f"{start_date}T00:00:00",
            "created_at_max": f"{end_date}T23:59:59",
        })

    def get_fulfillments(self, order_id: int) -> list:
        """Extrai fulfillments de um pedido específico."""
        return self._get(f"orders/{order_id}/fulfillments")

    def get_fulfillment_events(self, order_id: int, fulfillment_id: int) -> list:
        """Extrai eventos de rastreamento de um fulfillment."""
        return self._get(
            f"orders/{order_id}/fulfillments/{fulfillment_id}/events"
        )

    def get_locations(self) -> list:
        """Extrai todas as locations (warehouses/lojas) da conta."""
        logger.info("Extracting locations")
        return self._get("locations")
