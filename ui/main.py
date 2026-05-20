"""
Interface de controle do ETL — Shopify -> SQL Server
Dashboard: http://localhost:8000
Setup:     http://localhost:8000/setup
"""
import subprocess
import sys
import json
import hashlib
import hmac as hmac_lib
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

sys.path.append(str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"
SCHEDULES_FILE = ROOT / "config" / "schedules.json"
SCRIPTS_DIR = ROOT / "scripts"

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
        except Exception:
            pass
    return base


def save_schedules_file(schedules: dict):
    SCHEDULES_FILE.write_text(json.dumps(schedules, indent=2), encoding="utf-8")


def _date_range_for(cfg: dict) -> tuple:
    today = datetime.today()
    n = int(cfg.get("days_back", 1))
    if cfg.get("date_range") == "last_n_days":
        return (today - timedelta(days=n)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")
    return (today - timedelta(days=1)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _run_job(script_name: str, cfg: dict):
    start, end = _date_range_for(cfg)
    script_path = SCRIPTS_DIR / f"{script_name}.py"
    subprocess.Popen(
        [sys.executable, str(script_path), "--start-date", start, "--end-date", end],
        cwd=str(ROOT),
    )


def _make_trigger(cfg: dict):
    h, m = cfg.get("time", "06:00").split(":")
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
    _scheduler.remove_all_jobs()
    for name, cfg in schedules.items():
        if cfg.get("enabled"):
            _scheduler.add_job(
                _run_job, _make_trigger(cfg), args=[name, cfg],
                id=name, replace_existing=True
            )


@asynccontextmanager
async def lifespan(_):
    if HAS_SCHEDULER:
        apply_schedules(load_schedules())
        _scheduler.start()
    yield
    if HAS_SCHEDULER and _scheduler.running:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Shopify ETL Control Panel", lifespan=lifespan)

_oauth_states: dict = {}  # state -> timestamp

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
                    lines.append(f"{k}={updates[k]}")
                    written.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
    for k, v in updates.items():
        if k not in written:
            lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def get_recent_runs(limit=10):
    runs = []
    try:
        from loaders.sqlserver_loader import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT TOP {limit} script_name, start_date, end_date,
                   status, records_processed, error_message, finished_at
            FROM etl_run_log ORDER BY run_id DESC
        """)
        for row in cursor.fetchall():
            runs.append({
                "script": row[0] or "—", "start": str(row[1]), "end": str(row[2]),
                "status": row[3] or "—", "records": row[4] or 0,
                "error": row[5] or "", "finished_at": str(row[6])[:19] if row[6] else "—",
            })
        conn.close()
    except Exception as e:
        runs = [{"script": "—", "status": "error", "error": str(e)[:80],
                 "records": 0, "start": "—", "end": "—", "finished_at": "—"}]
    return runs


# -- HTML helpers -------------------------------------------------------------
BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #1a1a1a; }
header { background: #1a1a1a; color: white; padding: 14px 32px; display: flex; align-items: center; justify-content: space-between; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand h1 { font-size: 18px; font-weight: 500; }
.tag { font-size: 12px; background: #333; padding: 3px 8px; border-radius: 4px; color: #aaa; }
nav { display: flex; gap: 4px; }
nav a { color: #aaa; text-decoration: none; font-size: 14px; padding: 7px 16px; border-radius: 6px; transition: all .15s; }
nav a:hover { background: #333; color: white; }
nav a.active { background: #444; color: white; font-weight: 500; }
.container { max-width: 1100px; margin: 32px auto; padding: 0 24px; display: grid; gap: 24px; }
.card { background: white; border-radius: 10px; padding: 24px; border: 1px solid #e5e5e5; }
.card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.card-header h2 { font-size: 14px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: .5px; }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.field { flex: 1; min-width: 160px; }
.field label { display: block; font-size: 12px; font-weight: 500; color: #555; margin-bottom: 6px; }
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
/* Toggle switch */
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


def page(body: str, active: str) -> str:
    da = 'class="active"' if active == "dashboard" else ""
    sa = 'class="active"' if active == "setup" else ""
    extra_css = DASHBOARD_CSS if active == "dashboard" else SETUP_CSS
    title = "Control Panel" if active == "dashboard" else "Setup"
    return f"""<!DOCTYPE html>
<html lang="en">
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
    <a href="/" {da}>Dashboard</a>
    <a href="/setup" {sa}>Setup</a>
  </nav>
</header>
{body}
</body>
</html>"""


# -- Dashboard ----------------------------------------------------------------
def render_dashboard(stats, runs):
    stats_html = "".join(f"""
    <div class="stat-card">
      <div class="label">{s['table']}</div>
      <div class="value">{s['count']:,}</div>
      <div class="sub">Last update: {s['last_update']}</div>
    </div>""" for s in stats)

    runs_html = "".join(f"""
    <tr>
      <td>{r['script']}</td><td>{r['start']}</td><td>{r['end']}</td>
      <td><span class="status-{'success' if r['status']=='success' else 'error'}">{r['status']}</span></td>
      <td>{int(r['records']):,}</td><td>{r['finished_at']}</td>
      <td style="color:#aaa;font-size:11px;">{str(r['error'])[:80]}</td>
    </tr>""" for r in runs)

    env = read_env()
    shopify = env.get("SHOPIFY_STORE_URL", "Not configured")
    db_host = env.get("SQL_SERVER_HOST", "Not configured")
    db_name = env.get("SQL_SERVER_DATABASE", "Not configured")

    body = f"""
<div class="container">
  <div class="card">
    <div class="card-header"><h2>Configuration</h2></div>
    <div class="config-row">
      <div class="info-badge"><strong>Shopify Store</strong>{shopify}</div>
      <div class="info-badge"><strong>SQL Server Host</strong>{db_host}</div>
      <div class="info-badge"><strong>Database</strong>{db_name}</div>
    </div>
  </div>

  <div class="card">
    <div class="card-header">
      <h2>Tables</h2>
      <button class="refresh-btn" onclick="refreshStats()">&#8635; Refresh</button>
    </div>
    <div class="stats-grid" id="stats-grid">{stats_html}</div>
  </div>

  <div class="card">
    <div class="card-header"><h2>Run ETL</h2></div>
    <div class="etl-form">
      <div class="field">
        <label>Script</label>
        <select id="script">
          <option value="etl_orders">Orders</option>
          <option value="etl_fulfillments">Fulfillments + Events</option>
          <option value="etl_locations">Locations</option>
        </select>
      </div>
      <div class="field"><label>Start date</label><input type="date" id="start-date"></div>
      <div class="field"><label>End date</label><input type="date" id="end-date"></div>
      <button class="btn btn-dark" onclick="runETL()">&#9654; Run</button>
    </div>
    <div id="alert"></div>
  </div>

  <div class="card">
    <div class="card-header">
      <h2>Recent runs</h2>
      <button class="refresh-btn" onclick="refreshRuns()">&#8635; Refresh</button>
    </div>
    <table id="runs-table">
      <thead><tr><th>Script</th><th>Start</th><th>End</th><th>Status</th><th>Records</th><th>Finished at</th><th>Error</th></tr></thead>
      <tbody>{runs_html}</tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-header">
      <h2>Today's log</h2>
      <button class="refresh-btn" onclick="refreshLogs()">&#8635; Refresh</button>
    </div>
    <div id="log-box">Loading...</div>
  </div>
</div>
<script>
  const today = new Date().toISOString().split('T')[0];
  const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];
  document.getElementById('start-date').value = yesterday;
  document.getElementById('end-date').value = today;

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

  async function refreshStats() {{
    const res = await fetch('/api/stats');
    const data = await res.json();
    document.getElementById('stats-grid').innerHTML = data.map(s => `
      <div class="stat-card">
        <div class="label">${{s.table}}</div>
        <div class="value">${{Number(s.count).toLocaleString()}}</div>
        <div class="sub">Last update: ${{s.last_update}}</div>
      </div>`).join('');
  }}

  async function refreshRuns() {{
    const res = await fetch('/api/runs');
    const runs = await res.json();
    document.querySelector('#runs-table tbody').innerHTML = runs.map(r => `
      <tr>
        <td>${{r.script}}</td><td>${{r.start}}</td><td>${{r.end}}</td>
        <td><span class="status-${{r.status==='success'?'success':'error'}}">${{r.status}}</span></td>
        <td>${{Number(r.records||0).toLocaleString()}}</td><td>${{r.finished_at}}</td>
        <td style="color:#aaa;font-size:11px;">${{(r.error||'').slice(0,80)}}</td>
      </tr>`).join('');
  }}

  async function refreshLogs() {{
    const res = await fetch('/api/logs');
    const data = await res.json();
    const box = document.getElementById('log-box');
    box.textContent = data.lines.join('');
    box.scrollTop = box.scrollHeight;
  }}

  refreshLogs();
  setInterval(refreshLogs, 10000);
</script>"""
    return page(body, "dashboard")


# -- Setup page ---------------------------------------------------------------
def _sched_card(name: str, cfg: dict, next_run: str) -> str:
    label = cfg.get("label", name)
    enabled = cfg.get("enabled", False)
    freq = cfg.get("frequency", "daily")
    time_val = cfg.get("time", "06:00")
    dow = cfg.get("day_of_week", "mon")
    every_n = cfg.get("every_n", 2)
    date_range = cfg.get("date_range", "yesterday_today")
    days_back = cfg.get("days_back", 1)

    status_badge = (
        f'<span class="badge badge-green">&#9679; Active</span>'
        if enabled else
        f'<span class="badge badge-gray">&#9675; Inactive</span>'
    )
    dow_hidden  = "" if freq == "weekly" else "hidden"
    n_hidden    = "" if freq in ("every_n_days", "every_n_hours", "every_n_minutes") else "hidden"
    n_label     = {"every_n_minutes": "A cada quantos minutos?", "every_n_hours": "A cada quantas horas?", "every_n_days": "A cada quantos dias?"}.get(freq, "A cada quantos dias?")
    days_hidden = "" if date_range == "last_n_days" else "hidden"
    next_html = (
        f'<div class="next-run">Next run: <strong>{next_run}</strong></div>'
        if enabled and next_run != "—" else ""
    )

    dow_options = "".join(
        f'<option value="{v}" {"selected" if v == dow else ""}>{lbl}</option>'
        for v, lbl in [
            ("mon","Monday"),("tue","Tuesday"),("wed","Wednesday"),
            ("thu","Thursday"),("fri","Friday"),("sat","Saturday"),("sun","Sunday"),
        ]
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
      <span class="toggle-label">Enable schedule</span>
    </div>
  </div>

  <hr class="sep">

  <div class="sched-field">
    <label>Repeat</label>
    <select id="freq-{name}" onchange="onFreqChange('{name}')">
      <option value="every_n_minutes"{"selected" if freq=='every_n_minutes' else ""}>A cada N minutos</option>
      <option value="every_n_hours"  {"selected" if freq=='every_n_hours'   else ""}>A cada N horas</option>
      <option value="daily"          {"selected" if freq=='daily'           else ""}>Todo dia</option>
      <option value="weekdays"       {"selected" if freq=='weekdays'        else ""}>Dias úteis (Seg–Sex)</option>
      <option value="weekly"         {"selected" if freq=='weekly'          else ""}>Uma vez por semana</option>
      <option value="every_n_days"   {"selected" if freq=='every_n_days'    else ""}>A cada N dias</option>
    </select>
  </div>

  <div class="sched-field {dow_hidden}" id="dow-wrap-{name}">
    <label>Day of week</label>
    <select id="dow-{name}">{dow_options}</select>
  </div>

  <div class="sched-field {n_hidden}" id="n-wrap-{name}">
    <label id="n-label-{name}">{n_label}</label>
    <input type="number" id="every-n-{name}" value="{every_n}" min="1" max="9999"
           style="max-width:100px;">
  </div>

  <div class="sched-field">
    <label>At what time? (UTC)</label>
    <input type="time" id="time-{name}" value="{time_val}">
  </div>

  <hr class="sep">

  <div class="sched-field">
    <label>Data to pull</label>
    <select id="dr-{name}" onchange="onDrChange('{name}')">
      <option value="yesterday_today" {"selected" if date_range=='yesterday_today' else ""}>Since yesterday</option>
      <option value="last_n_days"     {"selected" if date_range=='last_n_days'     else ""}>Last N days</option>
    </select>
  </div>

  <div class="sched-field {days_hidden}" id="days-wrap-{name}">
    <label>How many days back?</label>
    <input type="number" id="days-{name}" value="{days_back}" min="1" max="365"
           style="max-width:100px;">
  </div>

  {next_html}

  <div class="actions-row">
    <button class="btn btn-dark btn-sm" onclick="saveSched('{name}')">Save</button>
    <button class="btn btn-outline btn-sm" onclick="runNow('{name}')">&#9654; Run now</button>
    <span id="sched-result-{name}" style="font-size:12px;color:#555;"></span>
  </div>
</div>"""


def render_setup(oauth_success: bool = False) -> str:
    env = read_env()
    schedules = load_schedules()

    next_runs: dict = {}
    if HAS_SCHEDULER:
        for name in schedules:
            job = _scheduler.get_job(name)
            next_runs[name] = str(job.next_run_time)[:19] if (job and job.next_run_time) else "—"
    else:
        next_runs = {k: "—" for k in schedules}

    shopify_url = env.get("SHOPIFY_STORE_URL", "")
    shopify_token = env.get("SHOPIFY_ACCESS_TOKEN", "")
    shopify_version = env.get("SHOPIFY_API_VERSION", "2024-01")
    client_id = env.get("SHOPIFY_CLIENT_ID", "")
    client_secret = env.get("SHOPIFY_CLIENT_SECRET", "")
    sql_host = env.get("SQL_SERVER_HOST", "")
    sql_port = env.get("SQL_SERVER_PORT", "1433")
    sql_db = env.get("SQL_SERVER_DATABASE", "")
    sql_user = env.get("SQL_SERVER_USER", "")

    sched_cards = "".join(
        _sched_card(name, cfg, next_runs.get(name, "—"))
        for name, cfg in schedules.items()
    )

    oauth_banner = """
  <div class="card" style="background:#d4edda;border-color:#c3e6cb;">
    <p style="color:#155724;font-size:14px;font-weight:600;">App autorizado com sucesso. Access token salvo no .env.</p>
  </div>""" if oauth_success else ""

    body = f"""
<div class="container">
  {oauth_banner}

  <!-- OAuth -->
  <div class="card">
    <div class="card-header">
      <h2>Autorizar App (OAuth)</h2>
      <span class="badge badge-blue">Recomendado</span>
    </div>
    <p style="font-size:13px;color:#666;margin-bottom:16px;">
      Autorize o <strong>Logistics_ETL</strong> na loja via OAuth. O access token é capturado e salvo automaticamente.
      Faça isso uma vez por loja.
    </p>
    <div class="row">
      <div class="field" style="flex:2;min-width:260px;">
        <label>Store URL</label>
        <input type="text" id="oauth-store-url" value="{shopify_url}" placeholder="https://rlm-vix.myshopify.com">
      </div>
    </div>
    <div class="row" style="margin-top:12px;">
      <div class="field" style="flex:2;min-width:260px;">
        <label>Client ID</label>
        <input type="text" id="oauth-client-id" value="{client_id}" placeholder="e7fe782d...">
      </div>
      <div class="field" style="flex:2;min-width:260px;">
        <label>Client Secret</label>
        <input type="password" id="oauth-client-secret" value="{client_secret}" placeholder="shpss_...">
      </div>
    </div>
    <div class="row" style="margin-top:14px;">
      <button class="btn btn-dark" onclick="authorizeOAuth()">Salvar e Autorizar no Shopify</button>
    </div>
    <div id="oauth-result" class="result"></div>
  </div>

  <!-- Shopify API -->
  <div class="card">
    <div class="card-header">
      <h2>Shopify API</h2>
      <span id="shopify-conn-badge" class="badge badge-gray">Not tested</span>
    </div>
    <div class="row">
      <div class="field" style="flex:2;min-width:260px;">
        <label>Store URL</label>
        <input type="text" id="shopify-url" value="{shopify_url}" placeholder="https://mystore.myshopify.com">
      </div>
      <div class="field" style="flex:2;min-width:260px;">
        <label>Access Token</label>
        <input type="password" id="shopify-token" value="{shopify_token}" placeholder="shpat_...">
      </div>
      <div class="field" style="max-width:140px;">
        <label>API Version</label>
        <input type="text" id="shopify-version" value="{shopify_version}" placeholder="2024-01">
      </div>
    </div>
    <div class="row" style="margin-top:14px;">
      <button class="btn btn-outline" onclick="testShopify()">Test Connection</button>
      <button class="btn btn-dark" onclick="saveShopify()">Save</button>
    </div>
    <div id="shopify-result" class="result"></div>
  </div>

  <!-- SQL Server -->
  <div class="card">
    <div class="card-header">
      <h2>SQL Server</h2>
      <span id="sql-conn-badge" class="badge badge-gray">Not tested</span>
    </div>
    <div class="row">
      <div class="field" style="flex:2;min-width:200px;">
        <label>Host</label>
        <input type="text" id="sql-host" value="{sql_host}" placeholder="localhost\\SQLEXPRESS">
      </div>
      <div class="field" style="max-width:100px;">
        <label>Port</label>
        <input type="text" id="sql-port" value="{sql_port}" placeholder="1433">
      </div>
      <div class="field" style="flex:2;min-width:160px;">
        <label>Database</label>
        <input type="text" id="sql-db" value="{sql_db}" placeholder="DW_BASE">
      </div>
    </div>
    <div class="row" style="margin-top:12px;">
      <div class="field" style="flex:2;min-width:180px;">
        <label>Username</label>
        <input type="text" id="sql-user" value="{sql_user}" placeholder="sa">
      </div>
      <div class="field" style="flex:2;min-width:180px;">
        <label>Password</label>
        <input type="password" id="sql-pass" value="" placeholder="••••••••">
      </div>
    </div>
    <div class="row" style="margin-top:14px;">
      <button class="btn btn-outline" onclick="testSQL()">Test Connection</button>
      <button class="btn btn-dark" onclick="saveSQL()">Save</button>
    </div>
    <div id="sql-result" class="result"></div>
  </div>

  <!-- Scheduling -->
  <div class="card">
    <div class="card-header">
      <h2>Scheduling</h2>
      <span class="badge badge-blue">{"APScheduler active" if HAS_SCHEDULER else "APScheduler not installed"}</span>
    </div>
    <p style="font-size:13px;color:#666;margin-bottom:20px;">
      Configure automated runs per category. Times are in UTC.
      Changes take effect immediately without restarting the server.
    </p>
    <div class="setup-grid">{sched_cards}</div>
  </div>

</div>
<script>
// ---- OAuth -----------------------------------------------------------------
async function authorizeOAuth() {{
  const storeUrl     = document.getElementById('oauth-store-url').value.trim();
  const clientId     = document.getElementById('oauth-client-id').value.trim();
  const clientSecret = document.getElementById('oauth-client-secret').value.trim();
  const r = document.getElementById('oauth-result');
  if (!storeUrl || !clientId || !clientSecret) {{
    r.className = 'result error'; r.textContent = 'Store URL, Client ID e Client Secret são obrigatórios.'; return;
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

// ---- Shopify ---------------------------------------------------------------
async function testShopify() {{
  const badge = document.getElementById('shopify-conn-badge');
  badge.className = 'badge badge-gray'; badge.textContent = 'Testing...';
  const r = document.getElementById('shopify-result');
  r.className = 'result';
  const res = await fetch('/api/setup/test-shopify?' + new URLSearchParams({{
    url: document.getElementById('shopify-url').value,
    token: document.getElementById('shopify-token').value,
    version: document.getElementById('shopify-version').value,
  }}));
  const data = await res.json();
  r.className = 'result ' + (data.status === 'ok' ? 'ok' : 'error');
  r.textContent = data.message;
  badge.className = data.status === 'ok' ? 'badge badge-green' : 'badge badge-red';
  badge.textContent = data.status === 'ok' ? 'Connected' : 'Failed';
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
  setTimeout(() => r.className = 'result', 3000);
}}

// ---- SQL -------------------------------------------------------------------
async function testSQL() {{
  const badge = document.getElementById('sql-conn-badge');
  badge.className = 'badge badge-gray'; badge.textContent = 'Testing...';
  const r = document.getElementById('sql-result');
  r.className = 'result';
  const res = await fetch('/api/setup/test-sql?' + new URLSearchParams({{
    host: document.getElementById('sql-host').value,
    port: document.getElementById('sql-port').value,
    database: document.getElementById('sql-db').value,
    user: document.getElementById('sql-user').value,
    password: document.getElementById('sql-pass').value,
  }}));
  const data = await res.json();
  r.className = 'result ' + (data.status === 'ok' ? 'ok' : 'error');
  r.textContent = data.message;
  badge.className = data.status === 'ok' ? 'badge badge-green' : 'badge badge-red';
  badge.textContent = data.status === 'ok' ? 'Connected' : 'Failed';
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
  setTimeout(() => r.className = 'result', 3000);
}}

// ---- Scheduling UI helpers -------------------------------------------------
function onFreqChange(name) {{
  const freq = document.getElementById('freq-' + name).value;
  const nFreqs = ['every_n_minutes', 'every_n_hours', 'every_n_days'];
  const labels = {{ every_n_minutes: 'A cada quantos minutos?', every_n_hours: 'A cada quantas horas?', every_n_days: 'A cada quantos dias?' }};
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
    ? '<span class="badge badge-green">&#9679; Active</span>'
    : '<span class="badge badge-gray">&#9675; Inactive</span>';
}}

async function saveSched(name) {{
  const result = document.getElementById('sched-result-' + name);
  result.textContent = 'Saving...';
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
  result.textContent = data.status === 'ok' ? '✓ Saved' : '✗ ' + data.message;
  setTimeout(() => result.textContent = '', 3000);
  if (data.next_run && data.next_run[name]) {{
    const nr = document.querySelector('#card-' + name + ' .next-run');
    if (nr) nr.innerHTML = 'Next run: <strong>' + data.next_run[name] + '</strong>';
  }}
}}

async function runNow(name) {{
  const result = document.getElementById('sched-result-' + name);
  result.textContent = 'Starting...';
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
  result.textContent = data.status === 'started' ? '✓ Started' : '✗ ' + data.message;
  setTimeout(() => result.textContent = '', 4000);
}}
</script>"""
    return page(body, "setup")


# -- Routes -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(render_dashboard(get_db_stats(), get_recent_runs()))


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(oauth: str = ""):
    return HTMLResponse(render_setup(oauth_success=(oauth == "success")))


@app.post("/api/setup/oauth-credentials")
async def save_oauth_credentials(store_url: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)):
    write_env({
        "SHOPIFY_STORE_URL": store_url.strip(),
        "SHOPIFY_CLIENT_ID": client_id.strip(),
        "SHOPIFY_CLIENT_SECRET": client_secret.strip(),
    })
    return JSONResponse({"status": "ok", "message": "Credenciais OAuth salvas."})


@app.get("/shopify/install")
async def shopify_install(request: Request):
    env = read_env()
    client_id = env.get("SHOPIFY_CLIENT_ID", "").strip()
    shop = env.get("SHOPIFY_STORE_URL", "").strip().replace("https://", "").rstrip("/")
    if not client_id or not shop:
        return JSONResponse({"status": "error", "message": "SHOPIFY_CLIENT_ID e SHOPIFY_STORE_URL são necessários."}, status_code=400)

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
        return JSONResponse({"status": "error", "message": "State inválido ou expirado."}, status_code=400)
    del _oauth_states[state]

    client_secret = read_env().get("SHOPIFY_CLIENT_SECRET", "").strip()
    params = {k: v for k, v in request.query_params.items() if k != "hmac"}
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    digest = hmac_lib.new(client_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac_lib.compare_digest(digest, hmac):
        return JSONResponse({"status": "error", "message": "Verificação HMAC falhou."}, status_code=400)

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
        return JSONResponse({"status": "error", "message": f"Troca de token falhou: HTTP {r.status_code}"}, status_code=400)

    access_token = r.json().get("access_token", "")
    if not access_token:
        return JSONResponse({"status": "error", "message": "Nenhum access_token na resposta."}, status_code=400)

    write_env({
        "SHOPIFY_ACCESS_TOKEN": access_token,
        "SHOPIFY_STORE_URL": f"https://{shop}",
    })
    return RedirectResponse("/setup?oauth=success")


@app.post("/run-etl")
async def run_etl(script: str = Form(...), start_date: str = Form(...), end_date: str = Form(...)):
    script_path = SCRIPTS_DIR / f"{script}.py"
    if not script_path.exists():
        return JSONResponse({"status": "error", "message": f"Script {script} not found"})
    try:
        subprocess.Popen(
            [sys.executable, str(script_path), "--start-date", start_date, "--end-date", end_date],
            cwd=str(ROOT),
        )
        return JSONResponse({"status": "started", "message": f"{script} started for {start_date} -> {end_date}"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


# -- Setup API ----------------------------------------------------------------
@app.post("/api/setup/shopify")
async def save_shopify(
    shopify_store_url: str = Form(...),
    shopify_access_token: str = Form(...),
    shopify_api_version: str = Form(...),
):
    write_env({
        "SHOPIFY_STORE_URL": shopify_store_url.strip(),
        "SHOPIFY_ACCESS_TOKEN": shopify_access_token.strip(),
        "SHOPIFY_API_VERSION": shopify_api_version.strip(),
    })
    return JSONResponse({"status": "ok", "message": "Shopify config saved. Restart the server to apply to ETL scripts."})


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
    return JSONResponse({"status": "ok", "message": "SQL Server config saved. Restart the server to apply to ETL scripts."})


@app.get("/api/setup/test-shopify")
async def test_shopify(url: str = "", token: str = "", version: str = "2024-01"):
    url = url.strip()
    token = token.strip()
    if not url or not token:
        return JSONResponse({"status": "error", "message": "URL and token are required."})
    try:
        import requests as req
        endpoint = f"{url.rstrip('/')}/admin/api/{version}/shop.json"
        r = req.get(endpoint, headers={"X-Shopify-Access-Token": token}, timeout=10)
        if r.status_code == 200:
            shop_name = r.json().get("shop", {}).get("name", url)
            return JSONResponse({"status": "ok", "message": f"Connected to '{shop_name}' successfully."})
        return JSONResponse({"status": "error", "message": f"HTTP {r.status_code} — check your URL and token."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)[:120]})


@app.get("/api/setup/test-sql")
async def test_sql(host: str = "", port: str = "1433", database: str = "", user: str = "", password: str = ""):
    if not host or not database:
        return JSONResponse({"status": "error", "message": "Host and database are required."})
    try:
        import pyodbc
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
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
        if script_name in schedules:
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
async def api_runs():
    return get_recent_runs()


@app.get("/api/logs")
async def api_logs():
    log_dir = ROOT / "logs"
    log_file = log_dir / f"{datetime.today().strftime('%Y-%m-%d')}.log"
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines(keepends=True)
        return {"lines": lines[-100:]}
    except FileNotFoundError:
        return {"lines": ["No logs for today yet."]}
