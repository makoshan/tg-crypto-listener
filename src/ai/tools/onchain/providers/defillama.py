"""DeFiLlama-backed on-chain liquidity provider."""

from __future__ import annotations

import time
from typing import Dict, Optional

import httpx

from ...base import ToolResult
from .base import OnchainProvider
from src.utils import setup_logger

logger = setup_logger(__name__)


def _safe_get_pegged_usd(node: Optional[dict]) -> Optional[float]:
    """Safely read pegged USD value from a nested structure."""
    if not isinstance(node, dict):
        return None
    value = node.get("peggedUSD") or node.get("pegged_usd")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class StablecoinDataset:
    """Helper to cache and retrieve stablecoin data from DeFiLlama."""

    DEFAULT_URL = "https://stablecoins.llama.fi/stablecoins"

    def __init__(self, base_url: str, cache_ttl: int, timeout: int) -> None:
        self._url = base_url.rstrip("?")
        if "includePrices=true" not in self._url:
            if "?" in self._url:
                self._url += "&includePrices=true"
            else:
                self._url += "?includePrices=true"
        self._cache_ttl = max(cache_ttl, 30)
        self._timeout = timeout
        self._payload: Optional[dict] = None
        self._fetched_at: float = 0.0

    async def get_asset(self, symbol: str) -> Optional[dict]:
        """Return the stablecoin entry matching the given symbol."""
        symbol_lower = symbol.lower()
        data = await self._load()
        if not data:
            return None

        assets = data.get("peggedAssets") or []
        best_match: Optional[dict] = None
        for entry in assets:
            if not isinstance(entry, dict):
                continue
            entry_symbol = str(entry.get("symbol", "")).lower()
            if entry_symbol == symbol_lower:
                best_match = entry
                break
            # fallback: fuzzy match by gecko id or name
            gecko_id = str(entry.get("gecko_id", "")).lower()
            name = str(entry.get("name", "")).lower()
            if symbol_lower in {gecko_id, name}:
                best_match = entry
        return best_match

    async def _load(self) -> Optional[dict]:
        now = time.time()
        if self._payload and now - self._fetched_at < self._cache_ttl:
            return self._payload

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    self._payload = payload
                    self._fetched_at = now
                    return payload
        except Exception as exc:
            logger.warning("获取 DeFiLlama 稳定币数据失败: %s", exc)

        return self._payload


class DeFiLlamaOnchainProvider(OnchainProvider):
    """Fetch on-chain liquidity metrics using DeFiLlama stablecoin API.

    Note: This provider only supports stablecoins (USDC, USDT, DAI, etc).
    Non-stablecoin assets (BTC, ETH, etc) are not supported by this data source.
    """

    # Known non-stablecoin assets that should skip this provider
    UNSUPPORTED_ASSETS = {
        "BTC", "ETH", "BNB", "SOL", "ADA", "XRP", "DOGE", "AVAX", "DOT", "MATIC",
        "WBTC", "WETH", "STETH", "WSTETH", "RETH", "CBETH", "WBETH", "BNSOL",
        "ARB", "OP", "LDO", "UNI", "AAVE", "LINK", "MKR", "SNX", "CRV", "SUSHI",
    }

    def __init__(self, config) -> None:
        super().__init__(config)
        self._base_url = getattr(
            config,
            "DEFI_LLAMA_STABLECOIN_URL",
            "https://stablecoins.llama.fi/stablecoins",
        )
        self._registry_cache_ttl = int(
            getattr(config, "ONCHAIN_REGISTRY_CACHE_SECONDS", 300)
        )
        self._tvl_drop_threshold = float(
            getattr(config, "ONCHAIN_TVL_DROP_THRESHOLD", 20.0)
        )
        self._redemption_threshold = float(
            getattr(config, "ONCHAIN_REDEMPTION_USD_THRESHOLD", 500_000_000.0)
        )

        timeout = getattr(config, "DEEP_ANALYSIS_TOOL_TIMEOUT", 10)
        self._dataset = StablecoinDataset(
            self._base_url, self._registry_cache_ttl, timeout
        )

    async def snapshot(self, *, asset: str) -> ToolResult:
        symbol = asset.strip().upper()
        if not symbol:
            return ToolResult(
                source="DeFiLlama",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_required",
            )

        # Skip known non-stablecoin assets to reduce log noise
        if symbol in self.UNSUPPORTED_ASSETS:
            logger.debug("DeFiLlama 跳过非稳定币资产: %s (仅支持稳定币数据)", symbol)
            return ToolResult(
                source="DeFiLlama",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_type_not_supported",
            )

        entry = await self._dataset.get_asset(symbol)
        if not entry:
            logger.debug("DeFiLlama 未找到资产: %s (可能非稳定币)", symbol)
            return ToolResult(
                source="DeFiLlama",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_not_found",
            )

        current = _safe_get_pegged_usd(entry.get("circulating"))
        prev_day = _safe_get_pegged_usd(entry.get("circulatingPrevDay"))
        prev_week = _safe_get_pegged_usd(entry.get("circulatingPrevWeek"))

        if current is None:
            return ToolResult(
                source="DeFiLlama",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="missing_current_value",
            )

        change_24h_pct = (
            ((current - prev_day) / prev_day * 100.0)
            if prev_day not in (None, 0)
            else None
        )
        change_7d_pct = (
            ((current - prev_week) / prev_week * 100.0)
            if prev_week not in (None, 0)
            else None
        )
        redemption_24h_usd = (prev_day - current) if prev_day and prev_day > current else 0.0
        redemption_7d_usd = (prev_week - current) if prev_week and prev_week > current else 0.0

        anomalies: Dict[str, bool] = {}
        if change_24h_pct is not None and change_24h_pct <= -self._tvl_drop_threshold:
            anomalies["tvl_drop_24h"] = True
        if redemption_24h_usd and redemption_24h_usd >= self._redemption_threshold:
            anomalies["redemption_spike_24h"] = True
        if change_7d_pct is not None and change_7d_pct <= -self._tvl_drop_threshold:
            anomalies["tvl_drop_7d"] = True
        if redemption_7d_usd and redemption_7d_usd >= self._redemption_threshold:
            anomalies["redemption_spike_7d"] = True

        metrics = {
            "asset": symbol,
            "tvl_usd": current,
            "circulating_prev_day": prev_day,
            "circulating_prev_week": prev_week,
            "tvl_change_24h_pct": change_24h_pct,
            "tvl_change_7d_pct": change_7d_pct,
            "redemption_24h_usd": redemption_24h_usd,
            "redemption_7d_usd": redemption_7d_usd,
            "supply_breakdown": entry.get("chains", []),
            "peg_type": entry.get("pegType"),
        }

        data = {
            "asset": symbol,
            "metrics": metrics,
            "anomalies": anomalies,
            "thresholds": {
                "tvl_drop_threshold_pct": self._tvl_drop_threshold,
                "redemption_usd_threshold": self._redemption_threshold,
            },
            "source": "DeFiLlama",
            "timestamp": ToolResult._format_timestamp(),
            "notes": "数据来源: stablecoins.llama.fi (circulating supply metrics)",
        }

        triggered = bool(anomalies)

        return ToolResult(
            source="DeFiLlama",
            timestamp=data["timestamp"],
            success=True,
            data=data,
            triggered=triggered,
            confidence=1.0,
        )
