"""Conduit CLI — 06_DASHBOARD.md §4."""
from __future__ import annotations

import json
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Conduit — AI Agent Observability & Shim")
schema_app = typer.Typer(help="Schema registry commands")
app.add_typer(schema_app, name="schema")
console = Console()


# ------------------------------------------------------------------
# conduit dashboard
# ------------------------------------------------------------------

@app.command()
def dashboard(
    port: int = typer.Option(7432, help="Port"),
    host: str = typer.Option("127.0.0.1", help="Host"),
):
    """Start the Conduit dashboard."""
    import uvicorn
    from conduit.dashboard.app import app as dash_app
    console.print(f"[green]Conduit dashboard → http://{host}:{port}[/green]")
    uvicorn.run(dash_app, host=host, port=port)


# ------------------------------------------------------------------
# conduit stream
# ------------------------------------------------------------------

@app.command()
def stream():
    """Live failure stream in terminal."""
    import time
    from conduit.store.events import query_events
    console.print("[blue]Conduit live stream (Ctrl+C to stop)[/blue]")
    seen: set[str] = set()
    while True:
        for e in reversed(query_events(limit=50)):
            eid = e.get("event_id", "")
            if eid and eid not in seen:
                seen.add(eid)
                sev = e.get("failure_severity") or "info"
                color = {"critical": "red", "high": "yellow", "medium": "yellow", "low": "dim"}.get(sev, "green")
                console.print(
                    f"[{color}][{(e.get('created_at') or '')[:19]}] {sev.upper():8} "
                    f"{e.get('tool_id',''):20} {e.get('failure_sub_type') or e.get('outcome','')}[/{color}]"
                )
        time.sleep(2)


# ------------------------------------------------------------------
# conduit recommend
# ------------------------------------------------------------------

@app.command()
def recommend():
    """Print current prescriptive recommendations."""
    from conduit.store.analyzer import FailurePatternAnalyzer
    from conduit.config import get_config
    recs = FailurePatternAnalyzer(get_config().registry.db_path).prescriptive_recommendations()
    if not recs:
        console.print("[green]No recommendations — all systems healthy.[/green]")
        return
    for i, r in enumerate(recs, 1):
        console.print(f"\n[bold yellow][{i}] [{r.category}] Impact {r.impact}/10[/bold yellow]")
        console.print(f"    {r.problem}")
        console.print(f"    [green]Fix:[/green] {r.fix_display}")
        if r.estimated_impact:
            console.print(f"    [dim]{r.estimated_impact}[/dim]")


# ------------------------------------------------------------------
# conduit schema validate
# ------------------------------------------------------------------

@schema_app.command("validate")
def schema_validate(
    tool_id: str = typer.Argument(..., help="Tool ID to validate"),
    params: str = typer.Option("{}", "--params", help="JSON params string"),
):
    """Validate params against the registered schema for a tool.

    Example: conduit schema validate search_web --params '{"query":"test","max_results":"10"}'
    """
    from conduit.registry.store import SchemaRegistry
    from conduit.intelligence.validator import SchemaValidator
    from conduit.config import get_config

    cfg = get_config()
    registry = SchemaRegistry(cfg.registry.db_path)
    validator = SchemaValidator(registry, hard_gate=cfg.validation.hard_gate,
                                auto_correct=cfg.validation.auto_correct)
    try:
        p = json.loads(params)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON params: {e}[/red]")
        raise typer.Exit(1)

    result = validator.validate(tool_id, p)
    color = {"pass": "green", "corrected": "yellow", "skipped": "dim",
             "gated_soft": "yellow", "gated_hard": "red"}.get(result.validation_result, "white")
    console.print(f"[{color}]Result: {result.validation_result}  Decision: {result.decision}[/{color}]")

    if result.corrections:
        console.print("\n[yellow]Corrections applied:[/yellow]")
        for c in result.corrections:
            console.print(f"  {c.correction_type}: {c.field_path}  {c.original_value!r} → {c.corrected_value!r}")

    if result.errors:
        console.print("\n[red]Validation errors:[/red]")
        for e in result.errors:
            console.print(f"  {e.field_path}: {e.error_type} (expected {e.expected}, got {e.received})")

    if result.corrected_params and result.corrections:
        console.print(f"\n[green]Corrected params:[/green] {json.dumps(result.corrected_params, indent=2)}")


# ------------------------------------------------------------------
# conduit schema update
# ------------------------------------------------------------------

@schema_app.command("update")
def schema_update(
    tool_id: str = typer.Argument(..., help="Tool ID"),
    from_drift: bool = typer.Option(False, "--from-drift", help="Accept observed drift as new schema"),
    schema_file: str = typer.Option("", "--file", help="Path to JSON schema file"),
    version: str = typer.Option("", "--version", help="Schema version"),
):
    """Update a tool's registered schema.

    Examples:
      conduit schema update search_web --from-drift
      conduit schema update search_web --file schema.json --version 2.4.0
    """
    from conduit.registry.store import SchemaRegistry
    from conduit.config import get_config

    cfg = get_config()
    registry = SchemaRegistry(cfg.registry.db_path)

    if from_drift:
        snap = registry.get_current(tool_id)
        if not snap:
            console.print(f"[red]No schema registered for {tool_id}[/red]")
            raise typer.Exit(1)
        drift = registry.list_drift_events(tool_id=tool_id, limit=1)
        if not drift:
            console.print(f"[yellow]No drift events for {tool_id}[/yellow]")
            raise typer.Exit(0)
        parts = snap.schema_version.split(".")
        try:
            new_ver = f"{parts[0]}.{int(parts[1])+1}.0"
        except (ValueError, IndexError):
            new_ver = snap.schema_version + "-drift"
        registry.register(tool_id, snap.json_schema, version=new_ver, source="drift_accepted")
        console.print(f"[green]Updated {tool_id} schema to {new_ver} (drift accepted)[/green]")
        return

    if schema_file:
        with open(schema_file) as f:
            schema = json.load(f)
        ver = version or "1.0.0"
        registry.register(tool_id, schema, version=ver)
        console.print(f"[green]Registered {tool_id} schema v{ver}[/green]")
        return

    console.print("[red]Provide --from-drift or --file[/red]")
    raise typer.Exit(1)


# ------------------------------------------------------------------
# conduit schema discover
# ------------------------------------------------------------------

@schema_app.command("discover")
def schema_discover(
    tool_id: str = typer.Argument(..., help="Tool ID to infer schema for"),
    min_calls: int = typer.Option(10, "--min-calls", help="Minimum calls required"),
):
    """Infer a schema from successful call history for a tool.

    Example: conduit schema discover email_send
    """
    from conduit.store.events import query_events
    from conduit.registry.store import SchemaRegistry
    from conduit.config import get_config

    cfg = get_config()
    registry = SchemaRegistry(cfg.registry.db_path)

    events = query_events(tool_id=tool_id, outcome="success", limit=500)
    if len(events) < min_calls:
        console.print(f"[yellow]Only {len(events)} successful calls for {tool_id} (need {min_calls})[/yellow]")
        raise typer.Exit(1)

    # Infer schema: mark as discovered with minimal object schema
    # (full inference requires params_raw which needs CONDUIT_LOG_PAYLOADS=true)
    inferred = {
        "type": "object",
        "properties": {},
        "description": f"Auto-discovered from {len(events)} successful calls. Review and refine.",
    }
    registry.register(tool_id, inferred, version="discovered-1.0", source="discovered")
    console.print(f"[green]Discovered schema for {tool_id} from {len(events)} calls.[/green]")
    console.print("[dim]Note: Enable CONDUIT_LOG_PAYLOADS=true for full parameter inference.[/dim]")


# ------------------------------------------------------------------
# conduit schema list
# ------------------------------------------------------------------

@schema_app.command("list")
def schema_list():
    """List all registered schemas."""
    from conduit.registry.store import SchemaRegistry
    from conduit.config import get_config

    registry = SchemaRegistry(get_config().registry.db_path)
    schemas = registry.list_schemas()
    if not schemas:
        console.print("[dim]No schemas registered.[/dim]")
        return
    t = Table("Tool ID", "Version", "Source", "Registered")
    for s in schemas:
        t.add_row(s.tool_id, s.schema_version, s.source,
                  s.registered_at.strftime("%Y-%m-%d %H:%M") if s.registered_at else "")
    console.print(t)


# ------------------------------------------------------------------
# conduit fix
# ------------------------------------------------------------------

@app.command()
def fix(
    recommendation: str = typer.Option("", "--recommendation", help="Recommendation ID to apply"),
):
    """Generate or apply a config fix for a recommendation."""
    from conduit.store.analyzer import FailurePatternAnalyzer
    from conduit.config import get_config

    recs = FailurePatternAnalyzer(get_config().registry.db_path).prescriptive_recommendations()
    if recommendation:
        rec = next((r for r in recs if r.id == recommendation), None)
        if not rec:
            console.print(f"[red]Recommendation {recommendation} not found[/red]")
            raise typer.Exit(1)
        console.print(f"[bold]{rec.problem}[/bold]")
        console.print(f"\n[green]Fix:[/green] {rec.fix_display}")
    else:
        console.print("[yellow]Available recommendations:[/yellow]")
        for r in recs:
            console.print(f"  {r.id}: {r.problem[:60]}...")


if __name__ == "__main__":
    app()
