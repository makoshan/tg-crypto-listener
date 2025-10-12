"""DeFiLlama-backed protocol provider implementation."""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

import httpx

from ...base import ToolResult
from .base import ProtocolProvider
from src.utils import setup_logger

logger = setup_logger(__name__)


def _safe_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_core_chain_key(key: str) -> bool:
    key_lower = key.lower()
    return not any(
        marker in key_lower
        for marker in ("borrowed", "staking", "pool2", "doublecounted", "mcap")
    )


class DeFiLlamaProtocolProvider(ProtocolProvider):
    """Fetch protocol-level metrics from DeFiLlama."""

    API_BASE_URL = "https://api.llama.fi/protocol"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._base_url = getattr(
            config,
            "DEFI_LLAMA_PROTOCOL_URL",
            self.API_BASE_URL,
        ).rstrip("/")
        self._cache_ttl = int(getattr(config, "PROTOCOL_CACHE_TTL_SECONDS", 600))
        self._top_chain_limit = int(getattr(config, "PROTOCOL_TOP_CHAIN_LIMIT", 5))
        self._tvl_drop_threshold_pct = float(
            getattr(config, "PROTOCOL_TVL_DROP_THRESHOLD_PCT", 15.0)
        )
        self._tvl_drop_threshold_usd = float(
            getattr(config, "PROTOCOL_TVL_DROP_THRESHOLD_USD", 300_000_000.0)
        )

        self._cache: Dict[str, Tuple[dict, float]] = {}

    async def snapshot(self, *, slug: str) -> ToolResult:
        normalized = slug.strip().lower()
        if not normalized:
            return ToolResult(
                source="DeFiLlama",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="protocol_required",
            )

        payload = await self._fetch_protocol(normalized)
        if payload is None:
            return ToolResult(
                source="DeFiLlama",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="protocol_not_found",
            )

        metrics, anomalies = self._calculate_metrics(payload)

        data = {
            "slug": normalized,
            "name": payload.get("name"),
            "url": payload.get("url"),
            "symbol": payload.get("symbol"),
            "twitter": payload.get("twitter"),
            "category": payload.get("category"),
            "chains": payload.get("chains"),
            "metrics": metrics,
            "anomalies": anomalies,
            "thresholds": {
                "tvl_drop_threshold_pct": self._tvl_drop_threshold_pct,
                "tvl_drop_threshold_usd": self._tvl_drop_threshold_usd,
            },
            "source": "DeFiLlama",
            "notes": f"数据来源: {self._base_url}/{normalized}",
        }

        triggered = bool(anomalies)

        return ToolResult(
            source="DeFiLlama",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data=data,
            triggered=triggered,
            confidence=1.0,
        )

    async def _fetch_protocol(self, slug: str) -> Optional[dict]:
        cached = self._cache.get(slug)
        now = time.time()
        if cached and now - cached[1] < self._cache_ttl:
            return cached[0]

        url = f"{self._base_url}/{slug}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)
                if response.status_code == 404:
                    logger.info("DeFiLlama 协议未找到: %s", slug)
                    return None
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    self._cache[slug] = (payload, now)
                    return payload
        except httpx.TimeoutException:
            logger.warning("DeFiLlama 协议请求超时: %s", slug)
        except Exception as exc:
            logger.error("DeFiLlama 协议请求失败: slug=%s error=%s", slug, exc)
        return None

    def _calculate_metrics(self, payload: dict) -> Tuple[dict, Dict[str, bool]]:
        tvl_series: List[dict] = payload.get("tvl") or []
        if not tvl_series:
            return {}, {}

        tvl_series_sorted = sorted(
            tvl_series, key=lambda entry: entry.get("date", 0)
        )

        current = _safe_float(tvl_series_sorted[-1].get("totalLiquidityUSD"))
        prev = _safe_float(tvl_series_sorted[-2].get("totalLiquidityUSD")) if len(tvl_series_sorted) > 1 else None
        prev7 = (
            _safe_float(tvl_series_sorted[-8].get("totalLiquidityUSD"))
            if len(tvl_series_sorted) > 7
            else None
        )

        change_24h_pct = (
            ((current - prev) / prev * 100.0) if current is not None and prev not in (None, 0) else None
        )
        change_7d_pct = (
            ((current - prev7) / prev7 * 100.0) if current is not None and prev7 not in (None, 0) else None
        )
        delta_24h_usd = (current - prev) if current is not None and prev is not None else None
        delta_7d_usd = (current - prev7) if current is not None and prev7 is not None else None

        current_chain_tvls = payload.get("currentChainTvls") or {}
        top_chains = self._select_top_chains(current_chain_tvls)

        metrics = {
            "tvl_usd": current,
            "tvl_1d_ago": prev,
            "tvl_7d_ago": prev7,
            "tvl_change_24h_pct": change_24h_pct,
            "tvl_change_7d_pct": change_7d_pct,
            "tvl_change_24h_usd": delta_24h_usd,
            "tvl_change_7d_usd": delta_7d_usd,
            "top_chains": top_chains,
        }

        anomalies: Dict[str, bool] = {}
        if change_24h_pct is not None and change_24h_pct <= -self._tvl_drop_threshold_pct:
            anomalies["tvl_drop_24h_pct"] = True
        if change_7d_pct is not None and change_7d_pct <= -self._tvl_drop_threshold_pct:
            anomalies["tvl_drop_7d_pct"] = True
        if delta_24h_usd is not None and delta_24h_usd <= -self._tvl_drop_threshold_usd:
            anomalies["tvl_drop_24h_usd"] = True
        if delta_7d_usd is not None and delta_7d_usd <= -self._tvl_drop_threshold_usd:
            anomalies["tvl_drop_7d_usd"] = True

        return metrics, anomalies

    def _select_top_chains(self, current_chain_tvls: dict) -> List[dict]:
        items: List[Tuple[str, float]] = []
        for key, value in (current_chain_tvls or {}).items():
            tvl_value = _safe_float(value)
            if tvl_value is None or tvl_value <= 0:
                continue
            if not _is_core_chain_key(key):
                continue
            items.append((key, tvl_value))

        items.sort(key=lambda entry: entry[1], reverse=True)
        limit = max(1, self._top_chain_limit)
        return [
            {"chain": chain, "tvl_usd": round(amount, 2)}
            for chain, amount in items[:limit]
        ]
