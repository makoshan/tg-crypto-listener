"""
Gemini-based planner using Function Calling API.

Uses Gemini's native Function Calling for structured output,
providing high-quality JSON responses with low latency.
"""

import json
import logging
import re
from typing import Any, Dict, List

from .base import BasePlanner, ToolPlan
from ..helpers.prompts import build_planner_prompt, build_synthesis_prompt

logger = logging.getLogger(__name__)


class GeminiPlanner(BasePlanner):
    """
    Planner using Gemini Function Calling API.

    Features:
    - High-quality structured output (~99% JSON stability)
    - Low latency (~1.5s)
    - Native Function Calling support
    """

    async def plan(
        self,
        state: Dict[str, Any],
        available_tools: List[str]
    ) -> ToolPlan:
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
                        "description": "需要调用的工具列表,可选值: search, price, macro, onchain, protocol",
                    },
                    "search_keywords": {
                        "type": "STRING",
                        "description": "搜索关键词（中英文混合，仅当 tools 包含 search 时需要）",
                    },
                    "macro_indicators": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "当 tools 包含 macro 时，列出需要查询的宏观指标（如 CPI、FED_FUNDS、VIX）",
                    },
                    "onchain_assets": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "当 tools 包含 onchain 时，列出需要重点关注的链上资产代码（如 USDC、USDT）",
                    },
                    "protocol_slugs": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "当 tools 包含 protocol 时，列出需要查询的协议 slug（如 aave、curve-dex）",
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
                tools_list = decision.get("tools", [])
                keywords = decision.get("search_keywords", "")
                macro_indicators = decision.get("macro_indicators", []) or []
                onchain_assets = decision.get("onchain_assets", []) or []
                protocol_slugs = decision.get("protocol_slugs", []) or []
                reason = decision.get("reason", "")

                logger.info(
                    "🤖 Gemini Planner 决策: tools=%s, keywords='%s', macro=%s, onchain=%s, protocol=%s, 理由: %s",
                    tools_list,
                    keywords,
                    macro_indicators,
                    onchain_assets,
                    protocol_slugs,
                    reason,
                )

                return ToolPlan(
                    tools=tools_list,
                    search_keywords=keywords,
                    macro_indicators=macro_indicators,
                    onchain_assets=onchain_assets,
                    protocol_slugs=protocol_slugs,
                    reason=reason,
                )

            logger.warning("Gemini Planner 未返回工具调用")
            return ToolPlan(tools=[], reason="No function call returned")

        except Exception as exc:
            logger.error("Gemini Planner 执行失败: %s", exc)
            raise RuntimeError(f"Gemini planning failed: {exc}") from exc

    async def synthesize(self, state: Dict[str, Any]) -> str:
        """Synthesize evidence into final signal JSON using Gemini."""
        prompt = build_synthesis_prompt(state, self.engine)

        try:
            final_json = await self._invoke_text_model(prompt)

            # Extract JSON from markdown if needed
            json_text = self._extract_json(final_json)

            # Validate JSON
            try:
                parsed = json.loads(json_text.strip())
                final_conf = parsed.get("confidence", 0.0)
                prelim_conf = state["preliminary"].confidence
                logger.info(
                    "📊 Gemini Synthesis: 最终置信度 %.2f (初步 %.2f)",
                    final_conf,
                    prelim_conf
                )
            except json.JSONDecodeError as exc:
                logger.error("📊 Gemini Synthesis: JSON 解析失败 - %s", exc)
                logger.error("📊 原始响应 (前500字符): %s", final_json[:500])
                raise RuntimeError(f"Invalid JSON from Gemini: {exc}") from exc

            return final_json

        except Exception as exc:
            logger.error("Gemini Synthesis 失败: %s", exc)
            raise RuntimeError(f"Gemini synthesis failed: {exc}") from exc

    async def _invoke_text_model(self, prompt: str) -> str:
        """Invoke Gemini for text generation."""
        messages = [{"role": "user", "content": prompt}]
        response = await self.engine._client.generate_content_with_tools(
            messages,
            tools=None
        )

        if not response or not response.text:
            raise RuntimeError("Gemini 返回空响应")

        return response.text.strip()

    def _extract_json(self, text: str) -> str:
        """Extract JSON from markdown code blocks if present."""
        if "```json" in text:
            match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
            if match:
                return match.group(1)
        elif "```" in text:
            match = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
            if match:
                return match.group(1)

        return text.strip()
