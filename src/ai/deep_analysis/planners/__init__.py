"""
Planner abstraction layer for deep analysis engines.

This package provides a flexible interface for tool planning and evidence synthesis,
allowing the deep analysis system to use different AI backends (Gemini, Claude CLI,
generic text models) without changing the core LangGraph pipeline.
"""

from .base import BasePlanner, ToolPlan
from .codex_cli_planner import CodexCliPlanner
from .factory import create_planner, create_planner_with_fallback
from .gemini_planner import GeminiPlanner
from .text_planner import TextPlanner

__all__ = [
    "BasePlanner",
    "ToolPlan",
    "GeminiPlanner",
    "CodexCliPlanner",
    "TextPlanner",
    "create_planner",
    "create_planner_with_fallback",
]
