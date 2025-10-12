"""Tool Planner node for deciding which tools to call."""
import logging
from typing import Any, Dict

from .base import BaseNode
from ..helpers.prompts import build_planner_prompt

logger = logging.getLogger(__name__)

# Event type filters
NEVER_SEARCH_EVENT_TYPES = {"macro", "governance", "airdrop", "celebrity"}
FORCE_SEARCH_EVENT_TYPES = {"hack", "regulation", "partnership"}


class ToolPlannerNode(BaseNode):
    """Node for AI-powered tool planning and keyword generation."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Decide which tools to call and generate search keywords."""
        logger.info("🤖 Tool Planner: 决策下一步工具")

        preliminary = state["preliminary"]

        # Blacklist check
        if preliminary.event_type in NEVER_SEARCH_EVENT_TYPES:
            logger.info("🤖 Tool Planner: 事件类型 '%s' 在黑名单", preliminary.event_type)
            return {"next_tools": []}

        # Whitelist check (first turn only)
        if preliminary.event_type in FORCE_SEARCH_EVENT_TYPES and state["tool_call_count"] == 0:
            logger.info("🤖 Tool Planner: 事件类型 '%s' 在白名单，强制搜索", preliminary.event_type)
            keyword = await self._generate_keywords_ai(state)
            return {"next_tools": ["search"], "search_keywords": keyword}

        # Already have search results
        if state.get("search_evidence"):
            logger.info("🤖 Tool Planner: 已有搜索结果")
            return {"next_tools": []}

        # AI decision using Function Calling
        return await self._decide_with_function_calling(state)

    async def _decide_with_function_calling(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Use Gemini Function Calling for structured decision."""
        prompt = build_planner_prompt(state, self.engine)

        tool_definition = {
            "name": "decide_next_tools",
            "description": "根据已有证据决定下一步需要调用的工具，并为搜索生成最优关键词",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "tools": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "需要调用的工具列表,可选值: search",
                    },
                    "search_keywords": {
                        "type": "STRING",
                        "description": "搜索关键词（中英文混合）",
                    },
                    "reason": {"type": "STRING", "description": "决策理由"},
                },
                "required": ["tools", "reason"],
            },
        }

        try:
            response = await self.engine._client.generate_content_with_tools(
                messages=[{"role": "user", "content": prompt}],
                tools=[tool_definition],
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

    async def _generate_keywords_ai(self, state: Dict[str, Any]) -> str:
        """Generate keywords using AI for whitelist events."""
        # Use Function Calling to generate keywords
        result = await self._decide_with_function_calling(state)
        return result.get("search_keywords", "")
