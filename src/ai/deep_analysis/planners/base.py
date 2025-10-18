"""
Base planner interface for deep analysis tool planning.

This module defines the abstract interface that all planner implementations
must follow, enabling flexible switching between different planning engines
(Gemini Function Calling, Codex CLI, text-only models, etc.).
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolPlan:
    """
    Standardized output from planner.plan() method.

    Represents the decision of which tools to invoke and their parameters.
    """
    tools: List[str]  # Tool names to invoke, e.g., ["search", "price"]
    search_keywords: str = ""  # Keywords for search tool
    macro_indicators: List[str] = field(default_factory=list)  # Economic indicators
    onchain_assets: List[str] = field(default_factory=list)  # Assets for on-chain analysis
    protocol_slugs: List[str] = field(default_factory=list)  # DeFi protocol identifiers
    reason: str = ""  # Human-readable reasoning for the plan
    confidence: float = 1.0  # Confidence in the plan (0.0-1.0)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for state updates."""
        return {
            "next_tools": self.tools,
            "search_keywords": self.search_keywords,
            "macro_indicators": self.macro_indicators,
            "onchain_assets": self.onchain_assets,
            "protocol_slugs": self.protocol_slugs,
            "planning_reason": self.reason,
            "planning_confidence": self.confidence,
        }


class BasePlanner(ABC):
    """
    Abstract base class for all planner implementations.

    A planner is responsible for:
    1. Deciding which tools to invoke based on the current state
    2. Extracting parameters for those tools
    3. Synthesizing evidence into final AI signal output

    Implementations can use:
    - Gemini Function Calling (structured API)
    - Claude Code CLI (subprocess)
    - Generic text models (prompt + JSON parsing)
    """

    def __init__(self, engine: Any, config: Any):
        """
        Initialize planner with engine and config.

        Args:
            engine: Deep analysis engine instance (provides access to clients)
            config: Configuration object with settings
        """
        self.engine = engine
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def plan(
        self,
        state: Dict[str, Any],
        available_tools: List[str]
    ) -> ToolPlan:
        """
        Decide which tools to invoke next.

        Args:
            state: Current LangGraph state containing:
                - payload: NewsEventPayload (original message)
                - preliminary: PreliminaryAnalysis (initial AI assessment)
                - evidence: Dict[str, Any] (results from executed tools)
            available_tools: List of tool names that can be invoked

        Returns:
            ToolPlan with selected tools and their parameters

        Raises:
            RuntimeError: If planning fails
            TimeoutError: If planner times out (for CLI-based planners)
        """
        pass

    @abstractmethod
    async def synthesize(self, state: Dict[str, Any]) -> str:
        """
        Synthesize evidence into final AI signal JSON.

        Args:
            state: Current LangGraph state with all gathered evidence

        Returns:
            JSON string conforming to AiSignalPayload schema

        Raises:
            RuntimeError: If synthesis fails
        """
        pass

    def discover_available_tools(self) -> List[str]:
        """
        Discover which tools are available in the current engine.

        Returns:
            List of tool names, e.g., ["search", "price", "macro"]
        """
        # Default implementation - engines can override if needed
        tools = []
        if hasattr(self.engine, '_tools'):
            engine_tools = self.engine._tools
            # Handle both dict and list formats
            if isinstance(engine_tools, dict):
                tools = list(engine_tools.keys())
            elif isinstance(engine_tools, list):
                # For Gemini-style tool lists, extract from individual tool attributes
                if hasattr(self.engine, '_search_tool') and self.engine._search_tool:
                    tools.append("search")
                if hasattr(self.engine, '_price_tool') and self.engine._price_tool:
                    tools.append("price")
                if hasattr(self.engine, '_macro_tool') and self.engine._macro_tool:
                    tools.append("macro")
                if hasattr(self.engine, '_onchain_tool') and self.engine._onchain_tool:
                    tools.append("onchain")
                if hasattr(self.engine, '_protocol_tool') and self.engine._protocol_tool:
                    tools.append("protocol")
        return tools

    def _summarize_evidence(self, state: Dict[str, Any]) -> str:
        """
        Create a human-readable summary of evidence for prompt building.

        Args:
            state: LangGraph state

        Returns:
            Formatted string describing current evidence
        """
        evidence = state.get("evidence", {})
        if not evidence:
            return "无证据"

        lines = []
        for key, value in evidence.items():
            if isinstance(value, dict):
                lines.append(f"- {key}: {value.get('summary', str(value)[:100])}")
            elif isinstance(value, list):
                lines.append(f"- {key}: {len(value)} 条记录")
            else:
                lines.append(f"- {key}: {str(value)[:100]}")

        return "\n".join(lines)
