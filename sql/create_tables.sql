-- ============================================================
-- Shopify ETL — Criação das tabelas no SQL Server
-- Projeto: Logística San Diego
-- Rodar uma única vez antes da primeira execução do ETL
-- ============================================================

-- ------------------------------------------------------------
-- 1. Pedidos
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_orders' AND xtype='U')
CREATE TABLE shopify_orders (
    order_id                BIGINT          NOT NULL PRIMARY KEY,
    order_number            INT,
    order_name              NVARCHAR(50),
    financial_status        NVARCHAR(50),
    fulfillment_status      NVARCHAR(50),
    created_at              DATETIMEOFFSET,
    updated_at              DATETIMEOFFSET,
    processed_at            DATETIMEOFFSET,
    cancelled_at            DATETIMEOFFSET,
    closed_at               DATETIMEOFFSET,
    total_price             DECIMAL(18,2),
    subtotal_price          DECIMAL(18,2),
    total_tax               DECIMAL(18,2),
    total_discounts         DECIMAL(18,2),
    total_shipping_price    DECIMAL(18,2),
    currency                NVARCHAR(10),
    taxes_included          BIT,
    test                    BIT,
    confirmed               BIT,
    source_name             NVARCHAR(50),
    gateway                 NVARCHAR(100),
    payment_gateway_names   NVARCHAR(500),
    location_id             BIGINT,
    extracted_at            DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET()
);

-- ------------------------------------------------------------
-- 2. Fulfillments
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_fulfillments' AND xtype='U')
CREATE TABLE shopify_fulfillments (
    fulfillment_id      BIGINT          NOT NULL PRIMARY KEY,
    order_id            BIGINT          NOT NULL,
    status              NVARCHAR(50),
    tracking_number     NVARCHAR(255),
    tracking_numbers    NVARCHAR(1000),
    tracking_company    NVARCHAR(100),
    tracking_url        NVARCHAR(1000),
    location_id         BIGINT,
    fulfillment_name    NVARCHAR(100),
    notify_customer     BIT,
    created_at          DATETIMEOFFSET,
    updated_at          DATETIMEOFFSET,
    extracted_at        DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT fk_fulfillment_order FOREIGN KEY (order_id)
        REFERENCES shopify_orders(order_id)
);

-- ------------------------------------------------------------
-- 3. Fulfillment Events (rastreamento)
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_fulfillment_events' AND xtype='U')
CREATE TABLE shopify_fulfillment_events (
    event_id            BIGINT          NOT NULL PRIMARY KEY,
    fulfillment_id      BIGINT          NOT NULL,
    order_id            BIGINT          NOT NULL,
    status              NVARCHAR(100),
    message             NVARCHAR(1000),
    happened_at         DATETIMEOFFSET,
    extracted_at        DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT fk_event_fulfillment FOREIGN KEY (fulfillment_id)
        REFERENCES shopify_fulfillments(fulfillment_id)
);

-- ------------------------------------------------------------
-- 4. Locations (warehouses / lojas)
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='shopify_locations' AND xtype='U')
CREATE TABLE shopify_locations (
    location_id         BIGINT          NOT NULL PRIMARY KEY,
    name                NVARCHAR(255),
    address1            NVARCHAR(255),
    city                NVARCHAR(100),
    province            NVARCHAR(100),
    country             NVARCHAR(10),
    extracted_at        DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET()
);

-- ------------------------------------------------------------
-- 5. Line items dos pedidos
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

-- ------------------------------------------------------------
-- 6. Shipping lines dos pedidos
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- 7. Discount codes dos pedidos
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- 8. Line items dos fulfillments (quais itens foram enviados)
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- 9. Log de execuções do ETL
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='etl_run_log' AND xtype='U')
CREATE TABLE etl_run_log (
    run_id              INT             IDENTITY(1,1) PRIMARY KEY,
    script_name         NVARCHAR(100),
    start_date          DATE,
    end_date            DATE,
    status              NVARCHAR(20),
    records_processed   INT,
    inserts             INT             DEFAULT 0,
    updates             INT             DEFAULT 0,
    pid                 INT,
    error_message       NVARCHAR(MAX),
    started_at          DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    finished_at         DATETIMEOFFSET
);
