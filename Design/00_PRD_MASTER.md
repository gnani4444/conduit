# Conduit — Product Requirements Document

**Version:** 0.1.0-alpha  
**Date:** May 2026  
**Status:** Pre-seed / Implementation Ready  
**Codename:** Conduit  

---

## 1. Problem Statement

AI agent teams are shipping agents to production and watching them fail in ways their frameworks cannot explain or recover from. The failure modes are not model failures — they are orchestration failures. They fall into four documented categories:

1. **Tool selection failure** — agent picks the wrong tool from a large registry, or picks the right tool but routes the wrong intent to it.
2. **Schema execution failure** — agent constructs a valid-looking tool call with stale or malformed parameters; the framework passes it directly to the tool and crashes.
3. **Mid-task planning failure** — agent diverges from the original task goal mid-execution, loops on a failed step, or hits a dead-end with no recovery path.
4. **Context engineering failure** — accumulated context grows beyond what is useful; the agent begins losing task state, contradicting prior steps, or hitting token limits.

No existing framework solves all four. LangGraph has excellent checkpointing but no routing intelligence. CrewAI has role-based coordination but opaque failure debugging. OpenAI Agents SDK has clean handoffs but coarse error handling. Pydantic AI catches schema errors at write time but not at runtime across heterogeneous tool sets. All of them solve part of the problem inside their own walls. None of them are cross-framework.

**Conduit is not a framework.** It is a protocol-first, cross-framework orchestration intelligence layer that sits above any agent framework and adds smart tool routing, runtime schema validation, mid-task failure detection, and dynamic context engineering through a thin interception shim.

---

## 2. Vision

> "LangSmith tells you what your agent did. Conduit tells you what went wrong, why, and fixes it — across any framework you already use."

The long-term vision is to be the reliability and intelligence plane for the entire agentic AI ecosystem — the Datadog of agent orchestration failures. Not a framework competing with LangGraph; the prescriptive intelligence layer that makes all of them production-safe.

---

## 3. Target Users

### Primary: AI/ML engineers and agent builders
- Building production agents on LangGraph, CrewAI, OpenAI Agents SDK, or Mastra
- Experiencing tool routing failures, schema drift crashes, and unexplained agent loops in production
- Spending engineering time debugging orchestration rather than building product
- Technically sophisticated — will read source before adopting; require open-source core

### Secondary: Platform and infrastructure teams
- Running multi-tenant agent infrastructure at scale
- Need governance, audit trails, and failure SLAs
- Evaluating SOC 2 / HIPAA-compliant tooling

### Anti-target (for now)
- No-code users, business analysts, non-technical operators
- Teams building single-agent, single-tool workflows (insufficient complexity for value)

---

## 4. Product Scope — v0.1 (MVP)

### In scope
- Interception shim (Python, LangGraph adapter, OpenAI SDK adapter)
- Runtime schema validator with JSON Schema enforcement
- Tool loop detector (repeated identical call signatures = loop)
- Tool failure classifier (timeout, schema error, execution error)
- OTel telemetry backbone (standard `gen_ai.*` spans)
- Schema registry (manual registration + MCP manifest ingestion)
- Local dashboard (failure trace viewer, schema drift alerts)
- CLI installer

### Out of scope for v0.1
- Semantic tool routing (requires training data — month 4+)
- Planning divergence detection (requires ML — month 10+)
- Dynamic context compression (month 7+)
- Enterprise self-hosted data plane (month 12+)
- TypeScript/Mastra adapter (month 7+)
- Cross-agent A2A communication routing

---

## 5. Success Metrics

| Metric | Target at 90 days | Target at 1 year |
|--------|-------------------|------------------|
| GitHub stars (open-source shim) | 500 | 5,000 |
| Design partner teams | 5 | 50 |
| Schema drift events caught | 1,000 | 1,000,000 |
| Loop detections in production | 500 | 500,000 |
| Avg time to detect failure (vs. user report) | < 30 seconds | < 5 seconds |
| Paid teams (SaaS) | 0 (free beta) | 20 |
| ARR | $0 | $120K |

---

## 6. Architecture Summary

```
┌──────────────────────────────────────────────────────────────┐
│              Your agent application                          │
│    LangGraph │ CrewAI │ OpenAI SDK │ Mastra │ any other      │
└────────────────────────┬─────────────────────────────────────┘
                         │ tool calls / model calls
┌────────────────────────▼─────────────────────────────────────┐
│           Interception Shim (open source, MIT)               │
│   Pre-hook → Around-hook → Post-hook at tool + model boundary │
│   OTel GenAI spans emitted on every event                    │
└───────┬──────────────────────────────────────┬───────────────┘
        │                                      │
┌───────▼───────────────────────────────────────▼──────────────┐
│                  Intelligence Plane                           │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────────┐  │
│  │ Tool Router │ │Schema Validtr│ │  Failure Detector      │  │
│  │ (v0.2+)     │ │ (v0.1)       │ │  (v0.1)               │  │
│  └─────────────┘ └──────────────┘ └───────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │              Recovery Action Engine (v0.1)               │ │
│  │  Retry │ Reroute │ Replan │ Escalate │ Degrade           │ │
│  └──────────────────────────────────────────────────────────┘ │
└───────────────────────┬──────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│               OTel Telemetry Backbone                        │
│       All events → gen_ai.* spans → collector                │
└──────────┬────────────────────────────────────┬──────────────┘
           │                                    │
┌──────────▼──────────┐              ┌──────────▼──────────────┐
│  Schema Registry    │              │  Failure Pattern Store  │
│  (drift detection)  │              │  (ToolCallEvent logs)   │
└─────────────────────┘              └─────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│          Prescriptive Dashboard (local → SaaS)               │
└──────────────────────────────────────────────────────────────┘
```

---

## 7. Open Source vs. Commercial Split

| Component | License | Rationale |
|-----------|---------|-----------|
| Interception shim | MIT | Must be open — engineers won't install black-box middleware |
| LangGraph adapter | MIT | Drives adoption |
| OpenAI SDK adapter | MIT | Drives adoption |
| Schema validator core | MIT | Deterministic, no secret sauce |
| Loop / tool failure detectors | MIT | Simple rule-based, no data advantage |
| OTel span emitter | MIT | Infrastructure |
| Schema registry | MIT (self-host) + SaaS | Drift alerting is the value-add |
| Failure pattern store | SaaS only | Cross-customer intelligence = moat |
| Routing model | SaaS only | Trained on aggregate outcomes = moat |
| Prescriptive dashboard | SaaS only | The commercial product |
| Enterprise self-hosted data plane | Enterprise license | For regulated industries |

---

## 8. Non-Functional Requirements

- **Shim latency:** Pre-hook + validation must complete in < 5ms (synchronous path). Does not add perceivable latency to tool calls.
- **Zero framework modification:** No changes to LangGraph, CrewAI, or OpenAI SDK internals. Integration is additive only.
- **Graceful degradation:** If Conduit's intelligence plane is unavailable, the shim passes through tool calls unmodified. Never blocks production traffic.
- **Privacy by default:** No tool call payloads leave the customer's environment without explicit opt-in. Telemetry is metadata-only by default (tool IDs, outcome codes, latencies — not parameter values or model outputs).
- **Standard telemetry:** All spans use OTel GenAI semantic conventions. No proprietary telemetry format.

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Framework breaks adapter (API change) | Medium | High | Hook at OTel layer, not framework internals; version-pin adapters |
| Cold start — routing model useless without data | High | Medium | Lead with schema validator + loop detector (deterministic, zero-data value); don't oversell ML on day one |
| ByteDance cross-framework tool (25K stars) covers same ground | Medium | High | Deep-dive their repo; confirm they don't cover schema validation + failure intelligence; if they do, pivot to complementary positioning |
| Engineers reject closed SaaS intelligence layer | Medium | High | Keep all core detection logic open-source; only failure *pattern DB* (aggregate cross-customer) is closed |
| OTel GenAI semantic conventions change | Low | Medium | Track OTel GenAI SIG; follow their versioning opt-in mechanism (`OTEL_SEMCONV_STABILITY_OPT_IN`) |

---

## 10. Document Index

| File | Purpose |
|------|---------|
| `00_PRD_MASTER.md` | This file — product overview, vision, scope |
| `01_ARCHITECTURE.md` | System architecture, data flow, component contracts |
| `02_INTERCEPTION_SHIM.md` | Shim spec, hook API, OTel span schema |
| `03_SCHEMA_VALIDATOR.md` | Schema registry, validation rules, drift detection |
| `04_FAILURE_DETECTOR.md` | Failure taxonomy, detection algorithms, recovery actions |
| `05_TELEMETRY_BACKBONE.md` | OTel pipeline, span schema, collector config |
| `06_DASHBOARD.md` | Dashboard spec, views, prescriptive actions |
| `07_ADAPTERS.md` | LangGraph, OpenAI SDK, CrewAI adapter implementation guides |
