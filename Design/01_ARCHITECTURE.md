# Conduit — System Architecture

**Document:** 01_ARCHITECTURE.md  
**Depends on:** 00_PRD_MASTER.md  

---

## 1. Architectural Principle

**Protocol-first, not framework-first.**

Conduit does not integrate with LangGraph's internals, CrewAI's internals, or OpenAI SDK's internals. It hooks into the OpenTelemetry (OTel) GenAI semantic convention — a standard that every major framework either already emits or is converging toward. This means:

- One shim, any framework that speaks OTel
- Framework API changes do not break Conduit
- New frameworks are supported by the community writing OTel exporters, not by Conduit writing adapters

---

## 2. System Layers

### Layer 1 — Agent Application (existing code, unmodified)
The user's existing agent code. LangGraph graph, CrewAI crew, OpenAI Agents SDK flow, or Mastra workflow. **Conduit requires zero changes here.**

### Layer 2 — Interception Shim (open source)
A thin Python package (and TypeScript package for Mastra) that:
- Registers as an OTel span processor in the agent's existing telemetry pipeline
- Intercepts `gen_ai.execute_tool` spans before and after execution
- Intercepts `gen_ai.invoke_agent` and model call spans
- Calls the Intelligence Plane for routing and validation decisions (synchronous, < 5ms)
- Emits enriched spans downstream

The shim is **additive only** — it never modifies framework internals, never blocks if unavailable, and degrades gracefully.

### Layer 3 — Intelligence Plane (open source core + SaaS intelligence)
Four modules called by the shim on every intercepted event:

| Module | Input | Output | Sync/Async | v0.1? |
|--------|-------|--------|-----------|-------|
| Tool Router | Intent string + tool registry | Ranked tool list + confidence | Sync < 5ms | No (v0.2) |
| Schema Validator | Tool ID + parameters | Valid/invalid + corrections | Sync < 3ms | Yes |
| Failure Detector | Span stream | Failure classification + severity | Async stream | Yes |
| Context Engine | Context buffer + token budget | Pruned context | Sync < 10ms | No (v0.2) |

### Layer 4 — Telemetry Backbone
An OTel collector pipeline that:
- Receives spans from the shim
- Applies processors (redaction, sampling, enrichment)
- Routes to the data plane (schema registry, failure store)
- Routes to downstream observability tools (LangSmith, Logfire, Datadog — user's existing tools)

Conduit does not replace the user's existing observability. It augments it.

### Layer 5 — Data Plane
Three persistent stores:

| Store | Contents | v0.1? |
|-------|---------|-------|
| Schema Registry | Tool schemas, version history, drift events | Yes |
| Failure Pattern Store | ToolCallEvents, failure classifications, recovery outcomes | Yes (local) |
| Routing Model Store | Embeddings, training data, model weights | No (v0.2) |

### Layer 6 — Prescriptive Dashboard
A local web UI (v0.1) evolving to SaaS. See `06_DASHBOARD.md`.

---

## 3. Data Flow — Single Tool Call

```
Agent decides to call tool "search_web" with params {query: "...", max_results: 10}
    │
    ▼
[SHIM: PRE-TOOL HOOK fires]
    │── Tool Router: "is search_web the best tool for this intent?" (v0.2)
    │── Schema Validator: "are these params valid against search_web v2.3 schema?"
    │        ├── PASS → continue
    │        └── FAIL → attempt auto-correction or gate execution
    │── Emits: gen_ai.execute_tool span (status=pre_execution)
    │
    ▼
Tool executes (search_web API call)
    │
    ▼
[SHIM: POST-TOOL HOOK fires]
    │── Result captured (success | error | timeout)
    │── Failure Detector: classify outcome
    │        ├── success → emit span, update routing signal (v0.2)
    │        ├── schema_error → Recovery Engine: retry with corrected params
    │        ├── tool_error → Recovery Engine: retry or reroute
    │        ├── timeout → Recovery Engine: escalate or degrade
    │        └── loop_detected → Recovery Engine: inject replan or escalate
    │── Emits: gen_ai.execute_tool span (status=post_execution, outcome=...)
    │
    ▼
[OTel COLLECTOR receives span]
    │── Schema Registry: update drift signals
    │── Failure Pattern Store: log ToolCallEvent
    │── Forward to user's existing observability (LangSmith, etc.)
    │
    ▼
[DASHBOARD] receives enriched event stream
    │── Updates failure timeline
    │── Updates schema drift alerts
    │── Surfaces prescriptive recovery recommendations
```

---

## 4. Data Flow — Loop Detection

```
Agent tool call #1: search_web({query: "X"}) → error
Agent tool call #2: search_web({query: "X"}) → error   ← same signature
Agent tool call #3: search_web({query: "X"}) → error   ← same signature

[SHIM: AROUND-TOOL HOOK]
    │── Failure Detector receives span stream
    │── Detects: 3 identical call signatures within N=5 steps
    │── No state progression between calls
    │── Classification: AGENT_LOOP
    │── Severity: HIGH
    │
    ▼
[RECOVERY ENGINE]
    │── Action: INJECT_REPLAN
    │── Inject message to agent: "Previous tool calls failed 3 times with identical
    │   parameters. Consider: (1) modifying the query, (2) using an alternative tool,
    │   (3) returning a partial result. Current failure reason: [schema_error: field
    │   'max_results' expects integer, received string]"
    │
    ▼
Agent receives injected context → adapts approach
```

---

## 5. Component Contracts

### 5.1 Shim ↔ Intelligence Plane

The shim calls the intelligence plane via a local gRPC channel (in-process mode) or HTTP (sidecar mode).

**Request (pre-tool):**
```json
{
  "event_type": "pre_tool",
  "trace_id": "abc123",
  "span_id": "def456",
  "tool_id": "search_web",
  "tool_version": "2.3.0",
  "params": { "query": "...", "max_results": "10" },
  "intent_text": "search for recent news about X",
  "step_index": 4,
  "prior_outcomes": ["success", "success", "schema_error"]
}
```

**Response:**
```json
{
  "decision": "gate" | "pass" | "correct" | "reroute",
  "corrected_params": { "query": "...", "max_results": 10 },
  "corrections_applied": ["type_coerce: max_results string→int"],
  "routing_suggestion": null,
  "validation_errors": [],
  "latency_ms": 2.1
}
```

### 5.2 Shim ↔ OTel Collector

The shim emits standard OTel spans using `gen_ai.*` semantic conventions. Every span carries:

```
gen_ai.system: "conduit"
gen_ai.operation.name: "execute_tool" | "invoke_agent" | "create_agent"
gen_ai.tool.name: <tool_id>
conduit.hook_phase: "pre" | "around" | "post"
conduit.validation.result: "pass" | "corrected" | "gated"
conduit.failure.class: "tool_failure" | "schema_error" | "agent_loop" | "timeout" | null
conduit.recovery.action: "retry" | "reroute" | "replan" | "escalate" | "degrade" | null
```

### 5.3 Intelligence Plane ↔ Schema Registry

**Lookup:**
```
GET /schemas/{tool_id}/current
→ { schema_version, json_schema, known_aliases, last_updated }
```

**Drift report:**
```
POST /schemas/{tool_id}/drift
{ observed_params, expected_schema, tool_version, trace_id }
→ { drift_id, auto_correctable, correction_map }
```

---

## 6. Deployment Topologies

### Topology A — In-Process (default, v0.1)
```
┌────────────────────────────────────────┐
│           Agent process                │
│  ┌──────────────┐  ┌─────────────────┐ │
│  │  Agent code  │  │  Conduit shim   │ │
│  │  (LangGraph) │◄─►  + Intel plane  │ │
│  └──────────────┘  └────────┬────────┘ │
│                             │ OTel     │
└─────────────────────────────┼──────────┘
                              ▼
                     OTel Collector (local)
                              ▼
                     Dashboard (localhost:4317)
```
Zero network hops on the critical path. Ideal for development and single-process deployments.

### Topology B — Sidecar (v0.2)
```
┌──────────────────┐     gRPC      ┌─────────────────────┐
│  Agent process   │◄─────────────►│  Conduit sidecar    │
│  (any framework) │               │  Intelligence plane  │
└──────────────────┘               │  Schema registry     │
         │ OTel                    │  Failure store       │
         ▼                        └─────────────────────────┘
   OTel Collector                           │
         │                                  │
         └──────────────────────────────────┘
                         ▼
                   Dashboard (SaaS or local)
```
For multi-process and containerised deployments.

### Topology C — Gateway (enterprise, v1.0)
Network-layer interception at infrastructure boundary. All agent-to-tool traffic routes through Conduit gateway. Maximum governance. Highest latency. AWS-style infrastructure deployment.

---

## 7. Technology Stack (Recommended)

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Shim (Python) | Python 3.11+, `opentelemetry-sdk`, `opentelemetry-api` | Matches agent ecosystem |
| Shim (TypeScript) | Node 20+, `@opentelemetry/sdk-node` | For Mastra / TypeScript agents |
| Intelligence plane | Python, FastAPI, async | Fast, familiar to AI engineers |
| Schema validation | `jsonschema`, `pydantic` v2 | Industry standard |
| OTel collector | OpenTelemetry Collector (otelcol) | Vendor-neutral |
| Local data store | SQLite (v0.1) → PostgreSQL (v0.2) | Zero-dependency start |
| Schema registry | SQLite + JSON storage (v0.1) | Simple, upgrade path clear |
| Dashboard | FastAPI + HTMX (v0.1) | Minimal JS, server-rendered, fast |
| Embeddings (v0.2) | `sentence-transformers`, local model | No external API dependency |
| gRPC (sidecar) | `grpcio`, `protobuf` | Low latency IPC |

---

## 8. Repository Structure

```
conduit/
├── conduit/                    # Main Python package
│   ├── shim/                   # Interception shim
│   │   ├── __init__.py
│   │   ├── processor.py        # OTel span processor (core shim logic)
│   │   ├── hooks.py            # Pre/around/post hook implementations
│   │   └── adapters/           # Framework-specific adapters
│   │       ├── langgraph.py
│   │       ├── openai_sdk.py
│   │       └── crewai.py
│   ├── intelligence/           # Intelligence plane modules
│   │   ├── __init__.py
│   │   ├── validator.py        # Schema validator
│   │   ├── detector.py         # Failure detector
│   │   ├── recovery.py         # Recovery action engine
│   │   └── router.py           # Tool router (v0.2 stub)
│   ├── registry/               # Schema registry
│   │   ├── __init__.py
│   │   ├── store.py            # Schema storage (SQLite)
│   │   ├── drift.py            # Drift detection logic
│   │   └── mcp.py              # MCP manifest ingestion
│   ├── telemetry/              # OTel pipeline
│   │   ├── __init__.py
│   │   ├── collector.py        # Local collector config
│   │   └── spans.py            # Span schema definitions
│   ├── store/                  # Failure pattern store
│   │   ├── __init__.py
│   │   └── events.py           # ToolCallEvent model + persistence
│   └── dashboard/              # Local dashboard
│       ├── __init__.py
│       ├── app.py              # FastAPI app
│       └── templates/          # HTMX templates
├── conduit-ts/                 # TypeScript shim (Mastra)
├── tests/
├── docs/
├── examples/
│   ├── langgraph_example.py
│   ├── openai_sdk_example.py
│   └── crewai_example.py
├── pyproject.toml
├── README.md
└── docker-compose.yml          # For sidecar topology
```

---

## 9. Integration Contract with Existing Frameworks

### What Conduit NEVER does
- Modifies framework source code
- Monkey-patches framework classes at import time
- Requires changes to user's agent logic
- Blocks tool execution if the intelligence plane is down
- Sends tool call parameter values to external servers without explicit opt-in

### What Conduit ALWAYS does
- Registers via the framework's official extension points (OTel processors, callbacks, middleware hooks)
- Falls back to pass-through if the intelligence plane is unavailable
- Uses only OTel GenAI standard span attributes — no proprietary formats
- Emits spans in the background — never on the critical execution path for async events
