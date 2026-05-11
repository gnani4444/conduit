# Conduit — Schema Validator and Registry

**Document:** 03_SCHEMA_VALIDATOR.md  
**Depends on:** 01_ARCHITECTURE.md, 02_INTERCEPTION_SHIM.md  

---

## 1. Problem Being Solved

Tool APIs change. Parameters get renamed, deprecated, or restructured. The agent was written against the old schema. No framework validates tool call parameters before execution — they pass whatever the LLM produces directly to the tool. When the call fails, the agent either crashes (LangChain's default: treats raw exceptions as fatal) or receives an error it cannot interpret (because the error is a schema mismatch, not a task failure).

The schema validator solves this at the **pre-execution gate** — before the tool runs, not after it crashes.

---

## 2. Schema Registry

The schema registry is the source of truth for what every tool expects. It is a persistent store with versioned schema snapshots.

### 2.1 Data Model

```python
@dataclass
class SchemaSnapshot:
    tool_id: str           # Canonical identifier, e.g. "search_web"
    schema_version: str    # Semver or content hash, e.g. "2.3.0" or "sha256:abc123"
    json_schema: dict      # Full JSON Schema (Draft 7 or 2020-12)
    known_aliases: dict    # {"deprecated_field": "current_field", ...}
    source: str            # "mcp_manifest" | "openapi" | "manual" | "discovered"
    registered_at: datetime
    last_validated_at: datetime
    drift_events: list[DriftEvent]

@dataclass
class DriftEvent:
    drift_id: str
    detected_at: datetime
    trace_id: str          # The call that revealed the drift
    observed_params: dict  # What the agent sent
    schema_at_time: dict   # What the registry had
    fields_changed: list[FieldChange]
    auto_correctable: bool
    correction_map: dict   # {"old_field": {"new_field": "...", "transform": "..."}}
```

### 2.2 Schema Sources (in priority order)

**1. MCP Server Manifests (highest quality)**
MCP servers expose their tool schemas as part of the protocol. On startup, Conduit ingests all connected MCP manifests:
```python
# Auto-ingestion at startup
conduit.registry.ingest_mcp_manifest("/path/to/mcp_server.json")
# or
conduit.registry.discover_mcp_servers()  # scans CLAUDE_MCP_SERVERS env var
```

**2. OpenAPI / Swagger specs**
REST APIs with OpenAPI specs can be ingested:
```python
conduit.registry.ingest_openapi("https://api.example.com/openapi.json", tool_prefix="example_")
```

**3. Manual registration**
For custom tools without manifests:
```python
conduit.registry.register(
    tool_id="my_custom_tool",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100}
        },
        "required": ["query"]
    },
    version="1.0.0"
)
```

**4. Auto-discovery from successful call history**
After seeing 10+ successful calls for an unknown tool, the registry infers a schema from the observed parameter patterns. This schema is marked `source=discovered` and flagged as low-confidence until manually confirmed.

### 2.3 Schema Drift Detection

Drift is detected when an observed call fails schema validation against the registered schema. The registry computes:

1. **Field rename** — field present in old schema but absent in new, AND a new field appeared with overlapping semantics (detected via name similarity + type match)
2. **Type change** — field present in both but type changed (e.g., `max_results` from `integer` to `string`)
3. **New required field** — field added to `required` without a default
4. **Field deprecation** — field removed entirely

**Drift severity:**
- `low` — deprecated optional field, agent still passed it
- `medium` — type change, auto-correctable
- `high` — new required field missing from call
- `critical` — schema fundamentally incompatible

---

## 3. Validator Logic

### 3.1 Validation Flow

```
Input: tool_id, params (dict), trace_id

1. Registry lookup: get current schema for tool_id
   → If not found: return {decision: "pass", result: "skipped", reason: "no_schema"}

2. JSON Schema validation against params
   → If valid: return {decision: "pass", result: "pass"}
   → If invalid: proceed to correction attempt

3. Correction attempt (if auto_correct=true)
   For each validation error:
   a. Type coercion  → string "10" to int 10, "true" to bool True, etc.
   b. Field rename   → known_aliases lookup; rename deprecated → current
   c. Optional strip → remove unknown fields if additionalProperties=false

4. Re-validate with corrected params
   → If valid: return {decision: "pass", result: "corrected", corrections: [...]}
   → If still invalid: 
       - If hard_gate=false: return {decision: "pass", result: "gated_soft", errors: [...]}
         (tool will run with original params, error likely, recovery engine activates post-execution)
       - If hard_gate=true: return {decision: "gate", result: "gated_hard", errors: [...]}
         (tool blocked; recovery engine activates immediately)

5. Log DriftEvent to registry if corrections were needed or schema was violated
```

### 3.2 Auto-Correction Rules

Corrections are applied conservatively. When in doubt, do not correct — log and let the tool fail naturally so the Failure Detector can classify it.

| Correction Type | Rule | Example |
|----------------|------|---------|
| `type_coerce.str_to_int` | Field expects integer, received string of digits | `"10"` → `10` |
| `type_coerce.str_to_bool` | Field expects boolean, received "true"/"false" | `"true"` → `True` |
| `type_coerce.str_to_float` | Field expects number, received string of float | `"3.14"` → `3.14` |
| `field_rename` | Field name in `known_aliases` map | `"num_results"` → `"max_results"` |
| `optional_strip` | Unknown field, schema has `additionalProperties: false` | Strip `{"debug": true}` |
| `default_inject` | Required field missing, schema has `default` | Inject `{"format": "json"}` |

**Never auto-correct:**
- Structural changes (object → array, array → object)
- Missing required fields with no default
- Value range violations (value outside `minimum`/`maximum`)

### 3.3 Validation Error Schema

```python
@dataclass
class ValidationError:
    field_path: str         # JSON path to the offending field, e.g. "$.params.max_results"
    error_type: str         # "type_mismatch" | "required_missing" | "unknown_field" | "range_violation"
    expected: str           # Human-readable: "integer"
    received: str           # Human-readable: "string ('10')"
    auto_correctable: bool
    correction_applied: Optional[Correction]

@dataclass
class Correction:
    correction_type: str    # e.g. "type_coerce.str_to_int"
    field_path: str
    original_value: Any
    corrected_value: Any
    confidence: float       # 0.0–1.0
```

---

## 4. Performance Requirements

| Operation | Target | Notes |
|-----------|--------|-------|
| Schema registry lookup | < 1ms | In-memory cache with SQLite backing |
| JSON Schema validation (simple schema) | < 1ms | `jsonschema` library, pre-compiled validators |
| JSON Schema validation (complex schema, 20+ fields) | < 2ms | |
| Correction attempt | < 1ms | Rule-based, no ML |
| Full validator round-trip (lookup + validate + correct) | < 3ms | Hard requirement for shim |
| Drift event write (async) | < 5ms | Non-blocking, batched |

### Cache strategy
- Schema snapshots are cached in memory on first lookup, TTL = 60 seconds
- Cache invalidated on new schema registration or drift event that changes `current` version
- Cache size limit: 1,000 schemas (sufficient for any single deployment)

---

## 5. Registry API

### REST API (served by Intelligence Plane)

```
GET  /api/v1/schemas                          # List all registered schemas
GET  /api/v1/schemas/{tool_id}                # Get current schema
GET  /api/v1/schemas/{tool_id}/versions       # Get version history
GET  /api/v1/schemas/{tool_id}/drift          # Get drift events
POST /api/v1/schemas                          # Register new schema
POST /api/v1/schemas/{tool_id}/drift          # Report observed drift
POST /api/v1/schemas/ingest/mcp              # Ingest MCP manifest
POST /api/v1/schemas/ingest/openapi          # Ingest OpenAPI spec
DELETE /api/v1/schemas/{tool_id}             # Remove schema
```

### Python SDK (used by shim internally)

```python
from conduit.registry import SchemaRegistry

registry = SchemaRegistry(db_path="./conduit.db")

# Register
registry.register(tool_id="search_web", schema={...}, version="2.3.0")

# Validate
result = registry.validate(tool_id="search_web", params={"query": "...", "max_results": "10"})
# result.decision = "pass" | "corrected" | "gated"
# result.corrections = [Correction(...)]
# result.errors = [ValidationError(...)]

# Drift
registry.report_drift(tool_id="search_web", observed_params={...}, trace_id="abc123")

# Ingest
registry.ingest_mcp_manifest(path="/path/to/manifest.json")
```

---

## 6. SQLite Schema (v0.1)

```sql
CREATE TABLE schemas (
    id          INTEGER PRIMARY KEY,
    tool_id     TEXT NOT NULL,
    version     TEXT NOT NULL,
    json_schema TEXT NOT NULL,   -- JSON blob
    aliases     TEXT,            -- JSON blob: {"old": "new"}
    source      TEXT NOT NULL,
    is_current  BOOLEAN NOT NULL DEFAULT 1,
    registered_at   DATETIME NOT NULL,
    last_validated  DATETIME,
    UNIQUE(tool_id, version)
);

CREATE TABLE drift_events (
    id              INTEGER PRIMARY KEY,
    drift_id        TEXT NOT NULL UNIQUE,
    tool_id         TEXT NOT NULL,
    detected_at     DATETIME NOT NULL,
    trace_id        TEXT,
    severity        TEXT NOT NULL,   -- low | medium | high | critical
    fields_changed  TEXT NOT NULL,   -- JSON blob
    auto_corrected  BOOLEAN NOT NULL,
    correction_map  TEXT,            -- JSON blob
    schema_version_at_time TEXT NOT NULL
);

CREATE INDEX idx_schemas_tool_current ON schemas(tool_id, is_current);
CREATE INDEX idx_drift_tool ON drift_events(tool_id);
CREATE INDEX idx_drift_detected ON drift_events(detected_at);
```

---

## 7. Dashboard Integration

The schema registry feeds the following dashboard views (see `06_DASHBOARD.md`):

- **Schema Inventory** — table of all registered tools, versions, source, last validated
- **Drift Alert Feed** — real-time feed of drift events with severity and auto-correction outcome
- **Drift Timeline** — per-tool timeline showing when schema versions changed
- **Auto-Correction Audit** — log of every correction applied, with before/after values

---

## 8. Testing Requirements

### Unit tests
- Validate a correct schema → `pass`
- Validate a schema with type mismatch → `corrected` (str→int)
- Validate a schema with missing required field → `gated_hard` (hard-gate mode)
- Validate a schema with unknown field + `additionalProperties:false` → field stripped, `corrected`
- Registry lookup with unknown tool → `skipped`
- Drift detection: observe renamed field → drift event created, `auto_correctable=true`
- Drift detection: observe missing required field → drift event created, `severity=high`

### Performance tests
- 10,000 validation calls against cached schema: p99 < 2ms
- 100 concurrent validation calls: no correctness failures

### Integration tests
- MCP manifest ingestion: ingest a real MCP server manifest, validate calls against it
- OpenAPI ingestion: ingest a public OpenAPI spec, validate a tool call against it
- Drift pipeline: register v1 schema → call with v2 params → drift event in DB → dashboard alert
