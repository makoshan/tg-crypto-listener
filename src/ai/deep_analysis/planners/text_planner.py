"""
Text-only planner supporting generic text generation models.

Supports any OpenAI-compatible API (OpenAI, DeepSeek, Qwen, etc.)
using prompt engineering and JSON parsing instead of Function Calling.
"""

import json
import logging
import re
from typing import Any, Dict, List

from .base import BasePlanner, ToolPlan

logger = logging.getLogger(__name__)


class TextPlanner(BasePlanner):
    """
    Planner using generic text models (OpenAI-compatible).

    Features:
    - Works with any OpenAI-compatible API
    - Prompt-based JSON generation
    - Cost-effective for high-volume processing

    Configuration (from config):
    - TEXT_PLANNER_PROVIDER: Provider name (openai, deepseek, qwen, etc.)
    - TEXT_PLANNER_API_KEY: API key
    - TEXT_PLANNER_MODEL: Model identifier
    - TEXT_PLANNER_BASE_URL: API base URL (optional)

    Note: Requires external dependencies (openai, httpx)
    """

    def __init__(self, engine: Any, config: Any):
        super().__init__(engine, config)
        self.provider = getattr(config, "TEXT_PLANNER_PROVIDER", "").lower()
        self.api_key = getattr(config, "TEXT_PLANNER_API_KEY", "")
        self.model = getattr(config, "TEXT_PLANNER_MODEL", "")
        self.base_url = getattr(config, "TEXT_PLANNER_BASE_URL", "")

        if not self.provider:
            raise ValueError("TEXT_PLANNER_PROVIDER not configured")
        if not self.api_key:
            raise ValueError("TEXT_PLANNER_API_KEY not configured")
        if not self.model:
            raise ValueError("TEXT_PLANNER_MODEL not configured")

        # Initialize client
        self._client = None
        self._init_client()

    def _init_client(self):
        """Initialize provider-specific client."""
        if self.provider == "openai":
            try:
                import openai
                if self.base_url:
                    self._client = openai.AsyncOpenAI(
                        api_key=self.api_key,
                        base_url=self.base_url
                    )
                else:
                    self._client = openai.AsyncOpenAI(api_key=self.api_key)
                logger.info("TextPlanner: 使用 OpenAI provider")
            except ImportError as exc:
                raise RuntimeError(
                    "OpenAI provider requires 'openai' package. "
                    "Install with: pip install openai"
                ) from exc

        elif self.provider in ("deepseek", "qwen", "generic"):
            # Generic OpenAI-compatible API
            try:
                import openai
                if not self.base_url:
                    raise ValueError(
                        f"TEXT_PLANNER_BASE_URL required for provider '{self.provider}'"
                    )
                self._client = openai.AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url
                )
                logger.info("TextPlanner: 使用 %s provider (OpenAI-compatible)", self.provider)
            except ImportError as exc:
                raise RuntimeError(
                    "Generic provider requires 'openai' package. "
                    "Install with: pip install openai"
                ) from exc

        else:
            raise ValueError(
                f"Unsupported TEXT_PLANNER_PROVIDER: {self.provider}. "
                f"Supported: openai, deepseek, qwen, generic"
            )

    async def plan(
        self,
        state: Dict[str, Any],
        available_tools: List[str]
    ) -> ToolPlan:
        """Use text model for tool planning decision."""
        prompt = self._build_planner_prompt(state, available_tools)

        try:
            text_output = await self._generate_text(prompt)
            json_text = self._extract_json(text_output)
            data = json.loads(json_text)

            tools_list = data.get("tools", [])
            keywords = data.get("search_keywords", "")
            macro_indicators = data.get("macro_indicators", []) or []
            onchain_assets = data.get("onchain_assets", []) or []
            protocol_slugs = data.get("protocol_slugs", []) or []
            reason = data.get("reason", "")

            logger.info(
                "🤖 Text Planner (%s) 决策: tools=%s, keywords='%s', macro=%s, onchain=%s, protocol=%s, 理由: %s",
                self.provider,
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

        except json.JSONDecodeError as exc:
            logger.error("Text Planner 返回无效 JSON: %s", exc)
            logger.error("模型输出 (前500字符): %s", text_output[:500] if 'text_output' in locals() else "N/A")
            raise RuntimeError(f"Invalid JSON from text model: {exc}") from exc
        except Exception as exc:
            logger.error("Text Planner 执行失败: %s", exc)
            raise RuntimeError(f"Text planner failed: {exc}") from exc

    async def synthesize(self, state: Dict[str, Any]) -> str:
        """Synthesize evidence into final signal JSON using text model."""
        prompt = self._build_synthesis_prompt(state)

        try:
            text_output = await self._generate_text(prompt)
            json_text = self._extract_json(text_output)

            # Validate JSON
            try:
                parsed = json.loads(json_text)
                final_conf = parsed.get("confidence", 0.0)
                prelim_conf = state["preliminary"].confidence
                logger.info(
                    "📊 Text Planner (%s) Synthesis: 最终置信度 %.2f (初步 %.2f)",
                    self.provider,
                    final_conf,
                    prelim_conf
                )
            except json.JSONDecodeError as exc:
                logger.error("📊 Text Planner Synthesis: JSON 解析失败 - %s", exc)
                logger.error("📊 原始响应 (前500字符): %s", text_output[:500])
                raise RuntimeError(f"Invalid JSON from text model: {exc}") from exc

            return text_output

        except Exception as exc:
            logger.error("Text Planner Synthesis 失败: %s", exc)
            raise RuntimeError(f"Text planner synthesis failed: {exc}") from exc

    async def _generate_text(self, prompt: str) -> str:
        """
        Generate text using provider API.

        Args:
            prompt: Full prompt text

        Returns:
            Model response text

        Raises:
            RuntimeError: If API call fails
        """
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,  # Lower temperature for more consistent JSON
            )

            if not response.choices:
                raise RuntimeError("No choices in API response")

            text = response.choices[0].message.content
            if not text:
                raise RuntimeError("Empty response from API")

            logger.debug("Text model 输出 (前200字符): %s", text[:200])
            return text

        except Exception as exc:
            logger.error("Text model API 调用失败: %s", exc)
            raise RuntimeError(f"Text model API call failed: {exc}") from exc

    def _build_planner_prompt(self, state: Dict[str, Any], available_tools: List[str]) -> str:
        """Build prompt for tool planning."""
        payload = state["payload"]
        preliminary = state["preliminary"]
        evidence_summary = self._summarize_evidence(state)

        prompt = f"""你是加密交易分析专家，决定需要调用哪些工具来验证事件真实性。

【事件消息】
{payload.text}

【初步分析】
类型: {preliminary.event_type}
资产: {preliminary.asset}
操作: {preliminary.action}
置信度: {preliminary.confidence}

【已有证据】
{evidence_summary}

【可用工具】
{', '.join(available_tools)}

【任务】
决定需要调用哪些工具，生成搜索关键词（如果需要搜索），选择宏观指标（如果需要宏观数据）。

【输出格式】
必须返回有效 JSON 格式，不要包含 markdown 标记：
{{
  "tools": ["search", "price"],
  "search_keywords": "搜索关键词（中英文混合）",
  "macro_indicators": ["CPI", "VIX"],
  "onchain_assets": ["USDC", "USDT"],
  "protocol_slugs": ["aave", "curve-dex"],
  "reason": "决策理由"
}}

只返回 JSON，不要包含任何其他文字。
"""
        return prompt

    def _build_synthesis_prompt(self, state: Dict[str, Any]) -> str:
        """Build prompt for evidence synthesis."""
        payload = state["payload"]
        preliminary = state["preliminary"]
        evidence_summary = self._summarize_evidence(state)

        prompt = f"""你是加密交易分析师，综合所有证据生成最终分析报告。

【原始消息】
{payload.text}

【初步分析】
类型: {preliminary.event_type}
资产: {preliminary.asset}
操作: {preliminary.action}
置信度: {preliminary.confidence}
摘要: {preliminary.summary}

【所有证据】
{evidence_summary}

【任务】
综合所有证据，生成最终分析结论，给出交易建议和置信度。

【输出格式】
必须返回有效 JSON 格式，不要包含 markdown 标记：
{{
  "summary": "中文摘要，简明扼要描述事件核心",
  "event_type": "{preliminary.event_type}",
  "asset": "{preliminary.asset}",
  "action": "buy|sell|observe",
  "confidence": 0.0-1.0,
  "notes": "推理依据，引用关键证据",
  "links": [],
  "risk_flags": []
}}

只返回 JSON，不要包含任何其他文字。
"""
        return prompt

    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from text, handling markdown wrapping.

        Tries multiple patterns:
        1. ```json ... ```
        2. ``` ... ```
        3. Raw JSON (fallback)
        """
        # Try markdown json block
        if "```json" in text:
            match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
            if match:
                return match.group(1)

        # Try generic code block
        if "```" in text:
            match = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
            if match:
                return match.group(1)

        # Try to find JSON object
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json_match.group(0)

        # Fallback: return as-is
        return text.strip()
