"""Tool Planner node for deciding which tools to call."""
import logging
from typing import Any, Dict

from .base import BaseNode
from ..helpers.prompts import build_planner_prompt

logger = logging.getLogger(__name__)


class ToolPlannerNode(BaseNode):
    """Node for AI-powered tool planning and keyword generation."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Decide which tools to call using AI-powered decision making."""
        logger.info("🤖 Tool Planner: AI 智能决策下一步工具")

        # Use AI Function Calling for intelligent decision making
        # No hardcoded rules - AI decides based on message content and context
        return await self._decide_with_function_calling(state)

    async def _decide_with_function_calling(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Use Gemini Function Calling for structured decision."""
        prompt = build_planner_prompt(state, self.engine)

        # Create tool definition using proper Gemini SDK types
        tool_definition = {
            "name": "decide_next_tools",
            "description": "根据已有证据决定下一步需要调用的工具，并为搜索生成最优关键词",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "tools": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "需要调用的工具列表,可选值: search, price",
                    },
                    "search_keywords": {
                        "type": "STRING",
                        "description": "搜索关键词（中英文混合，仅当 tools 包含 search 时需要）",
                    },
                    "reason": {"type": "STRING", "description": "决策理由，说明为什么需要或不需要调用这些工具"},
                },
                "required": ["tools", "reason"],
            },
        }

        # Try to use proper SDK types if available
        try:
            from google.genai.types import FunctionDeclaration, Tool  # type: ignore

            tools = [
                Tool(
                    function_declarations=[
                        FunctionDeclaration(**tool_definition),
                    ]
                )
            ]
        except ImportError:
            # Fallback to dict format
            tools = [{"function_declarations": [tool_definition]}]

        try:
            response = await self.engine._client.generate_content_with_tools(
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
            )

            if response and response.function_calls:
                decision = response.function_calls[0].args
                tools = decision.get("tools", [])
                keywords = decision.get("search_keywords", "")
                reason = decision.get("reason", "")

                logger.info(
                    "🤖 Tool Planner 决策: tools=%s, keywords='%s', 理由: %s",
                    tools,
                    keywords,
                    reason,
                )

                return {"next_tools": tools, "search_keywords": keywords}

            logger.warning("Tool Planner 未返回工具调用")
            return {"next_tools": []}

        except Exception as exc:
            logger.error("Tool Planner 执行失败: %s", exc)
            return {"next_tools": []}
