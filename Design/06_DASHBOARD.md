# Conduit — Prescriptive Dashboard Specification

**Document:** 06_DASHBOARD.md  
**Depends on:** 03_SCHEMA_VALIDATOR.md, 04_FAILURE_DETECTOR.md  

---

## 1. Purpose and Philosophy

### What this is NOT
- Not a trace viewer (LangSmith, Logfire do that)
- Not a metrics dashboard (Datadog, Grafana do that)
- Not a log aggregator

### What this IS
A **prescriptive** dashboard. Every view exists to answer one question: *"What should I do right now to make my agents more reliable?"*

The distinction matters. Observability tells you what happened. Prescriptive intelligence tells you what to do about it. Every screen in this dashboard surfaces a specific, actionable recommendation — not just data.

---

## 2. Technology Stack (v0.1)

- **Backend:** FastAPI (Python), served at `localhost:7432`
- **Frontend:** HTMX + minimal vanilla JS — no React, no build step
- **Styling:** CSS variables matching the agent's terminal environment (dark-mode first)
- **Real-time updates:** Server-Sent Events (SSE) for live failure feed
- **Data source:** SQLite (Failure Pattern Store + Schema Registry)

**v0.2+:** Extract to standalone React SPA with FastAPI backend. SaaS deployment on Conduit cloud.

---

## 3. Dashboard Views

### View 1: Command Center (default landing page)

**URL:** `localhost:7432/`

**Purpose:** Immediate situational awareness. Answer "is anything on fire right now?" in under 3 seconds.

**Layout — 3 zones:**

#### Zone A: Health Bar (top strip, always visible)
```
[ Agents running: 2 ] [ Tool calls (24h): 1,847 ] [ Failure rate: 4.3% ▲ ] [ Loops detected: 3 ] [ Schema drift alerts: 1 ]
```
Color codes:
- Failure rate: green < 5%, amber 5–15%, red > 15%
- All numbers link to the relevant detail view

#### Zone B: Live Failure Feed (left 60%, SSE stream)
Real-time stream of failure events as they occur. Each entry:
```
[14:32:07] HIGH   search_web       agent_loop.identical     Retry 3/3 → Replan injected
[14:31:54] MEDIUM file_read        tool_error.timeout       Retry 1/2 → Retrying...
[14:31:22] LOW    calculator       schema_error.type_mismatch  Auto-corrected: "10" → 10
[14:30:11] INFO   search_web       success                  2,341ms
```
- Severity colour-coded: critical=red, high=amber, medium=yellow, low=gray, info=green
- Click any row → opens Failure Detail view for that event
- Filter bar above: severity filter, tool filter, class filter, time range

#### Zone C: Active Recommendations (right 40%)
Top 3 prescriptive recommendations right now, ranked by impact:

```
┌─────────────────────────────────────────────────────┐
│ [1] UPDATE SCHEMA: search_web                        │
│ 73% of calls are failing schema validation           │
│ Your registered schema is v2.1; tool is now on v2.4  │
│ → View drift details   → Update schema now           │
└─────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────┐
│ [2] LOOP PATTERN DETECTED                            │
│ search_web loops detected 8x in 24h                 │
│ All loops: same query, empty result triggers retry   │
│ → Add "degrade on empty" recovery rule               │
└─────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────┐
│ [3] TIMEOUT THRESHOLD TOO LOW                        │
│ file_read times out 38% of the time (threshold: 5s) │
│ Median actual latency: 4.8s (very close to limit)   │
│ → Increase timeout to 15s                           │
└─────────────────────────────────────────────────────┘
```

Each recommendation has:
- Problem statement (1 sentence)
- Evidence (specific numbers, not vague)
- Primary action button (links to the fix)
- "Dismiss for 24h" link

---

### View 2: Failure Analysis

**URL:** `localhost:7432/failures`

**Purpose:** Deep-dive on failure patterns across time. "Why does my agent keep failing?"

#### Section A: Failure Rate Timeline
Line chart: failure rate % over last 24h/7d/30d (toggle). Overlay: schema drift events marked as vertical lines. Tooltip on hover: count, failure class breakdown.

#### Section B: Failure Breakdown Table
```
Failure Class          | Count (24h) | Rate  | Trend | Top Tool       | Recovery Success
-----------------------|-------------|-------|-------|----------------|----------------
schema_error.type_mismatch | 142     | 7.7%  | ▲ 23% | search_web     | 91% (auto-correct)
agent_loop.identical    | 28          | 1.5%  | ─     | search_web     | 62% (replan)
tool_error.timeout      | 19          | 1.0%  | ▼ 8%  | file_read      | 78% (retry)
tool_error.execution    | 11          | 0.6%  | ─     | calculator     | 45% (retry)
```
Click any row → filtered Failure Detail list for that class.

#### Section C: Mean Time to Detection vs. Mean Time to Recovery
```
Detection  ████████ 0.8s avg (time from failure to Conduit classification)
Recovery   ████████████████████ 4.2s avg (time from classification to agent adapting)
Manual     ████████████████████████████████████████████████████████████ 14 min avg (estimate)
```

#### Section D: Top 10 Most-Failed Tools
Bar chart: tool_id on Y axis, failure count on X axis, coloured by dominant failure class.

---

### View 3: Schema Registry

**URL:** `localhost:7432/schemas`

**Purpose:** "Are my tool schemas up to date? What drifted?"

#### Section A: Schema Inventory Table
```
Tool ID          | Version | Source      | Last Validated | Status      | Drift Events
-----------------|---------|-------------|----------------|-------------|-------------
search_web       | 2.1.0   | manual      | 2h ago         | DRIFT       | 3
file_read        | 1.0.0   | mcp_manifest| 1d ago         | OK          | 0
calculator       | 1.2.0   | manual      | 5d ago         | STALE       | 0
email_send       | 3.0.0   | openapi     | 1h ago         | OK          | 0
```
Status indicators:
- `OK` — no drift, recently validated
- `DRIFT` — drift events detected, schema may be outdated
- `STALE` — not validated in > 3 days
- `UNKNOWN` — no schema registered

Click any row → Schema Detail view.

#### Schema Detail view (`/schemas/{tool_id}`)
- Current schema (JSON, syntax highlighted)
- Version history timeline
- Drift events list:
  ```
  [2026-05-11 14:32] DRIFT DETECTED
  Field 'num_results' not in schema (renamed to 'max_results')
  Auto-corrected in 142 calls. Schema not yet updated.
  → Update schema to accept 'max_results'
  ```
- **"Accept drift as new schema"** button — one click to update the registry with observed params as the new baseline
- **"Re-ingest from MCP"** button — pull fresh schema from MCP server manifest

---

### View 4: Failure Detail (per-event)

**URL:** `localhost:7432/failures/{event_id}`

**Purpose:** Full context for a single failure event. For debugging.

#### Layout:
```
┌──────────────────────────────────────────────────────────────────┐
│ FAILURE: agent_loop.identical   HIGH   [2026-05-11 14:32:07]    │
│ Trace ID: abc123def456          Tool: search_web                 │
│ Framework: langgraph             Step: 7 of 12                   │
└──────────────────────────────────────────────────────────────────┘

WHAT HAPPENED:
search_web was called 3 times with identical parameters in steps 5, 6, 7.
No state progression was detected between calls.

EVIDENCE:
  Step 5: search_web({query: "X", max_results: 10}) → empty result
  Step 6: search_web({query: "X", max_results: 10}) → empty result  [SAME]
  Step 7: search_web({query: "X", max_results: 10}) → empty result  [SAME]

RECOVERY ACTION TAKEN:
  Action: replan
  Injected at: step 7, after 3rd identical call
  Message: "Previous tool calls failed or produced a loop condition. [...]"
  Outcome: Agent modified query in step 8 → search_web({query: "Y"}) → success

ROOT CAUSE ANALYSIS:
  Most likely: tool returned empty result for query "X" but agent's
  retry logic did not modify the query — it retried identically.

PRESCRIPTIVE RECOMMENDATION:
  ┌───────────────────────────────────────────────────────────────┐
  │ Add a "degrade on empty result" rule for search_web:          │
  │                                                               │
  │   recovery:                                                   │
  │     tool_error.empty_result:                                  │
  │       action: replan                                         │
  │       message: "Search returned no results. Try a broader    │
  │                query or return 'not found'."                  │
  └───────────────────────────────────────────────────────────────┘
  → Copy config snippet   → Apply to conduit.yaml

SPAN TIMELINE (this trace):
  [step 1] invoke_agent ─── 120ms
  [step 2] search_web ───── 1,204ms  success
  [step 3] file_read ─────── 342ms   success
  [step 4] calculator ────── 12ms    success
  [step 5] search_web ───── 1,198ms  empty_result ← first failure
  [step 6] search_web ───── 1,201ms  empty_result ← loop
  [step 7] search_web ───── 1,199ms  empty_result ← LOOP DETECTED, replan injected
  [step 8] search_web ───── 987ms    success       ← replan worked
  [step 9] file_read ─────── 344ms   success
```

---

### View 5: Prescriptive Recommendations

**URL:** `localhost:7432/recommendations`

**Purpose:** All recommendations in one place, ranked by impact. The "TODO list" for making agents more reliable.

#### Recommendation Card Structure
Every recommendation must have:
1. **Category badge**: `SCHEMA` | `RECOVERY` | `PERFORMANCE` | `LOOP` | `CONFIG`
2. **Impact score**: 1–10, computed from: frequency × severity × recovery success rate
3. **Problem** (1 sentence, specific numbers)
4. **Evidence** (2–3 data points)
5. **Fix** (exact change, copy-paste ready)
6. **Estimated impact** (e.g., "Would eliminate 73% of schema validation failures")
7. Actions: `Apply fix` | `Dismiss 24h` | `Dismiss forever` | `Open detail`

#### Recommendation Types and Their Fixes

**Type: UPDATE_SCHEMA**
```
Problem: search_web schema v2.1 is causing 142 validation failures/day.
Evidence: 3 drift events detected; field 'num_results' renamed to 'max_results'.
Fix: Run `conduit schema update search_web --from-drift` to accept observed schema.
Impact: Eliminates 73% of daily schema validation failures.
```

**Type: ADD_RECOVERY_RULE**
```
Problem: search_web empty result triggers agent loop 8x/day.
Evidence: 8 loops in 24h; all triggered by empty result, no recovery rule configured.
Fix: Add to conduit.yaml:
     recovery_rules:
       search_web:
         tool_error.empty_result:
           action: replan
           message: "No results found. Try a broader query."
Impact: Breaks loop on first empty result instead of waiting for N=3 threshold.
```

**Type: INCREASE_TIMEOUT**
```
Problem: file_read times out 38% of the time.
Evidence: Configured timeout: 5,000ms. Median actual latency: 4,823ms. P95: 6,241ms.
Fix: In conduit.yaml, set: tools.file_read.timeout_ms: 15000
Impact: Eliminates 92% of timeout failures based on observed latency distribution.
```

**Type: REGISTER_SCHEMA**
```
Problem: email_send has no registered schema. 100% of validation checks are skipped.
Evidence: 234 calls in 7 days with no validation. 3 failures with no root cause.
Fix: Run `conduit schema discover email_send` to infer schema from call history.
Impact: Enables schema validation on all future email_send calls.
```

**Type: LOOP_THRESHOLD**
```
Problem: Loop threshold of N=3 is catching loops late (avg 3.2 wasted calls before detection).
Evidence: All 8 loops were identical-params loops that could be detected at N=2.
Fix: In conduit.yaml, set: detection.loop_threshold: 2
Impact: Reduces wasted tool calls by 33% before loop recovery fires.
```

---

### View 6: Tool Performance

**URL:** `localhost:7432/tools`

**Purpose:** Per-tool reliability and performance profile.

#### Tool Summary Table
```
Tool             | Calls (24h) | Success% | Avg ms | P95 ms | Schema OK | Loops | Errors
-----------------|-------------|----------|--------|--------|-----------|-------|-------
search_web       | 842         | 88.2%    | 1,204  | 2,100  | DRIFT     | 8     | 23
file_read        | 412         | 94.7%    | 342    | 6,241  | OK        | 0     | 11
calculator       | 203         | 99.5%    | 12     | 24     | OK        | 0     | 1
email_send       | 234         | 91.0%    | 589    | 1,100  | NONE      | 0     | 7
```

Click any tool → Tool Detail view showing:
- Latency histogram (p50, p90, p95, p99)
- Success rate over time
- Failure class breakdown
- Schema drift timeline
- Recovery action outcomes for this tool

---

## 4. CLI Integration

The dashboard is accessible via `conduit` CLI as well as the web UI:

```bash
# Start dashboard
conduit dashboard

# Get current recommendations
conduit recommend

# Validate schema for a tool
conduit schema validate search_web --params '{"query": "test", "max_results": "10"}'

# Show live failure stream
conduit stream

# Update schema from observed drift
conduit schema update search_web --from-drift

# Generate config fix for a recommendation
conduit fix --recommendation REC_001
```

---

## 5. API Endpoints (Dashboard Backend)

```
GET  /api/v1/health                    # Overall health summary (for health bar)
GET  /api/v1/failures                  # List failures (filterable)
GET  /api/v1/failures/{event_id}       # Failure detail with root cause analysis
GET  /api/v1/failures/stream           # SSE stream of live failures
GET  /api/v1/recommendations           # All prescriptive recommendations
GET  /api/v1/tools                     # Tool performance summary table
GET  /api/v1/tools/{tool_id}          # Tool detail
GET  /api/v1/schemas                   # Schema inventory
GET  /api/v1/schemas/{tool_id}        # Schema detail
GET  /api/v1/analytics/failure_rate    # Time-series failure rate
GET  /api/v1/analytics/recovery_rate  # Recovery success rates
POST /api/v1/schemas/{tool_id}/accept_drift   # Accept drift as new schema
POST /api/v1/recommendations/{id}/dismiss     # Dismiss recommendation
```

---

## 6. Prescriptive Recommendation Engine

The engine runs on a schedule (every 60 seconds) and on demand. It queries the Failure Pattern Analyzer and generates `Recommendation` objects.

```python
class RecommendationEngine:
    
    def generate(self) -> list[Recommendation]:
        recommendations = []
        
        # Check for high-drift schemas
        for tool in self.registry.get_tools_with_drift(min_events=3):
            recommendations.append(Recommendation(
                id=f"UPDATE_SCHEMA_{tool.tool_id}",
                category="SCHEMA",
                impact=self._compute_impact(tool),
                problem=f"{tool.tool_id} schema has {tool.drift_event_count} drift events causing "
                        f"{tool.validation_failure_rate:.0%} validation failure rate.",
                evidence=self._schema_evidence(tool),
                fix_type="schema_update",
                fix_config={"tool_id": tool.tool_id, "action": "accept_drift"},
                fix_display=f"conduit schema update {tool.tool_id} --from-drift",
                estimated_impact=f"Eliminates {tool.validation_failure_rate:.0%} of daily schema failures"
            ))
        
        # Check for tools with no schema
        for tool in self.store.get_tools_without_schema(min_calls=10):
            recommendations.append(Recommendation(
                id=f"REGISTER_SCHEMA_{tool.tool_id}",
                category="SCHEMA",
                impact=5,
                problem=f"{tool.tool_id} has no registered schema — validation is skipped on all calls.",
                fix_display=f"conduit schema discover {tool.tool_id}",
            ))
        
        # Check for loops that could be caught earlier
        if self.store.get_loop_events(days=1):
            avg_loop_size = self.store.avg_calls_before_loop_detection(days=1)
            if avg_loop_size > 2.5 and self.config.loop_threshold > 2:
                recommendations.append(Recommendation(
                    id="REDUCE_LOOP_THRESHOLD",
                    category="LOOP",
                    impact=7,
                    problem=f"Loop threshold N={self.config.loop_threshold} wastes "
                            f"{avg_loop_size:.1f} calls on average before detection.",
                    fix_display="Set detection.loop_threshold: 2 in conduit.yaml",
                ))
        
        # Check for missing recovery rules
        for pattern in self.store.get_unrecovered_failure_patterns(days=7):
            if pattern.frequency >= 3:
                recommendations.append(Recommendation(
                    id=f"ADD_RECOVERY_{pattern.tool_id}_{pattern.sub_type}",
                    category="RECOVERY",
                    impact=self._compute_impact_from_frequency(pattern.frequency),
                    problem=f"{pattern.tool_id} {pattern.sub_type} occurs {pattern.frequency}x/week "
                            f"with no recovery rule configured.",
                    fix_display=self._generate_recovery_config(pattern),
                ))
        
        return sorted(recommendations, key=lambda r: r.impact, reverse=True)
```

---

## 7. Testing Requirements

### Dashboard unit tests
- Recommendation engine generates correct recommendations for each pattern type
- Impact scores computed correctly
- Dismiss functionality persists correctly (recommendation doesn't reappear within dismiss window)

### Dashboard integration tests
- Live failure feed receives SSE events within 2 seconds of failure in agent
- Schema detail page shows correct drift timeline
- "Accept drift" action updates schema in registry and removes related recommendations

### UI tests (manual for v0.1)
- All views load without errors on Chrome and Firefox
- Dark mode renders correctly
- Mobile viewport (320px minimum) does not break layout (responsive minimum)
- All action buttons have correct hover and active states
