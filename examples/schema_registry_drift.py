"""
Example: Schema Registry + Drift Detection

Problem: A tool's API changed (renamed a field, changed a type) but the agent
         still uses the old schema. Without Conduit, calls silently fail or crash.
         With Conduit, drift is detected, reported, and auto-correctable.

What this demonstrates:
  1. Register a schema for a tool
  2. Detect when observed params don't match the schema (drift)
  3. Report drift events for dashboard visibility
  4. Show which fields changed and whether they're auto-correctable
"""

from conduit.registry.store import SchemaRegistry
from conduit.registry.drift import detect_drift

# --- Step 1: Register the original schema ---
registry = SchemaRegistry(":memory:")
registry.register(
    tool_id="create_ticket",
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
            "assignee": {"type": "string"},
            "labels": {"type": "array"},
        },
        "required": ["title", "priority"],
        "additionalProperties": False,
    },
    version="2.0.0",
    aliases={"severity": "priority"},  # known rename from v1
)
print("✓ Registered schema for 'create_ticket' v2.0.0")
print("  Known aliases: severity → priority\n")

# --- Step 2: Agent sends params using OLD field names + wrong types ---
# This happens when the tool API was updated but the agent's training data is stale
observed_params = {
    "title": "Fix login bug",
    "severity": 3,          # ← old field name (renamed to 'priority' in v2)
    "priority": "high",     # ← type drift: string instead of integer
    "status": "open",       # ← field doesn't exist in schema
}

print(f"Agent called create_ticket with: {observed_params}\n")

# --- Step 3: Detect drift ---
schema = registry.get_current("create_ticket")
changes = detect_drift(schema.json_schema, observed_params, schema.known_aliases)

print(f"Drift detection found {len(changes)} issue(s):\n")
for change in changes:
    icon = "🔧" if change.auto_correctable else "❌"
    print(f"  {icon} {change.field}: {change.change_type} (severity: {change.severity})")
    if change.change_type == "field_rename":
        print(f"      → Agent used old name 'severity', should be 'priority'")
    elif change.change_type == "type_change":
        print(f"      → Got string 'high', expected integer 1-5")
    elif change.change_type == "field_removed":
        print(f"      → 'status' is not in the schema (additionalProperties: false)")

# --- Step 4: Report drift to registry for dashboard ---
registry.report_drift(
    tool_id="create_ticket",
    observed_params=observed_params,
    trace_id="trace-drift-001",
    severity="medium",
    fields_changed=[c.field for c in changes],
    auto_corrected=any(c.auto_correctable for c in changes),
    correction_map={"severity": "priority"},
)

print("\n✓ Drift event reported to registry")

# --- Step 5: Show stored drift events ---
events = registry.list_drift_events("create_ticket")
print(f"  Total drift events for create_ticket: {len(events)}")

# --- Step 6: Validate with auto-correction ---
print("\n" + "=" * 60)
print("Auto-correction via SchemaValidator:")
print("=" * 60 + "\n")

result = registry.validate("create_ticket", {"title": "Bug", "priority": "3"})
print(f"  Input:  {{'title': 'Bug', 'priority': '3'}}")
print(f"  Result: {result.validation_result}")
if result.corrected_params:
    print(f"  Output: {result.corrected_params}")
if result.corrections:
    for c in result.corrections:
        print(f"  Fix:    {c.field_path}: {c.original_value!r} → {c.corrected_value!r}")

print("""
--- WHAT THIS SOLVES ---
Without Conduit: Agent calls fail silently after API updates.
                 Team spends hours debugging "it worked yesterday" issues.
                 No visibility into which tools drifted or when.

With Conduit:    Drift detected on first call with mismatched params.
                 Dashboard shows exactly which fields changed.
                 `conduit schema update create_ticket --from-drift` accepts the new shape.
                 Auto-correction handles renames and type coercions transparently.
""")
