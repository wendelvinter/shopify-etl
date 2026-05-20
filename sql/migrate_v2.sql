-- Migration v2: adiciona inserts, updates, pid ao etl_run_log
-- Rodar uma vez em instalações existentes

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('etl_run_log') AND name = 'inserts')
    ALTER TABLE etl_run_log ADD inserts INT DEFAULT 0;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('etl_run_log') AND name = 'updates')
    ALTER TABLE etl_run_log ADD updates INT DEFAULT 0;

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('etl_run_log') AND name = 'pid')
    ALTER TABLE etl_run_log ADD pid INT;
