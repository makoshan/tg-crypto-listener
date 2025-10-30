"""Tool Planner node for deciding which tools to call."""
import logging
from typing import Any, Dict, Optional
from types import SimpleNamespace

from .base import BaseNode
from ..planners import BasePlanner, create_planner

logger = logging.getLogger(__name__)


class ToolPlannerNode(BaseNode):
    """Node for AI-powered tool planning and keyword generation."""

    def __init__(self, engine: Any):
        """Initialize node with planner."""
        super().__init__(engine)
        self._planner: Optional[BasePlanner] = None

    def _get_planner(self) -> BasePlanner:
        """Lazy initialization of planner."""
        if self._planner is None:
            engine_config = getattr(self.engine, "_config", SimpleNamespace())
            planner_type = getattr(engine_config, "DEEP_ANALYSIS_PLANNER", "gemini")
            self._planner = create_planner(
                planner_type,
                self.engine,
                engine_config
            )
        return self._planner

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Decide which tools to call using AI-powered decision making."""
        logger.info("🤖 Tool Planner: AI 智能决策下一步工具")

        # Use planner abstraction for flexible backend selection
        return await self._decide_with_planner(state)

    async def _decide_with_planner(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Use planner for intelligent decision making."""
        try:
            planner = self._get_planner()
            available_tools = planner.discover_available_tools()

            # Get planning decision
            plan = await planner.plan(state, available_tools)

            # Convert ToolPlan to state dict
            return plan.to_dict()

        except Exception as exc:
            logger.error("Tool Planner 执行失败: %s", exc)
            # Return empty decision on failure
            return {
                "next_tools": [],
                "search_keywords": "",
                "macro_indicators": [],
                "onchain_assets": [],
                "protocol_slugs": [],
            }

    # Legacy method kept for reference - now using planner abstraction
    # async def _decide_with_function_calling(self, state: Dict[str, Any]) -> Dict[str, Any]:
    #     """Use Gemini Function Calling for structured decision."""
    #     # This logic is now in GeminiPlanner class
    #     pass
