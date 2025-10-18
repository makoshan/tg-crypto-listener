"""Synthesis node for generating final signal."""
import json
import logging
from collections.abc import Mapping
from typing import Any, Dict, Optional

from .base import BaseNode
from ..planners import BasePlanner, create_planner

logger = logging.getLogger(__name__)


class SynthesisNode(BaseNode):
    """Node for synthesizing all evidence into final signal."""

    def __init__(self, engine: Any):
        """Initialize node with planner."""
        super().__init__(engine)
        self._planner: Optional[BasePlanner] = None

    def _get_planner(self) -> BasePlanner:
        """Lazy initialization of planner."""
        if self._planner is None:
            planner_type = getattr(
                self.engine._config,
                "DEEP_ANALYSIS_PLANNER",
                "gemini"
            )
            self._planner = create_planner(
                planner_type,
                self.engine,
                self.engine._config
            )
        return self._planner

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesize evidence and generate final signal."""
        logger.info("📊 Synthesis: 生成最终分析")

        try:
            planner = self._get_planner()
            final_json = await planner.synthesize(state)
            return {"final_response": final_json}

        except Exception as exc:
            logger.error("📊 Synthesis 失败: %s", exc)

            preliminary: Any = None
            if isinstance(state, Mapping):
                preliminary = state.get("preliminary")
            elif hasattr(state, "preliminary"):
                preliminary = getattr(state, "preliminary")
            else:
                preliminary = state

            def _extract(prelim: Any, key: str, default: Any) -> Any:
                if isinstance(prelim, Mapping):
                    value = prelim.get(key)
                    return default if value is None else value
                if hasattr(prelim, key):
                    value = getattr(prelim, key)
                    return default if value is None else value
                return default

            event_type = _extract(preliminary, "event_type", "unknown")
            asset = _extract(preliminary, "asset", "NONE")

            # Return error response
            return {
                "final_response": json.dumps({
                    "summary": "分析失败",
                    "event_type": event_type,
                    "asset": asset,
                    "action": "observe",
                    "confidence": 0.0,
                    "notes": f"Synthesis error: {str(exc)}",
                    "links": [],
                    "risk_flags": ["synthesis_error"],
                })
            }

    # Legacy method kept for reference - now using planner abstraction
    # async def _invoke_text_model(self, prompt: str) -> str:
    #     """Invoke Gemini for text generation."""
    #     # This logic is now in planner classes
    #     pass
