import logging

logger = logging.getLogger(__name__)


def log_run(loader, script_name, start_date, end_date, status, records,
            error_message=None, inserts=0, updates=0, pid=None):
    """Grava resultado da execução na tabela etl_run_log."""
    try:
        sql = """
        INSERT INTO etl_run_log
            (script_name, start_date, end_date, status, records_processed,
             inserts, updates, pid, error_message, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, SYSDATETIMEOFFSET())
        """
        loader.cursor.execute(sql, (
            script_name, start_date, end_date, status, records,
            inserts or 0, updates or 0, pid, error_message,
        ))
        loader.conn.commit()
    except Exception as e:
        logger.warning(f"Could not write to etl_run_log: {e}")
