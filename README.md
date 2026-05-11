# Conduit

> "LangSmith tells you what your agent did. Conduit tells you what went wrong, why, and fixes it — across any framework you already use."

Conduit is a cross-framework AI agent observability and reliability shim. It intercepts tool calls via OpenTelemetry, validates schemas, detects loops, and injects recovery context — all in < 5ms, without modifying your agent code.

## Quick Start (< 5 minutes)

```bash
pip install conduit-ai-agents
```

### LangGraph (2 lines)

```python
from conduit.shim.adapters.langgraph import install_for_langgraph
install_for_langgraph()

# Your existing LangGraph code — unchanged
graph = StateGraph(State)
# ...
```

### OpenAI Agents SDK (2 lines)

```python
from conduit.shim.adapters.openai_sdk import install_for_openai_sdk
install_for_openai_sdk()

# Your existing agent code — unchanged
agent = Agent(name="...", tools=[...])
```

### Start the dashboard

```bash
conduit dashboard
# → http://127.0.0.1:7432
```

## What Conduit Does

| Problem | Conduit's solution |
|---------|-------------------|
| Tool called with wrong param types | Schema validator auto-corrects `"10"` → `10` before execution |
| Agent loops on the same failed call | Loop detector fires at N=3, injects replan context |
| Tool times out repeatedly | Failure classifier + recovery engine retries with backoff |
| Schema drifted since agent was written | Drift detection + `conduit schema update` to fix |
| No visibility into agent failures | Dashboard with live failure feed + prescriptive recommendations |

## CLI

```bash
conduit dashboard                          # Start web dashboard
conduit stream                             # Live failure stream in terminal
conduit recommend                          # Print current recommendations

conduit schema list                        # List registered schemas
conduit schema validate search_web \
  --params '{"query":"test","max_results":"10"}'   # Validate params
conduit schema update search_web --from-drift      # Accept observed drift
conduit schema discover email_send                 # Infer schema from call history
conduit fix --recommendation UPDATE_SCHEMA_search_web
```

## Register a Schema

```python
from conduit.registry.store import SchemaRegistry

registry = SchemaRegistry("./conduit.db")
registry.register(
    tool_id="search_web",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["query", "max_results"],
    },
    version="1.0.0",
)
```

## Ingest MCP Manifests

```python
from conduit.registry.mcp import ingest_mcp_manifest, discover_mcp_servers

# From a file
ingest_mcp_manifest(registry, "/path/to/mcp_server.json")

# Auto-discover from CLAUDE_MCP_SERVERS env var
discover_mcp_servers(registry)
```

## Configuration (`conduit.yaml`)

```yaml
shim:
  timeout_ms: 5          # Pass-through if intelligence plane takes > 5ms
  fallback: pass_through

validation:
  hard_gate: false       # true = block invalid calls; false = log and pass
  auto_correct: true     # Auto-fix type mismatches, field renames

detection:
  loop_threshold: 3      # N identical calls = loop detected
  loop_window: 10

recovery:
  enabled: true
  max_retries: 2

registry:
  db_path: ./conduit.db
```

## Key Invariants

1. **Never raises into agent code** — all hooks are `try/except`
2. **Never blocks** — passes through if intelligence plane exceeds `timeout_ms`
3. **Privacy by default** — no param values logged without `CONDUIT_LOG_PAYLOADS=true`
4. **Loop detector is per-task** — parallel agents never interfere
5. **Recovery is idempotent** — same failure never injected twice

## Architecture

```
Agent (LangGraph / OpenAI SDK / CrewAI)
    │
    ▼ tool calls
[Conduit ConduitProcessor]  ← OTel SpanProcessor
    │ pre-hook: SchemaValidator (< 3ms)
    │ post-hook: ToolFailureDetector + AgentLoopDetector + RecoveryEngine
    ▼
OTel Collector → SQLite (conduit.db)
    ▼
Dashboard (localhost:7432) — prescriptive recommendations
```

## REST API

```
GET  /api/v1/health
GET  /api/v1/failures
GET  /api/v1/failures/{event_id}
GET  /api/v1/failures/stream          # SSE
GET  /api/v1/recommendations
GET  /api/v1/tools
GET  /api/v1/tools/{tool_id}
GET  /api/v1/schemas
GET  /api/v1/schemas/{tool_id}
POST /api/v1/schemas/{tool_id}/accept_drift
GET  /api/v1/analytics/failure_rate
GET  /api/v1/analytics/recovery_rate
```

## License

MIT — core shim, adapters, schema validator, loop detector.
