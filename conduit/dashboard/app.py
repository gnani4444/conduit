"""Conduit Dashboard — FastAPI app.

Implements all views and REST API endpoints from 06_DASHBOARD.md §3-5.
REST API: /api/v1/*
HTML views: /, /failures, /schemas, /failures/{id}, /recommendations, /tools
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from conduit.config import get_config
from conduit.store.events import query_events
from conduit.store.analyzer import FailurePatternAnalyzer
from conduit.registry.store import SchemaRegistry
from conduit.telemetry.collector import make_ingest_router

app = FastAPI(title="Conduit Dashboard", version="0.1.0")
app.include_router(make_ingest_router())

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _db() -> str:
    return get_config().registry.db_path

def _registry() -> SchemaRegistry:
    return SchemaRegistry(_db())

def _analyzer() -> FailurePatternAnalyzer:
    return FailurePatternAnalyzer(_db())

def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(_db(), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def _since(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

def _health() -> dict:
    try:
        con = _connect()
        s = _since(24)
        total = con.execute("SELECT COUNT(*) FROM tool_call_events WHERE created_at>?", (s,)).fetchone()[0]
        fails = con.execute("SELECT COUNT(*) FROM tool_call_events WHERE created_at>? AND outcome!='success'", (s,)).fetchone()[0]
        loops = con.execute("SELECT COUNT(*) FROM tool_call_events WHERE created_at>? AND failure_class='agent_loop'", (s,)).fetchone()[0]
        drift = con.execute("SELECT COUNT(*) FROM drift_events WHERE detected_at>?", (s,)).fetchone()[0]
        con.close()
        rate = round(fails / total * 100, 1) if total else 0.0
        return {"agents_running": 0, "tool_calls_24h": total, "failure_rate": rate,
                "loops_detected": loops, "schema_drift_alerts": drift}
    except Exception:
        return {"agents_running": 0, "tool_calls_24h": 0, "failure_rate": 0.0,
                "loops_detected": 0, "schema_drift_alerts": 0}

# ------------------------------------------------------------------
# REST API — /api/v1/*  (06_DASHBOARD.md §5)
# ------------------------------------------------------------------

@app.get("/api/v1/health")
async def api_health():
    return _health()

@app.get("/api/v1/failures")
async def api_failures(tool_id: str | None = None, outcome: str | None = None,
                        failure_class: str | None = None, limit: int = 100):
    return {"failures": query_events(tool_id=tool_id, outcome=outcome,
                                     failure_class=failure_class, limit=limit)}

@app.get("/api/v1/failures/stream")
async def api_failures_stream():
    return StreamingResponse(_sse_generator(), media_type="text/event-stream")

@app.get("/api/v1/failures/{event_id}")
async def api_failure_detail(event_id: str):
    events = query_events(limit=1000)
    event = next((e for e in events if e.get("event_id") == event_id), None)
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")
    return event

@app.get("/api/v1/recommendations")
async def api_recommendations():
    recs = _analyzer().prescriptive_recommendations()
    return {"recommendations": [
        {"id": r.id, "category": r.category, "impact": r.impact,
         "problem": r.problem, "evidence": r.evidence,
         "fix_display": r.fix_display, "estimated_impact": r.estimated_impact}
        for r in recs
    ]}

@app.post("/api/v1/recommendations/{rec_id}/dismiss")
async def api_dismiss_recommendation(rec_id: str):
    # v0.1: no-op (persistence added in v0.2)
    return {"dismissed": rec_id}

@app.get("/api/v1/tools")
async def api_tools():
    try:
        con = _connect()
        rows = con.execute("""
            SELECT tool_id, COUNT(*) as total,
                   SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) as successes,
                   AVG(latency_ms) as avg_ms, MAX(latency_ms) as p95_ms
            FROM tool_call_events GROUP BY tool_id ORDER BY total DESC
        """).fetchall()
        con.close()
        return {"tools": [dict(r) for r in rows]}
    except Exception:
        return {"tools": []}

@app.get("/api/v1/tools/{tool_id}")
async def api_tool_detail(tool_id: str):
    events = query_events(tool_id=tool_id, limit=500)
    snap = _registry().get_current(tool_id)
    drift = _registry().list_drift_events(tool_id=tool_id)
    return {"tool_id": tool_id, "schema": snap.__dict__ if snap else None,
            "drift_events": drift, "recent_events": events[:50]}

@app.get("/api/v1/schemas")
async def api_schemas():
    schemas = _registry().list_schemas()
    drift_counts = {}
    for d in _registry().list_drift_events():
        drift_counts[d["tool_id"]] = drift_counts.get(d["tool_id"], 0) + 1
    return {"schemas": [
        {"tool_id": s.tool_id, "version": s.schema_version, "source": s.source,
         "drift_events": drift_counts.get(s.tool_id, 0),
         "status": "DRIFT" if drift_counts.get(s.tool_id, 0) > 0 else "OK"}
        for s in schemas
    ]}

@app.get("/api/v1/schemas/{tool_id}")
async def api_schema_detail(tool_id: str):
    snap = _registry().get_current(tool_id)
    if not snap:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Schema not found")
    drift = _registry().list_drift_events(tool_id=tool_id)
    return {"tool_id": tool_id, "version": snap.schema_version,
            "schema": snap.json_schema, "aliases": snap.known_aliases,
            "source": snap.source, "drift_events": drift}

@app.post("/api/v1/schemas/{tool_id}/accept_drift")
async def api_accept_drift(tool_id: str):
    """Accept observed drift as the new schema baseline."""
    drift_events = _registry().list_drift_events(tool_id=tool_id, limit=1)
    if not drift_events:
        return {"ok": False, "reason": "no drift events"}
    snap = _registry().get_current(tool_id)
    if snap:
        # Re-register with bumped version to mark drift accepted
        import json as _json
        parts = snap.schema_version.split(".")
        try:
            new_version = f"{parts[0]}.{int(parts[1]) + 1}.0" if len(parts) >= 2 else snap.schema_version + "-drift"
        except (ValueError, IndexError):
            new_version = snap.schema_version + "-drift"
        _registry().register(tool_id, snap.json_schema, version=new_version, source="drift_accepted")
    return {"ok": True, "tool_id": tool_id}

@app.get("/api/v1/analytics/failure_rate")
async def api_failure_rate():
    return {"by_class": _analyzer().failure_rate_by_class(days=7)}

@app.get("/api/v1/analytics/recovery_rate")
async def api_recovery_rate():
    return {"by_action": _analyzer().recovery_success_rate(days=7)}

# ------------------------------------------------------------------
# SSE stream
# ------------------------------------------------------------------

async def _sse_generator() -> AsyncGenerator[str, None]:
    seen: set[str] = set()
    while True:
        for e in reversed(query_events(limit=50)):
            eid = e.get("event_id", "")
            if eid and eid not in seen:
                seen.add(eid)
                yield f"data: {json.dumps(e)}\n\n"
        await asyncio.sleep(2)

@app.get("/stream")
async def stream_sse():
    return StreamingResponse(_sse_generator(), media_type="text/event-stream")

# ------------------------------------------------------------------
# HTML views (06_DASHBOARD.md §3)
# ------------------------------------------------------------------

_CSS = """<style>
:root{--bg:#0d1117;--sf:#161b22;--bd:#30363d;--tx:#e6edf3;--mu:#8b949e;
--gr:#3fb950;--am:#d29922;--rd:#f85149;--bl:#58a6ff;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--tx);font-family:monospace;font-size:13px;}
nav{padding:8px 16px;background:var(--sf);border-bottom:1px solid var(--bd);}
nav a{color:var(--bl);text-decoration:none;margin-right:16px;}
.bar{display:flex;gap:16px;padding:10px 16px;background:var(--sf);border-bottom:1px solid var(--bd);}
.bar span{color:var(--mu);} .bar b{color:var(--tx);}
.wrap{padding:16px;} h2{color:var(--bl);margin-bottom:12px;}
table{width:100%;border-collapse:collapse;margin-bottom:16px;}
th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd);color:var(--mu);}
td{padding:6px 8px;border-bottom:1px solid var(--bd);}
.high{color:var(--am);}.critical{color:var(--rd);}.low{color:var(--mu);}
.ok{color:var(--gr);}.drift{color:var(--am);}
.feed{height:280px;overflow-y:auto;background:var(--sf);border:1px solid var(--bd);padding:8px;}
.grid{display:grid;grid-template-columns:60% 40%;gap:16px;}
.card{background:var(--sf);border:1px solid var(--bd);padding:12px;margin-bottom:8px;border-radius:4px;}
.card h3{color:var(--am);margin-bottom:6px;font-size:12px;}
.badge{display:inline-block;padding:2px 6px;border-radius:3px;font-size:11px;margin-right:6px;}
.SCHEMA{background:#1f3a1f;color:var(--gr);}
.LOOP{background:#3a1f1f;color:var(--rd);}
.RECOVERY{background:#3a2f1f;color:var(--am);}
.CONFIG{background:#1f2a3a;color:var(--bl);}
</style>"""

_NAV = """<nav>
  <a href="/">Command Center</a>
  <a href="/failures">Failures</a>
  <a href="/schemas">Schemas</a>
  <a href="/recommendations">Recommendations</a>
  <a href="/tools">Tools</a>
</nav>"""

def _rate_cls(rate: float) -> str:
    return "ok" if rate < 5 else ("high" if rate < 15 else "critical")

@app.get("/", response_class=HTMLResponse)
async def view_command_center():
    h = _health()
    rc = _rate_cls(h["failure_rate"])
    bar = f"""<div class="bar">
      <span>Tool calls (24h): <b>{h['tool_calls_24h']}</b></span>
      <span>Failure rate: <b class="{rc}">{h['failure_rate']}%</b></span>
      <span>Loops: <b>{h['loops_detected']}</b></span>
      <span>Schema drift: <b>{h['schema_drift_alerts']}</b></span>
    </div>"""
    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Conduit</title>{_CSS}</head><body>
{bar}{_NAV}<div class="wrap"><div class="grid">
  <div><h2>Live Failure Feed</h2><div class="feed" id="feed"></div></div>
  <div><h2>Recommendations</h2><div id="recs"></div></div>
</div></div>
<script>
const feed=document.getElementById('feed');
const es=new EventSource('/stream');
es.onmessage=e=>{{
  const d=JSON.parse(e.data);
  const r=document.createElement('div');
  r.style.borderBottom='1px solid #30363d';r.style.padding='2px 0';
  const sev=d.failure_severity||'info';
  const cls={{critical:'#f85149',high:'#d29922',medium:'#d29922',low:'#8b949e',info:'#3fb950'}}[sev]||'#e6edf3';
  r.innerHTML=`<span style="color:${{cls}}">${{d.created_at?.slice(11,19)||''}} ${{sev.toUpperCase().padEnd(8)}} ${{d.tool_id}} ${{d.failure_sub_type||d.outcome}}</span>`;
  feed.prepend(r);if(feed.children.length>200)feed.lastChild.remove();
}};
fetch('/api/v1/recommendations').then(r=>r.json()).then(data=>{{
  const el=document.getElementById('recs');
  (data.recommendations||[]).slice(0,3).forEach(r=>{{
    el.innerHTML+=`<div class="card"><span class="badge ${{r.category}}">${{r.category}}</span><h3>${{r.problem}}</h3><small>${{r.fix_display}}</small></div>`;
  }});
}});
</script></body></html>""")

@app.get("/failures", response_class=HTMLResponse)
async def view_failures():
    """06_DASHBOARD.md §3 View 2 — Failure Analysis with breakdown table."""
    events = query_events(limit=200)

    # Section B: Failure Breakdown Table — sub_type, count, rate, top tool, recovery success
    try:
        con = _connect()
        s = _since(24)
        breakdown = con.execute("""
            SELECT failure_sub_type,
                   COUNT(*) as cnt,
                   tool_id as top_tool,
                   SUM(CASE WHEN recovery_succeeded=1 THEN 1 ELSE 0 END) as recovered,
                   recovery_action
            FROM tool_call_events
            WHERE created_at > ? AND failure_sub_type IS NOT NULL
            GROUP BY failure_sub_type
            ORDER BY cnt DESC
        """, (s,)).fetchall()
        total_24h = con.execute(
            "SELECT COUNT(*) FROM tool_call_events WHERE created_at > ?", (s,)
        ).fetchone()[0] or 1
        con.close()
    except Exception:
        breakdown, total_24h = [], 1

    breakdown_rows = "".join(f"""<tr>
      <td>{r['failure_sub_type']}</td>
      <td>{r['cnt']}</td>
      <td>{round(r['cnt']/total_24h*100,1)}%</td>
      <td>{r['top_tool'] or ''}</td>
      <td>{round((r['recovered'] or 0)/max(r['cnt'],1)*100,0):.0f}% ({r['recovery_action'] or 'none'})</td>
    </tr>""" for r in breakdown)

    event_rows = "".join(f"""<tr>
      <td>{e.get('created_at','')[:19]}</td>
      <td class="{e.get('failure_severity','low') or 'low'}">{(e.get('failure_severity') or 'info').upper()}</td>
      <td><a href="/failures/{e.get('event_id','')}" style="color:#58a6ff">{e.get('tool_id','')}</a></td>
      <td>{e.get('failure_sub_type','') or ''}</td>
      <td>{e.get('outcome','')}</td>
      <td>{e.get('recovery_action','') or ''}</td>
      <td>{round(e.get('latency_ms') or 0,1)}ms</td>
    </tr>""" for e in events)

    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Failures — Conduit</title>{_CSS}</head><body>
{_NAV}<div class="wrap">
<h2>Failure Breakdown (24h)</h2>
<table><tr><th>Failure Sub-type</th><th>Count</th><th>Rate</th><th>Top Tool</th><th>Recovery Success</th></tr>
{breakdown_rows}</table>
<h2>Recent Failures</h2>
<table><tr><th>Time</th><th>Sev</th><th>Tool</th><th>Sub-type</th><th>Outcome</th><th>Recovery</th><th>Latency</th></tr>
{event_rows}</table>
</div></body></html>""")

@app.get("/failures/{event_id}", response_class=HTMLResponse)
async def view_failure_detail(event_id: str):
    events = query_events(limit=2000)
    event = next((e for e in events if e.get("event_id") == event_id), None)
    if not event:
        return HTMLResponse("<h2>Event not found</h2>", status_code=404)
    rows = "".join(f"<tr><td style='color:#8b949e'>{k}</td><td>{v}</td></tr>" for k, v in event.items())
    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Failure Detail</title>{_CSS}</head><body>
{_NAV}<div class="wrap"><h2>Failure: {event.get('failure_sub_type') or event.get('outcome','')}</h2>
<table><tr><th>Field</th><th>Value</th></tr>{rows}</table></div></body></html>""")

@app.get("/schemas", response_class=HTMLResponse)
async def view_schemas():
    from datetime import timedelta
    reg = _registry()
    schemas = reg.list_schemas()
    drift_counts = {}
    for d in reg.list_drift_events():
        drift_counts[d["tool_id"]] = drift_counts.get(d["tool_id"], 0) + 1

    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=3)

    def _status(s, dc):
        if dc > 0:
            return "DRIFT", "drift"
        if s.last_validated_at and s.last_validated_at < stale_cutoff:
            return "STALE", "stale"
        if s.registered_at and s.registered_at < stale_cutoff:
            return "STALE", "stale"
        return "OK", "ok"

    rows = "".join(f"""<tr>
      <td><a href="/schemas/{s.tool_id}" style="color:#58a6ff">{s.tool_id}</a></td>
      <td>{s.schema_version}</td><td>{s.source}</td>
      <td>{s.registered_at.strftime('%Y-%m-%d %H:%M') if s.registered_at else ''}</td>
      <td class="{_status(s, drift_counts.get(s.tool_id,0))[1]}">{_status(s, drift_counts.get(s.tool_id,0))[0]}</td>
      <td>{drift_counts.get(s.tool_id,0)}</td>
    </tr>""" for s in schemas)
    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Schemas — Conduit</title>{_CSS}</head><body>
{_NAV}<div class="wrap"><h2>Schema Registry</h2>
<table><tr><th>Tool</th><th>Version</th><th>Source</th><th>Registered</th><th>Status</th><th>Drift</th></tr>
{rows}</table></div></body></html>""")


@app.get("/schemas/{tool_id}", response_class=HTMLResponse)
async def view_schema_detail(tool_id: str):
    """Schema detail view — 06_DASHBOARD.md §3 Schema Detail."""
    import json as _json
    reg = _registry()
    snap = reg.get_current(tool_id)
    if not snap:
        return HTMLResponse(f"<h2>No schema registered for {tool_id}</h2>", status_code=404)
    drift_events = reg.list_drift_events(tool_id=tool_id)
    schema_json = _json.dumps(snap.json_schema, indent=2)

    drift_rows = "".join(f"""<div class="card">
      <span class="badge SCHEMA">DRIFT</span>
      <b>{d.get('detected_at','')[:19]}</b> — severity: <span class="{'drift' if d.get('severity') in ('high','critical') else 'low'}">{d.get('severity','')}</span><br>
      Fields changed: {d.get('fields_changed','[]')}<br>
      Auto-corrected: {d.get('auto_corrected', False)}
    </div>""" for d in drift_events) or "<p style='color:#8b949e'>No drift events.</p>"

    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>{tool_id} — Conduit</title>{_CSS}</head><body>
{_NAV}<div class="wrap">
<h2>Schema: {tool_id}</h2>
<p style="color:#8b949e">Version: {snap.schema_version} &nbsp;|&nbsp; Source: {snap.source} &nbsp;|&nbsp; Registered: {snap.registered_at.strftime('%Y-%m-%d %H:%M') if snap.registered_at else ''}</p>
<h2 style="margin-top:16px">Current Schema</h2>
<pre style="background:#161b22;padding:12px;border:1px solid #30363d;overflow:auto;color:#e6edf3">{schema_json}</pre>
<div style="margin:12px 0">
  <a href="/api/v1/schemas/{tool_id}/accept_drift" style="color:#3fb950;margin-right:16px" onclick="fetch(this.href,{{method:'POST'}});return false">✓ Accept drift as new schema</a>
</div>
<h2 style="margin-top:16px">Drift Events ({len(drift_events)})</h2>
{drift_rows}
</div></body></html>""")

@app.get("/recommendations", response_class=HTMLResponse)
async def view_recommendations():
    recs = _analyzer().prescriptive_recommendations()
    cards = "".join(f"""<div class="card">
      <span class="badge {r.category}">{r.category}</span>
      <span style="color:#8b949e;font-size:11px">Impact: {r.impact}/10</span>
      <h3>{r.problem}</h3>
      <p style="color:#8b949e;margin:4px 0">{' | '.join(r.evidence)}</p>
      <code style="color:#3fb950">{r.fix_display}</code>
      {f'<p style="color:#8b949e;font-size:11px">{r.estimated_impact}</p>' if r.estimated_impact else ''}
    </div>""" for r in recs) or "<p style='color:#8b949e'>No recommendations — all systems healthy.</p>"
    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Recommendations — Conduit</title>{_CSS}</head><body>
{_NAV}<div class="wrap"><h2>Prescriptive Recommendations</h2>{cards}</div></body></html>""")

@app.get("/tools", response_class=HTMLResponse)
async def view_tools():
    """06_DASHBOARD.md §3 View 6 — Tool Performance with Schema OK column."""
    try:
        con = _connect()
        rows_data = con.execute("""
            SELECT tool_id, COUNT(*) as total,
                   SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) as ok,
                   AVG(latency_ms) as avg_ms, MAX(latency_ms) as max_ms,
                   SUM(CASE WHEN failure_class='agent_loop' THEN 1 ELSE 0 END) as loops,
                   SUM(CASE WHEN outcome!='success' THEN 1 ELSE 0 END) as errors
            FROM tool_call_events GROUP BY tool_id ORDER BY total DESC
        """).fetchall()
        con.close()
    except Exception:
        rows_data = []

    # Build schema status map: DRIFT / OK / NONE
    reg = _registry()
    drift_counts = {d["tool_id"]: 1 for d in reg.list_drift_events()}
    registered = {s.tool_id for s in reg.list_schemas()}

    def _schema_status(tid):
        if tid not in registered:
            return "NONE", "low"
        if tid in drift_counts:
            return "DRIFT", "drift"
        return "OK", "ok"

    rows = "".join(f"""<tr>
      <td><a href="/api/v1/tools/{r['tool_id']}" style="color:#58a6ff">{r['tool_id']}</a></td>
      <td>{r['total']}</td>
      <td class="{'ok' if (r['ok'] or 0)/max(r['total'],1)>0.95 else 'high'}">{round((r['ok'] or 0)/max(r['total'],1)*100,1)}%</td>
      <td>{round(r['avg_ms'] or 0,1)}ms</td>
      <td>{round(r['max_ms'] or 0,1)}ms</td>
      <td class="{_schema_status(r['tool_id'])[1]}">{_schema_status(r['tool_id'])[0]}</td>
      <td>{r['loops'] or 0}</td>
      <td>{r['errors'] or 0}</td>
    </tr>""" for r in rows_data)
    return HTMLResponse(f"""<!DOCTYPE html><html><head><title>Tools — Conduit</title>{_CSS}</head><body>
{_NAV}<div class="wrap"><h2>Tool Performance</h2>
<table><tr><th>Tool</th><th>Calls</th><th>Success%</th><th>Avg ms</th><th>Max ms</th><th>Schema OK</th><th>Loops</th><th>Errors</th></tr>
{rows}</table></div></body></html>""")
