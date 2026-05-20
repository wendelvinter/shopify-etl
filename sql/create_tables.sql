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
    order_id            BIGINT          NOT NULL PRIMARY KEY,
    order_number        INT,
    financial_status    NVARCHAR(50),
    fulfillment_status  NVARCHAR(50),
    created_at          DATETIMEOFFSET,
    updated_at          DATETIMEOFFSET,
    total_price         DECIMAL(18, 2),
    currency            NVARCHAR(10),
    extracted_at        DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET()
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
    tracking_company    NVARCHAR(100),
    tracking_url        NVARCHAR(1000),
    created_at          DATETIMEOFFSET,
    updated_at          DATETIMEOFFSET,
    extracted_at        DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT fk_fulfillment_order FOREIGN KEY (order_id)
        REFERENCES shopify_orders(order_id)
);

-- ------------------------------------------------------------
-- 3. Fulfillment Events (rastreamento em tempo real)
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
-- 5. Log de execuções do ETL
-- ------------------------------------------------------------
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='etl_run_log' AND xtype='U')
CREATE TABLE etl_run_log (
    run_id              INT             IDENTITY(1,1) PRIMARY KEY,
    script_name         NVARCHAR(100),
    start_date          DATE,
    end_date            DATE,
    status              NVARCHAR(20),   -- success | error
    records_processed   INT,
    error_message       NVARCHAR(MAX),
    started_at          DATETIMEOFFSET  DEFAULT SYSDATETIMEOFFSET(),
    finished_at         DATETIMEOFFSET
);
