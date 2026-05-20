-- ============================================================
-- migrate_v3.sql — Novos campos e tabelas (expansão de schema)
-- Rodar no SSMS em instâncias que já têm as tabelas criadas
-- ============================================================

-- ------------------------------------------------------------
-- shopify_orders — novos campos
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'order_name')
    ALTER TABLE shopify_orders ADD order_name NVARCHAR(50);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'processed_at')
    ALTER TABLE shopify_orders ADD processed_at DATETIMEOFFSET;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'cancelled_at')
    ALTER TABLE shopify_orders ADD cancelled_at DATETIMEOFFSET;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'closed_at')
    ALTER TABLE shopify_orders ADD closed_at DATETIMEOFFSET;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'subtotal_price')
    ALTER TABLE shopify_orders ADD subtotal_price DECIMAL(18,2);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'total_tax')
    ALTER TABLE shopify_orders ADD total_tax DECIMAL(18,2);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'total_discounts')
    ALTER TABLE shopify_orders ADD total_discounts DECIMAL(18,2);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'total_shipping_price')
    ALTER TABLE shopify_orders ADD total_shipping_price DECIMAL(18,2);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'taxes_included')
    ALTER TABLE shopify_orders ADD taxes_included BIT;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'test')
    ALTER TABLE shopify_orders ADD test BIT;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'confirmed')
    ALTER TABLE shopify_orders ADD confirmed BIT;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'source_name')
    ALTER TABLE shopify_orders ADD source_name NVARCHAR(50);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'gateway')
    ALTER TABLE shopify_orders ADD gateway NVARCHAR(100);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'payment_gateway_names')
    ALTER TABLE shopify_orders ADD payment_gateway_names NVARCHAR(500);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_orders') AND name = 'location_id')
    ALTER TABLE shopify_orders ADD location_id BIGINT;

-- ------------------------------------------------------------
-- shopify_fulfillments — novos campos
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_fulfillments') AND name = 'tracking_numbers')
    ALTER TABLE shopify_fulfillments ADD tracking_numbers NVARCHAR(1000);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_fulfillments') AND name = 'location_id')
    ALTER TABLE shopify_fulfillments ADD location_id BIGINT;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_fulfillments') AND name = 'fulfillment_name')
    ALTER TABLE shopify_fulfillments ADD fulfillment_name NVARCHAR(100);

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('shopify_fulfillments') AND name = 'notify_customer')
    ALTER TABLE shopify_fulfillments ADD notify_customer BIT;

-- ------------------------------------------------------------
-- Novas tabelas (só cria se não existir)
-- ------------------------------------------------------------

IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_order_line_items' AND xtype='U')
CREATE TABLE shopify_order_line_items (
    line_item_id            BIGINT          NOT NULL PRIMARY KEY,
    order_id                BIGINT          NOT NULL,
    product_id              BIGINT,
    variant_id              BIGINT,
    title                   NVARCHAR(500),
    variant_title           NVARCHAR(255),
    sku                     NVARCHAR(255),
    quantity                INT,
    price                   DECIMAL(18,2),
    total_discount          DECIMAL(18,2),
    fulfillment_status      NVARCHAR(50),
    fulfillment_service     NVARCHAR(100),
    vendor                  NVARCHAR(255),
    requires_shipping       BIT,
    taxable                 BIT,
    gift_card               BIT,
    fulfillable_quantity    INT,
    grams                   INT,
    extracted_at            DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT fk_line_item_order FOREIGN KEY (order_id)
        REFERENCES shopify_orders(order_id)
);

IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_order_shipping_lines' AND xtype='U')
CREATE TABLE shopify_order_shipping_lines (
    shipping_line_id        BIGINT          NOT NULL PRIMARY KEY,
    order_id                BIGINT          NOT NULL,
    title                   NVARCHAR(255),
    price                   DECIMAL(18,2),
    code                    NVARCHAR(100),
    source                  NVARCHAR(100),
    carrier_identifier      NVARCHAR(100),
    extracted_at            DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT fk_shipping_line_order FOREIGN KEY (order_id)
        REFERENCES shopify_orders(order_id)
);

IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_order_discount_codes' AND xtype='U')
CREATE TABLE shopify_order_discount_codes (
    order_id                BIGINT          NOT NULL,
    code                    NVARCHAR(255)   NOT NULL,
    amount                  DECIMAL(18,2),
    type                    NVARCHAR(50),
    extracted_at            DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT pk_discount_codes PRIMARY KEY (order_id, code),
    CONSTRAINT fk_discount_code_order FOREIGN KEY (order_id)
        REFERENCES shopify_orders(order_id)
);

IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_fulfillment_line_items' AND xtype='U')
CREATE TABLE shopify_fulfillment_line_items (
    fulfillment_id          BIGINT          NOT NULL,
    line_item_id            BIGINT          NOT NULL,
    order_id                BIGINT          NOT NULL,
    quantity                INT,
    fulfillment_service     NVARCHAR(100),
    extracted_at            DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT pk_fulfillment_line_items PRIMARY KEY (fulfillment_id, line_item_id),
    CONSTRAINT fk_fli_fulfillment FOREIGN KEY (fulfillment_id)
        REFERENCES shopify_fulfillments(fulfillment_id)
);

PRINT 'migrate_v3 concluído com sucesso.';
