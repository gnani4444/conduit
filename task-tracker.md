# Conduit — Build Task Tracker

**Product:** Conduit — AI Agent Observability & Shim  
**Version:** v0.1  
**Started:** 2026-05-11  
**Design docs:** `Design/00_PRD_MASTER.md` through `Design/07_IMPLEMENTATION_GUIDE.md`

---

## Progress: 8 phases + 11 gap-fill tasks — ALL COMPLETE ✅

**Tests:** 38/38 passing

---

## Phase 0: Project Skeleton ✅
- [x] `pyproject.toml` with all dependencies (opentelemetry, fastapi, jsonschema, typer, rich…)
- [x] All package directories + `__init__.py` files
- [x] `conduit.yaml` minimal config
- [x] `python -c "from conduit.shim.processor import ConduitProcessor"` works

## Phase 1: OTel Span Emitter ✅ — `02_INTERCEPTION_SHIM.md`
- [x] `conduit/telemetry/spans.py` — all OTel GenAI + `conduit.*` attribute constants
- [x] `conduit/shim/processor.py` — `ConduitProcessor(SpanProcessor)` — wired to validator, detector, recovery
- [x] `conduit/shim/hooks.py` — `pre_tool_hook`, `post_tool_hook`, `on_agent_start`, `on_agent_end`
- [x] Unit tests: intercept, ignore non-tool spans, never-raise invariant

## Phase 2: Schema Registry + Validator ✅ — `03_SCHEMA_VALIDATOR.md`
- [x] `conduit/registry/store.py` — `SchemaRegistry` (SQLite + in-memory cache, TTL 60s)
- [x] `conduit/registry/drift.py` — drift detection helpers
- [x] `conduit/registry/mcp.py` — MCP manifest ingestion + `CLAUDE_MCP_SERVERS` auto-discovery
- [x] `conduit/intelligence/validator.py` — `SchemaValidator`: type coerce, field rename, optional strip, hard/soft gate
- [x] Unit tests: 8/8 passing (type coerce, alias rename, strip, hard gate, soft gate, skipped)

## Phase 3: Failure Detector + Recovery Engine ✅ — `04_FAILURE_DETECTOR.md`
- [x] `conduit/store/events.py` — `ToolCallEvent` + SQLite persistence
- [x] `conduit/store/analyzer.py` — `FailurePatternAnalyzer` (all 7 queries from §6)
- [x] `conduit/intelligence/detector.py` — `ToolFailureDetector` + `AgentLoopDetector` (identical + cycling)
- [x] `conduit/intelligence/recovery.py` — `RecoveryEngine` (action matrix, replan templates, idempotency)
- [x] `conduit/intelligence/router.py` — v0.2 stub (returns None, interface defined)
- [x] Unit tests: 9/9 detector + 9/9 recovery passing

## Phase 4: Telemetry Pipeline ✅ — `05_TELEMETRY_AND_ADAPTERS.md §1-3`
- [x] `conduit/telemetry/collector.py` — `OtelCollector` (launches otelcol) + `/ingest` HTTP endpoint
- [x] `conduit/config.py` — loads `conduit.yaml` + env vars (`CONDUIT_DB_PATH`, `CONDUIT_LOG_PAYLOADS`, etc.)

## Phase 5: Framework Adapters ✅ — `05_TELEMETRY_AND_ADAPTERS.md §4`
- [x] `conduit/shim/adapters/langgraph.py` — `install_for_langgraph()` (2-line install)
- [x] `conduit/shim/adapters/openai_sdk.py` — `install_for_openai_sdk()` (2-line install)
- [x] `conduit/shim/adapters/crewai.py` — `install_for_crewai()` (event hooks + legacy fallback)

## Phase 6: Dashboard ✅ — `06_DASHBOARD.md`
- [x] `conduit/dashboard/app.py` — FastAPI app
- [x] HTML views: `/` (Command Center), `/failures`, `/schemas`, `/failures/{id}`, `/recommendations`, `/tools`
- [x] REST API: all 12 endpoints from `06_DASHBOARD.md §5`
  - `GET /api/v1/health`
  - `GET /api/v1/failures` (filterable)
  - `GET /api/v1/failures/{event_id}`
  - `GET /api/v1/failures/stream` (SSE)
  - `GET /api/v1/recommendations`
  - `POST /api/v1/recommendations/{id}/dismiss`
  - `GET /api/v1/tools`, `GET /api/v1/tools/{tool_id}`
  - `GET /api/v1/schemas`, `GET /api/v1/schemas/{tool_id}`
  - `POST /api/v1/schemas/{tool_id}/accept_drift`
  - `GET /api/v1/analytics/failure_rate`
  - `GET /api/v1/analytics/recovery_rate`
- [x] `conduit/cli.py` — `dashboard`, `stream`, `recommend`, `schema list/validate/update/discover`, `fix`

## Phase 7: Integration + E2E Tests ✅ — `07_IMPLEMENTATION_GUIDE.md §7`
- [x] `tests/unit/` — 30 unit tests (processor, validator, detector, recovery)
- [x] `tests/integration/test_full_flow.py` — 8 integration tests (full pipeline, per-trace isolation, timeout passthrough)
- [x] `tests/e2e/test_langgraph_e2e.py` — stubs (require real framework install)

---

## Gap-Fill Tasks (from design doc review) ✅
- [x] Wire validator+detector+recovery into `ConduitProcessor` (was stubs)
- [x] `registry/mcp.py` — MCP manifest ingestion (`03_SCHEMA_VALIDATOR.md §2.2`)
- [x] `intelligence/router.py` — v0.2 stub (`01_ARCHITECTURE.md §3`)
- [x] `store/analyzer.py` — `FailurePatternAnalyzer` (`04_FAILURE_DETECTOR.md §6`)
- [x] Full `/api/v1/*` REST API (`06_DASHBOARD.md §5`)
- [x] `RecommendationEngine` in analyzer (`06_DASHBOARD.md §6`)
- [x] CLI schema commands: `validate`, `update`, `discover`, `list`, `fix` (`06_DASHBOARD.md §4`)
- [x] `examples/langgraph_example.py`, `examples/openai_sdk_example.py`
- [x] `tests/integration/test_full_flow.py`
- [x] `README.md` with quick-start
- [x] `docker-compose.yml` for sidecar topology (`01_ARCHITECTURE.md §6`)

---

## Key Invariants (All Verified) ✅
1. Shim never raises into agent code — `try/except` on every hook ✅
2. Shim never blocks — timeout guard in `_run_pre`, pass-through on timeout ✅
3. No payload logging without `CONDUIT_LOG_PAYLOADS=true` — hashes only ✅
4. Loop detector is per-trace-id — `test_loop_detector_per_trace_isolation` ✅
5. Recovery injection is idempotent — `test_idempotency_second_injection_skipped` ✅
6. Schema registry is the authority in hard-gate mode ✅

---

## Quick Start
```bash
pip install -e ".[dev]"
conduit dashboard          # http://127.0.0.1:7432
conduit stream             # live failure stream
conduit recommend          # prescriptive recommendations
conduit schema list        # registered schemas
```

## Status Legend
- [x] Complete  - [ ] Not started  - [~] In progress
