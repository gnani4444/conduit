# Conduit — Telemetry Backbone and Framework Adapters

**Document:** 05_TELEMETRY_AND_ADAPTERS.md  
**Depends on:** 01_ARCHITECTURE.md, 02_INTERCEPTION_SHIM.md  

---

## 1. Telemetry Backbone Overview

Conduit's telemetry backbone is a standard OpenTelemetry (OTel) pipeline. It:
- Receives spans from the shim (via OTel SDK)
- Applies processors (enrichment, redaction, sampling)
- Routes spans to the Conduit data plane (schema registry, failure store)
- Forwards spans to the user's existing observability backend (LangSmith, Logfire, Datadog, etc.)

**Critical principle:** Conduit does not replace the user's existing telemetry. It runs in the same OTel pipeline and enriches spans before forwarding.

---

## 2. OTel Pipeline Architecture

```
Agent code
    │
    ▼ (SDK auto-instrumentation or manual spans)
OTel Tracer (in agent process)
    │
    ▼ span created
[Conduit ConduitProcessor]        ← registered as SpanProcessor
    │ pre-hook: validate, route
    │ around-hook: wrap execution
    │ post-hook: classify, recover
    ▼ enriched span exported
OTel BatchSpanExporter
    │
    ▼ OTLP (gRPC or HTTP)
OTel Collector (otelcol)
    ├──[conduit_processor]──► SQLite (schema registry, failure store)
    ├──[redaction_processor]  (strip params if CONDUIT_LOG_PAYLOADS=false)
    └──[forward_exporter]───► User's backend (LangSmith, Logfire, Datadog, etc.)
```

---

## 3. OTel Collector Configuration (v0.1)

The Conduit local collector is an `otelcol` instance with a minimal config. Conduit ships a pre-configured `otelcol-config.yaml`.

```yaml
# conduit/telemetry/otelcol-config.yaml

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: "0.0.0.0:4317"
      http:
        endpoint: "0.0.0.0:4318"

processors:
  # Redact parameter values by default (privacy)
  attributes/redact_params:
    actions:
      - key: "conduit.params.raw"
        action: delete
      - key: "conduit.result.raw"
        action: delete
  
  # Enrich with conduit metadata
  resource:
    attributes:
      - key: "conduit.version"
        value: "0.1.0"
        action: insert
  
  # Batch for efficiency
  batch:
    timeout: 200ms
    send_batch_size: 512

exporters:
  # Conduit internal data plane (SQLite writer)
  conduit_internal:
    endpoint: "http://localhost:7431/ingest"   # Conduit intelligence plane HTTP
  
  # Forward to user's backend (configured in conduit.yaml)
  otlp/downstream:
    endpoint: "${CONDUIT_DOWNSTREAM_ENDPOINT}"
    headers:
      authorization: "Bearer ${CONDUIT_DOWNSTREAM_TOKEN}"

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [attributes/redact_params, resource, batch]
      exporters: [conduit_internal, otlp/downstream]
```

---

## 4. Framework Adapters

Each adapter's job is to ensure OTel spans are emitted for tool calls and model calls, and that the Conduit shim processor is registered in the OTel pipeline. If the framework already emits OTel GenAI spans, the adapter is just registration. If not, the adapter wraps the framework's native events.

---

### 4.1 LangGraph Adapter

**Integration method:** OTel SpanProcessor registered via LangSmith's OTel export pipeline.  
**Effort:** ~10 lines. No LangGraph internals touched.  
**OTel support:** LangGraph → LangSmith → OTel export (native, via LangSmith).

```python
# conduit/shim/adapters/langgraph.py

from opentelemetry.sdk.trace import TracerProvider
from conduit.shim.processor import ConduitProcessor

def install_for_langgraph(tracer_provider: TracerProvider | None = None) -> None:
    """
    Registers ConduitProcessor into the active OTel tracer provider.
    LangSmith already emits gen_ai.* spans — Conduit enriches them in-flight.
    
    Usage:
        from conduit.shim.adapters.langgraph import install_for_langgraph
        install_for_langgraph()
        # Then use LangGraph normally
    """
    from opentelemetry import trace as otel_trace
    
    provider = tracer_provider or otel_trace.get_tracer_provider()
    
    if not hasattr(provider, 'add_span_processor'):
        raise RuntimeError(
            "No OTel TracerProvider found. Ensure LangSmith tracing is configured "
            "before calling install_for_langgraph()."
        )
    
    processor = ConduitProcessor()
    provider.add_span_processor(processor)
    
    # Register agent lifecycle hooks via LangGraph callbacks
    _register_langgraph_callbacks(processor)

def _register_langgraph_callbacks(processor: ConduitProcessor) -> None:
    """Register LangGraph-specific lifecycle callbacks for on_agent_start/end."""
    try:
        from langgraph.callbacks import BaseCallbackHandler
        
        class ConduitLangGraphCallback(BaseCallbackHandler):
            def on_chain_start(self, serialized, inputs, **kwargs):
                processor.on_agent_start(
                    task_id=kwargs.get("run_id", ""),
                    task_goal=str(inputs.get("messages", inputs)),
                    framework="langgraph"
                )
            
            def on_chain_end(self, outputs, **kwargs):
                processor.on_agent_end(
                    task_id=kwargs.get("run_id", ""),
                    outcome="success",
                    framework="langgraph"
                )
            
            def on_chain_error(self, error, **kwargs):
                processor.on_agent_error(
                    task_id=kwargs.get("run_id", ""),
                    error=error,
                    framework="langgraph"
                )
        
        # LangGraph reads callbacks from environment — inject globally
        import langgraph.callbacks as lg_callbacks
        lg_callbacks._default_callbacks.append(ConduitLangGraphCallback())
    
    except ImportError:
        pass  # LangGraph not installed; OTel-only integration still works
```

**What to tell users:**
```python
# Add these 2 lines to your existing LangGraph setup
from conduit.shim.adapters.langgraph import install_for_langgraph
install_for_langgraph()

# Everything else stays exactly the same
graph = StateGraph(State)
# ... your existing graph definition ...
result = graph.invoke({"messages": [...]})
```

---

### 4.2 OpenAI Agents SDK Adapter

**Integration method:** OpenAI Agents SDK `add_trace_processor()` API (official, stable since Mar 2025).  
**Effort:** ~15 lines. Uses SDK's first-class extension point.  
**OTel support:** OpenAI SDK emits native traces — Conduit wraps as OTel spans.

```python
# conduit/shim/adapters/openai_sdk.py

from conduit.shim.processor import ConduitProcessor

def install_for_openai_sdk() -> None:
    """
    Registers Conduit as a trace processor in the OpenAI Agents SDK.
    
    Usage:
        from conduit.shim.adapters.openai_sdk import install_for_openai_sdk
        install_for_openai_sdk()
    """
    try:
        from agents import add_trace_processor
    except ImportError:
        raise RuntimeError("OpenAI Agents SDK not installed. Run: pip install openai-agents")
    
    processor = ConduitProcessor()
    
    class OpenAISDKTraceProcessor:
        """Adapter: translates OpenAI SDK trace events → Conduit ConduitProcessor calls."""
        
        def on_trace_start(self, trace):
            processor.on_agent_start(
                task_id=trace.trace_id,
                task_goal=str(trace.metadata.get("input", "")),
                framework="openai_sdk"
            )
        
        def on_span_start(self, span):
            if span.span_data.type == "function":
                processor.pre_tool_hook(
                    tool_id=span.span_data.name,
                    params=span.span_data.input or {},
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    framework="openai_sdk"
                )
        
        def on_span_end(self, span):
            if span.span_data.type == "function":
                processor.post_tool_hook(
                    tool_id=span.span_data.name,
                    outcome="success" if span.error is None else "tool_error",
                    result=span.span_data.output,
                    error=span.error,
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    latency_ms=span.duration_ms,
                    framework="openai_sdk"
                )
        
        def on_trace_end(self, trace):
            processor.on_agent_end(
                task_id=trace.trace_id,
                outcome="success" if trace.error is None else "failure",
                framework="openai_sdk"
            )
    
    add_trace_processor(OpenAISDKTraceProcessor())
```

**What to tell users:**
```python
# Add these 2 lines before creating your agent
from conduit.shim.adapters.openai_sdk import install_for_openai_sdk
install_for_openai_sdk()

# Everything else stays the same
from agents import Agent, Runner
agent = Agent(name="...", instructions="...", tools=[...])
result = Runner.run_sync(agent, "...")
```

---

### 4.3 CrewAI Adapter

**Integration method:** CrewAI callback system.  
**Effort:** ~50 lines. Hooks into CrewAI's event system; requires version-pinning.  
**OTel support:** CrewAI does not emit OTel natively — adapter translates events.  
**Tested against:** CrewAI >= 0.80.0 (Flows-compatible)

```python
# conduit/shim/adapters/crewai.py

from conduit.shim.processor import ConduitProcessor

def install_for_crewai(crew_or_flow) -> None:
    """
    Installs Conduit callbacks on a CrewAI Crew or Flow object.
    
    Usage:
        from conduit.shim.adapters.crewai import install_for_crewai
        crew = Crew(agents=[...], tasks=[...])
        install_for_crewai(crew)
        crew.kickoff(inputs={...})
    """
    processor = ConduitProcessor()
    
    try:
        from crewai.utilities.events import (
            on_tool_usage_started,
            on_tool_usage_finished,
            on_tool_usage_error,
            on_crew_started,
            on_crew_finished,
        )
        
        @on_tool_usage_started
        def handle_tool_start(source, event):
            processor.pre_tool_hook(
                tool_id=event.tool_name,
                params=event.tool_input or {},
                trace_id=str(id(source)),   # CrewAI has no native trace_id
                framework="crewai"
            )
        
        @on_tool_usage_finished
        def handle_tool_finish(source, event):
            processor.post_tool_hook(
                tool_id=event.tool_name,
                outcome="success",
                result=event.tool_output,
                latency_ms=event.run_attempts * 1000,  # CrewAI exposes attempts
                framework="crewai"
            )
        
        @on_tool_usage_error
        def handle_tool_error(source, event):
            processor.post_tool_hook(
                tool_id=event.tool_name,
                outcome="tool_error",
                error=str(event.error),
                framework="crewai"
            )
        
        @on_crew_started
        def handle_crew_start(source, event):
            processor.on_agent_start(
                task_id=str(id(source)),
                task_goal=str(event.inputs or ""),
                framework="crewai"
            )
        
        @on_crew_finished
        def handle_crew_finish(source, event):
            processor.on_agent_end(
                task_id=str(id(source)),
                outcome="success",
                framework="crewai"
            )
    
    except ImportError as e:
        # Fall back to older CrewAI callback API (< 0.80.0)
        _install_crewai_legacy(crew_or_flow, processor)

def _install_crewai_legacy(crew, processor: ConduitProcessor) -> None:
    """Legacy callback injection for CrewAI < 0.80.0."""
    # Monkey-patch the tool execution method as a last resort
    # This is the least preferred approach — upgrade to CrewAI >= 0.80.0
    original_execute = getattr(crew, '_execute_task', None)
    if original_execute is None:
        return
    
    def patched_execute(task, agent, context):
        # Minimal instrumentation without event system
        result = original_execute(task, agent, context)
        return result
    
    crew._execute_task = patched_execute
```

**What to tell users:**
```python
from conduit.shim.adapters.crewai import install_for_crewai
crew = Crew(agents=[researcher, writer], tasks=[research_task, write_task])
install_for_crewai(crew)     # Install before kickoff
crew.kickoff(inputs={"topic": "AI agent failures"})
```

---

## 5. Adapter Compatibility Matrix

| Framework | Adapter method | Lines | OTel native | Version tested | Maintenance risk |
|-----------|---------------|-------|------------|----------------|-----------------|
| LangGraph | OTel processor | 10 | Yes (via LangSmith) | ≥ 1.0 | Low |
| OpenAI Agents SDK | `add_trace_processor()` | 15 | Yes | ≥ 0.0.14 | Low |
| CrewAI | Event hooks | 50 | No | ≥ 0.80.0 | Medium |
| Pydantic AI | OTel processor (Logfire) | 10 | Yes (Logfire) | ≥ 0.0.15 | Very low |
| MS Agent Framework | Middleware plugin | 20 | Yes (Azure Monitor) | GA Apr 2026 | Low |
| Mastra (TypeScript) | npm middleware package | 40 (TS) | Yes | ≥ 0.1.0 | Medium |
| Google ADK | Callback + Cloud Trace bridge | 60 | Partial | ≥ 0.1.0 | Medium |
| Any OTel-native | OTel processor only | 5 | Yes | — | Very low |

---

## 6. Testing Adapters

### Contract tests (each adapter must pass)
For each adapter, run the full integration test suite against a mock intelligence plane:

1. Tool call → pre-hook fires → validator called → span emitted
2. Tool failure → post-hook fires → failure detector called → recovery action emitted
3. Agent start → on_agent_start called → loop detector initialised
4. Agent end → on_agent_end called → final span emitted
5. Intelligence plane down → all calls pass-through → no adapter exception propagated

### Framework version matrix tests
Each adapter is tested against the min and latest supported framework version in CI.

### Cross-adapter equivalence test
Run the same agent task through LangGraph, OpenAI SDK, and CrewAI (each doing the same thing). Verify that the ToolCallEvents in the failure store have consistent schema regardless of framework.
