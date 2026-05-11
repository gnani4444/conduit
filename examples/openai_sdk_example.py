"""OpenAI Agents SDK example — 05_TELEMETRY_AND_ADAPTERS.md §4.2

2-line Conduit install. Everything else is standard OpenAI Agents SDK.

Requirements:
    pip install conduit[openai]
    export OPENAI_API_KEY=...
"""
from __future__ import annotations

# ── 2-line Conduit install ──────────────────────────────────────────
from conduit.shim.adapters.openai_sdk import install_for_openai_sdk
install_for_openai_sdk()
# ───────────────────────────────────────────────────────────────────

# Register tool schemas
from conduit.registry.store import SchemaRegistry
from conduit.config import get_config

registry = SchemaRegistry(get_config().registry.db_path)
registry.register(
    tool_id="get_weather",
    schema={
        "type": "object",
        "properties": {
            "location": {"type": "string"},
            "units": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        },
        "required": ["location"],
    },
    version="1.0.0",
)

# ── Standard OpenAI Agents SDK (unchanged) ─────────────────────────
try:
    from agents import Agent, Runner, function_tool  # type: ignore

    @function_tool
    def get_weather(location: str, units: str = "celsius") -> str:
        """Get the current weather for a location."""
        return f"Weather in {location}: 22°{units[0].upper()}, sunny"

    agent = Agent(
        name="WeatherAgent",
        instructions="You are a helpful weather assistant.",
        tools=[get_weather],
    )

    result = Runner.run_sync(agent, "What's the weather in London?")
    print(result.final_output)
    print("Check conduit dashboard: http://127.0.0.1:7432")

except ImportError:
    print("OpenAI Agents SDK not installed. Run: pip install conduit[openai]")
    print("Conduit shim is installed and ready — install openai-agents to run this example.")
