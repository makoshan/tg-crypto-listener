"""On-chain tool facade with caching and provider abstraction."""

from __future__ import annotations

import time
from typing import Dict, Tuple

from src.utils import setup_logger

from ..base import ToolResult
from . import create_onchain_provider

logger = setup_logger(__name__)


class OnchainTool:
    """Provide cached on-chain liquidity snapshots."""

    def __init__(self, config) -> None:
        self._config = config
        self._provider = create_onchain_provider(config)
        self._cache: Dict[str, Tuple[ToolResult, float]] = {}
        self._cache_ttl = getattr(config, "ONCHAIN_CACHE_TTL_SECONDS", 300)

    async def snapshot(self, *, asset: str, force_refresh: bool = False) -> ToolResult:
        """Fetch an on-chain liquidity snapshot."""
        symbol = (asset or "").strip().upper()
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
                logger.debug("链上工具命中缓存: %s", symbol)
                return cached_result
            del self._cache[symbol]

        logger.info("链上工具请求: asset=%s", symbol)
        result = await self._provider.snapshot(asset=symbol)

        if result.success:
            self._cache[symbol] = (result, time.time())
            logger.info(
                "链上工具成功: asset=%s, triggered=%s, confidence=%.2f",
                symbol,
                result.triggered,
                result.confidence,
            )
        else:
            # Use debug level for expected asset type mismatches
            if result.error in ("asset_type_not_supported", "asset_not_found"):
                logger.debug(
                    "链上工具跳过: asset=%s, error=%s",
                    symbol,
                    result.error,
                )
            else:
                logger.warning(
                    "链上工具失败: asset=%s, error=%s",
                    symbol,
                    result.error or "unknown",
                )

        return result

    def refresh_provider(self) -> None:
        """Reload provider and clear cache."""
        self._provider = create_onchain_provider(self._config)
        self._cache.clear()
        logger.info("链上工具 provider 已刷新并清空缓存")
