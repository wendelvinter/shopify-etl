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
    def __init__(self):
        self.conn = get_connection()
        self.cursor = self.conn.cursor()

    def close(self):
        self.conn.commit()
        self.cursor.close()
        self.conn.close()

    def _count(self, table: str) -> int:
        self.cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return self.cursor.fetchone()[0]

    # ------------------------------------------------------------------ #
    # Orders                                                               #
    # ------------------------------------------------------------------ #

    def upsert_orders(self, orders: list) -> tuple:
        sql = """
        MERGE shopify_orders AS target
        USING (VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)) AS source
            (order_id, order_number, order_name, financial_status, fulfillment_status,
             created_at, updated_at, processed_at, cancelled_at, closed_at,
             total_price, subtotal_price, total_tax, total_discounts, total_shipping_price,
             currency, taxes_included, test, confirmed, source_name, gateway,
             payment_gateway_names, location_id)
        ON target.order_id = source.order_id
        WHEN MATCHED THEN UPDATE SET
            financial_status      = source.financial_status,
            fulfillment_status    = source.fulfillment_status,
            updated_at            = source.updated_at,
            cancelled_at          = source.cancelled_at,
            closed_at             = source.closed_at,
            total_price           = source.total_price,
            subtotal_price        = source.subtotal_price,
            total_tax             = source.total_tax,
            total_discounts       = source.total_discounts,
            total_shipping_price  = source.total_shipping_price,
            payment_gateway_names = source.payment_gateway_names
        WHEN NOT MATCHED THEN INSERT
            (order_id, order_number, order_name, financial_status, fulfillment_status,
             created_at, updated_at, processed_at, cancelled_at, closed_at,
             total_price, subtotal_price, total_tax, total_discounts, total_shipping_price,
             currency, taxes_included, test, confirmed, source_name, gateway,
             payment_gateway_names, location_id)
        VALUES
            (source.order_id, source.order_number, source.order_name,
             source.financial_status, source.fulfillment_status,
             source.created_at, source.updated_at, source.processed_at,
             source.cancelled_at, source.closed_at,
             source.total_price, source.subtotal_price, source.total_tax,
             source.total_discounts, source.total_shipping_price,
             source.currency, source.taxes_included, source.test, source.confirmed,
             source.source_name, source.gateway,
             source.payment_gateway_names, source.location_id);
        """
        rows = []
        for o in orders:
            shipping_set = o.get("total_shipping_price_set") or {}
            total_shipping = shipping_set.get("shop_money", {}).get("amount")
            pgw = o.get("payment_gateway_names") or []
            rows.append((
                o.get("id"), o.get("order_number"), o.get("name"),
                o.get("financial_status"), o.get("fulfillment_status"),
                o.get("created_at"), o.get("updated_at"), o.get("processed_at"),
                o.get("cancelled_at"), o.get("closed_at"),
                o.get("total_price"), o.get("subtotal_price"), o.get("total_tax"),
                o.get("total_discounts"), total_shipping,
                o.get("currency"), o.get("taxes_included"), o.get("test"),
                o.get("confirmed"), o.get("source_name"), o.get("gateway"),
                ",".join(pgw)[:500] if pgw else None,
                o.get("location_id"),
            ))

        before = self._count("shopify_orders")
        self.cursor.executemany(sql, rows)
        after = self._count("shopify_orders")
        ins = after - before
        upd = len(rows) - ins
        logger.info(f"Upserted {len(rows)} orders ({ins} inserts, {upd} updates)")

        # Sub-tables
        self._upsert_order_line_items(orders)
        self._upsert_order_shipping_lines(orders)
        self._upsert_order_discount_codes(orders)

        # Embedded fulfillments (dentro da resposta da order)
        embedded = []
        for o in orders:
            for f in (o.get("fulfillments") or []):
                f.setdefault("order_id", o.get("id"))
                embedded.append(f)
        if embedded:
            self.upsert_fulfillments(embedded)

        return ins, upd

    def _upsert_order_line_items(self, orders: list):
        sql = """
        MERGE shopify_order_line_items AS target
        USING (VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)) AS source
            (line_item_id, order_id, product_id, variant_id, title, variant_title,
             sku, quantity, price, total_discount, fulfillment_status, fulfillment_service,
             vendor, requires_shipping, taxable, gift_card, fulfillable_quantity, grams)
        ON target.line_item_id = source.line_item_id
        WHEN MATCHED THEN UPDATE SET
            fulfillment_status   = source.fulfillment_status,
            fulfillable_quantity = source.fulfillable_quantity,
            total_discount       = source.total_discount
        WHEN NOT MATCHED THEN INSERT
            (line_item_id, order_id, product_id, variant_id, title, variant_title,
             sku, quantity, price, total_discount, fulfillment_status, fulfillment_service,
             vendor, requires_shipping, taxable, gift_card, fulfillable_quantity, grams)
        VALUES
            (source.line_item_id, source.order_id, source.product_id, source.variant_id,
             source.title, source.variant_title, source.sku, source.quantity, source.price,
             source.total_discount, source.fulfillment_status, source.fulfillment_service,
             source.vendor, source.requires_shipping, source.taxable, source.gift_card,
             source.fulfillable_quantity, source.grams);
        """
        rows = []
        for o in orders:
            oid = o.get("id")
            for li in (o.get("line_items") or []):
                rows.append((
                    li.get("id"), oid,
                    li.get("product_id"), li.get("variant_id"),
                    li.get("title"), li.get("variant_title"),
                    li.get("sku"), li.get("quantity"),
                    li.get("price"), li.get("total_discount"),
                    li.get("fulfillment_status"), li.get("fulfillment_service"),
                    li.get("vendor"), li.get("requires_shipping"),
                    li.get("taxable"), li.get("gift_card"),
                    li.get("fulfillable_quantity"), li.get("grams"),
                ))
        if rows:
            self.cursor.executemany(sql, rows)
            logger.info(f"Upserted {len(rows)} order line items")

    def _upsert_order_shipping_lines(self, orders: list):
        sql = """
        MERGE shopify_order_shipping_lines AS target
        USING (VALUES (?,?,?,?,?,?,?)) AS source
            (shipping_line_id, order_id, title, price, code, source, carrier_identifier)
        ON target.shipping_line_id = source.shipping_line_id
        WHEN MATCHED THEN UPDATE SET price = source.price
        WHEN NOT MATCHED THEN INSERT
            (shipping_line_id, order_id, title, price, code, source, carrier_identifier)
        VALUES
            (source.shipping_line_id, source.order_id, source.title, source.price,
             source.code, source.source, source.carrier_identifier);
        """
        rows = []
        for o in orders:
            oid = o.get("id")
            for sl in (o.get("shipping_lines") or []):
                rows.append((
                    sl.get("id"), oid,
                    sl.get("title"), sl.get("price"),
                    sl.get("code"), sl.get("source"),
                    sl.get("carrier_identifier"),
                ))
        if rows:
            self.cursor.executemany(sql, rows)
            logger.info(f"Upserted {len(rows)} shipping lines")

    def _upsert_order_discount_codes(self, orders: list):
        sql = """
        MERGE shopify_order_discount_codes AS target
        USING (VALUES (?,?,?,?)) AS source (order_id, code, amount, type)
        ON target.order_id = source.order_id AND target.code = source.code
        WHEN MATCHED THEN UPDATE SET amount = source.amount
        WHEN NOT MATCHED THEN INSERT (order_id, code, amount, type)
        VALUES (source.order_id, source.code, source.amount, source.type);
        """
        rows = []
        for o in orders:
            oid = o.get("id")
            for dc in (o.get("discount_codes") or []):
                rows.append((
                    oid, dc.get("code"), dc.get("amount"), dc.get("type"),
                ))
        if rows:
            self.cursor.executemany(sql, rows)
            logger.info(f"Upserted {len(rows)} discount codes")

    # ------------------------------------------------------------------ #
    # Fulfillments                                                         #
    # ------------------------------------------------------------------ #

    def upsert_fulfillments(self, fulfillments: list) -> tuple:
        sql = """
        MERGE shopify_fulfillments AS target
        USING (VALUES (?,?,?,?,?,?,?,?,?,?,?,?)) AS source
            (fulfillment_id, order_id, status, tracking_number, tracking_numbers,
             tracking_company, tracking_url, location_id, fulfillment_name,
             notify_customer, created_at, updated_at)
        ON target.fulfillment_id = source.fulfillment_id
        WHEN MATCHED THEN UPDATE SET
            status           = source.status,
            tracking_number  = source.tracking_number,
            tracking_numbers = source.tracking_numbers,
            tracking_company = source.tracking_company,
            tracking_url     = source.tracking_url,
            updated_at       = source.updated_at
        WHEN NOT MATCHED THEN INSERT
            (fulfillment_id, order_id, status, tracking_number, tracking_numbers,
             tracking_company, tracking_url, location_id, fulfillment_name,
             notify_customer, created_at, updated_at)
        VALUES
            (source.fulfillment_id, source.order_id, source.status,
             source.tracking_number, source.tracking_numbers,
             source.tracking_company, source.tracking_url,
             source.location_id, source.fulfillment_name, source.notify_customer,
             source.created_at, source.updated_at);
        """
        rows = []
        for f in fulfillments:
            tnums = f.get("tracking_numbers") or []
            rows.append((
                f.get("id"), f.get("order_id"), f.get("status"),
                f.get("tracking_number"),
                ",".join(str(t) for t in tnums)[:1000] if tnums else None,
                f.get("tracking_company"), f.get("tracking_url"),
                f.get("location_id"), f.get("name"),
                f.get("notify_customer"),
                f.get("created_at"), f.get("updated_at"),
            ))

        before = self._count("shopify_fulfillments")
        self.cursor.executemany(sql, rows)
        after = self._count("shopify_fulfillments")
        ins = after - before
        upd = len(rows) - ins
        logger.info(f"Upserted {len(rows)} fulfillments ({ins} inserts, {upd} updates)")

        self._upsert_fulfillment_line_items(fulfillments)
        return ins, upd

    def _upsert_fulfillment_line_items(self, fulfillments: list):
        sql = """
        MERGE shopify_fulfillment_line_items AS target
        USING (VALUES (?,?,?,?,?)) AS source
            (fulfillment_id, line_item_id, order_id, quantity, fulfillment_service)
        ON target.fulfillment_id = source.fulfillment_id
           AND target.line_item_id = source.line_item_id
        WHEN MATCHED THEN UPDATE SET quantity = source.quantity
        WHEN NOT MATCHED THEN INSERT
            (fulfillment_id, line_item_id, order_id, quantity, fulfillment_service)
        VALUES
            (source.fulfillment_id, source.line_item_id, source.order_id,
             source.quantity, source.fulfillment_service);
        """
        rows = []
        for f in fulfillments:
            fid = f.get("id")
            oid = f.get("order_id")
            for li in (f.get("line_items") or []):
                rows.append((
                    fid, li.get("id"), oid,
                    li.get("quantity"), li.get("fulfillment_service"),
                ))
        if rows:
            self.cursor.executemany(sql, rows)
            logger.info(f"Upserted {len(rows)} fulfillment line items")

    # ------------------------------------------------------------------ #
    # Fulfillment Events                                                   #
    # ------------------------------------------------------------------ #

    def upsert_fulfillment_events(self, events: list) -> tuple:
        sql = """
        MERGE shopify_fulfillment_events AS target
        USING (VALUES (?,?,?,?,?,?)) AS source
            (event_id, fulfillment_id, order_id, status, message, happened_at)
        ON target.event_id = source.event_id
        WHEN NOT MATCHED THEN INSERT
            (event_id, fulfillment_id, order_id, status, message, happened_at)
        VALUES
            (source.event_id, source.fulfillment_id, source.order_id,
             source.status, source.message, source.happened_at);
        """
        rows = [
            (e.get("id"), e.get("fulfillment_id"), e.get("order_id"),
             e.get("status"), e.get("message"), e.get("happened_at"))
            for e in events
        ]
        before = self._count("shopify_fulfillment_events")
        self.cursor.executemany(sql, rows)
        after = self._count("shopify_fulfillment_events")
        ins = after - before
        logger.info(f"Upserted {len(rows)} fulfillment events ({ins} inserts)")
        return ins, 0

    # ------------------------------------------------------------------ #
    # Locations                                                            #
    # ------------------------------------------------------------------ #

    def upsert_locations(self, locations: list) -> tuple:
        sql = """
        MERGE shopify_locations AS target
        USING (VALUES (?,?,?,?,?,?)) AS source
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
            (l.get("id"), l.get("name"), l.get("address1"),
             l.get("city"), l.get("province"), l.get("country"))
            for l in locations
        ]
        before = self._count("shopify_locations")
        self.cursor.executemany(sql, rows)
        after = self._count("shopify_locations")
        ins = after - before
        upd = len(rows) - ins
        logger.info(f"Upserted {len(rows)} locations ({ins} inserts, {upd} updates)")
        return ins, upd
