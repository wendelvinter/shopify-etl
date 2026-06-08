"""
Verificação e execução automática de migrations SQL Server na inicialização.
"""
import logging
import re
from pathlib import Path

from config.constants import (
    SQL_SERVER_DATABASE,
    SQL_SERVER_HOST,
    SQL_SERVER_PASSWORD,
    SQL_SERVER_USER,
)

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

MIGRATIONS = [
    ("001_create_tables", "create_tables.sql", "Tabelas base do ETL"),
    ("002_migrate_v2", "migrate_v2.sql", "Campos inserts/updates/pid no etl_run_log"),
    ("003_migrate_v3", "migrate_v3.sql", "Expansão de schema (pedidos, fulfillments, novas tabelas)"),
    ("004_migrate_v4", "migrate_v4.sql", "Tabelas de Returns (devoluções)"),
]

_SCHEMA_TABLE_SQL = """
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='schema_migrations' AND xtype='U')
CREATE TABLE schema_migrations (
    version     NVARCHAR(100)   NOT NULL PRIMARY KEY,
    applied_at  DATETIMEOFFSET  NOT NULL DEFAULT SYSDATETIMEOFFSET()
);
"""

_checked = False


def _sql_configured() -> bool:
    return bool(SQL_SERVER_HOST and SQL_SERVER_DATABASE and SQL_SERVER_USER and SQL_SERVER_PASSWORD)


def _split_sql_batches(sql: str) -> list[str]:
    """Divide o script em batches (cada bloco IF NOT EXISTS ...)."""
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.upper().startswith("PRINT "):
            continue
        lines.append(line)

    text = "\n".join(lines)
    parts = re.split(r"(?=^\s*IF NOT EXISTS)", text, flags=re.MULTILINE | re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _ensure_tracking_table(cursor) -> None:
    cursor.execute(_SCHEMA_TABLE_SQL)


def _get_applied_versions(cursor) -> set[str]:
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def _apply_migration_file(cursor, version: str, filename: str) -> None:
    path = SQL_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de migration não encontrado: {path}")

    batches = _split_sql_batches(path.read_text(encoding="utf-8"))
    if not batches:
        raise ValueError(f"Migration {version} não contém statements executáveis")

    for batch in batches:
        cursor.execute(batch)

    cursor.execute("INSERT INTO schema_migrations (version) VALUES (?)", version)


def run_pending_migrations() -> dict:
    """
    Garante que todas as migrations pendentes foram aplicadas.
    Idempotente: pode ser chamado várias vezes (flag em memória evita reexecução no mesmo processo).
    """
    global _checked

    if _checked:
        return {"status": "skipped", "reason": "already_checked_this_process"}

    result = {
        "status": "ok",
        "applied": [],
        "pending_before": [],
        "skipped": [],
        "error": None,
    }

    if not _sql_configured():
        result["status"] = "skipped"
        result["reason"] = "sql_not_configured"
        logger.warning("Migrations ignoradas: credenciais SQL Server incompletas no .env")
        _checked = True
        return result

    try:
        from loaders.sqlserver_loader import get_connection

        conn = get_connection()
        cursor = conn.cursor()

        try:
            _ensure_tracking_table(cursor)
            conn.commit()

            applied = _get_applied_versions(cursor)
            pending = [m for m in MIGRATIONS if m[0] not in applied]
            result["pending_before"] = [m[0] for m in pending]

            if not pending:
                result["skipped"] = [m[0] for m in MIGRATIONS]
                logger.info("Migrations: banco já está atualizado (%d versões)", len(applied))
                _checked = True
                return result

            for version, filename, description in pending:
                logger.info("Aplicando migration %s — %s", version, description)
                try:
                    _apply_migration_file(cursor, version, filename)
                    conn.commit()
                    result["applied"].append(version)
                    logger.info("Migration %s aplicada com sucesso", version)
                except Exception:
                    conn.rollback()
                    raise

            logger.info(
                "Migrations concluídas: %d aplicada(s), %d já existente(s)",
                len(result["applied"]),
                len(MIGRATIONS) - len(result["applied"]),
            )
        finally:
            cursor.close()
            conn.close()

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        logger.error("Falha ao executar migrations: %s", exc)
        raise

    _checked = True
    return result


def ensure_migrations() -> dict:
    """Wrapper seguro para startup: não interrompe o app se SQL estiver indisponível."""
    try:
        return run_pending_migrations()
    except Exception as exc:
        logger.error("ensure_migrations falhou: %s", exc)
        return {"status": "error", "error": str(exc)}
