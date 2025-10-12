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
