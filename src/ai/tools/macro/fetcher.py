"\"\"\"Macro tool facade with caching and provider abstraction.\"\"\""

from __future__ import annotations

import time
from typing import Dict, Tuple

from src.utils import setup_logger

from ..base import ToolResult
from . import create_macro_provider

logger = setup_logger(__name__)


class MacroTool:
    """Provide cached macro indicator snapshots via configured provider."""

    def __init__(self, config) -> None:
        self._config = config
        self._provider = create_macro_provider(config)
        self._cache: Dict[str, Tuple[ToolResult, float]] = {}
        self._cache_ttl = getattr(config, "MACRO_CACHE_TTL_SECONDS", 1800)  # default 30 minutes

    async def snapshot(self, *, indicator: str, force_refresh: bool = False) -> ToolResult:
        """Fetch a macro snapshot, reusing cached results when valid."""
        normalized = (indicator or "").strip().upper()
        if not normalized:
            return ToolResult(
                source=self._provider.__class__.__name__,
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="indicator_required",
            )

        if not force_refresh and normalized in self._cache:
            cached_result, cached_at = self._cache[normalized]
            if time.time() - cached_at < self._cache_ttl:
                logger.debug("宏观工具命中缓存: %s", normalized)
                return cached_result
            del self._cache[normalized]

        logger.info("宏观工具请求: indicator=%s", normalized)
        result = await self._provider.snapshot(indicator=normalized)

        if result.success:
            self._cache[normalized] = (result, time.time())
            logger.info(
                "宏观工具成功: indicator=%s, triggered=%s, confidence=%.2f",
                normalized,
                result.triggered,
                result.confidence,
            )
        else:
            logger.warning(
                "宏观工具失败: indicator=%s, error=%s",
                normalized,
                result.error or "unknown",
            )

        return result

    def refresh_provider(self) -> None:
        """Reload provider, clearing cache to reflect new configuration."""
        self._provider = create_macro_provider(self._config)
        self._cache.clear()
        logger.info("宏观工具 provider 已刷新并清空缓存")
