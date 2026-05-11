"""Conduit — AI Agent Observability & Shim.

02_INTERCEPTION_SHIM.md §2 — Installation Contract:

    from conduit import ConduitShim
    shim = ConduitShim.for_langgraph(graph)
    ConduitShim.for_openai_sdk()

    # Generic OTel:
    from conduit import ConduitProcessor
    tracer_provider.add_span_processor(ConduitProcessor())
"""
from conduit.shim.processor import ConduitProcessor

__all__ = ["ConduitProcessor", "ConduitShim"]
__version__ = "0.1.0"


class ConduitShim:
    """Convenience façade — 02_INTERCEPTION_SHIM.md §2."""

    @staticmethod
    def for_langgraph(graph=None, tracer_provider=None) -> "ConduitShim":
        """2-line install for LangGraph. graph arg accepted but not required."""
        from conduit.shim.adapters.langgraph import install_for_langgraph
        install_for_langgraph(tracer_provider=tracer_provider)
        return ConduitShim()

    @staticmethod
    def for_openai_sdk() -> "ConduitShim":
        """2-line install for OpenAI Agents SDK."""
        from conduit.shim.adapters.openai_sdk import install_for_openai_sdk
        install_for_openai_sdk()
        return ConduitShim()

    @staticmethod
    def for_crewai(crew_or_flow=None) -> "ConduitShim":
        """2-line install for CrewAI."""
        from conduit.shim.adapters.crewai import install_for_crewai
        install_for_crewai(crew_or_flow)
        return ConduitShim()
