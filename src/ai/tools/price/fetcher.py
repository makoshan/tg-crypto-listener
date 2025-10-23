"""Facade for price tool with caching and provider indirection."""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

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
        candidates = self._extract_candidates(asset)

        if not candidates:
            return ToolResult(
                source=self._provider.__class__.__name__,
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_required",
            )

        errors: List[Tuple[str, str]] = []
        last_failure: Optional[ToolResult] = None
        successes: List[Tuple[str, ToolResult]] = []
        triggered_any = False
        max_confidence = 0.0

        for symbol in candidates:
            result = await self._snapshot_single(symbol, force_refresh=force_refresh)
            if result.success:
                successes.append((symbol, result))
                triggered_any = triggered_any or result.triggered
                max_confidence = max(max_confidence, float(result.confidence))
                continue

            errors.append((symbol, result.error or "unknown"))
            last_failure = result

        if len(candidates) == 1:
            return successes[0][1] if successes else last_failure or ToolResult(
                source=self._provider.__class__.__name__,
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=errors[0][1] if errors else "asset_not_supported",
            )

        if successes:
            asset_codes = [symbol.upper() for symbol, _ in successes]
            if errors:
                error_summary = "; ".join(f"{sym.upper()}:{err}" for sym, err in errors)
                logger.warning(
                    "价格工具多资产输入部分失败: raw='%s', successes=%s, failures=%s",
                    asset,
                    asset_codes,
                    error_summary,
                )
            else:
                logger.info(
                    "价格工具多资产输入成功: raw='%s' assets=%s",
                    asset,
                    asset_codes,
                )

            primary_symbol, primary_result = successes[0]
            primary_data = primary_result.data or {}

            snapshots = [
                {
                    "asset": symbol.upper(),
                    "data": result.data,
                    "triggered": result.triggered,
                    "confidence": result.confidence,
                    "timestamp": result.timestamp,
                }
                for symbol, result in successes
            ]

            multi_payload: Dict[str, object] = {
                "multiple": True,
                "asset": ",".join(asset_codes),
                "assets": asset_codes,
                "metrics": primary_data.get("metrics", {}) if isinstance(primary_data, dict) else {},
                "anomalies": primary_data.get("anomalies", {}) if isinstance(primary_data, dict) else {},
                "notes": primary_data.get("notes") if isinstance(primary_data, dict) else None,
                "snapshots": snapshots,
            }
            if errors:
                multi_payload["failed_assets"] = [
                    {"asset": sym.upper(), "error": err}
                    for sym, err in errors
                ]

            return ToolResult(
                source=primary_result.source,
                timestamp=primary_result.timestamp,
                success=True,
                data=multi_payload,
                triggered=triggered_any,
                confidence=round(max_confidence, 2),
            )

        if errors:
            error_summary = "; ".join(f"{sym.upper()}:{err}" for sym, err in errors)
            logger.warning(
                "价格工具多资产输入全部失败: raw='%s', details=%s",
                asset,
                error_summary,
            )
            return ToolResult(
                source=self._provider.__class__.__name__,
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=f"multi_asset_failed: {error_summary}",
            )

        return ToolResult(
            source=self._provider.__class__.__name__,
            timestamp=ToolResult._format_timestamp(),
            success=False,
            data={},
            triggered=False,
            confidence=0.0,
            error="asset_not_supported",
        )

    async def _snapshot_single(self, symbol: str, *, force_refresh: bool) -> ToolResult:
        """Fetch snapshot for a single normalized symbol."""
        symbol = symbol.strip().lower()

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

    @staticmethod
    def _extract_candidates(asset: str) -> List[str]:
        """Split raw asset string into candidate symbols."""
        if not asset:
            return []

        # Support comma/space/slash separated assets (e.g. "WBETH,ETH")
        tokens = [token.strip() for token in re.split(r"[,/|\s]+", asset) if token.strip()]
        if tokens:
            return tokens

        normalized = asset.strip()
        return [normalized] if normalized else []
