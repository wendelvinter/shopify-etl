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

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


class ShopifyAPIError(Exception):
    """Erro na comunicação com a API Shopify."""


class OrderNotFoundError(ShopifyAPIError):
    """Pedido não encontrado ou resposta inválida."""


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

    def _request(self, url: str, params: Optional[dict] = None) -> requests.Response:
        last_err = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(
                    url, headers=self.headers, params=params, timeout=REQUEST_TIMEOUT,
                )
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "2"))
                    logger.warning(f"Rate limited — aguardando {retry_after}s (tentativa {attempt})")
                    import time
                    time.sleep(retry_after)
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                last_err = e
                if attempt < MAX_RETRIES:
                    logger.warning(f"Request falhou (tentativa {attempt}): {e}")
        raise ShopifyAPIError(str(last_err)) from last_err

    def _parse_list_response(self, data: dict, endpoint: str) -> list:
        if not data:
            raise ShopifyAPIError(f"Resposta vazia de {endpoint}")
        if "errors" in data:
            raise ShopifyAPIError(f"Shopify API errors: {data['errors']}")
        keys = [k for k in data if k != "errors"]
        if not keys:
            raise ShopifyAPIError(f"Resposta sem chave de lista em {endpoint}")
        page_data = data[keys[0]]
        if not isinstance(page_data, list):
            raise ShopifyAPIError(f"Esperado lista em '{keys[0]}', recebido {type(page_data)}")
        return page_data

    def _get(self, endpoint: str, params: Optional[dict] = None) -> list:
        """Executa GET com paginação automática via cursor."""
        results = []
        url = f"{self.base_url}/{endpoint}.json"
        params = dict(params or {})
        params["limit"] = self.batch_size

        while url:
            logger.info(f"Fetching: {url} | params: {params}")
            response = self._request(url, params=params)
            page_data = self._parse_list_response(response.json(), endpoint)
            results.extend(page_data)
            logger.info(f"Fetched {len(page_data)} records (total: {len(results)})")
            url = self._get_next_page(response.headers.get("Link", ""))
            params = {}

        return results

    def _get_next_page(self, link_header: str) -> Optional[str]:
        """Extrai URL da próxima página do header Link."""
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                return part.split(";")[0].strip().strip("<>")
        return None

    def get_orders(self, start_date: str, end_date: str) -> list:
        """Extrai pedidos atualizados no intervalo de datas."""
        logger.info(f"Extracting orders from {start_date} to {end_date}")
        return self._get("orders", {
            "status": "any",
            "updated_at_min": f"{start_date}T00:00:00",
            "updated_at_max": f"{end_date}T23:59:59",
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

    def get_returns(self, order_id: int) -> list:
        """Extrai returns (devoluções) de um pedido específico."""
        return self._get(f"orders/{order_id}/returns")

    def get_order_by_id(self, order_id: str) -> list:
        """Extrai um pedido específico pelo ID. Levanta OrderNotFoundError se ausente."""
        logger.info(f"Extracting order {order_id}")
        url = f"{self.base_url}/orders/{order_id}.json"
        try:
            response = self._request(url)
        except ShopifyAPIError as e:
            raise OrderNotFoundError(f"Failed to fetch order {order_id}: {e}") from e

        data = response.json()
        if data.get("errors"):
            raise OrderNotFoundError(f"Shopify errors for order {order_id}: {data['errors']}")
        order = data.get("order")
        if not order:
            raise OrderNotFoundError(f"Order {order_id} not found")
        return [order]
