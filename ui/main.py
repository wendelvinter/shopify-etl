"""
Interface de controle do ETL — Shopify -> SQL Server
Dashboard: http://localhost:8000
Setup:     http://localhost:8000/setup
"""
import html
import logging
import os
import subprocess
import sys
import json
import hashlib
import hmac as hmac_lib
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

sys.path.append(str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"
SCHEDULES_FILE = ROOT / "config" / "schedules.json"
SCRIPTS_DIR = ROOT / "scripts"

ALLOWED_ETL_SCRIPTS = frozenset({"etl_orders", "etl_fulfillments", "etl_locations"})
_schedule_lock = threading.Lock()
_etl_lock = threading.Lock()
_ui_logger = logging.getLogger(__name__)

# -- Scheduler ----------------------------------------------------------------
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    _scheduler = BackgroundScheduler(timezone="UTC")
    HAS_SCHEDULER = True
except ImportError:
    _scheduler = None
    HAS_SCHEDULER = False

DEFAULT_SCHEDULES = {
    "etl_orders": {
        "label": "Orders",
        "enabled": False,
        "frequency": "daily",
        "time": "06:00",
        "day_of_week": "mon",
        "every_n": 2,
        "date_range": "yesterday_today",
        "days_back": 1,
    },
    "etl_fulfillments": {
        "label": "Fulfillments + Events",
        "enabled": False,
        "frequency": "daily",
        "time": "07:00",
        "day_of_week": "mon",
        "every_n": 2,
        "date_range": "yesterday_today",
        "days_back": 1,
    },
    "etl_locations": {
        "label": "Locations",
        "enabled": False,
        "frequency": "weekly",
        "time": "05:00",
        "day_of_week": "mon",
        "every_n": 7,
        "date_range": "last_n_days",
        "days_back": 30,
    },
}


def load_schedules() -> dict:
    base = {k: dict(v) for k, v in DEFAULT_SCHEDULES.items()}
    if SCHEDULES_FILE.exists():
        try:
            saved = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
            for key, defaults in base.items():
                if key in saved:
                    defaults.update(saved[key])
        except Exception as e:
            _ui_logger.warning("Não foi possível ler schedules.json: %s", e)
    return base


def save_schedules_file(schedules: dict):
    SCHEDULES_FILE.write_text(json.dumps(schedules, indent=2), encoding="utf-8")


def _date_range_for(cfg: dict) -> tuple:
    today = datetime.today()
    n = int(cfg.get("days_back", 1))
    if cfg.get("date_range") == "last_n_days":
        return (today - timedelta(days=n)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")
    return (today - timedelta(days=1)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _escape_html(value) -> str:
    return html.escape(str(value) if value is not None else "", quote=True)


def _label_with_configured(label: str, configured: bool, hint_text: str) -> str:
    """Badge no label (não após o input) para não quebrar o layout da row."""
    if not configured:
        return label
    return (
        f'{label} <span class="badge badge-green configured-badge">'
        f'{_escape_html(hint_text)}</span>'
    )


def _env_quote(value: str) -> str:
    s = "" if value is None else str(value)
    if any(c in s for c in ' \t\n\r#="\\\''):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _parse_time_hm(time_str: str) -> tuple:
    parts = (time_str or "06:00").strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Horário inválido: {time_str}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Horário fora do intervalo: {time_str}")
    return h, m


def _validate_shopify_store_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https", "http"):
        raise ValueError("URL da loja deve usar http ou https")
    host = (parsed.hostname or "").lower()
    if not host.endswith(".myshopify.com"):
        raise ValueError("URL deve ser de um domínio *.myshopify.com")
    return f"https://{host}"


def _normalize_shop_domain(shop: str) -> str:
    return shop.strip().lower().replace("https://", "").split("/")[0]


def _resolve_script(script_name: str) -> Path:
    if script_name not in ALLOWED_ETL_SCRIPTS:
        raise ValueError(f"Script não permitido: {script_name}")
    path = (SCRIPTS_DIR / f"{script_name}.py").resolve()
    if not path.is_file() or SCRIPTS_DIR.resolve() not in path.parents:
        raise ValueError(f"Script inválido: {script_name}")
    return path


def _reap_running_procs():
    for pid, proc in list(_running_procs.items()):
        if proc.poll() is not None:
            _running_procs.pop(pid, None)


def _spawn_etl(cmd: list) -> subprocess.Popen:
    with _etl_lock:
        _reap_running_procs()
        if _running_procs:
            raise RuntimeError(
                "Já existe um ETL em execução. Aguarde terminar ou cancele o run atual."
            )
        proc = subprocess.Popen(cmd, cwd=str(ROOT))
        _running_procs[proc.pid] = proc
        return proc


def _run_job(script_name: str, cfg: dict):
    if script_name not in ALLOWED_ETL_SCRIPTS:
        _ui_logger.warning("Job ignorado — script não permitido: %s", script_name)
        return
    start, end = _date_range_for(cfg)
    try:
        script_path = _resolve_script(script_name)
        _spawn_etl(
            [sys.executable, str(script_path), "--start-date", start, "--end-date", end],
        )
    except RuntimeError as e:
        _ui_logger.warning("Scheduler não iniciou ETL: %s", e)
    except Exception as e:
        _ui_logger.error("Scheduler falhou ao iniciar %s: %s", script_name, e)


def _make_trigger(cfg: dict):
    h, m = _parse_time_hm(cfg.get("time", "06:00"))
    freq = cfg.get("frequency", "daily")
    n = max(1, int(cfg.get("every_n", 15)))
    if freq == "every_n_minutes":
        return IntervalTrigger(minutes=n)
    if freq == "every_n_hours":
        return IntervalTrigger(hours=n)
    if freq == "weekly":
        dow = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}.get(
            cfg.get("day_of_week", "mon"), 0
        )
        return CronTrigger(day_of_week=dow, hour=int(h), minute=int(m))
    if freq == "weekdays":
        return CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m))
    if freq == "every_n_days":
        return CronTrigger(day=f"*/{n}", hour=int(h), minute=int(m))
    return CronTrigger(hour=int(h), minute=int(m))


def apply_schedules(schedules: dict):
    if not HAS_SCHEDULER:
        return
    with _schedule_lock:
        _scheduler.remove_all_jobs()
        for name, cfg in schedules.items():
            if name not in ALLOWED_ETL_SCRIPTS:
                continue
            if cfg.get("enabled"):
                try:
                    _scheduler.add_job(
                        _run_job, _make_trigger(cfg), args=[name, cfg],
                        id=name, replace_existing=True,
                    )
                except ValueError as e:
                    _ui_logger.error("Agendamento inválido para %s: %s", name, e)


@asynccontextmanager
async def lifespan(_):
    import logging
    from utils.logger import setup_logger
    from utils.db_migrations import ensure_migrations

    setup_logger()
    mig = ensure_migrations()
    if mig.get("applied"):
        logging.getLogger(__name__).info(
            "Migrations aplicadas na inicialização: %s", ", ".join(mig["applied"])
        )
    if HAS_SCHEDULER:
        apply_schedules(load_schedules())
        _scheduler.start()
    yield
    if HAS_SCHEDULER and _scheduler.running:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Shopify ETL Control Panel", lifespan=lifespan)

_oauth_states: dict = {}
_running_procs: dict = {}

CONFIG_CHANGES_FILE = ROOT / "config" / "config_changes.json"

# -- i18n ---------------------------------------------------------------------
TRANSLATIONS = {
    "en": {
        "nav_dashboard": "Dashboard", "nav_setup": "Setup",
        "card_config": "Configuration", "card_tables": "Tables",
        "card_run_etl": "Run ETL", "card_recent_runs": "Recent Runs",
        "card_volume": "Processed Volume", "card_log": "Today's Log",
        "label_script": "Script", "label_start_date": "Start date",
        "label_end_date": "End date", "btn_run": "&#9654; Run",
        "label_run_by_order": "Or run by specific Order ID",
        "btn_run_order": "&#9654; Run Order",
        "filter_all_scripts": "All scripts", "filter_all_status": "All status",
        "th_script": "Script", "th_start": "Start", "th_end": "End",
        "th_status": "Status", "th_records": "Records", "th_ins_upd": "Ins/Upd",
        "th_duration": "Duration", "th_finished": "Finished at", "th_error": "Error",
        "sched_paused": "Paused", "sched_active": "Active",
        "btn_pause": "&#9208; Pause", "btn_resume": "&#9654; Resume",
        "label_shopify_store": "Shopify Store", "label_sql_host": "SQL Server Host",
        "label_database": "Database", "last_update": "Last update",
        "modal_error_title": "Full error",
        "sched_inactive": "Inactive", "sched_enable": "Enable schedule",
        "sched_repeat": "Repeat",
        "sched_every_n_min": "Every N minutes", "sched_every_n_hours": "Every N hours",
        "sched_daily": "Every day", "sched_weekdays": "Weekdays (Mon–Fri)",
        "sched_weekly": "Once a week", "sched_every_n_days": "Every N days",
        "sched_dow": "Day of week", "sched_time": "At what time? (UTC)",
        "sched_n_minutes": "How many minutes?", "sched_n_hours": "How many hours?",
        "sched_n_days": "How many days?",
        "sched_data": "Data to pull", "sched_since_yesterday": "Since yesterday",
        "sched_last_n_days": "Last N days", "sched_days_back": "How many days back?",
        "sched_next_run": "Next run", "btn_save": "Save", "btn_run_now": "&#9654; Run now",
        "card_oauth": "Authorize App (OAuth)", "badge_recommended": "Recommended",
        "oauth_desc": "Authorize <strong>Logistics_ETL</strong> in the store via OAuth. The access token is captured and saved automatically. Do this once per store.",
        "label_store_url": "Store URL", "label_client_id": "Client ID",
        "label_client_secret": "Client Secret",
        "btn_oauth_authorize": "Save and Authorize on Shopify",
        "card_shopify_api": "Shopify API", "label_access_token": "Access Token",
        "label_api_version": "API Version", "btn_test": "Test Connection",
        "card_sql": "SQL Server", "label_host": "Host", "label_port": "Port",
        "label_username": "Username", "label_password": "Password",
        "card_config_changes": "Config Change Log", "no_changes": "No changes recorded.",
        "card_scheduling": "Scheduling",
        "sched_desc": "Configure automated runs per category. Times are in UTC. Changes take effect immediately without restarting the server.",
        "loading": "Loading...", "saving": "Saving...", "starting": "Starting...",
        "testing": "Testing...", "saved": "&#10003; Saved", "started": "&#10003; Started",
        "confirm_cancel": "Cancel this run?", "type_order_id": "Enter an Order ID.",
        "oauth_required": "Store URL, Client ID and Client Secret are required.",
        "oauth_success_msg": "App authorized successfully. Access token saved to .env.",
        "token_configured": "Token configured",
        "secret_configured": "Secret configured",
        "password_configured": "Password configured",
        "apscheduler_active": "APScheduler active",
        "apscheduler_missing": "APScheduler not installed",
        "dow_mon": "Monday", "dow_tue": "Tuesday", "dow_wed": "Wednesday",
        "dow_thu": "Thursday", "dow_fri": "Friday", "dow_sat": "Saturday", "dow_sun": "Sunday",
    },
    "pt": {
        "nav_dashboard": "Dashboard", "nav_setup": "Configurações",
        "card_config": "Configuração", "card_tables": "Tabelas",
        "card_run_etl": "Executar ETL", "card_recent_runs": "Execuções Recentes",
        "card_volume": "Volume Processado", "card_log": "Log de Hoje",
        "label_script": "Script", "label_start_date": "Data inicial",
        "label_end_date": "Data final", "btn_run": "&#9654; Executar",
        "label_run_by_order": "Ou executar por Order ID específico",
        "btn_run_order": "&#9654; Executar Order",
        "filter_all_scripts": "Todos os scripts", "filter_all_status": "Todos os status",
        "th_script": "Script", "th_start": "Início", "th_end": "Fim",
        "th_status": "Status", "th_records": "Registros", "th_ins_upd": "Ins/Upd",
        "th_duration": "Duração", "th_finished": "Finalizado em", "th_error": "Erro",
        "sched_paused": "Pausado", "sched_active": "Ativo",
        "btn_pause": "&#9208; Pausar", "btn_resume": "&#9654; Retomar",
        "label_shopify_store": "Loja Shopify", "label_sql_host": "Servidor SQL",
        "label_database": "Banco de Dados", "last_update": "Última atualização",
        "modal_error_title": "Erro completo",
        "sched_inactive": "Inativo", "sched_enable": "Ativar agendamento",
        "sched_repeat": "Repetir",
        "sched_every_n_min": "A cada N minutos", "sched_every_n_hours": "A cada N horas",
        "sched_daily": "Todo dia", "sched_weekdays": "Dias úteis (Seg–Sex)",
        "sched_weekly": "Uma vez por semana", "sched_every_n_days": "A cada N dias",
        "sched_dow": "Dia da semana", "sched_time": "Horário? (UTC)",
        "sched_n_minutes": "A cada quantos minutos?", "sched_n_hours": "A cada quantas horas?",
        "sched_n_days": "A cada quantos dias?",
        "sched_data": "Dados a buscar", "sched_since_yesterday": "Desde ontem",
        "sched_last_n_days": "Últimos N dias", "sched_days_back": "Quantos dias atrás?",
        "sched_next_run": "Próxima execução", "btn_save": "Salvar", "btn_run_now": "&#9654; Executar agora",
        "card_oauth": "Autorizar App (OAuth)", "badge_recommended": "Recomendado",
        "oauth_desc": "Autorize o <strong>Logistics_ETL</strong> na loja via OAuth. O access token é capturado e salvo automaticamente. Faça isso uma vez por loja.",
        "label_store_url": "URL da Loja", "label_client_id": "Client ID",
        "label_client_secret": "Client Secret",
        "btn_oauth_authorize": "Salvar e Autorizar no Shopify",
        "card_shopify_api": "Shopify API", "label_access_token": "Access Token",
        "label_api_version": "Versão da API", "btn_test": "Testar Conexão",
        "card_sql": "SQL Server", "label_host": "Host", "label_port": "Porta",
        "label_username": "Usuário", "label_password": "Senha",
        "card_config_changes": "Log de Alterações", "no_changes": "Nenhuma alteração registrada.",
        "card_scheduling": "Agendamento",
        "sched_desc": "Configure execuções automáticas por categoria. Horários em UTC. As alterações têm efeito imediato sem reiniciar o servidor.",
        "loading": "Carregando...", "saving": "Salvando...", "starting": "Iniciando...",
        "testing": "Testando...", "saved": "&#10003; Salvo", "started": "&#10003; Iniciado",
        "confirm_cancel": "Cancelar este run?", "type_order_id": "Digite um Order ID.",
        "oauth_required": "URL da loja, Client ID e Client Secret são obrigatórios.",
        "oauth_success_msg": "App autorizado com sucesso. Access token salvo no .env.",
        "token_configured": "Token configurado",
        "secret_configured": "Secret configurado",
        "password_configured": "Senha configurada",
        "apscheduler_active": "APScheduler ativo",
        "apscheduler_missing": "APScheduler não instalado",
        "dow_mon": "Segunda", "dow_tue": "Terça", "dow_wed": "Quarta",
        "dow_thu": "Quinta", "dow_fri": "Sexta", "dow_sat": "Sábado", "dow_sun": "Domingo",
    },
}


def get_lang(request: Request) -> str:
    return request.cookies.get("lang", "pt")


def log_config_change(changed_keys: list):
    entry = {"timestamp": datetime.now().isoformat(), "keys": changed_keys}
    changes = []
    if CONFIG_CHANGES_FILE.exists():
        try:
            changes = json.loads(CONFIG_CHANGES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            _ui_logger.warning("Não foi possível ler config_changes.json: %s", e)
    changes.insert(0, entry)
    CONFIG_CHANGES_FILE.write_text(json.dumps(changes[:200], indent=2), encoding="utf-8")


def get_config_changes(limit: int = 30) -> list:
    if not CONFIG_CHANGES_FILE.exists():
        return []
    try:
        return json.loads(CONFIG_CHANGES_FILE.read_text(encoding="utf-8"))[:limit]
    except Exception:
        return []


# -- .env helpers -------------------------------------------------------------
def read_env() -> dict:
    out = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_env(updates: dict):
    lines = []
    written = set()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k = s.split("=", 1)[0].strip()
                if k in updates:
                    lines.append(f"{k}={_env_quote(updates[k])}")
                    written.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
    for k, v in updates.items():
        if k not in written:
            lines.append(f"{k}={_env_quote(v)}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    from config.constants import reload_config
    reload_config()


# -- DB helpers ---------------------------------------------------------------
def get_db_stats():
    stats = []
    try:
        from loaders.sqlserver_loader import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        for table in ["shopify_orders", "shopify_fulfillments", "shopify_fulfillment_events", "shopify_locations"]:
            try:
                cursor.execute(f"SELECT COUNT(*), MAX(extracted_at) FROM {table}")
                count, last = cursor.fetchone()
                stats.append({"table": table, "count": count or 0,
                               "last_update": str(last)[:19] if last else "—"})
            except Exception:
                stats.append({"table": table, "count": 0, "last_update": "—"})
        conn.close()
    except Exception as e:
        stats = [{"table": "SQL Server", "count": 0, "last_update": f"Error: {str(e)[:60]}"}]
    return stats


def get_recent_runs(limit=20, script_filter="", status_filter=""):
    runs = []
    limit = max(1, min(int(limit), 100))
    try:
        from loaders.sqlserver_loader import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        where_parts = []
        params = []
        if script_filter:
            if script_filter not in ALLOWED_ETL_SCRIPTS:
                return runs
            where_parts.append("script_name = ?")
            params.append(script_filter)
        if status_filter:
            if status_filter not in ("success", "error"):
                return runs
            where_parts.append("status = ?")
            params.append(status_filter)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        try:
            sql = f"""
                SELECT TOP {limit} script_name, start_date, end_date, status,
                       records_processed, error_message, finished_at,
                       ISNULL(inserts,0), ISNULL(updates,0), pid,
                       DATEDIFF(second, started_at, ISNULL(finished_at, SYSDATETIMEOFFSET()))
                FROM etl_run_log {where} ORDER BY run_id DESC
            """
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
        except Exception:
            sql = f"""
                SELECT TOP {limit} script_name, start_date, end_date, status,
                       records_processed, error_message, finished_at,
                       0, 0, NULL, NULL
                FROM etl_run_log {where} ORDER BY run_id DESC
            """
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
        for row in cursor.fetchall():
            dur = row[10]
            if dur is not None:
                dur_str = f"{dur//60}m {dur%60}s" if dur >= 60 else f"{dur}s"
            else:
                dur_str = "—"
            runs.append({
                "script": row[0] or "—", "start": str(row[1]), "end": str(row[2]),
                "status": row[3] or "—", "records": row[4] or 0,
                "error": row[5] or "", "finished_at": str(row[6])[:19] if row[6] else "—",
                "inserts": row[7] or 0, "updates": row[8] or 0,
                "pid": row[9], "duration": dur_str,
            })
        conn.close()
    except Exception as e:
        runs = [{"script": "—", "status": "error", "error": str(e)[:120],
                 "records": 0, "start": "—", "end": "—", "finished_at": "—",
                 "inserts": 0, "updates": 0, "pid": None, "duration": "—"}]
    return runs


# -- HTML helpers -------------------------------------------------------------
BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #1a1a1a; }
header { background: #1a1a1a; color: white; padding: 14px 32px; display: flex; align-items: center; justify-content: space-between; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand h1 { font-size: 18px; font-weight: 500; }
.tag { font-size: 12px; background: #333; padding: 3px 8px; border-radius: 4px; color: #aaa; }
nav { display: flex; gap: 4px; align-items: center; }
nav a { color: #aaa; text-decoration: none; font-size: 14px; padding: 7px 16px; border-radius: 6px; transition: all .15s; }
nav a:hover { background: #333; color: white; }
nav a.active { background: #444; color: white; font-weight: 500; }
.lang-switcher { display: flex; gap: 2px; margin-left: 12px; border-left: 1px solid #333; padding-left: 12px; }
.lang-btn { background: none; border: 1px solid transparent; color: #aaa; font-size: 12px; font-weight: 600; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all .15s; letter-spacing: .5px; }
.lang-btn:hover { color: white; border-color: #555; }
.lang-btn.active { color: white; border-color: #666; background: #333; }
.container { max-width: 1100px; margin: 32px auto; padding: 0 24px; display: grid; gap: 24px; }
.card { background: white; border-radius: 10px; padding: 24px; border: 1px solid #e5e5e5; }
.card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.card-header h2 { font-size: 14px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: .5px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.field { flex: 1; min-width: 160px; }
.field label { display: block; font-size: 12px; font-weight: 500; color: #555; margin-bottom: 6px; }
.configured-badge { margin-left: 6px; font-weight: 500; vertical-align: middle; text-transform: none; letter-spacing: 0; }
input[type=text], input[type=password], input[type=time], input[type=number], select {
  width: 100%; padding: 9px 12px; border: 1px solid #ddd; border-radius: 6px;
  font-size: 14px; background: white; color: #1a1a1a; }
input:focus, select:focus { outline: none; border-color: #1a1a1a; box-shadow: 0 0 0 2px rgba(26,26,26,.08); }
.btn { padding: 9px 18px; border-radius: 6px; border: none; cursor: pointer; font-size: 14px; font-weight: 500; transition: all .15s; }
.btn-dark { background: #1a1a1a; color: white; }
.btn-dark:hover { background: #333; }
.btn-outline { background: white; color: #555; border: 1px solid #ddd; }
.btn-outline:hover { background: #f5f5f5; border-color: #bbb; }
.btn-sm { padding: 6px 12px; font-size: 13px; }
.badge { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.badge-green { background: #d4edda; color: #155724; }
.badge-gray  { background: #f0f0f0; color: #777; }
.badge-red   { background: #f8d7da; color: #721c24; }
.badge-blue  { background: #d0e8ff; color: #004085; }
.result { display: none; margin-top: 12px; padding: 9px 14px; border-radius: 6px; font-size: 13px; }
.result.ok    { background: #d4edda; color: #155724; display: block; }
.result.error { background: #f8d7da; color: #721c24; display: block; }
.sep { border: none; border-top: 1px solid #f0f0f0; margin: 20px 0; }
.toggle-wrap { display: flex; align-items: center; gap: 10px; }
.toggle { position: relative; display: inline-block; width: 44px; height: 24px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; inset: 0; background: #ccc; border-radius: 24px; transition: .2s; }
.slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: .2s; }
input:checked + .slider { background: #1a1a1a; }
input:checked + .slider:before { transform: translateX(20px); }
.toggle-label { font-size: 14px; font-weight: 500; }
"""

DASHBOARD_CSS = """
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
.stat-card { border: 1px solid #e5e5e5; border-radius: 8px; padding: 16px; }
.stat-card .label { font-size: 12px; color: #888; margin-bottom: 6px; }
.stat-card .value { font-size: 28px; font-weight: 600; }
.stat-card .sub { font-size: 11px; color: #aaa; margin-top: 4px; }
.etl-form { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 12px; align-items: end; }
#alert { display: none; margin-top: 12px; padding: 10px 14px; border-radius: 6px; font-size: 13px; }
#alert.success { background: #d4edda; color: #155724; display: block; }
#alert.error   { background: #f8d7da; color: #721c24; display: block; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 12px; font-size: 11px; color: #888; text-transform: uppercase; border-bottom: 1px solid #e5e5e5; }
td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; }
tr:last-child td { border-bottom: none; }
.status-success { background: #d4edda; color: #155724; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.status-error   { background: #f8d7da; color: #721c24; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
#log-box { background: #1a1a1a; color: #d4d4d4; padding: 16px; border-radius: 8px; font-family: monospace; font-size: 12px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; }
.refresh-btn { font-size: 12px; color: #888; cursor: pointer; background: none; border: none; }
.refresh-btn:hover { color: #333; }
.config-row { display: flex; gap: 12px; flex-wrap: wrap; }
.info-badge { background: #f5f5f5; border-radius: 6px; padding: 8px 14px; font-size: 13px; }
.info-badge strong { display: block; font-size: 11px; color: #888; margin-bottom: 2px; }
"""

SETUP_CSS = """
.setup-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
.sched-card { border: 1px solid #e5e5e5; border-radius: 10px; padding: 20px; }
.sched-card-title { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.sched-card-sub { font-size: 12px; color: #888; margin-bottom: 16px; }
.sched-field { margin-bottom: 12px; }
.sched-field label { display: block; font-size: 12px; font-weight: 500; color: #555; margin-bottom: 5px; }
.hidden { display: none !important; }
.next-run { font-size: 12px; color: #555; margin-top: 12px; padding: 8px 12px; background: #f8f8f8; border-radius: 6px; }
.next-run strong { color: #1a1a1a; }
.actions-row { display: flex; gap: 8px; margin-top: 16px; align-items: center; }
"""


def page(body: str, active: str, lang: str = "pt") -> str:
    t = TRANSLATIONS[lang]
    da = 'class="active"' if active == "dashboard" else ""
    sa = 'class="active"' if active == "setup" else ""
    extra_css = DASHBOARD_CSS if active == "dashboard" else SETUP_CSS
    title = t["nav_dashboard"] if active == "dashboard" else t["nav_setup"]
    en_active = 'active' if lang == 'en' else ''
    pt_active = 'active' if lang == 'pt' else ''
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Shopify ETL — {title}</title>
  <style>{BASE_CSS}{extra_css}</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>Shopify ETL</h1>
    <span class="tag">Logistics San Diego</span>
  </div>
  <nav>
    <a href="/" {da}>{t["nav_dashboard"]}</a>
    <a href="/setup" {sa}>{t["nav_setup"]}</a>
    <div class="lang-switcher">
      <button class="lang-btn {pt_active}" onclick="setLang('pt')">PT</button>
      <button class="lang-btn {en_active}" onclick="setLang('en')">EN</button>
    </div>
  </nav>
</header>
{body}
<script>
function setLang(lang) {{
  document.cookie = 'lang=' + lang + ';path=/;max-age=31536000';
  location.reload();
}}
</script>
</body>
</html>"""


# -- Dashboard ----------------------------------------------------------------
def render_dashboard(stats, runs, lang: str = "pt"):
    t = TRANSLATIONS[lang]

    stats_html = "".join(f"""
    <div class="stat-card">
      <div class="label">{s['table']}</div>
      <div class="value">{s['count']:,}</div>
      <div class="sub">{t['last_update']}: {s['last_update']}</div>
    </div>""" for s in stats)

    def _run_row(r):
        sc = r['status']
        err_full = _escape_html(r.get("error") or "")
        err_short = (r.get("error") or "")[:80]
        if len(r.get("error") or "") > 80:
            err_short += "…"
        err_short = _escape_html(err_short)
        cancel_btn = (
            f'<button class="btn btn-outline btn-sm" style="color:#c00;border-color:#f8d7da;" '
            f'onclick="cancelRun({r["pid"]})">&#9632;</button>'
            if r.get("pid") and r["finished_at"] == "—" else ""
        )
        ins_upd = f'{r["inserts"]:,} / {r["updates"]:,}' if (r.get("inserts") or r.get("updates")) else "—"
        return f"""
    <tr>
      <td>{_escape_html(r['script'])}</td><td>{_escape_html(r['start'])}</td><td>{_escape_html(r['end'])}</td>
      <td><span class="status-{'success' if sc=='success' else 'error'}">{_escape_html(sc)}</span></td>
      <td style="font-size:12px;">{int(r['records']):,}</td>
      <td style="font-size:12px;color:#888;">{ins_upd}</td>
      <td style="font-size:12px;color:#888;">{_escape_html(r['duration'])}</td>
      <td style="font-size:12px;">{_escape_html(r['finished_at'])}</td>
      <td style="color:#aaa;font-size:11px;cursor:pointer;max-width:200px;"
          title="{_escape_html(t['modal_error_title'])}" onclick="showError(this)"
          data-full="{err_full}">{err_short}</td>
      <td>{cancel_btn}</td>
    </tr>"""

    runs_html = "".join(_run_row(r) for r in runs)

    env = read_env()
    shopify = env.get("SHOPIFY_STORE_URL", "—")
    db_host = env.get("SQL_SERVER_HOST", "—")
    db_name = env.get("SQL_SERVER_DATABASE", "—")

    sched_status = "—"
    next_runs_html = ""
    sched_btn = ""
    if HAS_SCHEDULER:
        from apscheduler.schedulers.base import STATE_PAUSED
        paused = _scheduler.state == STATE_PAUSED
        sched_status = (
            f'<span class="badge badge-red">{t["sched_paused"]}</span>' if paused
            else f'<span class="badge badge-green">{t["sched_active"]}</span>'
        )
        pause_label = t["btn_resume"] if paused else t["btn_pause"]
        pause_action = "resumeScheduler" if paused else "pauseScheduler"
        sched_btn = f'<button class="btn btn-outline btn-sm" onclick="{pause_action}()">{pause_label}</button>'
        jobs_html = ""
        for job in _scheduler.get_jobs():
            nrt = str(job.next_run_time)[:19] if job.next_run_time else "—"
            jobs_html += f'<div class="info-badge"><strong>{job.id}</strong>{nrt}</div>'
        next_runs_html = f'<div class="config-row" style="margin-top:12px;">{jobs_html}</div>' if jobs_html else ""

    # JS messages for client-side use
    js_msgs = json.dumps({
        "confirm_cancel": t["confirm_cancel"],
        "type_order_id": t["type_order_id"],
        "loading": t["loading"],
        "last_update": t["last_update"],
        "modal_error_title": t["modal_error_title"],
    })

    body = f"""
<div class="container">
  <div class="card">
    <div class="card-header">
      <h2>{t['card_config']}</h2>
      <div style="display:flex;align-items:center;gap:10px;">{sched_status}{sched_btn}</div>
    </div>
    <div class="config-row">
      <div class="info-badge"><strong>{t['label_shopify_store']}</strong>{shopify}</div>
      <div class="info-badge"><strong>{t['label_sql_host']}</strong>{db_host}</div>
      <div class="info-badge"><strong>{t['label_database']}</strong>{db_name}</div>
    </div>
    {next_runs_html}
  </div>

  <div class="card">
    <div class="card-header">
      <h2>{t['card_tables']}</h2>
      <button class="refresh-btn" onclick="refreshStats()">&#8635; Refresh</button>
    </div>
    <div class="stats-grid" id="stats-grid">{stats_html}</div>
  </div>

  <div class="card">
    <div class="card-header"><h2>{t['card_run_etl']}</h2></div>
    <div class="etl-form">
      <div class="field">
        <label>{t['label_script']}</label>
        <select id="script">
          <option value="etl_orders">Orders</option>
          <option value="etl_fulfillments">Fulfillments + Events</option>
          <option value="etl_locations">Locations</option>
        </select>
      </div>
      <div class="field"><label>{t['label_start_date']}</label><input type="date" id="start-date"></div>
      <div class="field"><label>{t['label_end_date']}</label><input type="date" id="end-date"></div>
      <button class="btn btn-dark" onclick="runETL()">{t['btn_run']}</button>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px;align-items:flex-end;">
      <div class="field" style="max-width:260px;">
        <label style="font-size:12px;color:#555;">{t['label_run_by_order']}</label>
        <input type="text" id="order-id-input" placeholder="ex: 6995328762108">
      </div>
      <button class="btn btn-outline" onclick="runByOrderId()">{t['btn_run_order']}</button>
    </div>
    <div id="alert"></div>
  </div>

  <div class="card">
    <div class="card-header">
      <h2>{t['card_recent_runs']}</h2>
      <div style="display:flex;gap:8px;align-items:center;">
        <select id="filter-script" onchange="refreshRuns()" style="font-size:12px;padding:4px 8px;border:1px solid #ddd;border-radius:4px;">
          <option value="">{t['filter_all_scripts']}</option>
          <option value="etl_orders">Orders</option>
          <option value="etl_fulfillments">Fulfillments</option>
          <option value="etl_locations">Locations</option>
        </select>
        <select id="filter-status" onchange="refreshRuns()" style="font-size:12px;padding:4px 8px;border:1px solid #ddd;border-radius:4px;">
          <option value="">{t['filter_all_status']}</option>
          <option value="success">Success</option>
          <option value="error">Error</option>
        </select>
        <button class="refresh-btn" onclick="refreshRuns()">&#8635; Refresh</button>
      </div>
    </div>
    <table id="runs-table">
      <thead><tr>
        <th>{t['th_script']}</th><th>{t['th_start']}</th><th>{t['th_end']}</th>
        <th>{t['th_status']}</th><th>{t['th_records']}</th><th>{t['th_ins_upd']}</th>
        <th>{t['th_duration']}</th><th>{t['th_finished']}</th><th>{t['th_error']}</th><th></th>
      </tr></thead>
      <tbody>{runs_html}</tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-header">
      <h2>{t['card_volume']}</h2>
      <button class="refresh-btn" onclick="refreshChart()">&#8635; Refresh</button>
    </div>
    <canvas id="volume-chart" style="max-height:260px;"></canvas>
  </div>

  <div class="card">
    <div class="card-header">
      <h2>{t['card_log']}</h2>
      <button class="refresh-btn" onclick="refreshLogs()">&#8635; Refresh</button>
    </div>
    <div id="log-box">{t['loading']}</div>
  </div>
</div>

<!-- Error modal -->
<div id="error-modal" onclick="this.style.display='none'"
     style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:999;align-items:center;justify-content:center;">
  <div onclick="event.stopPropagation()"
       style="background:white;border-radius:10px;padding:24px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;position:relative;">
    <button onclick="document.getElementById('error-modal').style.display='none'"
            style="position:absolute;top:12px;right:12px;background:none;border:none;font-size:18px;cursor:pointer;">&#x2715;</button>
    <h3 style="margin-bottom:12px;font-size:14px;color:#666;">{t['modal_error_title']}</h3>
    <pre id="error-modal-text" style="white-space:pre-wrap;font-size:13px;color:#721c24;background:#f8d7da;padding:12px;border-radius:6px;"></pre>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
  const MSGS = {js_msgs};
  const today = new Date().toISOString().split('T')[0];
  const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];
  document.getElementById('start-date').value = yesterday;
  document.getElementById('end-date').value = today;

  let volumeChart = null;

  function escHtml(s) {{
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }}

  function showError(el) {{
    const full = el.getAttribute('data-full');
    if (!full) return;
    document.getElementById('error-modal-text').textContent = full;
    document.getElementById('error-modal').style.display = 'flex';
  }}

  async function runETL() {{
    const body = new FormData();
    body.append('script', document.getElementById('script').value);
    body.append('start_date', document.getElementById('start-date').value);
    body.append('end_date', document.getElementById('end-date').value);
    const res = await fetch('/run-etl', {{ method: 'POST', body }});
    const data = await res.json();
    const el = document.getElementById('alert');
    el.className = data.status === 'started' ? 'success' : 'error';
    el.textContent = data.message;
    setTimeout(() => {{ el.className = ''; el.textContent = ''; }}, 5000);
    setTimeout(refreshRuns, 3000);
  }}

  async function runByOrderId() {{
    const orderId = document.getElementById('order-id-input').value.trim();
    if (!orderId) {{ alert(MSGS.type_order_id); return; }}
    const body = new FormData();
    body.append('order_id', orderId);
    const res = await fetch('/run-order', {{ method: 'POST', body }});
    const data = await res.json();
    const el = document.getElementById('alert');
    el.className = data.status === 'started' ? 'success' : 'error';
    el.textContent = data.message;
    setTimeout(() => {{ el.className = ''; el.textContent = ''; }}, 5000);
    setTimeout(refreshRuns, 3000);
  }}

  async function cancelRun(pid) {{
    if (!confirm(MSGS.confirm_cancel)) return;
    const body = new FormData();
    body.append('pid', pid);
    const res = await fetch('/api/runs/cancel', {{ method: 'POST', body }});
    const data = await res.json();
    alert(data.message);
    refreshRuns();
  }}

  async function pauseScheduler() {{
    await fetch('/api/scheduler/pause', {{ method: 'POST' }});
    location.reload();
  }}

  async function resumeScheduler() {{
    await fetch('/api/scheduler/resume', {{ method: 'POST' }});
    location.reload();
  }}

  async function refreshStats() {{
    const res = await fetch('/api/stats');
    const data = await res.json();
    document.getElementById('stats-grid').innerHTML = data.map(s => `
      <div class="stat-card">
        <div class="label">${{s.table}}</div>
        <div class="value">${{Number(s.count).toLocaleString()}}</div>
        <div class="sub">${{MSGS.last_update}}: ${{s.last_update}}</div>
      </div>`).join('');
  }}

  async function refreshRuns() {{
    const script = document.getElementById('filter-script').value;
    const status = document.getElementById('filter-status').value;
    const params = new URLSearchParams();
    if (script) params.append('script', script);
    if (status) params.append('status', status);
    const res = await fetch('/api/runs?' + params);
    const runs = await res.json();
    document.querySelector('#runs-table tbody').innerHTML = runs.map(r => {{
      const errRaw = r.error || '';
      const errShort = errRaw.length > 80 ? errRaw.slice(0, 80) + '…' : errRaw;
      const insUpd = (r.inserts||r.updates) ? `${{r.inserts||0}}/${{r.updates||0}}` : '—';
      const cancelBtn = (r.pid && r.finished_at === '—')
        ? `<button class="btn btn-outline btn-sm" style="color:#c00;border-color:#f8d7da;" onclick="cancelRun(${{r.pid}})">&#9632;</button>` : '';
      return `<tr>
        <td>${{escHtml(r.script)}}</td><td>${{escHtml(r.start)}}</td><td>${{escHtml(r.end)}}</td>
        <td><span class="status-${{r.status==='success'?'success':'error'}}">${{escHtml(r.status)}}</span></td>
        <td style="font-size:12px;">${{Number(r.records||0).toLocaleString()}}</td>
        <td style="font-size:12px;color:#888;">${{insUpd}}</td>
        <td style="font-size:12px;color:#888;">${{escHtml(r.duration||'—')}}</td>
        <td style="font-size:12px;">${{escHtml(r.finished_at)}}</td>
        <td style="color:#aaa;font-size:11px;cursor:pointer;max-width:200px;"
            title="${{escHtml(MSGS.modal_error_title)}}" onclick="showError(this)"
            data-full="${{escHtml(errRaw)}}">${{escHtml(errShort)}}</td>
        <td>${{cancelBtn}}</td>
      </tr>`;
    }}).join('');
  }}

  async function refreshChart() {{
    try {{
      const res = await fetch('/api/stats/chart');
      const data = await res.json();
      if (!data.labels || !data.datasets) return;
      const ctx = document.getElementById('volume-chart').getContext('2d');
      if (volumeChart) volumeChart.destroy();
      volumeChart = new Chart(ctx, {{
        type: 'bar',
        data: data,
        options: {{
          responsive: true,
          plugins: {{ legend: {{ position: 'top' }} }},
          scales: {{ x: {{ stacked: false }}, y: {{ beginAtZero: true }} }}
        }}
      }});
    }} catch(e) {{}}
  }}

  async function refreshLogs() {{
    const res = await fetch('/api/logs');
    const data = await res.json();
    const box = document.getElementById('log-box');
    box.textContent = data.lines.join('');
    box.scrollTop = box.scrollHeight;
  }}

  refreshLogs();
  refreshChart();
  setInterval(refreshLogs, 10000);
</script>"""
    return page(body, "dashboard", lang)


# -- Setup page ---------------------------------------------------------------
def _sched_card(name: str, cfg: dict, next_run: str, t: dict) -> str:
    label = cfg.get("label", name)
    enabled = cfg.get("enabled", False)
    freq = cfg.get("frequency", "daily")
    time_val = cfg.get("time", "06:00")
    dow = cfg.get("day_of_week", "mon")
    every_n = cfg.get("every_n", 2)
    date_range = cfg.get("date_range", "yesterday_today")
    days_back = cfg.get("days_back", 1)

    status_badge = (
        f'<span class="badge badge-green">&#9679; {t["sched_active"]}</span>'
        if enabled else
        f'<span class="badge badge-gray">&#9675; {t["sched_inactive"]}</span>'
    )
    dow_hidden  = "" if freq == "weekly" else "hidden"
    n_hidden    = "" if freq in ("every_n_days", "every_n_hours", "every_n_minutes") else "hidden"
    n_label_map = {
        "every_n_minutes": t["sched_n_minutes"],
        "every_n_hours": t["sched_n_hours"],
        "every_n_days": t["sched_n_days"],
    }
    n_label = n_label_map.get(freq, t["sched_n_days"])
    days_hidden = "" if date_range == "last_n_days" else "hidden"
    next_html = (
        f'<div class="next-run">{t["sched_next_run"]}: <strong>{next_run}</strong></div>'
        if enabled and next_run != "—" else ""
    )

    dow_map = [
        ("mon", t["dow_mon"]), ("tue", t["dow_tue"]), ("wed", t["dow_wed"]),
        ("thu", t["dow_thu"]), ("fri", t["dow_fri"]), ("sat", t["dow_sat"]), ("sun", t["dow_sun"]),
    ]
    dow_options = "".join(
        f'<option value="{v}" {"selected" if v == dow else ""}>{lbl}</option>'
        for v, lbl in dow_map
    )

    return f"""
<div class="sched-card" id="card-{name}">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
    <div>
      <div class="sched-card-title">{label}</div>
      <div class="sched-card-sub" id="status-badge-{name}">{status_badge}</div>
    </div>
  </div>

  <div class="sched-field">
    <div class="toggle-wrap">
      <label class="toggle">
        <input type="checkbox" id="enabled-{name}" {"checked" if enabled else ""}
               onchange="onToggle('{name}')">
        <span class="slider"></span>
      </label>
      <span class="toggle-label">{t["sched_enable"]}</span>
    </div>
  </div>

  <hr class="sep">

  <div class="sched-field">
    <label>{t["sched_repeat"]}</label>
    <select id="freq-{name}" onchange="onFreqChange('{name}')">
      <option value="every_n_minutes"{"selected" if freq=='every_n_minutes' else ""}>{t["sched_every_n_min"]}</option>
      <option value="every_n_hours"  {"selected" if freq=='every_n_hours'   else ""}>{t["sched_every_n_hours"]}</option>
      <option value="daily"          {"selected" if freq=='daily'           else ""}>{t["sched_daily"]}</option>
      <option value="weekdays"       {"selected" if freq=='weekdays'        else ""}>{t["sched_weekdays"]}</option>
      <option value="weekly"         {"selected" if freq=='weekly'          else ""}>{t["sched_weekly"]}</option>
      <option value="every_n_days"   {"selected" if freq=='every_n_days'    else ""}>{t["sched_every_n_days"]}</option>
    </select>
  </div>

  <div class="sched-field {dow_hidden}" id="dow-wrap-{name}">
    <label>{t["sched_dow"]}</label>
    <select id="dow-{name}">{dow_options}</select>
  </div>

  <div class="sched-field {n_hidden}" id="n-wrap-{name}">
    <label id="n-label-{name}">{n_label}</label>
    <input type="number" id="every-n-{name}" value="{every_n}" min="1" max="9999"
           style="max-width:100px;">
  </div>

  <div class="sched-field">
    <label>{t["sched_time"]}</label>
    <input type="time" id="time-{name}" value="{time_val}">
  </div>

  <hr class="sep">

  <div class="sched-field">
    <label>{t["sched_data"]}</label>
    <select id="dr-{name}" onchange="onDrChange('{name}')">
      <option value="yesterday_today" {"selected" if date_range=='yesterday_today' else ""}>{t["sched_since_yesterday"]}</option>
      <option value="last_n_days"     {"selected" if date_range=='last_n_days'     else ""}>{t["sched_last_n_days"]}</option>
    </select>
  </div>

  <div class="sched-field {days_hidden}" id="days-wrap-{name}">
    <label>{t["sched_days_back"]}</label>
    <input type="number" id="days-{name}" value="{days_back}" min="1" max="365"
           style="max-width:100px;">
  </div>

  {next_html}

  <div class="actions-row">
    <button class="btn btn-dark btn-sm" onclick="saveSched('{name}')">{t["btn_save"]}</button>
    <button class="btn btn-outline btn-sm" onclick="runNow('{name}')">{t["btn_run_now"]}</button>
    <span id="sched-result-{name}" style="font-size:12px;color:#555;"></span>
  </div>
</div>"""


def render_setup(oauth_success: bool = False, lang: str = "pt") -> str:
    t = TRANSLATIONS[lang]
    env = read_env()
    schedules = load_schedules()

    next_runs: dict = {}
    if HAS_SCHEDULER:
        for name in schedules:
            job = _scheduler.get_job(name)
            next_runs[name] = str(job.next_run_time)[:19] if (job and job.next_run_time) else "—"
    else:
        next_runs = {k: "—" for k in schedules}

    shopify_url = _escape_html(env.get("SHOPIFY_STORE_URL", ""))
    has_shopify_token = bool(env.get("SHOPIFY_ACCESS_TOKEN"))
    has_client_secret = bool(env.get("SHOPIFY_CLIENT_SECRET"))
    has_sql_password = bool(env.get("SQL_SERVER_PASSWORD"))
    shopify_version = _escape_html(env.get("SHOPIFY_API_VERSION", "2024-01"))
    client_id = _escape_html(env.get("SHOPIFY_CLIENT_ID", ""))
    sql_host = _escape_html(env.get("SQL_SERVER_HOST", ""))
    sql_port = _escape_html(env.get("SQL_SERVER_PORT", "1433"))
    sql_db = _escape_html(env.get("SQL_SERVER_DATABASE", ""))
    sql_user = _escape_html(env.get("SQL_SERVER_USER", ""))
    token_ph = "shpat_..." if not has_shopify_token else "••••••••"
    secret_ph = "shpss_..." if not has_client_secret else "••••••••"
    sql_pass_ph = "••••••••"
    lbl_access_token = _label_with_configured(
        t["label_access_token"], has_shopify_token, t["token_configured"],
    )
    lbl_client_secret = _label_with_configured(
        t["label_client_secret"], has_client_secret, t["secret_configured"],
    )
    lbl_sql_password = _label_with_configured(
        t["label_password"], has_sql_password, t["password_configured"],
    )

    sched_cards = "".join(
        _sched_card(name, cfg, next_runs.get(name, "—"), t)
        for name, cfg in schedules.items()
    )

    oauth_banner = f"""
  <div class="card" style="background:#d4edda;border-color:#c3e6cb;">
    <p style="color:#155724;font-size:14px;font-weight:600;">{t["oauth_success_msg"]}</p>
  </div>""" if oauth_success else ""

    sched_badge_text = t["apscheduler_active"] if HAS_SCHEDULER else t["apscheduler_missing"]

    js_msgs = json.dumps({
        "saving": t["saving"], "saved": t["saved"],
        "starting": t["starting"], "started": t["started"],
        "testing": t["testing"],
        "oauth_required": t["oauth_required"],
        "no_changes": t["no_changes"],
    })

    body = f"""
<div class="container">
  {oauth_banner}

  <!-- OAuth -->
  <div class="card">
    <div class="card-header">
      <h2>{t["card_oauth"]}</h2>
      <span class="badge badge-blue">{t["badge_recommended"]}</span>
    </div>
    <p style="font-size:13px;color:#666;margin-bottom:16px;">{t["oauth_desc"]}</p>
    <div class="row">
      <div class="field" style="flex:2;min-width:260px;">
        <label>{t["label_store_url"]}</label>
        <input type="text" id="oauth-store-url" value="{shopify_url}" placeholder="https://rlm-vix.myshopify.com">
      </div>
    </div>
    <div class="row" style="margin-top:12px;">
      <div class="field" style="flex:2;min-width:260px;">
        <label>{t["label_client_id"]}</label>
        <input type="text" id="oauth-client-id" value="{client_id}" placeholder="e7fe782d...">
      </div>
      <div class="field" style="flex:2;min-width:260px;">
        <label>{lbl_client_secret}</label>
        <input type="password" id="oauth-client-secret" value="" placeholder="{secret_ph}" autocomplete="new-password">
      </div>
    </div>
    <div class="row" style="margin-top:14px;">
      <button class="btn btn-dark" onclick="authorizeOAuth()">{t["btn_oauth_authorize"]}</button>
    </div>
    <div id="oauth-result" class="result"></div>
  </div>

  <!-- Shopify API -->
  <div class="card">
    <div class="card-header">
      <h2>{t["card_shopify_api"]}</h2>
      <span id="shopify-conn-badge" class="badge badge-gray">—</span>
    </div>
    <div class="row">
      <div class="field" style="flex:2;min-width:260px;">
        <label>{t["label_store_url"]}</label>
        <input type="text" id="shopify-url" value="{shopify_url}" placeholder="https://mystore.myshopify.com">
      </div>
      <div class="field" style="flex:2;min-width:260px;">
        <label>{lbl_access_token}</label>
        <input type="password" id="shopify-token" value="" placeholder="{token_ph}" autocomplete="new-password">
      </div>
      <div class="field" style="max-width:140px;">
        <label>{t["label_api_version"]}</label>
        <input type="text" id="shopify-version" value="{shopify_version}" placeholder="2024-01">
      </div>
    </div>
    <div class="row" style="margin-top:14px;">
      <button class="btn btn-outline" onclick="testShopify()">{t["btn_test"]}</button>
      <button class="btn btn-dark" onclick="saveShopify()">{t["btn_save"]}</button>
    </div>
    <div id="shopify-result" class="result"></div>
  </div>

  <!-- SQL Server -->
  <div class="card">
    <div class="card-header">
      <h2>{t["card_sql"]}</h2>
      <span id="sql-conn-badge" class="badge badge-gray">—</span>
    </div>
    <div class="row">
      <div class="field" style="flex:2;min-width:200px;">
        <label>{t["label_host"]}</label>
        <input type="text" id="sql-host" value="{sql_host}" placeholder="localhost\\SQLEXPRESS">
      </div>
      <div class="field" style="max-width:100px;">
        <label>{t["label_port"]}</label>
        <input type="text" id="sql-port" value="{sql_port}" placeholder="1433">
      </div>
      <div class="field" style="flex:2;min-width:160px;">
        <label>{t["label_database"]}</label>
        <input type="text" id="sql-db" value="{sql_db}" placeholder="DW_BASE">
      </div>
    </div>
    <div class="row" style="margin-top:12px;">
      <div class="field" style="flex:2;min-width:180px;">
        <label>{t["label_username"]}</label>
        <input type="text" id="sql-user" value="{sql_user}" placeholder="sa">
      </div>
      <div class="field" style="flex:2;min-width:180px;">
        <label>{lbl_sql_password}</label>
        <input type="password" id="sql-pass" value="" placeholder="{sql_pass_ph}" autocomplete="new-password">
      </div>
    </div>
    <div class="row" style="margin-top:14px;">
      <button class="btn btn-outline" onclick="testSQL()">{t["btn_test"]}</button>
      <button class="btn btn-dark" onclick="saveSQL()">{t["btn_save"]}</button>
    </div>
    <div id="sql-result" class="result"></div>
  </div>

  <!-- Config Changes -->
  <div class="card">
    <div class="card-header">
      <h2>{t["card_config_changes"]}</h2>
      <button class="refresh-btn" onclick="loadConfigChanges()">&#8635; Refresh</button>
    </div>
    <div id="config-changes-list" style="font-size:13px;color:#555;">{t["loading"]}</div>
  </div>

  <!-- Scheduling -->
  <div class="card">
    <div class="card-header">
      <h2>{t["card_scheduling"]}</h2>
      <span class="badge badge-blue">{sched_badge_text}</span>
    </div>
    <p style="font-size:13px;color:#666;margin-bottom:20px;">{t["sched_desc"]}</p>
    <div class="setup-grid">{sched_cards}</div>
  </div>

</div>
<script>
const MSGS = {js_msgs};

async function loadConfigChanges() {{
  const res = await fetch('/api/config-changes');
  const data = await res.json();
  const el = document.getElementById('config-changes-list');
  if (!data.length) {{ el.textContent = MSGS.no_changes; return; }}
  el.innerHTML = data.map(c => `
    <div style="padding:8px 0;border-bottom:1px solid #f0f0f0;display:flex;gap:12px;align-items:baseline;">
      <span style="color:#aaa;font-size:11px;white-space:nowrap;">${{c.timestamp?.slice(0,19)||'—'}}</span>
      <span>${{(c.keys||[]).join(', ')}}</span>
    </div>`).join('');
}}
loadConfigChanges();

async function authorizeOAuth() {{
  const storeUrl     = document.getElementById('oauth-store-url').value.trim();
  const clientId     = document.getElementById('oauth-client-id').value.trim();
  const clientSecret = document.getElementById('oauth-client-secret').value.trim();
  const r = document.getElementById('oauth-result');
  if (!storeUrl || !clientId || !clientSecret) {{
    r.className = 'result error'; r.textContent = MSGS.oauth_required; return;
  }}
  const body = new FormData();
  body.append('store_url', storeUrl);
  body.append('client_id', clientId);
  body.append('client_secret', clientSecret);
  const res = await fetch('/api/setup/oauth-credentials', {{ method: 'POST', body }});
  const data = await res.json();
  if (data.status !== 'ok') {{
    r.className = 'result error'; r.textContent = data.message; return;
  }}
  window.location.href = '/shopify/install';
}}

async function testShopify() {{
  const badge = document.getElementById('shopify-conn-badge');
  badge.className = 'badge badge-gray'; badge.textContent = MSGS.testing;
  const r = document.getElementById('shopify-result');
  r.className = 'result';
  const body = new FormData();
  body.append('url', document.getElementById('shopify-url').value);
  body.append('token', document.getElementById('shopify-token').value);
  body.append('version', document.getElementById('shopify-version').value);
  const res = await fetch('/api/setup/test-shopify', {{ method: 'POST', body }});
  const data = await res.json();
  r.className = 'result ' + (data.status === 'ok' ? 'ok' : 'error');
  r.textContent = data.message;
  badge.className = data.status === 'ok' ? 'badge badge-green' : 'badge badge-red';
  badge.textContent = data.status === 'ok' ? '&#10003; OK' : '&#10005; Failed';
}}

async function saveShopify() {{
  const body = new FormData();
  body.append('shopify_store_url', document.getElementById('shopify-url').value);
  body.append('shopify_access_token', document.getElementById('shopify-token').value);
  body.append('shopify_api_version', document.getElementById('shopify-version').value);
  const res = await fetch('/api/setup/shopify', {{ method: 'POST', body }});
  const data = await res.json();
  const r = document.getElementById('shopify-result');
  r.className = 'result ' + (data.status === 'ok' ? 'ok' : 'error');
  r.textContent = data.message;
  if (data.status === 'ok') setTimeout(testShopify, 500);
  setTimeout(() => r.className = 'result', 5000);
}}

async function testSQL() {{
  const badge = document.getElementById('sql-conn-badge');
  badge.className = 'badge badge-gray'; badge.textContent = MSGS.testing;
  const r = document.getElementById('sql-result');
  r.className = 'result';
  const body = new FormData();
  body.append('host', document.getElementById('sql-host').value);
  body.append('port', document.getElementById('sql-port').value);
  body.append('database', document.getElementById('sql-db').value);
  body.append('user', document.getElementById('sql-user').value);
  body.append('password', document.getElementById('sql-pass').value);
  const res = await fetch('/api/setup/test-sql', {{ method: 'POST', body }});
  const data = await res.json();
  r.className = 'result ' + (data.status === 'ok' ? 'ok' : 'error');
  r.textContent = data.message;
  badge.className = data.status === 'ok' ? 'badge badge-green' : 'badge badge-red';
  badge.textContent = data.status === 'ok' ? '&#10003; OK' : '&#10005; Failed';
}}

async function saveSQL() {{
  const body = new FormData();
  body.append('sql_server_host', document.getElementById('sql-host').value);
  body.append('sql_server_port', document.getElementById('sql-port').value);
  body.append('sql_server_database', document.getElementById('sql-db').value);
  body.append('sql_server_user', document.getElementById('sql-user').value);
  body.append('sql_server_password', document.getElementById('sql-pass').value);
  const res = await fetch('/api/setup/sql', {{ method: 'POST', body }});
  const data = await res.json();
  const r = document.getElementById('sql-result');
  r.className = 'result ' + (data.status === 'ok' ? 'ok' : 'error');
  r.textContent = data.message;
  if (data.status === 'ok') setTimeout(testSQL, 500);
  setTimeout(() => r.className = 'result', 5000);
}}

function onFreqChange(name) {{
  const freq = document.getElementById('freq-' + name).value;
  const nFreqs = ['every_n_minutes', 'every_n_hours', 'every_n_days'];
  const labels = {{
    every_n_minutes: {json.dumps(TRANSLATIONS["pt"]["sched_n_minutes"])!r},
    every_n_hours:   {json.dumps(TRANSLATIONS["pt"]["sched_n_hours"])!r},
    every_n_days:    {json.dumps(TRANSLATIONS["pt"]["sched_n_days"])!r},
  }};
  document.getElementById('dow-wrap-' + name).classList.toggle('hidden', freq !== 'weekly');
  document.getElementById('n-wrap-'   + name).classList.toggle('hidden', !nFreqs.includes(freq));
  if (labels[freq]) document.getElementById('n-label-' + name).textContent = labels[freq];
}}

function onDrChange(name) {{
  const dr = document.getElementById('dr-' + name).value;
  document.getElementById('days-wrap-' + name).classList.toggle('hidden', dr !== 'last_n_days');
}}

function onToggle(name) {{
  const enabled = document.getElementById('enabled-' + name).checked;
  const badge = document.getElementById('status-badge-' + name);
  badge.innerHTML = enabled
    ? '<span class="badge badge-green">&#9679; {t["sched_active"]}</span>'
    : '<span class="badge badge-gray">&#9675; {t["sched_inactive"]}</span>';
}}

async function saveSched(name) {{
  const result = document.getElementById('sched-result-' + name);
  result.textContent = MSGS.saving;
  const payload = {{}};
  payload[name] = {{
    enabled:     document.getElementById('enabled-'  + name).checked,
    frequency:   document.getElementById('freq-'     + name).value,
    time:        document.getElementById('time-'     + name).value,
    day_of_week: document.getElementById('dow-'      + name).value,
    every_n:     parseInt(document.getElementById('every-n-' + name).value) || 2,
    date_range:  document.getElementById('dr-'       + name).value,
    days_back:   parseInt(document.getElementById('days-'    + name).value) || 1,
  }};
  const res = await fetch('/api/schedules', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(payload),
  }});
  const data = await res.json();
  result.innerHTML = data.status === 'ok' ? MSGS.saved : '&#10005; ' + data.message;
  setTimeout(() => result.textContent = '', 3000);
  if (data.next_run && data.next_run[name]) {{
    const nr = document.querySelector('#card-' + name + ' .next-run');
    if (nr) nr.innerHTML = '{t["sched_next_run"]}: <strong>' + data.next_run[name] + '</strong>';
  }}
}}

async function runNow(name) {{
  const result = document.getElementById('sched-result-' + name);
  result.textContent = MSGS.starting;
  const res = await fetch('/run-etl', {{
    method: 'POST',
    body: (() => {{
      const f = new FormData();
      const today = new Date().toISOString().split('T')[0];
      const yest  = new Date(Date.now() - 86400000).toISOString().split('T')[0];
      f.append('script', name); f.append('start_date', yest); f.append('end_date', today);
      return f;
    }})(),
  }});
  const data = await res.json();
  result.innerHTML = data.status === 'started' ? MSGS.started : '&#10005; ' + data.message;
  setTimeout(() => result.textContent = '', 4000);
}}
</script>"""
    return page(body, "setup", lang)


# -- Routes -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    lang = get_lang(request)
    return HTMLResponse(render_dashboard(get_db_stats(), get_recent_runs(), lang))


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, oauth: str = ""):
    lang = get_lang(request)
    return HTMLResponse(render_setup(oauth_success=(oauth == "success"), lang=lang))


@app.post("/api/setup/oauth-credentials")
async def save_oauth_credentials(
    store_url: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(""),
):
    try:
        safe_url = _validate_shopify_store_url(store_url)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)})
    updates = {
        "SHOPIFY_STORE_URL": safe_url,
        "SHOPIFY_CLIENT_ID": client_id.strip(),
    }
    if client_secret.strip():
        updates["SHOPIFY_CLIENT_SECRET"] = client_secret.strip()
    write_env(updates)
    return JSONResponse({"status": "ok", "message": "OK"})


@app.get("/shopify/install")
async def shopify_install(request: Request):
    env = read_env()
    client_id = env.get("SHOPIFY_CLIENT_ID", "").strip()
    shop = env.get("SHOPIFY_STORE_URL", "").strip().replace("https://", "").rstrip("/")
    if not client_id or not shop:
        return JSONResponse({"status": "error", "message": "SHOPIFY_CLIENT_ID and SHOPIFY_STORE_URL required."}, status_code=400)

    state = secrets.token_hex(16)
    _oauth_states[state] = time.time()

    scopes = "read_orders,read_fulfillments,read_inventory,read_locations,read_products"
    redirect_uri = str(request.base_url).rstrip("/") + "/shopify/callback"
    auth_url = (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
        f"&grant_options[]=offline"
    )
    return RedirectResponse(auth_url)


@app.get("/shopify/callback")
async def shopify_callback(request: Request, code: str = "", hmac: str = "", shop: str = "", state: str = ""):
    now = time.time()
    for k in [k for k, t in _oauth_states.items() if now - t > 600]:
        del _oauth_states[k]

    if state not in _oauth_states:
        return JSONResponse({"status": "error", "message": "Invalid or expired state."}, status_code=400)
    del _oauth_states[state]

    client_secret = read_env().get("SHOPIFY_CLIENT_SECRET", "").strip()
    params = {k: v for k, v in request.query_params.items() if k != "hmac"}
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    digest = hmac_lib.new(client_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac_lib.compare_digest(digest, hmac):
        return JSONResponse({"status": "error", "message": "HMAC verification failed."}, status_code=400)

    import requests as req
    r = req.post(
        f"https://{shop}/admin/oauth/access_token",
        json={
            "client_id": read_env().get("SHOPIFY_CLIENT_ID", ""),
            "client_secret": client_secret,
            "code": code,
        },
        timeout=10,
    )
    if r.status_code != 200:
        return JSONResponse({"status": "error", "message": f"Token exchange failed: HTTP {r.status_code}"}, status_code=400)

    access_token = r.json().get("access_token", "")
    if not access_token:
        return JSONResponse({"status": "error", "message": "No access_token in response."}, status_code=400)

    configured = _normalize_shop_domain(read_env().get("SHOPIFY_STORE_URL", ""))
    callback_shop = _normalize_shop_domain(shop)
    if configured and configured != callback_shop:
        return JSONResponse(
            {"status": "error", "message": "Loja do callback não confere com SHOPIFY_STORE_URL configurada."},
            status_code=400,
        )

    write_env({
        "SHOPIFY_ACCESS_TOKEN": access_token,
        "SHOPIFY_STORE_URL": f"https://{callback_shop}",
    })
    return RedirectResponse("/setup?oauth=success")


@app.post("/run-etl")
async def run_etl(script: str = Form(...), start_date: str = Form(...), end_date: str = Form(...)):
    try:
        script_path = _resolve_script(script.strip())
        proc = _spawn_etl(
            [sys.executable, str(script_path), "--start-date", start_date, "--end-date", end_date],
        )
        return JSONResponse({"status": "started", "message": f"{script} → {start_date} / {end_date} (PID {proc.pid})"})
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)})
    except RuntimeError as e:
        return JSONResponse({"status": "error", "message": str(e)})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@app.post("/run-order")
async def run_order(order_id: str = Form(...)):
    order_id = order_id.strip()
    if not order_id or not order_id.isdigit():
        return JSONResponse({"status": "error", "message": "Order ID inválido (apenas dígitos)."})
    try:
        script_path = _resolve_script("etl_orders")
        proc = _spawn_etl(
            [sys.executable, str(script_path), "--order-id", order_id],
        )
        return JSONResponse({"status": "started", "message": f"Order {order_id} (PID {proc.pid})"})
    except RuntimeError as e:
        return JSONResponse({"status": "error", "message": str(e)})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@app.post("/api/runs/cancel")
async def cancel_run(pid: int = Form(...)):
    proc = _running_procs.get(pid)
    if not proc:
        _reap_running_procs()
        return JSONResponse(
            {"status": "error", "message": f"PID {pid} não pertence a um ETL ativo deste painel."},
            status_code=404,
        )
    try:
        proc.kill()
        _running_procs.pop(pid, None)
        return JSONResponse({"status": "ok", "message": f"PID {pid} cancelled."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@app.post("/api/scheduler/pause")
async def pause_scheduler():
    if HAS_SCHEDULER and _scheduler.running:
        _scheduler.pause()
    return JSONResponse({"status": "ok"})


@app.post("/api/scheduler/resume")
async def resume_scheduler():
    if HAS_SCHEDULER and _scheduler.running:
        _scheduler.resume()
    return JSONResponse({"status": "ok"})


# -- Setup API ----------------------------------------------------------------
@app.post("/api/setup/shopify")
async def save_shopify(
    shopify_store_url: str = Form(...),
    shopify_access_token: str = Form(""),
    shopify_api_version: str = Form(...),
):
    try:
        store_url = _validate_shopify_store_url(shopify_store_url)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)})
    updates = {
        "SHOPIFY_STORE_URL": store_url,
        "SHOPIFY_API_VERSION": shopify_api_version.strip(),
    }
    if shopify_access_token.strip():
        updates["SHOPIFY_ACCESS_TOKEN"] = shopify_access_token.strip()
    write_env(updates)
    log_config_change(list(updates.keys()))
    return JSONResponse({"status": "ok", "message": "Shopify config saved."})


@app.post("/api/setup/sql")
async def save_sql(
    sql_server_host: str = Form(...),
    sql_server_port: str = Form(...),
    sql_server_database: str = Form(...),
    sql_server_user: str = Form(...),
    sql_server_password: str = Form(...),
):
    updates = {
        "SQL_SERVER_HOST": sql_server_host,
        "SQL_SERVER_PORT": sql_server_port,
        "SQL_SERVER_DATABASE": sql_server_database,
        "SQL_SERVER_USER": sql_server_user,
    }
    if sql_server_password:
        updates["SQL_SERVER_PASSWORD"] = sql_server_password
    write_env(updates)
    log_config_change(list(updates.keys()))
    return JSONResponse({"status": "ok", "message": "SQL Server config saved."})


@app.post("/api/setup/test-shopify")
async def test_shopify(url: str = Form(""), token: str = Form(""), version: str = Form("2024-01")):
    url = url.strip()
    token = token.strip()
    if not url:
        return JSONResponse({"status": "error", "message": "URL is required."})
    if not token:
        token = read_env().get("SHOPIFY_ACCESS_TOKEN", "").strip()
    if not token:
        return JSONResponse({"status": "error", "message": "Token is required (ou já configurado no .env)."})
    try:
        import requests as req
        safe_url = _validate_shopify_store_url(url)
        endpoint = f"{safe_url.rstrip('/')}/admin/api/{version.strip()}/shop.json"
        r = req.get(endpoint, headers={"X-Shopify-Access-Token": token}, timeout=10)
        if r.status_code == 200:
            shop_name = r.json().get("shop", {}).get("name", url)
            return JSONResponse({"status": "ok", "message": f"Connected to '{shop_name}'."})
        return JSONResponse({"status": "error", "message": f"HTTP {r.status_code} — check URL and token."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)[:120]})


@app.post("/api/setup/test-sql")
async def test_sql(
    host: str = Form(""),
    port: str = Form("1433"),
    database: str = Form(""),
    user: str = Form(""),
    password: str = Form(""),
):
    if not host or not database:
        return JSONResponse({"status": "error", "message": "Host and database are required."})
    for field_name, val in (("host", host), ("database", database), ("user", user), ("password", password)):
        if ";" in str(val) or "{" in str(val) or "}" in str(val):
            return JSONResponse(
                {"status": "error", "message": f"Caractere inválido em {field_name}."},
            )
    if not password:
        password = read_env().get("SQL_SERVER_PASSWORD", "")
    try:
        import pyodbc
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={host},{port};DATABASE={database};"
            f"UID={user};PWD={password};TrustServerCertificate=yes;"
        )
        conn = pyodbc.connect(conn_str, timeout=8)
        cursor = conn.cursor()
        cursor.execute("SELECT @@VERSION")
        version_str = (cursor.fetchone()[0] or "")[:80]
        conn.close()
        return JSONResponse({"status": "ok", "message": f"Connected. {version_str}"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)[:150]})


# -- Schedules API ------------------------------------------------------------
@app.get("/api/schedules")
async def get_schedules_api():
    schedules = load_schedules()
    result = {}
    for name, cfg in schedules.items():
        item = dict(cfg)
        if HAS_SCHEDULER:
            job = _scheduler.get_job(name)
            item["next_run"] = str(job.next_run_time)[:19] if (job and job.next_run_time) else "—"
        else:
            item["next_run"] = "—"
        result[name] = item
    return JSONResponse(result)


@app.post("/api/schedules")
async def save_schedules_api(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON body."}, status_code=400)

    schedules = load_schedules()
    for script_name, cfg in data.items():
        if script_name not in schedules or script_name not in ALLOWED_ETL_SCRIPTS:
            continue
        if "every_n" in cfg:
            cfg["every_n"] = max(1, min(int(cfg["every_n"]), 9999))
        if "days_back" in cfg:
            cfg["days_back"] = max(1, min(int(cfg["days_back"]), 365))
        schedules[script_name].update(cfg)

    save_schedules_file(schedules)
    apply_schedules(schedules)

    next_run = {}
    if HAS_SCHEDULER:
        for name in data:
            job = _scheduler.get_job(name)
            next_run[name] = str(job.next_run_time)[:19] if (job and job.next_run_time) else "—"

    return JSONResponse({"status": "ok", "message": "Schedules updated.", "next_run": next_run})


# -- Data API -----------------------------------------------------------------
@app.get("/api/stats")
async def api_stats():
    return get_db_stats()


@app.get("/api/runs")
async def api_runs(script: str = "", status: str = ""):
    return get_recent_runs(script_filter=script, status_filter=status)


@app.get("/api/stats/chart")
async def api_chart():
    try:
        from loaders.sqlserver_loader import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT CONVERT(VARCHAR(10), started_at, 120), script_name, SUM(records_processed)
            FROM etl_run_log
            WHERE status = 'success' AND started_at >= DATEADD(day, -30, GETDATE())
            GROUP BY CONVERT(VARCHAR(10), started_at, 120), script_name
            ORDER BY 1
        """)
        rows = cursor.fetchall()
        conn.close()
        dates = sorted({r[0] for r in rows})
        scripts = sorted({r[1] for r in rows if r[1]})
        color_map = {
            "etl_orders":       "rgba(26,26,26,0.8)",
            "etl_fulfillments": "rgba(100,149,237,0.8)",
            "etl_locations":    "rgba(60,179,113,0.8)",
        }
        datasets = []
        for sc in scripts:
            lookup = {r[0]: r[2] for r in rows if r[1] == sc}
            datasets.append({
                "label": sc,
                "data": [lookup.get(d, 0) for d in dates],
                "backgroundColor": color_map.get(sc, "rgba(150,150,150,0.7)"),
            })
        return JSONResponse({"labels": dates, "datasets": datasets})
    except Exception:
        return JSONResponse({"labels": [], "datasets": []})


@app.get("/api/config-changes")
async def api_config_changes():
    return JSONResponse(get_config_changes())


@app.get("/api/logs")
async def api_logs():
    log_dir = ROOT / "logs"
    log_file = log_dir / f"{datetime.today().strftime('%Y-%m-%d')}.log"
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines(keepends=True)
        return {"lines": lines[-100:]}
    except FileNotFoundError:
        return {"lines": ["No logs for today yet."]}
