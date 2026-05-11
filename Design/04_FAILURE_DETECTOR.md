# Conduit — Failure Detector and Recovery Engine

**Document:** 04_FAILURE_DETECTOR.md  
**Depends on:** 01_ARCHITECTURE.md, 02_INTERCEPTION_SHIM.md  

---

## 1. Problem Being Solved

Agent failures in production are invisible until they manifest as a user complaint or a hung process. Existing observability tools (LangSmith, Logfire) show you the trace of what happened. They do not:
- Detect failure patterns mid-execution before the agent halts
- Classify the failure by type so the right recovery action can be applied
- Inject recovery context into the agent to allow self-correction
- Log the failure with enough structured metadata to prevent future occurrences

The Failure Detector is a real-time stream processor over the OTel span stream. It classifies failures, determines severity, selects a recovery action, and hands off to the Recovery Engine — all before the user reports a problem.

---

## 2. Failure Taxonomy

### Class 1: Tool Failure
**Definition:** The tool call was executed but returned an error, exception, or empty result.  
**Sub-types:**
- `tool_error.execution` — tool raised an unhandled exception
- `tool_error.timeout` — tool exceeded the configured timeout
- `tool_error.empty_result` — tool returned successfully but result was null/empty when non-empty was expected
- `tool_error.rate_limit` — tool returned 429 or equivalent
- `tool_error.auth` — tool returned 401/403

**Detection:** Post-tool hook captures exception or result. Classified immediately.  
**Latency to detect:** < 1ms (synchronous post-hook).

### Class 2: Schema Error
**Definition:** The tool call parameters did not conform to the registered schema.  
**Sub-types:**
- `schema_error.type_mismatch` — wrong type for a field
- `schema_error.required_missing` — required field absent
- `schema_error.drift` — schema changed since agent was last updated
- `schema_error.unknown_field` — field not in schema

**Detection:** Schema Validator (pre-hook) catches most cases. Post-hook catches uncaught schema violations that the tool handles internally with a bad response.  
**Latency to detect:** < 3ms (synchronous pre-hook).

### Class 3: Agent Loop
**Definition:** The agent is making repeated identical or near-identical tool calls without state progression.  
**Sub-types:**
- `agent_loop.identical` — exact same tool + exact same params, N times
- `agent_loop.semantic` — same tool + semantically similar params (embedding distance < threshold)
- `agent_loop.tool_cycling` — agent cycles between 2–3 tools without resolving (A→B→A→B...)

**Detection:** Span stream analyzer maintains a rolling window of recent calls. Checked on every post-hook.  
**Latency to detect:** Typically at call N+1 after threshold is exceeded (configurable, default N=3).

### Class 4: Planning Failure (v0.2)
**Definition:** The agent's execution path has diverged from the original task goal.  
**Sub-types:**
- `planning.divergence` — embedding distance between current state and task goal exceeds threshold
- `planning.dead_end` — agent has exhausted all available tools without resolution
- `planning.contradition` — agent's current plan contradicts a prior confirmed step

**Detection:** Requires embedding comparison between task goal (captured at `on_agent_start`) and current agent state (captured at each `post_model` hook). Not implemented in v0.1.

---

## 3. Detection Algorithms

### 3.1 Tool Failure Detection (v0.1)
```python
class ToolFailureDetector:
    def classify(self, span: ToolCallSpan) -> FailureClassification | None:
        if span.outcome == "success":
            return None
        
        if span.exception_type is not None:
            if "Timeout" in span.exception_type:
                return FailureClassification(
                    failure_class="tool_error",
                    sub_type="tool_error.timeout",
                    severity="high",
                    evidence={"exception": span.exception_type, "latency_ms": span.latency_ms}
                )
            if "Auth" in span.exception_type or span.http_status in (401, 403):
                return FailureClassification(failure_class="tool_error", sub_type="tool_error.auth", severity="critical")
            if span.http_status == 429:
                return FailureClassification(failure_class="tool_error", sub_type="tool_error.rate_limit", severity="medium")
            return FailureClassification(failure_class="tool_error", sub_type="tool_error.execution", severity="high",
                                         evidence={"exception": span.exception_type, "message": span.exception_message})
        
        if span.result is None or span.result == "" or span.result == []:
            return FailureClassification(failure_class="tool_error", sub_type="tool_error.empty_result", severity="low")
        
        return None
```

### 3.2 Agent Loop Detection (v0.1)
```python
class AgentLoopDetector:
    def __init__(self, window_size=10, threshold=3):
        self.window_size = window_size   # Look at last N calls
        self.threshold = threshold       # N identical calls = loop
        self._call_history: deque = deque(maxlen=window_size)
    
    def check(self, span: ToolCallSpan) -> FailureClassification | None:
        # Create call signature: tool_id + params hash (stable hash, not Python hash())
        call_sig = f"{span.tool_id}:{stable_hash(span.params)}"
        self._call_history.append(call_sig)
        
        # Count occurrences of this signature in the window
        count = sum(1 for s in self._call_history if s == call_sig)
        
        if count >= self.threshold:
            return FailureClassification(
                failure_class="agent_loop",
                sub_type="agent_loop.identical",
                severity="high",
                evidence={
                    "call_signature": call_sig,
                    "count_in_window": count,
                    "window_size": self.window_size
                }
            )
        
        # Check tool cycling (A→B→A→B pattern)
        if len(self._call_history) >= 4:
            recent = list(self._call_history)[-4:]
            if recent[0] == recent[2] and recent[1] == recent[3] and recent[0] != recent[1]:
                return FailureClassification(
                    failure_class="agent_loop",
                    sub_type="agent_loop.tool_cycling",
                    severity="medium",
                    evidence={"pattern": [s.split(":")[0] for s in recent]}
                )
        
        return None
    
    def reset(self):
        """Call at on_agent_start to reset per-task state."""
        self._call_history.clear()
```

### 3.3 FailureClassification Data Model

```python
@dataclass
class FailureClassification:
    failure_class: str       # "tool_error" | "schema_error" | "agent_loop" | "planning_failure"
    sub_type: str            # e.g. "tool_error.timeout"
    severity: str            # "low" | "medium" | "high" | "critical"
    evidence: dict           # Supporting data for dashboard display
    detected_at: datetime = field(default_factory=datetime.utcnow)
    trace_id: str = ""
    span_id: str = ""
    step_index: int = 0
```

---

## 4. Recovery Engine

The Recovery Engine receives a `FailureClassification` and selects and executes the best recovery action.

### 4.1 Recovery Action Selection

```python
RECOVERY_MATRIX = {
    # (failure_class, sub_type, attempt_number) → action
    ("schema_error", "*", 0):              "retry_corrected",   # Retry with validator-corrected params
    ("schema_error", "*", 1):              "escalate",          # Second failure = escalate
    
    ("tool_error", "tool_error.timeout", 0):  "retry",          # Retry once on timeout
    ("tool_error", "tool_error.timeout", 1):  "degrade",        # Second timeout = degrade gracefully
    ("tool_error", "tool_error.rate_limit", 0): "retry_backoff",# Retry with exponential backoff
    ("tool_error", "tool_error.auth", 0):   "escalate",         # Auth errors = immediate escalate
    ("tool_error", "tool_error.execution", 0): "retry",         # Retry once
    ("tool_error", "tool_error.execution", 1): "reroute",       # Try alternative tool (v0.2)
    ("tool_error", "tool_error.execution", 2): "escalate",
    
    ("agent_loop", "agent_loop.identical", 0): "replan",        # Inject replan context
    ("agent_loop", "agent_loop.identical", 1): "escalate",      # Still looping = escalate
    ("agent_loop", "agent_loop.tool_cycling", 0): "replan",
}
```

### 4.2 Recovery Actions

#### `retry`
Re-execute the same tool call with original parameters. Used for transient errors (network blips).

```python
@dataclass
class RetryAction:
    delay_ms: int = 0         # Immediate retry
    preserve_params: bool = True
```

#### `retry_corrected`
Re-execute with validator-corrected parameters. Used after schema errors where auto-correction was applied.

#### `retry_backoff`
Re-execute with exponential backoff. Used for rate limiting.
```python
delay_ms = min(1000 * (2 ** attempt), 30000)  # Max 30s
```

#### `replan`
Inject a structured recovery message into the agent's context, prompting it to reconsider its approach. This is the most powerful recovery action.

**Replan message template:**
```
ORCHESTRATION RECOVERY NOTICE

The previous tool call failed or produced a loop condition. Details:
- Tool: {tool_id}
- Failure: {failure_sub_type}
- Attempt: {attempt_number} of {max_retries}
- Error: {error_summary}

Suggested approaches:
{suggestions}

Please adapt your approach. Do not retry the same call with the same parameters.
```

**Suggestions are generated per failure type:**
- `agent_loop.identical` → "Modify the query/parameters", "Use a different tool", "Return a partial result if available"
- `tool_error.empty_result` → "The data may not exist; consider reporting 'not found'", "Try a broader query"
- `schema_error.drift` → "The tool API may have changed; try with simplified parameters"

#### `reroute` (v0.2)
Ask the Tool Router to suggest an alternative tool for the same intent. Falls back to `replan` in v0.1.

#### `escalate`
Trigger the configured escalation webhook (if set). Emit a `critical` severity span. If no webhook configured, inject a replan with explicit instruction to surface the error to the user.

#### `degrade`
Inject a replan message instructing the agent to proceed without the failing tool. Return partial or degraded results to the user rather than failing completely.

### 4.3 Recovery Context Injection

The Recovery Engine returns a `RecoveryInstruction` to the shim:

```python
@dataclass
class RecoveryInstruction:
    action: str                     # "retry" | "retry_corrected" | "replan" | "escalate" | "degrade"
    delay_ms: int = 0
    corrected_params: dict | None = None   # For retry_corrected
    injection_message: str | None = None   # For replan/escalate/degrade
    webhook_payload: dict | None = None    # For escalate
    emit_span: bool = True
```

The shim handles injection differently per framework (see `02_INTERCEPTION_SHIM.md § 5`).

---

## 5. Failure Pattern Store

Every failure is persisted as a `ToolCallEvent` for analysis and future model training.

### 5.1 ToolCallEvent Data Model

```python
@dataclass
class ToolCallEvent:
    event_id: str              # UUID
    trace_id: str
    span_id: str
    tool_id: str
    tool_version: str
    step_index: int
    framework: str             # "langgraph" | "crewai" | "openai_sdk" | etc.
    
    # Params — stored as hashes only by default (privacy)
    params_hash: str           # SHA256 of params JSON
    params_schema_version: str # Schema version at call time
    
    # Outcomes
    outcome: str               # "success" | "schema_error" | "tool_error" | "timeout" | "agent_loop"
    failure_class: str | None
    failure_sub_type: str | None
    failure_severity: str | None
    
    # Recovery
    recovery_action: str | None
    recovery_attempt: int
    recovery_succeeded: bool | None
    
    # Validation
    validation_result: str     # "pass" | "corrected" | "gated" | "skipped"
    corrections_applied: list[dict]
    
    # Timing
    latency_ms: float
    validation_latency_ms: float
    created_at: datetime
    
    # Optional (opt-in only, requires CONDUIT_LOG_PAYLOADS=true)
    params_raw: dict | None = None
    result_summary: str | None = None
```

### 5.2 SQLite Schema (v0.1)

```sql
CREATE TABLE tool_call_events (
    id                  INTEGER PRIMARY KEY,
    event_id            TEXT NOT NULL UNIQUE,
    trace_id            TEXT NOT NULL,
    span_id             TEXT NOT NULL,
    tool_id             TEXT NOT NULL,
    tool_version        TEXT,
    step_index          INTEGER NOT NULL DEFAULT 0,
    framework           TEXT NOT NULL,
    params_hash         TEXT NOT NULL,
    params_schema_version TEXT,
    outcome             TEXT NOT NULL,
    failure_class       TEXT,
    failure_sub_type    TEXT,
    failure_severity    TEXT,
    recovery_action     TEXT,
    recovery_attempt    INTEGER DEFAULT 0,
    recovery_succeeded  BOOLEAN,
    validation_result   TEXT NOT NULL,
    corrections_applied TEXT,   -- JSON array
    latency_ms          REAL,
    validation_latency_ms REAL,
    created_at          DATETIME NOT NULL
);

CREATE INDEX idx_events_tool    ON tool_call_events(tool_id);
CREATE INDEX idx_events_outcome ON tool_call_events(outcome);
CREATE INDEX idx_events_created ON tool_call_events(created_at);
CREATE INDEX idx_events_trace   ON tool_call_events(trace_id);
CREATE INDEX idx_events_failure ON tool_call_events(failure_class, failure_sub_type);
```

---

## 6. Failure Pattern Analysis (Dashboard Feeds)

Queries the Failure Pattern Store for dashboard views:

```python
class FailurePatternAnalyzer:
    
    def top_failing_tools(self, days=7, limit=10) -> list[ToolFailureSummary]:
        """Tools with highest failure rate in the past N days."""
    
    def failure_rate_by_class(self, days=7) -> dict[str, float]:
        """Breakdown: schema_error 42%, tool_error 31%, agent_loop 27%"""
    
    def recovery_success_rate(self, days=7) -> dict[str, float]:
        """Per action: retry 78%, replan 62%, retry_corrected 91%"""
    
    def loop_frequency_by_tool(self, days=7) -> list[LoopFrequency]:
        """Which tools trigger the most loops."""
    
    def mean_steps_to_failure(self, days=7) -> float:
        """Average step_index at which first failure occurs."""
    
    def drift_events_timeline(self, tool_id: str) -> list[DriftEvent]:
        """Schema drift history for a specific tool."""
    
    def prescriptive_recommendations(self) -> list[Recommendation]:
        """
        Returns actionable recommendations based on patterns, e.g.:
        - "search_web has 73% schema validation failures → update your schema to v2.4"
        - "Agent loops detected 12 times in 24h → add tool diversity constraints"
        - "file_read tool times out 40% of the time → increase timeout or add degrade action"
        """
```

---

## 7. Testing Requirements

### Unit tests
- Tool failure detection: each sub_type triggers correct classification
- Loop detection: 3 identical calls → `agent_loop.identical` at call 3
- Loop detection: A→B→A→B pattern → `agent_loop.tool_cycling`
- Recovery matrix: each (class, sub_type, attempt) returns correct action
- Replan message generation: template populated correctly for each failure type
- Recovery instruction: correct structure per action type

### Integration tests
- Full flow: schema validation fail → recovery retry → success on retry → both spans emitted
- Full flow: loop detection → replan injection → agent modifies approach → loop cleared
- Full flow: tool auth error → immediate escalation → webhook called (mock)
- Failure store persistence: events written to SQLite correctly
- Failure pattern analysis: queries return correct aggregates

### Edge cases
- Loop detector resets correctly between tasks (`on_agent_start` clears history)
- Recovery engine does not retry beyond `max_retries` config
- Graceful fallback: if recovery engine crashes, agent execution continues unmodified
- Concurrent calls: loop detector is per-trace-id, not global (two agents running in parallel don't interfere)
