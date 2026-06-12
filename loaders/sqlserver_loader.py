import struct
import pyodbc
import logging
from decimal import Decimal, InvalidOperation

from config.constants import (
    SQL_SERVER_HOST,
    SQL_SERVER_PORT,
    SQL_SERVER_DATABASE,
    SQL_SERVER_USER,
    SQL_SERVER_PASSWORD,
)

logger = logging.getLogger(__name__)

ALLOWED_TABLES = frozenset({
    "shopify_orders",
    "shopify_fulfillments",
    "shopify_fulfillment_events",
    "shopify_locations",
    "shopify_order_line_items",
    "shopify_order_shipping_lines",
    "shopify_order_discount_codes",
    "shopify_fulfillment_line_items",
    "shopify_returns",
    "shopify_return_line_items",
})


def _handle_datetimeoffset(raw):
    tup = struct.unpack("<6hI2h", raw)
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*tup[:6])


def _to_decimal(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _to_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return None


def _to_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_connection():
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={SQL_SERVER_HOST},{SQL_SERVER_PORT};"
        f"DATABASE={SQL_SERVER_DATABASE};"
        f"UID={SQL_SERVER_USER};"
        f"PWD={SQL_SERVER_PASSWORD};"
        f"TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(conn_str, timeout=15)
    conn.add_output_converter(-155, _handle_datetimeoffset)
    return conn


class SQLServerLoader:
    def __init__(self):
        from utils.db_migrations import run_pending_migrations

        run_pending_migrations()
        self.conn = get_connection()
        self.cursor = self.conn.cursor()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self, commit: bool = True):
        try:
            if commit:
                self.conn.commit()
        finally:
            self.cursor.close()
            self.conn.close()

    def _count(self, table: str) -> int:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Tabela não permitida: {table}")
        self.cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return self.cursor.fetchone()[0]

    def _merge_stats(self, merge_sql: str, rows: list) -> tuple:
        """Executa MERGE com OUTPUT $action por linha para contagem precisa."""
        if not rows:
            return 0, 0
        sql = merge_sql.rstrip().rstrip(";") + " OUTPUT $action AS _act;"
        inserts = updates = 0
        for row in rows:
            self.cursor.execute(sql, row)
            for result in self.cursor.fetchall():
                action = result[0] if result else None
                if action == "INSERT":
                    inserts += 1
                elif action == "UPDATE":
                    updates += 1
        return inserts, updates

    def _dedupe_rows(self, rows: list, key_indexes: tuple) -> list:
        seen = {}
        for row in rows:
            key = tuple(row[i] for i in key_indexes)
            seen[key] = row
        return list(seen.values())

    # ------------------------------------------------------------------ #
    # Orders                                                               #
    # ------------------------------------------------------------------ #

    def upsert_orders(self, orders: list, include_embedded_fulfillments: bool = True) -> tuple:
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
             source.payment_gateway_names, source.location_id)
        """
        rows = []
        for o in orders:
            shipping_set = o.get("total_shipping_price_set") or {}
            total_shipping = shipping_set.get("shop_money", {}).get("amount")
            pgw = o.get("payment_gateway_names") or []
            rows.append((
                _to_int(o.get("id")), _to_int(o.get("order_number")), o.get("name"),
                o.get("financial_status"), o.get("fulfillment_status"),
                o.get("created_at"), o.get("updated_at"), o.get("processed_at"),
                o.get("cancelled_at"), o.get("closed_at"),
                _to_decimal(o.get("total_price")), _to_decimal(o.get("subtotal_price")),
                _to_decimal(o.get("total_tax")), _to_decimal(o.get("total_discounts")),
                _to_decimal(total_shipping),
                o.get("currency"), _to_bool(o.get("taxes_included")), _to_bool(o.get("test")),
                _to_bool(o.get("confirmed")), o.get("source_name"), o.get("gateway"),
                ",".join(pgw)[:500] if pgw else None,
                _to_int(o.get("location_id")),
            ))

        rows = self._dedupe_rows(rows, (0,))
        ins, upd = self._merge_stats(sql, rows)
        logger.info(f"Upserted {len(rows)} orders ({ins} inserts, {upd} updates)")

        self._upsert_order_line_items(orders)
        self._upsert_order_shipping_lines(orders)
        self._upsert_order_discount_codes(orders)

        if include_embedded_fulfillments:
            embedded = []
            for o in orders:
                oid = o.get("id")
                for f in (o.get("fulfillments") or []):
                    fc = dict(f)
                    fc["order_id"] = oid
                    embedded.append(fc)
            if embedded:
                self.upsert_fulfillments(embedded)

        return ins, upd

    def _delete_orphans(self, table: str, id_col: str, parent_col: str,
                        parent_ids: list, keep_ids: list):
        # Tabelas temporárias em vez de IN (?,?,...): o SQL Server aceita no
        # máximo ~2100 parâmetros por statement, e cargas grandes estouravam
        # esse limite (erro HY000 "parameter markers").
        if not parent_ids:
            return
        self.cursor.execute("CREATE TABLE #orphan_parents (id BIGINT PRIMARY KEY)")
        self.cursor.execute("CREATE TABLE #orphan_keep (id BIGINT PRIMARY KEY)")
        try:
            self.cursor.executemany(
                "INSERT INTO #orphan_parents (id) VALUES (?)",
                [(int(p),) for p in dict.fromkeys(parent_ids)],
            )
            if keep_ids:
                self.cursor.executemany(
                    "INSERT INTO #orphan_keep (id) VALUES (?)",
                    [(int(k),) for k in dict.fromkeys(keep_ids)],
                )
            self.cursor.execute(
                f"DELETE FROM {table} "
                f"WHERE {parent_col} IN (SELECT id FROM #orphan_parents) "
                f"AND {id_col} NOT IN (SELECT id FROM #orphan_keep)"
            )
            if self.cursor.rowcount:
                logger.info(f"Removed {self.cursor.rowcount} orphan rows from {table}")
        finally:
            self.cursor.execute("DROP TABLE #orphan_parents")
            self.cursor.execute("DROP TABLE #orphan_keep")

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
            total_discount       = source.total_discount,
            quantity             = source.quantity,
            price                = source.price
        WHEN NOT MATCHED THEN INSERT
            (line_item_id, order_id, product_id, variant_id, title, variant_title,
             sku, quantity, price, total_discount, fulfillment_status, fulfillment_service,
             vendor, requires_shipping, taxable, gift_card, fulfillable_quantity, grams)
        VALUES
            (source.line_item_id, source.order_id, source.product_id, source.variant_id,
             source.title, source.variant_title, source.sku, source.quantity, source.price,
             source.total_discount, source.fulfillment_status, source.fulfillment_service,
             source.vendor, source.requires_shipping, source.taxable, source.gift_card,
             source.fulfillable_quantity, source.grams)
        """
        rows = []
        order_ids = []
        keep_ids = []
        for o in orders:
            oid = _to_int(o.get("id"))
            if oid is None:
                continue
            order_ids.append(oid)
            for li in (o.get("line_items") or []):
                lid = _to_int(li.get("id"))
                if lid is not None:
                    keep_ids.append(lid)
                rows.append((
                    lid, oid,
                    _to_int(li.get("product_id")), _to_int(li.get("variant_id")),
                    li.get("title"), li.get("variant_title"),
                    li.get("sku"), _to_int(li.get("quantity")),
                    _to_decimal(li.get("price")), _to_decimal(li.get("total_discount")),
                    li.get("fulfillment_status"), li.get("fulfillment_service"),
                    li.get("vendor"), _to_bool(li.get("requires_shipping")),
                    _to_bool(li.get("taxable")), _to_bool(li.get("gift_card")),
                    _to_int(li.get("fulfillable_quantity")), _to_int(li.get("grams")),
                ))
        rows = self._dedupe_rows(rows, (0,))
        if rows:
            self._merge_stats(sql, rows)
            logger.info(f"Upserted {len(rows)} order line items")
        self._delete_orphans(
            "shopify_order_line_items", "line_item_id", "order_id", order_ids, keep_ids,
        )

    def _upsert_order_shipping_lines(self, orders: list):
        sql = """
        MERGE shopify_order_shipping_lines AS target
        USING (VALUES (?,?,?,?,?,?,?)) AS source
            (shipping_line_id, order_id, title, price, code, source, carrier_identifier)
        ON target.shipping_line_id = source.shipping_line_id
        WHEN MATCHED THEN UPDATE SET price = source.price, title = source.title
        WHEN NOT MATCHED THEN INSERT
            (shipping_line_id, order_id, title, price, code, source, carrier_identifier)
        VALUES
            (source.shipping_line_id, source.order_id, source.title, source.price,
             source.code, source.source, source.carrier_identifier)
        """
        rows = []
        order_ids = []
        keep_ids = []
        for o in orders:
            oid = _to_int(o.get("id"))
            if oid is None:
                continue
            order_ids.append(oid)
            for sl in (o.get("shipping_lines") or []):
                sid = _to_int(sl.get("id"))
                if sid is not None:
                    keep_ids.append(sid)
                rows.append((
                    sid, oid,
                    sl.get("title"), _to_decimal(sl.get("price")),
                    sl.get("code"), sl.get("source"),
                    sl.get("carrier_identifier"),
                ))
        rows = self._dedupe_rows(rows, (0,))
        if rows:
            self._merge_stats(sql, rows)
            logger.info(f"Upserted {len(rows)} shipping lines")
        self._delete_orphans(
            "shopify_order_shipping_lines", "shipping_line_id", "order_id",
            order_ids, keep_ids,
        )

    def _upsert_order_discount_codes(self, orders: list):
        sql = """
        MERGE shopify_order_discount_codes AS target
        USING (VALUES (?,?,?,?)) AS source (order_id, code, amount, type)
        ON target.order_id = source.order_id AND target.code = source.code
        WHEN MATCHED THEN UPDATE SET amount = source.amount, type = source.type
        WHEN NOT MATCHED THEN INSERT (order_id, code, amount, type)
        VALUES (source.order_id, source.code, source.amount, source.type)
        """
        rows = []
        order_ids = []
        keep_keys = []
        for o in orders:
            oid = _to_int(o.get("id"))
            if oid is None:
                continue
            order_ids.append(oid)
            for dc in (o.get("discount_codes") or []):
                code = dc.get("code")
                if code:
                    keep_keys.append((oid, code))
                rows.append((
                    oid, code, _to_decimal(dc.get("amount")), dc.get("type"),
                ))
        rows = self._dedupe_rows(rows, (0, 1))
        if rows:
            self._merge_stats(sql, rows)
            logger.info(f"Upserted {len(rows)} discount codes")
        for oid in set(order_ids):
            codes_for_order = [c for o, c in keep_keys if o == oid]
            if not codes_for_order:
                self.cursor.execute(
                    "DELETE FROM shopify_order_discount_codes WHERE order_id = ?", (oid,),
                )
            else:
                ph = ",".join("?" * len(codes_for_order))
                self.cursor.execute(
                    f"DELETE FROM shopify_order_discount_codes "
                    f"WHERE order_id = ? AND code NOT IN ({ph})",
                    [oid] + codes_for_order,
                )

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
             source.created_at, source.updated_at)
        """
        rows = []
        for f in fulfillments:
            tnums = f.get("tracking_numbers") or []
            rows.append((
                _to_int(f.get("id")), _to_int(f.get("order_id")), f.get("status"),
                f.get("tracking_number"),
                ",".join(str(t) for t in tnums)[:1000] if tnums else None,
                f.get("tracking_company"), f.get("tracking_url"),
                _to_int(f.get("location_id")), f.get("name"),
                _to_bool(f.get("notify_customer")),
                f.get("created_at"), f.get("updated_at"),
            ))

        rows = self._dedupe_rows(rows, (0,))
        ins, upd = self._merge_stats(sql, rows)
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
             source.quantity, source.fulfillment_service)
        """
        rows = []
        fulfillment_ids = []
        keep_keys = []
        for f in fulfillments:
            fid = _to_int(f.get("id"))
            oid = _to_int(f.get("order_id"))
            if fid is None:
                continue
            fulfillment_ids.append(fid)
            for li in (f.get("line_items") or []):
                lid = _to_int(li.get("id"))
                if lid is not None:
                    keep_keys.append((fid, lid))
                rows.append((
                    fid, lid, oid,
                    _to_int(li.get("quantity")), li.get("fulfillment_service"),
                ))
        rows = self._dedupe_rows(rows, (0, 1))
        if rows:
            self._merge_stats(sql, rows)
            logger.info(f"Upserted {len(rows)} fulfillment line items")
        for fid in set(fulfillment_ids):
            keys_for_f = [lid for ff, lid in keep_keys if ff == fid]
            if not keys_for_f:
                self.cursor.execute(
                    "DELETE FROM shopify_fulfillment_line_items WHERE fulfillment_id = ?",
                    (fid,),
                )
            else:
                ph = ",".join("?" * len(keys_for_f))
                self.cursor.execute(
                    f"DELETE FROM shopify_fulfillment_line_items "
                    f"WHERE fulfillment_id = ? AND line_item_id NOT IN ({ph})",
                    [fid] + keys_for_f,
                )

    # ------------------------------------------------------------------ #
    # Fulfillment Events                                                   #
    # ------------------------------------------------------------------ #

    def upsert_fulfillment_events(self, events: list) -> tuple:
        sql = """
        MERGE shopify_fulfillment_events AS target
        USING (VALUES (?,?,?,?,?,?)) AS source
            (event_id, fulfillment_id, order_id, status, message, happened_at)
        ON target.event_id = source.event_id
        WHEN MATCHED THEN UPDATE SET
            status      = source.status,
            message     = source.message,
            happened_at = source.happened_at
        WHEN NOT MATCHED THEN INSERT
            (event_id, fulfillment_id, order_id, status, message, happened_at)
        VALUES
            (source.event_id, source.fulfillment_id, source.order_id,
             source.status, source.message, source.happened_at)
        """
        rows = [
            (
                _to_int(e.get("id")), _to_int(e.get("fulfillment_id")),
                _to_int(e.get("order_id")),
                e.get("status"), e.get("message"), e.get("happened_at"),
            )
            for e in events
        ]
        rows = self._dedupe_rows(rows, (0,))
        ins, upd = self._merge_stats(sql, rows)
        logger.info(f"Upserted {len(rows)} fulfillment events ({ins} inserts, {upd} updates)")
        return ins, upd

    # ------------------------------------------------------------------ #
    # Returns                                                              #
    # ------------------------------------------------------------------ #

    def upsert_returns(self, returns: list, order_id: int) -> tuple:
        sql = """
        MERGE shopify_returns AS target
        USING (VALUES (?,?,?,?,?,?,?)) AS source
            (return_id, order_id, status, name, decline_reason, created_at, updated_at)
        ON target.return_id = source.return_id
        WHEN MATCHED THEN UPDATE SET
            status         = source.status,
            decline_reason = source.decline_reason,
            updated_at     = source.updated_at
        WHEN NOT MATCHED THEN INSERT
            (return_id, order_id, status, name, decline_reason, created_at, updated_at)
        VALUES
            (source.return_id, source.order_id, source.status, source.name,
             source.decline_reason, source.created_at, source.updated_at)
        """
        rows = [
            (
                _to_int(r.get("id")), _to_int(order_id), r.get("status"),
                r.get("name"), r.get("decline_reason"),
                r.get("created_at"), r.get("updated_at"),
            )
            for r in returns
        ]
        rows = self._dedupe_rows(rows, (0,))
        ins, upd = self._merge_stats(sql, rows)
        logger.info(f"Upserted {len(rows)} returns ({ins} inserts, {upd} updates)")

        li_count = self._upsert_return_line_items(returns, order_id)
        return ins, upd, li_count

    def _upsert_return_line_items(self, returns: list, order_id: int) -> int:
        sql = """
        MERGE shopify_return_line_items AS target
        USING (VALUES (?,?,?,?,?,?,?,?)) AS source
            (return_line_item_id, return_id, order_id, fulfillment_line_item_id,
             quantity, reason, reason_notes, customer_note)
        ON target.return_line_item_id = source.return_line_item_id
        WHEN MATCHED THEN UPDATE SET
            quantity      = source.quantity,
            reason        = source.reason,
            reason_notes  = source.reason_notes,
            customer_note = source.customer_note
        WHEN NOT MATCHED THEN INSERT
            (return_line_item_id, return_id, order_id, fulfillment_line_item_id,
             quantity, reason, reason_notes, customer_note)
        VALUES
            (source.return_line_item_id, source.return_id, source.order_id,
             source.fulfillment_line_item_id, source.quantity,
             source.reason, source.reason_notes, source.customer_note)
        """
        rows = []
        for r in returns:
            rid = _to_int(r.get("id"))
            if rid is None:
                continue
            for li in (r.get("return_line_items") or []):
                rows.append((
                    _to_int(li.get("id")), rid, _to_int(order_id),
                    _to_int(li.get("fulfillment_line_item_id")),
                    _to_int(li.get("quantity")),
                    li.get("reason"), li.get("reason_notes"), li.get("customer_note"),
                ))
        rows = self._dedupe_rows(rows, (0,))
        if rows:
            self._merge_stats(sql, rows)
            logger.info(f"Upserted {len(rows)} return line items")
        return len(rows)

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
            address1 = source.address1,
            city     = source.city,
            province = source.province,
            country  = source.country
        WHEN NOT MATCHED THEN INSERT
            (location_id, name, address1, city, province, country)
        VALUES
            (source.location_id, source.name, source.address1,
             source.city, source.province, source.country)
        """
        rows = [
            (
                _to_int(l.get("id")), l.get("name"), l.get("address1"),
                l.get("city"), l.get("province"), l.get("country"),
            )
            for l in locations
        ]
        rows = self._dedupe_rows(rows, (0,))
        ins, upd = self._merge_stats(sql, rows)
        logger.info(f"Upserted {len(rows)} locations ({ins} inserts, {upd} updates)")
        return ins, upd
