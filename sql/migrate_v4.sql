-- ============================================================
-- migrate_v4.sql — Tabelas de Returns do Shopify
-- ============================================================

-- ------------------------------------------------------------
-- 10. Returns (devoluções)
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_returns' AND xtype='U')
CREATE TABLE shopify_returns (
    return_id           BIGINT          NOT NULL PRIMARY KEY,
    order_id            BIGINT          NOT NULL,
    status              NVARCHAR(50),
    name                NVARCHAR(100),
    decline_reason      NVARCHAR(255),
    created_at          DATETIMEOFFSET,
    updated_at          DATETIMEOFFSET,
    extracted_at        DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT fk_return_order FOREIGN KEY (order_id)
        REFERENCES shopify_orders(order_id)
);

-- ------------------------------------------------------------
-- 11. Return line items (itens devolvidos)
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_return_line_items' AND xtype='U')
CREATE TABLE shopify_return_line_items (
    return_line_item_id         BIGINT          NOT NULL PRIMARY KEY,
    return_id                   BIGINT          NOT NULL,
    order_id                    BIGINT          NOT NULL,
    fulfillment_line_item_id    BIGINT,
    quantity                    INT,
    reason                      NVARCHAR(255),
    reason_notes                NVARCHAR(1000),
    customer_note               NVARCHAR(1000),
    extracted_at                DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT fk_rli_return FOREIGN KEY (return_id)
        REFERENCES shopify_returns(return_id)
);

PRINT 'migrate_v4 concluido com sucesso.'
