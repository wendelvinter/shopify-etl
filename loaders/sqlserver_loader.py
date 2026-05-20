import struct
import pyodbc
import logging
from config.constants import (
    SQL_SERVER_HOST,
    SQL_SERVER_PORT,
    SQL_SERVER_DATABASE,
    SQL_SERVER_USER,
    SQL_SERVER_PASSWORD,
)

logger = logging.getLogger(__name__)


def _handle_datetimeoffset(raw):
    tup = struct.unpack("<6hI2h", raw)
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*tup[:6])


def get_connection():
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={SQL_SERVER_HOST},{SQL_SERVER_PORT};"
        f"DATABASE={SQL_SERVER_DATABASE};"
        f"UID={SQL_SERVER_USER};"
        f"PWD={SQL_SERVER_PASSWORD};"
        f"TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(conn_str)
    conn.add_output_converter(-155, _handle_datetimeoffset)
    return conn


class SQLServerLoader:
    """
    Responsável por gravar dados no SQL Server.
    Usa MERGE (upsert) para evitar duplicatas em reprocessamentos.
    """

    def __init__(self):
        self.conn = get_connection()
        self.cursor = self.conn.cursor()

    def close(self):
        self.conn.commit()
        self.cursor.close()
        self.conn.close()

    def upsert_orders(self, orders: list):
        """Upsert de pedidos na tabela shopify_orders."""
        sql = """
        MERGE shopify_orders AS target
        USING (VALUES (?, ?, ?, ?, ?, ?, ?, ?)) AS source
            (order_id, order_number, financial_status,
             fulfillment_status, created_at, updated_at, total_price, currency)
        ON target.order_id = source.order_id
        WHEN MATCHED THEN UPDATE SET
            financial_status   = source.financial_status,
            fulfillment_status = source.fulfillment_status,
            updated_at         = source.updated_at,
            total_price        = source.total_price
        WHEN NOT MATCHED THEN INSERT
            (order_id, order_number, financial_status,
             fulfillment_status, created_at, updated_at, total_price, currency)
        VALUES
            (source.order_id, source.order_number,
             source.financial_status, source.fulfillment_status,
             source.created_at, source.updated_at, source.total_price,
             source.currency);
        """
        rows = [
            (
                o.get("id"),
                o.get("order_number"),
                o.get("financial_status"),
                o.get("fulfillment_status"),
                o.get("created_at"),
                o.get("updated_at"),
                o.get("total_price"),
                o.get("currency"),
            )
            for o in orders
        ]
        self.cursor.executemany(sql, rows)
        logger.info(f"Upserted {len(rows)} orders")

    def upsert_fulfillments(self, fulfillments: list):
        """Upsert de fulfillments na tabela shopify_fulfillments."""
        sql = """
        MERGE shopify_fulfillments AS target
        USING (VALUES (?, ?, ?, ?, ?, ?, ?, ?)) AS source
            (fulfillment_id, order_id, status, tracking_number,
             tracking_company, tracking_url, created_at, updated_at)
        ON target.fulfillment_id = source.fulfillment_id
        WHEN MATCHED THEN UPDATE SET
            status           = source.status,
            tracking_number  = source.tracking_number,
            tracking_company = source.tracking_company,
            tracking_url     = source.tracking_url,
            updated_at       = source.updated_at
        WHEN NOT MATCHED THEN INSERT
            (fulfillment_id, order_id, status, tracking_number,
             tracking_company, tracking_url, created_at, updated_at)
        VALUES
            (source.fulfillment_id, source.order_id, source.status,
             source.tracking_number, source.tracking_company,
             source.tracking_url, source.created_at, source.updated_at);
        """
        rows = [
            (
                f.get("id"),
                f.get("order_id"),
                f.get("status"),
                f.get("tracking_number"),
                f.get("tracking_company"),
                f.get("tracking_url"),
                f.get("created_at"),
                f.get("updated_at"),
            )
            for f in fulfillments
        ]
        self.cursor.executemany(sql, rows)
        logger.info(f"Upserted {len(rows)} fulfillments")

    def upsert_fulfillment_events(self, events: list):
        """Upsert de eventos de rastreamento."""
        sql = """
        MERGE shopify_fulfillment_events AS target
        USING (VALUES (?, ?, ?, ?, ?, ?)) AS source
            (event_id, fulfillment_id, order_id, status, message, happened_at)
        ON target.event_id = source.event_id
        WHEN NOT MATCHED THEN INSERT
            (event_id, fulfillment_id, order_id, status, message, happened_at)
        VALUES
            (source.event_id, source.fulfillment_id, source.order_id,
             source.status, source.message, source.happened_at);
        """
        rows = [
            (
                e.get("id"),
                e.get("fulfillment_id"),
                e.get("order_id"),
                e.get("status"),
                e.get("message"),
                e.get("happened_at"),
            )
            for e in events
        ]
        self.cursor.executemany(sql, rows)
        logger.info(f"Upserted {len(rows)} fulfillment events")

    def upsert_locations(self, locations: list):
        """Upsert de locations."""
        sql = """
        MERGE shopify_locations AS target
        USING (VALUES (?, ?, ?, ?, ?, ?)) AS source
            (location_id, name, address1, city, province, country)
        ON target.location_id = source.location_id
        WHEN MATCHED THEN UPDATE SET
            name     = source.name,
            address1 = source.address1
        WHEN NOT MATCHED THEN INSERT
            (location_id, name, address1, city, province, country)
        VALUES
            (source.location_id, source.name, source.address1,
             source.city, source.province, source.country);
        """
        rows = [
            (
                l.get("id"),
                l.get("name"),
                l.get("address1"),
                l.get("city"),
                l.get("province"),
                l.get("country"),
            )
            for l in locations
        ]
        self.cursor.executemany(sql, rows)
        logger.info(f"Upserted {len(rows)} locations")
