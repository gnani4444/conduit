"""
Example: LangGraph + Conduit Schema Validation

Problem: An agent calls `search_web` with max_results="10" (string instead of int).
         Without Conduit, this crashes the tool. With Conduit, it's auto-corrected.

What this demonstrates:
  1. Register a tool schema with Conduit
  2. Validate parameters — Conduit catches type mismatches
  3. Auto-correct invalid params before the tool ever sees them
"""

from conduit.registry.store import SchemaRegistry
from conduit.intelligence.validator import SchemaValidator

# --- Step 1: Register the tool schema ---
registry = SchemaRegistry(":memory:")
registry.register(
    tool_id="search_web",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["query", "max_results"],
    },
    version="1.0.0",
)
print("✓ Registered schema for 'search_web'\n")

# --- Step 2: Simulate what an LLM agent typically sends ---
# LLMs often send numbers as strings — this is the #1 schema failure in production
agent_params = {"query": "latest AI news", "max_results": "10"}
print(f"Agent called search_web with: {agent_params}")
print(f"  ⚠ max_results is a string, schema expects integer\n")

# --- Step 3: Conduit validates and auto-corrects ---
validator = SchemaValidator(registry, auto_correct=True)
result = validator.validate("search_web", agent_params)

print(f"Conduit validation result: {result.validation_result}")
print(f"  Decision: {result.decision}")

if result.corrections:
    print(f"  Corrections applied:")
    for c in result.corrections:
        print(f"    • {c.field_path}: {c.original_value!r} → {c.corrected_value!r} ({c.correction_type})")

if result.corrected_params:
    print(f"\n  Corrected params sent to tool: {result.corrected_params}")
    print(f"  ✓ Tool receives valid integer — no crash")
else:
    print(f"\n  Original params passed through (already valid or no schema)")

# --- Step 4: Show what happens with a hard gate on unfixable errors ---
print("\n" + "=" * 60)
print("Example 2: Missing required field (not auto-correctable)\n")

bad_params = {"query": "test"}  # missing max_results entirely
result2 = validator.validate("search_web", bad_params)

print(f"Agent called search_web with: {bad_params}")
print(f"Conduit validation result: {result2.validation_result}")
if result2.errors:
    print(f"  Errors:")
    for e in result2.errors:
        print(f"    • {e.field_path}: {e.error_type} (expected: {e.expected}, got: {e.received})")

print("""
--- WHAT THIS SOLVES ---
Without Conduit: Tool crashes with a cryptic TypeError or API 400 error.
                 Agent retries with same bad params. Loop begins.

With Conduit:    Type mismatches auto-corrected in <3ms.
                 Missing fields caught before execution.
                 Agent gets actionable error instead of crash.
""")
