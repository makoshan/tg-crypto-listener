"""Facade for price tool with caching and provider indirection."""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from src.utils import setup_logger

from ..base import ToolResult
from . import create_price_provider

logger = setup_logger(__name__)


class PriceTool:
    """Provide cached CoinGecko-based price snapshots."""

    def __init__(self, config) -> None:
        self._config = config
        self._provider = create_price_provider(config)
        self._cache: Dict[str, Tuple[ToolResult, float]] = {}
        self._cache_ttl = getattr(config, "PRICE_CACHE_TTL_SECONDS", 60)

    async def snapshot(self, *, asset: str, force_refresh: bool = False) -> ToolResult:
        """Fetch a price snapshot, reusing cached results when valid."""
        symbol = asset.strip().lower()
        if not symbol:
            return ToolResult(
                source=self._provider.__class__.__name__,
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_required",
            )

        if not force_refresh and symbol in self._cache:
            cached_result, cached_at = self._cache[symbol]
            if time.time() - cached_at < self._cache_ttl:
                logger.debug("价格工具命中缓存: %s", symbol.upper())
                return cached_result
            del self._cache[symbol]

        logger.info("价格工具请求: asset=%s", symbol.upper())
        result = await self._provider.snapshot(asset=symbol)

        if result.success:
            self._cache[symbol] = (result, time.time())
            logger.info(
                "价格工具成功: asset=%s, triggered=%s, confidence=%.2f",
                symbol.upper(),
                result.triggered,
                result.confidence,
            )
        else:
            logger.warning(
                "价格工具失败: asset=%s, error=%s",
                symbol.upper(),
                result.error or "unknown",
            )

        return result

    def refresh_provider(self) -> None:
        """Reload provider, allowing runtime configuration changes."""
        self._provider = create_price_provider(self._config)
        self._cache.clear()
        logger.info("价格工具 provider 已刷新并清空缓存")
