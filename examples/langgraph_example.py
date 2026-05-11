"""LangGraph example — 01_ARCHITECTURE.md §8 / 05_TELEMETRY_AND_ADAPTERS.md §4.1

2-line Conduit install. Everything else is standard LangGraph.

Requirements:
    pip install conduit[langgraph]
    export LANGSMITH_API_KEY=...  # optional, for LangSmith tracing
"""
from __future__ import annotations

# ── 2-line Conduit install ──────────────────────────────────────────
from conduit.shim.adapters.langgraph import install_for_langgraph
install_for_langgraph()
# ───────────────────────────────────────────────────────────────────

# Register tool schemas so the validator can catch drift
from conduit.registry.store import SchemaRegistry
from conduit.config import get_config

registry = SchemaRegistry(get_config().registry.db_path)
registry.register(
    tool_id="search_web",
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["query", "max_results"],
    },
    version="1.0.0",
)

# ── Standard LangGraph agent (unchanged) ───────────────────────────
try:
    from typing import TypedDict, Annotated
    from langgraph.graph import StateGraph, END  # type: ignore
    from langgraph.graph.message import add_messages  # type: ignore

    class State(TypedDict):
        messages: Annotated[list, add_messages]

    def agent_node(state: State) -> dict:
        # Simulate a tool call with a type mismatch — Conduit will auto-correct
        print("Agent: calling search_web with max_results='5' (string, should be int)")
        # In a real agent this would go through the LLM → tool call path
        return {"messages": state["messages"]}

    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    compiled = graph.compile()

    result = compiled.invoke({"messages": [{"role": "user", "content": "Search for AI news"}]})
    print("Agent completed. Check conduit dashboard: http://127.0.0.1:7432")

except ImportError:
    print("LangGraph not installed. Run: pip install conduit[langgraph]")
    print("Conduit shim is installed and ready — install LangGraph to run this example.")
