# Conduit Examples

Practical examples showing how Conduit catches and fixes agent failures at runtime.

## Examples

| File | What it demonstrates | Problem solved |
|------|---------------------|----------------|
| `langgraph_schema_validation.py` | LangGraph + schema validation + auto-correction | Agent sends `"10"` (string) instead of `10` (int) — Conduit fixes it before the tool crashes |
| `openai_sdk_loop_detection.py` | OpenAI Agents SDK + loop detection | Agent retries the same failed call 3x — Conduit detects the loop and injects recovery context |
| `schema_registry_drift.py` | Schema registration + drift detection | Tool API changed field names — Conduit detects drift and shows what to fix |

## Running

```bash
pip install conduit-ai-agents

# Each example is self-contained
python examples/langgraph_schema_validation.py
python examples/openai_sdk_loop_detection.py
python examples/schema_registry_drift.py
```

## What to look for

Each example prints:
1. **The problem** — what the agent did wrong
2. **Conduit's detection** — what Conduit caught
3. **The fix** — what Conduit did (auto-correct, loop alert, drift report)
