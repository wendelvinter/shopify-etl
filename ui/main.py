"""
Interface de controle do ETL — Shopify → SQL Server
Acesse: http://localhost:8000
"""
import subprocess
import sys
import os
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config.constants import (
    SQL_SERVER_HOST, SQL_SERVER_DATABASE,
    SHOPIFY_STORE_URL,
)

app = FastAPI(title="Shopify ETL Control Panel")
app.mount("/static", StaticFiles(directory="ui/static"), name="static")

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")


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
                stats.append({"table": table, "count": count or 0, "last_update": str(last)[:19] if last else "—"})
            except:
                stats.append({"table": table, "count": 0, "last_update": "—"})
        conn.close()
    except Exception as e:
        stats = [{"table": "SQL Server", "count": 0, "last_update": f"Erro: {str(e)[:60]}"}]
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
                "error": row[5] or "", "finished_at": str(row[6])[:19] if row[6] else "—"
            })
        conn.close()
    except Exception as e:
        runs = [{"script": "—", "status": "error", "error": str(e)[:80],
                 "records": 0, "start": "—", "end": "—", "finished_at": "—"}]
    return runs


def render_page(stats, runs):
    stats_html = ""
    for s in stats:
        stats_html += f"""
        <div class="stat-card">
          <div class="label">{s['table']}</div>
          <div class="value">{s['count']:,}</div>
          <div class="sub">Last update: {s['last_update']}</div>
        </div>"""

    runs_html = ""
    for r in runs:
        sc = "success" if r["status"] == "success" else "error"
        records = f"{int(r['records']):,}" if r["records"] else "0"
        error = str(r["error"])[:80] if r["error"] else ""
        runs_html += f"""
        <tr>
          <td>{r['script']}</td><td>{r['start']}</td><td>{r['end']}</td>
          <td><span class="status-{sc}">{r['status']}</span></td>
          <td>{records}</td><td>{r['finished_at']}</td>
          <td style="color:#aaa;font-size:11px;">{error}</td>
        </tr>"""

    shopify = SHOPIFY_STORE_URL or "Não configurado"
    db_host = SQL_SERVER_HOST or "Não configurado"
    db_name = SQL_SERVER_DATABASE or "Não configurado"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shopify ETL — Control Panel</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #1a1a1a; }}
    header {{ background: #1a1a1a; color: white; padding: 16px 32px; display: flex; align-items: center; gap: 12px; }}
    header h1 {{ font-size: 18px; font-weight: 500; }}
    header span {{ font-size: 12px; background: #333; padding: 3px 8px; border-radius: 4px; color: #aaa; }}
    .container {{ max-width: 1100px; margin: 32px auto; padding: 0 24px; display: grid; gap: 24px; }}
    .card {{ background: white; border-radius: 10px; padding: 24px; border: 1px solid #e5e5e5; }}
    .card h2 {{ font-size: 14px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 16px; }}
    .config-row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .badge {{ background: #f0f0f0; border-radius: 6px; padding: 8px 14px; font-size: 13px; }}
    .badge strong {{ display: block; font-size: 11px; color: #888; margin-bottom: 2px; }}
    .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }}
    .stat-card {{ border: 1px solid #e5e5e5; border-radius: 8px; padding: 16px; }}
    .stat-card .label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
    .stat-card .value {{ font-size: 28px; font-weight: 600; }}
    .stat-card .sub {{ font-size: 11px; color: #aaa; margin-top: 4px; }}
    .etl-form {{ display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 12px; align-items: end; }}
    .form-group label {{ display: block; font-size: 12px; color: #666; margin-bottom: 6px; }}
    .form-group select, .form-group input {{ width: 100%; padding: 9px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; background: white; }}
    .btn {{ padding: 9px 20px; border-radius: 6px; border: none; cursor: pointer; font-size: 14px; font-weight: 500; }}
    .btn-primary {{ background: #1a1a1a; color: white; }}
    .btn-primary:hover {{ background: #333; }}
    #alert {{ display: none; margin-top: 12px; padding: 10px 14px; border-radius: 6px; font-size: 13px; }}
    #alert.success {{ background: #d4edda; color: #155724; }}
    #alert.error {{ background: #f8d7da; color: #721c24; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{ text-align: left; padding: 8px 12px; font-size: 11px; color: #888; text-transform: uppercase; border-bottom: 1px solid #e5e5e5; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f0; }}
    tr:last-child td {{ border-bottom: none; }}
    .status-success {{ background: #d4edda; color: #155724; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
    .status-error {{ background: #f8d7da; color: #721c24; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
    #log-box {{ background: #1a1a1a; color: #d4d4d4; padding: 16px; border-radius: 8px; font-family: monospace; font-size: 12px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; }}
    .refresh-btn {{ float: right; font-size: 12px; color: #888; cursor: pointer; background: none; border: none; }}
    .refresh-btn:hover {{ color: #333; }}
  </style>
</head>
<body>
<header>
  <h1>Shopify ETL — Control Panel</h1>
  <span>Logistics San Diego</span>
</header>
<div class="container">
  <div class="card">
    <h2>Configuration</h2>
    <div class="config-row">
      <div class="badge"><strong>Shopify Store</strong>{shopify}</div>
      <div class="badge"><strong>SQL Server Host</strong>{db_host}</div>
      <div class="badge"><strong>Database</strong>{db_name}</div>
    </div>
  </div>
  <div class="card">
    <h2>Tables <button class="refresh-btn" onclick="refreshStats()">&#8635; Refresh</button></h2>
    <div class="stats-grid" id="stats-grid">{stats_html}</div>
  </div>
  <div class="card">
    <h2>Run ETL</h2>
    <div class="etl-form">
      <div class="form-group">
        <label>Script</label>
        <select id="script">
          <option value="etl_orders">Orders</option>
          <option value="etl_fulfillments">Fulfillments + Events</option>
          <option value="etl_locations">Locations</option>
        </select>
      </div>
      <div class="form-group"><label>Start date</label><input type="date" id="start-date"></div>
      <div class="form-group"><label>End date</label><input type="date" id="end-date"></div>
      <button class="btn btn-primary" onclick="runETL()">&#9654; Run</button>
    </div>
    <div id="alert"></div>
  </div>
  <div class="card">
    <h2>Recent runs <button class="refresh-btn" onclick="refreshRuns()">&#8635; Refresh</button></h2>
    <table id="runs-table">
      <thead><tr><th>Script</th><th>Start</th><th>End</th><th>Status</th><th>Records</th><th>Finished at</th><th>Error</th></tr></thead>
      <tbody>{runs_html}</tbody>
    </table>
  </div>
  <div class="card">
    <h2>Today's log <button class="refresh-btn" onclick="refreshLogs()">&#8635; Refresh</button></h2>
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
    el.style.display = 'block';
    el.className = data.status === 'started' ? 'success' : 'error';
    el.textContent = data.message;
    setTimeout(() => {{ el.style.display = 'none'; }}, 5000);
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
        <td><span class="status-${{r.status === 'success' ? 'success' : 'error'}}">${{r.status}}</span></td>
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
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return HTMLResponse(content=render_page(get_db_stats(), get_recent_runs()))


@app.post("/run-etl")
async def run_etl(script: str = Form(...), start_date: str = Form(...), end_date: str = Form(...)):
    script_path = os.path.join(SCRIPTS_DIR, f"{script}.py")
    if not os.path.exists(script_path):
        return JSONResponse({"status": "error", "message": f"Script {script} não encontrado"})
    cmd = [sys.executable, script_path, "--start-date", start_date, "--end-date", end_date]
    try:
        subprocess.Popen(cmd, cwd=os.path.join(os.path.dirname(__file__), ".."))
        return JSONResponse({"status": "started", "message": f"{script} iniciado para {start_date} -> {end_date}"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@app.get("/api/stats")
async def api_stats():
    return get_db_stats()


@app.get("/api/runs")
async def api_runs():
    return get_recent_runs()


@app.get("/api/logs")
async def api_logs():
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    log_file = os.path.join(log_dir, f"{datetime.today().strftime('%Y-%m-%d')}.log")
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return {"lines": lines[-100:]}
    except FileNotFoundError:
        return {"lines": ["Nenhum log para hoje ainda."]}
