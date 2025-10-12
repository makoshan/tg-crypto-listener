"""LangGraph graph builder for tool-enhanced deep analysis."""
import logging
from typing import TYPE_CHECKING, Any, Mapping

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - optional dependency
    END = None  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]

from .nodes import (
    ContextGatherNode,
    SynthesisNode,
    ToolExecutorNode,
    ToolPlannerNode,
)

if TYPE_CHECKING:
    from .gemini import DeepAnalysisState, GeminiDeepAnalysisEngine
else:  # pragma: no cover - runtime fallback to avoid circular imports
    try:
        from .gemini import DeepAnalysisState, GeminiDeepAnalysisEngine  # type: ignore
    except Exception:  # pragma: no cover - tolerate import issues during startup
        DeepAnalysisState = dict  # type: ignore[assignment]  # Fallback for runtime hints
        GeminiDeepAnalysisEngine = Any  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def build_deep_graph(engine: "GeminiDeepAnalysisEngine"):
    """
    Build LangGraph for tool-enhanced deep analysis.

    Args:
        engine: GeminiDeepAnalysisEngine instance

    Returns:
        Compiled LangGraph
    """
    if StateGraph is None or END is None:
        raise ImportError("langgraph not installed, cannot build tool-enhanced deep analysis graph")

    # Import and inject types to avoid NameError at runtime
    import src.ai.deep_analysis.gemini as gemini_module

    # Lazy import to avoid circular dependency
    from src.ai.signal_engine import EventPayload, SignalResult

    # Inject types into gemini module namespace so get_type_hints() can resolve them
    gemini_module.EventPayload = EventPayload
    gemini_module.SignalResult = SignalResult

    graph = StateGraph(DeepAnalysisState)

    # Create node instances
    context_node = ContextGatherNode(engine)
    planner_node = ToolPlannerNode(engine)
    executor_node = ToolExecutorNode(engine)
    synthesis_node = SynthesisNode(engine)

    # Add nodes
    graph.add_node("context_gather", context_node.execute)
    graph.add_node("planner", planner_node.execute)
    graph.add_node("executor", executor_node.execute)
    graph.add_node("synthesis", synthesis_node.execute)

    # Define edges
    graph.set_entry_point("context_gather")
    graph.add_edge("context_gather", "planner")

    # Conditional routing
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {"executor": "executor", "synthesis": "synthesis"},
    )

    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"planner": "planner", "synthesis": "synthesis"},
    )

    graph.add_edge("synthesis", END)

    return graph.compile()


def build_deep_analysis_graph(engine: "GeminiDeepAnalysisEngine"):
    """
    Backwards-compatible wrapper for legacy imports.

    Some test utilities and documentation still reference
    `build_deep_analysis_graph`, so keep this alias to avoid
    import failures while the codebase migrates.
    """
    return build_deep_graph(engine)


def _route_after_planner(state: Mapping[str, Any]) -> str:
    """
    Router after Tool Planner.

    Args:
        state: LangGraph state

    Returns:
        str: Next node name ("executor" or "synthesis")
    """
    if not state.get("next_tools"):
        logger.debug("Planner decided no tools needed, going to Synthesis")
        return "synthesis"
    return "executor"


def _route_after_executor(state: Mapping[str, Any]) -> str:
    """
    Router after Tool Executor.

    Args:
        state: LangGraph state

    Returns:
        str: Next node name ("planner" or "synthesis")
    """
    if state["tool_call_count"] >= state["max_tool_calls"]:
        logger.info(
            "Reached max tool calls (%d/%d), going to Synthesis",
            state["tool_call_count"],
            state["max_tool_calls"],
        )
        return "synthesis"

    logger.debug(
        "Tool calls (%d/%d), going back to Planner",
        state["tool_call_count"],
        state["max_tool_calls"],
    )
    return "planner"
