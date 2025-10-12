"""Tool Executor node for executing planned tools."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .base import BaseNode

logger = logging.getLogger(__name__)


class ToolExecutorNode(BaseNode):
    """Node for executing tools decided by planner."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute planned tools with quota checking."""
        tools_to_call = state.get("next_tools", [])
        logger.info("🔧 Tool Executor: 调用工具: %s", tools_to_call)

        # Check daily quota
        if not self._check_quota():
            logger.warning("⚠️ 超出每日配额")
            return {"tool_call_count": state["tool_call_count"] + 1}

        updates = {"tool_call_count": state["tool_call_count"] + 1}

        for tool_name in tools_to_call:
            if tool_name == "search":
                result = await self._execute_search(state)
                if result:
                    updates["search_evidence"] = result
            elif tool_name == "price":
                result = await self._execute_price(state)
                if result:
                    updates["price_evidence"] = result
            elif tool_name == "macro":
                result = await self._execute_macro(state)
                if result:
                    updates["macro_evidence"] = result
            elif tool_name == "onchain":
                result = await self._execute_onchain(state)
                if result:
                    updates["onchain_evidence"] = result
            else:
                logger.warning("未知工具: %s", tool_name)

        return updates

    async def _execute_search(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute search tool with domain whitelisting."""
        if not self.engine._search_tool:
            logger.warning("搜索工具未初始化")
            return None

        preliminary = state["preliminary"]
        keyword = state.get("search_keywords", "").strip()

        # Fallback to hardcoded keywords
        if not keyword:
            keyword = f"{preliminary.asset} {preliminary.event_type}"
            logger.info("使用硬编码关键词: '%s'", keyword)
        else:
            logger.info("使用 AI 生成关键词: '%s'", keyword)

        # Get domain whitelist
        include_domains = None
        if hasattr(self.engine._config, "HIGH_PRIORITY_EVENT_DOMAINS"):
            include_domains = self.engine._config.HIGH_PRIORITY_EVENT_DOMAINS.get(
                preliminary.event_type
            )

        try:
            result = await self.engine._search_tool.fetch(
                keyword=keyword,
                max_results=5,
                include_domains=include_domains,
            )

            if result.success:
                logger.info(
                    "🔧 搜索返回 %d 条结果 (multi_source=%s)",
                    result.data.get("source_count", 0),
                    result.data.get("multi_source"),
                )
                return {
                    "success": True,
                    "data": result.data,
                    "triggered": result.triggered,
                    "confidence": result.confidence,
                }

            logger.warning("搜索失败: %s", result.error)
            return None

        except Exception as exc:
            logger.error("搜索工具异常: %s", exc)
            return None

    async def _execute_price(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute price tool to fetch market data for the asset."""
        if not self.engine._price_tool:
            logger.warning("价格工具未初始化")
            return None

        preliminary = state["preliminary"]
        asset = preliminary.asset

        # Skip if no valid asset
        if not asset or asset.upper() == "NONE":
            logger.info("跳过价格查询: 无有效资产")
            return None

        try:
            result = await self.engine._price_tool.snapshot(asset=asset)

            if result.success:
                logger.info(
                    "💰 价格工具返回数据 (triggered=%s, confidence=%.2f)",
                    result.triggered,
                    result.confidence,
                )
                return {
                    "success": True,
                    "data": result.data,
                    "triggered": result.triggered,
                    "confidence": result.confidence,
                }

            logger.warning("价格工具失败: %s", result.error)
            return None

        except Exception as exc:
                logger.error("价格工具异常: %s", exc)
                return None

    async def _execute_macro(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute macro tool to fetch macro-economic indicators."""
        if not self.engine._macro_tool:
            logger.warning("宏观工具未初始化")
            return None

        indicators = self._resolve_macro_indicators(state)
        if not indicators:
            logger.info("宏观工具跳过: 未找到合适的宏观指标")
            return None

        primary_indicator = indicators[0]
        try:
            result = await self.engine._macro_tool.snapshot(indicator=primary_indicator)

            if result.success:
                logger.info(
                    "🌐 宏观工具返回数据 (indicator=%s, triggered=%s, confidence=%.2f)",
                    primary_indicator,
                    result.triggered,
                    result.confidence,
                )
                return {
                    "success": True,
                    "data": result.data,
                    "triggered": result.triggered,
                    "confidence": result.confidence,
                    "indicator": primary_indicator,
                }

            logger.warning("宏观工具失败: %s", result.error)
            return None
        except Exception as exc:
            logger.error("宏观工具异常: %s", exc)
            return None

    async def _execute_onchain(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute on-chain tool to fetch liquidity metrics."""
        if not self.engine._onchain_tool:
            logger.warning("链上工具未初始化")
            return None

        assets = self._resolve_onchain_assets(state)
        if not assets:
            logger.info("链上工具跳过: 未找到合适的资产")
            return None

        for candidate in assets:
            try:
                result = await self.engine._onchain_tool.snapshot(asset=candidate)
            except Exception as exc:
                logger.error("链上工具异常 (asset=%s): %s", candidate, exc)
                continue

            if result.success:
                logger.info(
                    "⛓️ 链上工具返回数据 (asset=%s, triggered=%s, confidence=%.2f)",
                    candidate,
                    result.triggered,
                    result.confidence,
                )
                return {
                    "success": True,
                    "data": result.data,
                    "triggered": result.triggered,
                    "confidence": result.confidence,
                    "asset": candidate,
                }

            logger.warning("链上工具失败 (asset=%s): %s", candidate, result.error)

        return None

    def _resolve_macro_indicators(self, state: Dict[str, Any]) -> list[str]:
        """Resolve indicator list from planner output or heuristics."""
        indicators = [
            indicator.strip().upper()
            for indicator in state.get("macro_indicators", []) or []
            if isinstance(indicator, str) and indicator.strip()
        ]
        if indicators:
            return indicators

        payload = state.get("payload")
        preliminary = state.get("preliminary")

        text = ""
        if payload is not None:
            text = f"{getattr(payload, 'text', '')}\n{getattr(payload, 'translated_text', '')}"
        text_lower = text.lower()

        suggestions: list[str] = []
        if "cpi" in text_lower or "通胀" in text_lower or "inflation" in text_lower:
            suggestions.append("CPI")
        if "核心通胀" in text_lower or "core" in text_lower:
            suggestions.append("CORE_CPI")
        if any(keyword in text_lower for keyword in ["加息", "降息", "rate hike", "interest rate"]):
            suggestions.append("FED_FUNDS")
        if any(keyword in text_lower for keyword in ["就业", "失业", "job", "labor"]):
            suggestions.append("UNEMPLOYMENT")
        if any(keyword in text_lower for keyword in ["美元", "dxy", "usd index", "trade war", "贸易战"]):
            suggestions.append("DXY")
        if any(keyword in text_lower for keyword in ["恐慌", "战争", "war", "conflict", "geopolitical"]):
            suggestions.append("VIX")

        event_type = getattr(preliminary, "event_type", "").lower() if preliminary else ""
        if event_type == "macro" and not suggestions:
            suggestions.append("CPI")

        # 去重同时保持顺序
        seen = set()
        ordered = []
        for indicator in suggestions:
            if indicator not in seen:
                seen.add(indicator)
                ordered.append(indicator)

        return ordered

    def _resolve_onchain_assets(self, state: Dict[str, Any]) -> list[str]:
        """Resolve which assets require on-chain inspection."""
        assets = [
            token.strip().upper()
            for token in state.get("onchain_assets", []) or []
            if isinstance(token, str) and token.strip()
        ]

        if assets:
            return assets

        preliminary = state.get("preliminary")
        if not preliminary:
            return assets

        asset_field = getattr(preliminary, "asset", "") or ""
        tokens = [
            token.strip().upper()
            for token in asset_field.split(",")
            if token.strip() and token.strip().upper() != "NONE"
        ]

        seen: set[str] = set()
        ordered: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.add(token)
                ordered.append(token)

        return ordered

    def _check_quota(self) -> bool:
        """Check if daily quota is exceeded."""
        today = datetime.now(timezone.utc).date()

        if today != self.engine._tool_call_reset_date:
            if self.engine._tool_call_count_today:
                logger.info(
                    "🔄 重置工具调用计数: 之前=%d, 日期=%s",
                    self.engine._tool_call_count_today,
                    self.engine._tool_call_reset_date,
                )
            self.engine._tool_call_count_today = 0
            self.engine._tool_call_reset_date = today

        if self.engine._tool_call_count_today >= self.engine._tool_call_daily_limit:
            logger.warning(
                "⚠️ 工具调用达到每日上限 (%d/%d)",
                self.engine._tool_call_count_today,
                self.engine._tool_call_daily_limit,
            )
            return False

        self.engine._tool_call_count_today += 1
        return True
