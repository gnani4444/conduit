# Conduit — Implementation Guide for Claude Code

**Document:** 07_IMPLEMENTATION_GUIDE.md  
**Purpose:** Step-by-step build instructions. Read this first before touching any other file.  

---

## 0. Start Here

This document tells you **what to build, in what order, and how to know when each piece is done**. The other six documents are the detailed specs for each component. Refer to them as you work on each phase.

The golden rule: **always build the smallest thing that proves the concept works end-to-end first.** Don't implement all of the schema validator before testing that the shim can intercept a single tool call.

---

## 1. Development Environment Setup

### Prerequisites
```bash
# Python 3.11+ required
python --version   # must be >= 3.11

# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install otelcol (OpenTelemetry Collector)
# macOS
brew install opentelemetry-collector
# Linux
wget https://github.com/open-telemetry/opentelemetry-collector-releases/releases/latest/download/otelcol_linux_amd64.tar.gz
```

### Repository setup
```bash
git clone <your-repo>
cd conduit

# Create virtual environment and install dependencies
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Verify setup
python -c "import conduit; print('OK')"
pytest tests/ -x -q   # all should pass (initially just stubs)
```

### `pyproject.toml` (minimal start)
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "conduit"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "opentelemetry-api>=1.25.0",
    "opentelemetry-sdk>=1.25.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.25.0",
    "fastapi>=0.111.0",
    "uvicorn>=0.30.0",
    "pydantic>=2.7.0",
    "jsonschema>=4.22.0",
    "aiosqlite>=0.20.0",
    "httpx>=0.27.0",
    "typer>=0.12.0",       # CLI
    "rich>=13.7.0",        # CLI output
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "httpx>=0.27.0",       # Test client for FastAPI
    "ruff>=0.4.0",
    "mypy>=1.10.0",
]
langgraph = ["langgraph>=1.0.0", "langsmith>=0.1.0"]
openai = ["openai-agents>=0.0.14"]
crewai = ["crewai>=0.80.0"]

[project.scripts]
conduit = "conduit.cli:app"
```

---

## 2. Build Order — Phases

Build in this exact order. Each phase produces something testable before the next begins.

---

### Phase 0: Project Skeleton (Day 1)

Create all directories and stub files. Every module should be importable but functions raise `NotImplementedError`.

```bash
mkdir -p conduit/{shim/adapters,intelligence,registry,telemetry,store,dashboard/templates}
mkdir -p tests/{unit,integration,e2e}
touch conduit/__init__.py
touch conduit/shim/__init__.py conduit/shim/processor.py conduit/shim/hooks.py
touch conduit/shim/adapters/__init__.py conduit/shim/adapters/langgraph.py
touch conduit/shim/adapters/openai_sdk.py conduit/shim/adapters/crewai.py
touch conduit/intelligence/__init__.py conduit/intelligence/validator.py
touch conduit/intelligence/detector.py conduit/intelligence/recovery.py
touch conduit/registry/__init__.py conduit/registry/store.py conduit/registry/drift.py
touch conduit/telemetry/__init__.py conduit/telemetry/spans.py
touch conduit/store/__init__.py conduit/store/events.py
touch conduit/dashboard/__init__.py conduit/dashboard/app.py
touch conduit/cli.py conduit/config.py
```

**Done when:** `python -c "from conduit.shim.processor import ConduitProcessor"` works without error.

---

### Phase 1: OTel Span Emitter (Days 1–2)

Build the foundation that everything else depends on. The shim must be able to intercept a tool call and emit a correctly-structured OTel span.

**Build:**
1. `conduit/telemetry/spans.py` — define all span attribute constants
2. `conduit/shim/processor.py` — `ConduitProcessor` class implementing `SpanProcessor`
3. `conduit/shim/hooks.py` — `pre_tool_hook`, `post_tool_hook`, `on_agent_start`, `on_agent_end`

**ConduitProcessor skeleton:**
```python
from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter

class ConduitProcessor(SpanProcessor):
    """
    Core shim. Registered as an OTel SpanProcessor.
    Intercepts gen_ai.execute_tool spans before and after execution.
    """
    
    def on_start(self, span, parent_context=None):
        """Called when a span is started. Pre-hook fires here."""
        if self._is_tool_span(span):
            self._pre_tool_hook(span)
    
    def on_end(self, span: ReadableSpan):
        """Called when a span ends. Post-hook fires here."""
        if self._is_tool_span(span):
            self._post_tool_hook(span)
    
    def _is_tool_span(self, span) -> bool:
        op = span.attributes.get("gen_ai.operation.name", "")
        return op == "execute_tool"
    
    def _pre_tool_hook(self, span):
        # Phase 1: just emit — no validation yet
        tool_id = span.attributes.get("gen_ai.tool.name", "unknown")
        span.set_attribute("conduit.hook_phase", "pre")
        span.set_attribute("conduit.validation.result", "skipped")  # until validator built
    
    def _post_tool_hook(self, span: ReadableSpan):
        span.set_attribute("conduit.hook_phase", "post")
        # Phase 1: classify outcome from span status
        if span.status.status_code.name == "ERROR":
            span.set_attribute("conduit.failure.class", "tool_error")
        else:
            span.set_attribute("conduit.failure.class", "none")
    
    def shutdown(self): pass
    def force_flush(self, timeout_millis=30000): pass
```

**Test:**
```python
# tests/unit/test_processor.py
def test_processor_intercepts_tool_span():
    """ConduitProcessor.on_start fires for execute_tool spans."""
    processor = ConduitProcessor()
    span = make_mock_span(operation="execute_tool", tool_name="search_web")
    processor.on_start(span)
    assert span.attributes["conduit.hook_phase"] == "pre"

def test_processor_ignores_non_tool_spans():
    """ConduitProcessor does not fire for chat spans."""
    processor = ConduitProcessor()
    span = make_mock_span(operation="chat")
    processor.on_start(span)
    assert "conduit.hook_phase" not in span.attributes
```

**Done when:** Unit tests pass. Run a real LangGraph agent with `ConduitProcessor` registered; verify spans appear in OTel collector logs with `conduit.hook_phase=pre/post`.

---

### Phase 2: Schema Registry + Validator (Days 3–5)

Spec: `03_SCHEMA_VALIDATOR.md`

**Build order within Phase 2:**
1. `conduit/registry/store.py` — `SchemaRegistry` class with SQLite backing
   - `register(tool_id, schema, version)`
   - `get_current(tool_id) → SchemaSnapshot | None`
   - `report_drift(tool_id, observed_params, trace_id)`
2. `conduit/intelligence/validator.py` — `SchemaValidator` class
   - `validate(tool_id, params) → ValidationResult`
   - Auto-correction: type coerce, field rename, optional strip
3. Wire validator into `ConduitProcessor._pre_tool_hook()`

**Key test to pass before moving on:**
```python
def test_validator_type_coerce_str_to_int():
    registry = SchemaRegistry(":memory:")
    registry.register("search_web", {
        "type": "object",
        "properties": {"max_results": {"type": "integer"}},
        "required": ["max_results"]
    }, version="1.0")
    
    validator = SchemaValidator(registry)
    result = validator.validate("search_web", {"max_results": "10"})
    
    assert result.decision == "pass"  # corrected = still passes
    assert result.validation_result == "corrected"
    assert result.corrected_params["max_results"] == 10
    assert result.corrections[0].correction_type == "type_coerce.str_to_int"
```

**Done when:** All unit tests in `03_SCHEMA_VALIDATOR.md §8` pass. Manual test: register a schema, call a tool with wrong param type, verify correction appears in span attributes.

---

### Phase 3: Failure Detector + Recovery Engine (Days 6–9)

Spec: `04_FAILURE_DETECTOR.md`

**Build order within Phase 3:**
1. `conduit/store/events.py` — `ToolCallEvent` dataclass + SQLite persistence
2. `conduit/intelligence/detector.py` — `ToolFailureDetector` + `AgentLoopDetector`
3. `conduit/intelligence/recovery.py` — `RecoveryEngine` with action selection matrix
4. Wire detector + recovery into `ConduitProcessor._post_tool_hook()`

**Key test to pass before moving on:**
```python
def test_loop_detector_fires_at_threshold():
    detector = AgentLoopDetector(window_size=10, threshold=3)
    
    result1 = detector.check(make_span(tool="search_web", params={"q": "X"}))
    assert result1 is None  # call 1 — no loop
    
    result2 = detector.check(make_span(tool="search_web", params={"q": "X"}))
    assert result2 is None  # call 2 — not yet
    
    result3 = detector.check(make_span(tool="search_web", params={"q": "X"}))
    assert result3 is not None  # call 3 — LOOP DETECTED
    assert result3.failure_class == "agent_loop"
    assert result3.sub_type == "agent_loop.identical"

def test_loop_detector_resets_between_tasks():
    detector = AgentLoopDetector(threshold=3)
    detector.check(make_span(tool="search_web", params={"q": "X"}))
    detector.check(make_span(tool="search_web", params={"q": "X"}))
    detector.reset()  # new task
    detector.check(make_span(tool="search_web", params={"q": "X"}))
    detector.check(make_span(tool="search_web", params={"q": "X"}))
    result = detector.check(make_span(tool="search_web", params={"q": "X"}))
    assert result is not None  # loop counter reset → fires at 3 again
```

**Done when:** All unit tests in `04_FAILURE_DETECTOR.md §7` pass. End-to-end manual test: run a LangGraph agent that intentionally loops; verify loop detected, replan injected, agent adapts.

---

### Phase 4: Telemetry Pipeline (Days 10–11)

Spec: `05_TELEMETRY_AND_ADAPTERS.md §1–3`

**Build:**
1. `conduit/telemetry/collector.py` — generate and launch local `otelcol` with Conduit config
2. Internal HTTP endpoint at `localhost:7431/ingest` that receives spans from collector → writes to SQLite
3. `conduit/config.py` — load `conduit.yaml` + environment variables

**Done when:** Real LangGraph agent run → spans visible in `conduit.db` via `sqlite3 conduit.db "SELECT tool_id, outcome FROM tool_call_events"`.

---

### Phase 5: Framework Adapters (Days 12–14)

Spec: `05_TELEMETRY_AND_ADAPTERS.md §4`

Build adapters in this order (easiest first):
1. LangGraph adapter (`install_for_langgraph`)
2. OpenAI SDK adapter (`install_for_openai_sdk`)
3. CrewAI adapter (`install_for_crewai`) — optional for MVP if team doesn't use CrewAI

**Done when:** Each adapter has its integration test suite passing (see spec §6).

---

### Phase 6: Dashboard (Days 15–20)

Spec: `06_DASHBOARD.md`

Build views in this order (highest value first):
1. `GET /` — Command Center with health bar + live failure feed (SSE) + top 3 recommendations
2. `GET /failures` — Failure analysis table and timeline
3. `GET /schemas` — Schema inventory with drift alerts
4. `GET /failures/{event_id}` — Failure detail view
5. `GET /recommendations` — Full recommendation list
6. `GET /tools` — Tool performance table

**CLI (`conduit/cli.py`):**
```python
import typer
app = typer.Typer()

@app.command()
def dashboard(port: int = 7432):
    """Start the Conduit dashboard."""

@app.command()
def stream():
    """Live failure stream in terminal."""

@app.command()
def recommend():
    """Print current recommendations."""
```

**Done when:** `conduit dashboard` starts without errors. All 6 views render without errors. SSE stream updates in real-time when a test agent runs.

---

### Phase 7: Integration + E2E Tests (Days 21–25)

Write the end-to-end tests that prove the whole system works together.

**E2E test scenarios** (each requires a real framework installed):
```python
# tests/e2e/test_langgraph_e2e.py

def test_schema_validation_auto_correct_e2e():
    """
    Real LangGraph agent → Conduit intercepts → validator corrects type →
    tool executes successfully → span shows correction → event in DB.
    """

def test_loop_detection_e2e():
    """
    Real LangGraph agent → intentional loop → Conduit detects at call 3 →
    replan injected → agent adapts → success on next call.
    """

def test_passthrough_when_plane_down_e2e():
    """
    Conduit intelligence plane stopped → agent runs → all tool calls
    pass through → no agent exception → spans still emitted with skipped validation.
    """
```

---

## 3. Configuration Reference

`conduit.yaml` minimal working config:
```yaml
shim:
  mode: "in_process"
  timeout_ms: 5
  fallback: "pass_through"
  log_payloads: false

validation:
  hard_gate: false
  auto_correct: true
  correction_types: [type_coerce, field_rename, optional_strip]

detection:
  loop_threshold: 3
  loop_window: 10
  timeout_ms: 30000

recovery:
  enabled: true
  actions: [retry, replan, escalate]
  max_retries: 2

telemetry:
  otel_endpoint: "http://localhost:4317"
  service_name: "my-agent"

registry:
  db_path: "./conduit.db"

dashboard:
  enabled: true
  port: 7432
  host: "127.0.0.1"
```

---

## 4. Key Invariants (Never Break These)

1. **Shim never raises exceptions into agent code.** Every hook is wrapped in `try/except Exception`. On any internal error, log and pass through.

2. **Shim never blocks on timeout.** If intelligence plane takes > `timeout_ms`, pass through immediately. Use `asyncio.wait_for` or `concurrent.futures.TimeoutError`.

3. **No payload logging without opt-in.** Parameter values, model outputs, and tool results are never written to any store unless `CONDUIT_LOG_PAYLOADS=true`. Hashes only by default.

4. **Loop detector is per-task, not global.** Each trace_id gets its own `AgentLoopDetector` instance. Two agents running in parallel never interfere.

5. **Recovery injection is idempotent.** If the same recovery message would be injected twice, skip the second injection. Track by `(trace_id, step_index, failure_sub_type)`.

6. **Schema registry is the authority.** The validator never accepts parameters that violate the registered schema in hard-gate mode, regardless of what the agent or framework says.

---

## 5. Definition of Done — v0.1

Conduit v0.1 is ready for design partner release when:

- [ ] `pip install conduit` works (published to PyPI or TestPyPI)
- [ ] `conduit dashboard` starts and renders all 6 views
- [ ] LangGraph adapter: 2-line install, all integration tests passing
- [ ] OpenAI SDK adapter: 2-line install, all integration tests passing
- [ ] Schema validator: type coerce, field rename, optional strip — all passing
- [ ] Loop detector: identical loop and tool-cycling loop — both passing
- [ ] Tool failure classifier: all 5 sub-types correctly classified
- [ ] Recovery engine: retry, replan, escalate — all triggering correctly
- [ ] Failure Pattern Store: events persisting to SQLite, queries returning correct results
- [ ] Recommendation engine: generating at least 3 recommendation types correctly
- [ ] Shim graceful degradation: all 6 failure modes in `02_INTERCEPTION_SHIM.md §7` tested
- [ ] Performance: p99 shim overhead < 5ms (with plane healthy)
- [ ] Performance: p99 shim overhead < 0.5ms (with plane down, pass-through)
- [ ] README with quick-start guide (< 5 minutes to first value)
- [ ] 0 known data-exfiltration paths without explicit opt-in

---

## 6. What NOT to Build in v0.1

Resist the temptation to build these. They require data that doesn't exist yet:

- Semantic tool routing (needs training data from failure store)
- Planning divergence detection (needs embedding infrastructure + ML)
- Dynamic context compression (needs usage data to tune)
- Cross-customer failure pattern DB (needs multiple customers)
- TypeScript/Mastra adapter (build when you have a TypeScript design partner)
- Enterprise self-hosted mode (build at Series A)

---

## 7. Quick Reference — Document Index

| Document | Read when you're building... |
|---------|----------------------------|
| `00_PRD_MASTER.md` | Starting — understand the product |
| `01_ARCHITECTURE.md` | Setting up the project structure and data flow |
| `02_INTERCEPTION_SHIM.md` | Phase 1: the span processor and hooks |
| `03_SCHEMA_VALIDATOR.md` | Phase 2: schema registry and validation |
| `04_FAILURE_DETECTOR.md` | Phase 3: loop detection and recovery |
| `05_TELEMETRY_AND_ADAPTERS.md` | Phase 4–5: OTel pipeline and framework adapters |
| `06_DASHBOARD.md` | Phase 6: the web dashboard |
| `07_IMPLEMENTATION_GUIDE.md` | This file — always open |
