import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def log_run(loader, script_name, start_date, end_date, status, records, error_message=None):
    """Grava resultado da execução na tabela etl_run_log."""
    try:
        sql = """
        INSERT INTO etl_run_log
            (script_name, start_date, end_date, status, records_processed, error_message, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, SYSDATETIMEOFFSET())
        """
        loader.cursor.execute(sql, (
            script_name,
            start_date,
            end_date,
            status,
            records,
            error_message,
        ))
        loader.conn.commit()
    except Exception as e:
        logger.warning(f"Could not write to etl_run_log: {e}")
