# Conduit — Interception Shim Specification

**Document:** 02_INTERCEPTION_SHIM.md  
**Depends on:** 01_ARCHITECTURE.md  

---

## 1. Purpose

The interception shim is the only part of Conduit that the user installs inside their agent process. Everything else (intelligence plane, schema registry, dashboard) runs alongside or is hosted. The shim must therefore be:

- **Tiny** — < 500 lines of core logic, no heavy dependencies
- **Safe** — fails open (pass-through) if the intelligence plane is unavailable
- **Standard** — uses only OTel GenAI semantic conventions
- **Invisible** — adds < 5ms latency on the synchronous path; async events never block execution

---

## 2. Installation Contract

### 2-line install (LangGraph example)
```python
from conduit import ConduitShim
shim = ConduitShim.for_langgraph(graph)  # wraps the graph's OTel processor chain
```

### 2-line install (OpenAI SDK example)
```python
from conduit import ConduitShim
ConduitShim.for_openai_sdk()  # registers as a global trace processor
```

### 2-line install (generic OTel)
```python
from conduit import ConduitProcessor
tracer_provider.add_span_processor(ConduitProcessor())
```

The shim auto-detects the installed framework from the environment if not specified. Falls back to the generic OTel processor.

---

## 3. Hook Architecture

The shim operates at three hook points on every tool call and model call:

### 3.1 PRE_TOOL hook
Fires **before** tool parameters are sent to the tool function.

**Responsibilities:**
- Call Schema Validator (sync, < 3ms)
- Call Tool Router for routing suggestion (sync, < 2ms, v0.2+)
- Mutate parameters if validator returns corrections (auto-correct mode)
- Emit `gen_ai.execute_tool` span with `conduit.hook_phase=pre`
- Gate execution if validator returns `decision=gate` and hard-gate mode is enabled

**Must not:**
- Block execution if intelligence plane timeout exceeds 5ms (fall through)
- Raise exceptions that propagate to the agent code
- Log parameter values unless `CONDUIT_LOG_PAYLOADS=true` is set

### 3.2 AROUND_TOOL hook
Wraps tool execution in a context manager. Captures:
- Wall-clock execution time
- Unhandled exceptions (including framework-fatal crashes)
- Timeout events (configurable per-tool timeout override)
- Partial results (tools that stream)

Feeds real-time span data to the Failure Detector's stream analyzer.

### 3.3 POST_TOOL hook
Fires **after** tool result returns (or after exception is caught).

**Responsibilities:**
- Classify outcome: `success | schema_error | tool_error | timeout | partial`
- Call Failure Detector (async — does not block)
- Call Recovery Engine if outcome is not `success` (async — may inject recovery context into agent)
- Emit `gen_ai.execute_tool` span with `conduit.hook_phase=post` and full outcome
- Send ToolCallEvent to Failure Pattern Store (async, batched)
- Feed routing signal to Router (async, v0.2+)

### 3.4 PRE_MODEL hook
Fires **before** the LLM call is sent.

**Responsibilities:**
- Capture context size (token count estimate)
- Run Context Engine if token budget threshold is exceeded (v0.2+)
- Capture baseline task goal embedding for planning divergence detection (v0.2+)
- Emit `gen_ai.chat` span start

### 3.5 POST_MODEL hook
Fires **after** LLM response returns.

**Responsibilities:**
- Capture response token count
- Compute planning divergence delta (v0.2+)
- Detect planning divergence escalation (v0.2+)
- Emit `gen_ai.chat` span completion with token usage

### 3.6 AGENT lifecycle hooks
| Hook | Trigger | Purpose |
|------|---------|---------|
| `on_agent_start` | Agent begins processing a task | Capture task goal, initialize loop state |
| `on_agent_checkpoint` | LangGraph checkpoint, CrewAI task output | Snapshot state for recovery replay |
| `on_agent_error` | Unhandled exception in agent code | Capture full stack, classify, alert |
| `on_agent_end` | Task complete (success or failure) | Send final outcome signal, flush batched events |

---

## 4. OTel Span Schema

All Conduit-emitted spans follow OTel GenAI semantic conventions with Conduit-specific extensions prefixed `conduit.*`.

### 4.1 Tool execution span (`gen_ai.execute_tool`)

**Standard attributes (OTel GenAI):**
```
gen_ai.operation.name = "execute_tool"
gen_ai.tool.name       = <string>           # canonical tool ID
gen_ai.system          = <string>           # "langgraph" | "crewai" | "openai_sdk" | etc.
```

**Conduit extensions:**
```
conduit.hook_phase              = "pre" | "around" | "post"
conduit.tool.version            = <string>    # schema version at call time
conduit.tool.step_index         = <int>       # position in agent execution sequence
conduit.validation.result       = "pass" | "corrected" | "gated" | "skipped"
conduit.validation.corrections  = <JSON[]>    # list of {field, from, to, reason}
conduit.validation.errors       = <JSON[]>    # list of {field, error_type, message}
conduit.failure.class           = "none" | "schema_error" | "tool_error" | "timeout" | "agent_loop"
conduit.failure.severity        = "low" | "medium" | "high" | "critical"
conduit.recovery.action         = "none" | "retry" | "reroute" | "replan" | "escalate" | "degrade"
conduit.recovery.attempt        = <int>       # which retry attempt (0 = first)
conduit.latency.validation_ms   = <float>     # time spent in validator
conduit.latency.routing_ms      = <float>     # time spent in router (v0.2)
```

**Events on span (not attributes — avoids indexing large payloads):**
```
Event: "conduit.params.pre"    # params as submitted by agent (opt-in only)
Event: "conduit.params.post"   # corrected params (opt-in only)
Event: "conduit.result"        # tool result summary (opt-in only)
Event: "conduit.recovery_context" # what was injected into agent on recovery
```

### 4.2 Model call span (`gen_ai.chat`)

**Standard attributes:**
```
gen_ai.operation.name   = "chat"
gen_ai.request.model    = <string>
gen_ai.usage.input_tokens  = <int>
gen_ai.usage.output_tokens = <int>
gen_ai.response.finish_reason = "stop" | "length" | "tool_calls" | "error"
```

**Conduit extensions:**
```
conduit.context.token_count    = <int>    # tokens in context at call time
conduit.context.budget_pct     = <float>  # % of context budget used
conduit.context.pruned         = <bool>   # whether context engine ran (v0.2)
conduit.planning.divergence    = <float>  # 0.0–1.0 drift from task goal (v0.2)
```

### 4.3 Agent lifecycle span (`gen_ai.invoke_agent`)

```
gen_ai.operation.name   = "invoke_agent"
conduit.agent.task_id   = <string>    # stable ID for this task run
conduit.agent.step_count = <int>      # total steps at end
conduit.agent.loop_count = <int>      # how many loops detected
conduit.agent.outcome   = "success" | "failure" | "partial" | "escalated"
```

---

## 5. Recovery Context Injection

When the Recovery Engine decides to inject a replan message into the agent, the shim handles the injection differently per framework:

### LangGraph
Injects a `HumanMessage` into the graph's state at the current node, carrying the recovery context. The graph naturally processes this on the next step.

### OpenAI Agents SDK
Injects a tool result message with role `tool` and content set to the recovery context. The SDK treats this as the tool's response.

### CrewAI
Injects a task observation via the agent's memory system. The agent includes it in its next reasoning step.

### Generic
Emits a `conduit.recovery_injection` span event. The agent code can optionally register a `recovery_callback` that receives the injection and handles it in a framework-specific way.

---

## 6. Configuration

All configuration via environment variables or a `conduit.yaml` file in the project root.

```yaml
# conduit.yaml

shim:
  mode: "in_process"           # in_process | sidecar | gateway
  intelligence_plane_url: null  # null = in-process; URL for sidecar
  timeout_ms: 5                 # Max time to wait for sync intelligence plane response
  fallback: "pass_through"      # pass_through | gate | log_only
  log_payloads: false           # NEVER set true in production (privacy)

validation:
  hard_gate: false              # If true, gate tool calls that fail validation (risky)
  auto_correct: true            # Attempt auto-correction before gating
  correction_types:             # Which corrections are allowed automatically
    - type_coerce               # string "10" → int 10
    - field_rename              # known deprecated field → current field
    - optional_strip            # strip unknown optional fields

detection:
  loop_threshold: 3             # N identical calls = loop
  loop_window: 10               # Look back N steps for loop detection
  timeout_ms: 30000             # Tool call timeout (30s default)

recovery:
  enabled: true
  actions:                      # Which actions are permitted
    - retry
    - replan
    - escalate
  max_retries: 2
  escalation_webhook: null      # POST to this URL on escalation

telemetry:
  otel_endpoint: "http://localhost:4317"  # Local collector
  service_name: "my-agent"
  sample_rate: 1.0
  forward_to: []               # Forward spans to these additional endpoints

registry:
  db_path: "./conduit.db"       # SQLite path (v0.1)
  mcp_manifest_paths: []        # Paths to MCP server manifests to auto-ingest

dashboard:
  enabled: true
  port: 7432
  host: "127.0.0.1"
```

---

## 7. Failure Modes and Graceful Degradation

| Failure Mode | Behaviour |
|-------------|-----------|
| Intelligence plane timeout (> 5ms) | Pass-through: tool call proceeds unmodified. Span emitted with `conduit.validation.result=skipped`. |
| Intelligence plane down | Pass-through for all calls. Spans still emitted (tool name, outcome) — no validation or recovery. Dashboard shows "intelligence plane offline". |
| Schema registry empty (no schemas registered) | Validation skipped for unknown tools. Loop detection still runs (no schema dependency). |
| OTel collector down | Spans buffered in memory (configurable max 1MB). Dropped if buffer full. Agent execution unaffected. |
| Recovery injection fails | Span event records failure. Agent continues without recovery context. No exception propagated. |

---

## 8. Testing the Shim

### Unit test contract
Every hook function must be testable in isolation without a real agent, framework, or intelligence plane. The intelligence plane should be mockable via a simple interface.

### Integration test scenarios (required)
1. **Schema validator pass-through** — valid params → tool executes normally → span with `validation.result=pass`
2. **Schema validator auto-correct** — `max_results: "10"` → corrected to `max_results: 10` → tool executes → span shows correction
3. **Schema validator gate** — structurally invalid params in hard-gate mode → tool blocked → `decision=gate` span emitted
4. **Loop detection** — 3 identical tool calls → loop detected → recovery context injected → agent changes approach
5. **Intelligence plane timeout** — plane takes > 5ms → pass-through → tool executes normally → no exception
6. **Intelligence plane down** — plane unreachable → all calls pass-through → spans still emitted with `validation.result=skipped`

### Performance test contract
- 1,000 pass-through tool calls (plane healthy, all pass): p99 overhead < 3ms
- 1,000 calls with auto-correction: p99 overhead < 6ms
- 1,000 calls with plane down: p99 overhead < 0.5ms (pass-through only)
