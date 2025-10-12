"""CoinGecko-backed price provider implementation."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Dict, Optional

import httpx

from ...base import ToolResult
from ...exceptions import ToolRateLimitError
from .base import PriceProvider
from src.config import PROJECT_ROOT
from src.utils import setup_logger

logger = setup_logger(__name__)


class AssetRegistry:
    """Resolve asset symbols to CoinGecko IDs with caching support."""

    STATIC_PATH = PROJECT_ROOT / "data" / "asset_registry.json"
    CACHE_PATH = PROJECT_ROOT / ".cache" / "coingecko_ids.json"

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._ids: Dict[str, str] = {}
        self._load_static()
        self._load_cache()

    def _load_static(self) -> None:
        if not self.STATIC_PATH.exists():
            logger.warning("资产映射文件缺失: %s", self.STATIC_PATH)
            return

        try:
            data = json.loads(self.STATIC_PATH.read_text(encoding="utf-8"))
            for symbol, cg_id in data.items():
                self._ids[symbol.lower()] = cg_id
        except Exception as exc:
            logger.warning("读取资产映射失败: %s", exc)

    def _load_cache(self) -> None:
        if not self.CACHE_PATH.exists():
            return

        try:
            data = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            for symbol, cg_id in data.items():
                self._ids.setdefault(symbol.lower(), cg_id)
        except Exception as exc:
            logger.warning("读取资产缓存失败: %s", exc)

    async def resolve(self, symbol: str) -> Optional[str]:
        """Return CoinGecko ID if known."""
        async with self._lock:
            return self._ids.get(symbol.lower())

    async def store(self, symbol: str, coingecko_id: str) -> None:
        """Persist newly discovered mapping for future lookups."""
        async with self._lock:
            updated = False
            if symbol.lower() not in self._ids:
                self._ids[symbol.lower()] = coingecko_id
                updated = True

            if updated:
                cache_dir = self.CACHE_PATH.parent
                cache_dir.mkdir(parents=True, exist_ok=True)
                self.CACHE_PATH.write_text(
                    json.dumps(self._ids, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )


class CoinGeckoPriceProvider(PriceProvider):
    """Fetch price snapshots using the CoinGecko API."""

    API_BASE_URL = "https://api.coingecko.com/api/v3"
    SEARCH_ENDPOINT = "/search"
    SIMPLE_PRICE_ENDPOINT = "/simple/price"
    MARKET_CHART_ENDPOINT = "/coins/{id}/market_chart"

    STABLECOIN_SYMBOLS = {
        "usdc",
        "usdt",
        "dai",
        "frax",
        "busd",
        "tusd",
        "usdd",
        "gusd",
        "usdp",
        "pyusd",
        "susd",
        "lusd",
        "usde",
        "fdusd",
        "eurc",
    }

    def __init__(self, config) -> None:
        super().__init__(config)
        self._api_key = getattr(config, "COINGECKO_API_KEY", "").strip()
        self._base_url = getattr(config, "COINGECKO_API_BASE_URL", self.API_BASE_URL).rstrip("/")
        self._price_threshold = float(getattr(config, "PRICE_DEVIATION_THRESHOLD", 2.0))
        self._stablecoin_tolerance = float(getattr(config, "PRICE_STABLECOIN_TOLERANCE", 0.5))
        self._volatility_spike_multiplier = float(
            getattr(config, "PRICE_VOLATILITY_SPIKE_MULTIPLIER", 3.0)
        )
        self._market_chart_cache_ttl = int(
            getattr(config, "PRICE_MARKET_CHART_CACHE_SECONDS", 300)
        )
        self._binance_enabled = bool(getattr(config, "PRICE_BINANCE_FALLBACK_ENABLED", True))

        self._registry = AssetRegistry()
        self._market_chart_cache: Dict[str, tuple[dict, float]] = {}
        self._volatility_baseline: Dict[str, float] = {}
        self._binance_base_url = getattr(
            config, "BINANCE_REST_BASE_URL", "https://api.binance.com"
        ).rstrip("/")

    async def snapshot(self, *, asset: str) -> ToolResult:
        asset_symbol = asset.lower()
        try:
            coingecko_id = await self._resolve_asset_id(asset_symbol)
        except Exception as exc:  # Defensive catch to avoid breaking the pipeline
            logger.error("无法解析资产 ID: asset=%s error=%s", asset_symbol, exc)
            return ToolResult(
                source="CoinGecko",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_resolution_failed",
            )

        if not coingecko_id:
            logger.warning("未找到资产的 CoinGecko ID: asset=%s", asset_symbol)
            return ToolResult(
                source="CoinGecko",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_not_supported",
            )

        headers = self._build_headers()

        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                simple_task = self._fetch_simple_price(client, coingecko_id)
                chart_task = self._fetch_market_chart(client, coingecko_id)
                simple_data, chart_data = await asyncio.gather(
                    simple_task, chart_task, return_exceptions=False
                )
        except httpx.TimeoutException as exc:
            logger.warning("CoinGecko 请求超时: asset=%s", asset_symbol)
            return self._handle_timeout(exc)
        except ToolRateLimitError:
            return ToolResult(
                source="CoinGecko",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="rate_limit",
            )
        except Exception as exc:
            logger.error("CoinGecko 请求失败: asset=%s error=%s", asset_symbol, exc)
            return ToolResult(
                source="CoinGecko",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=str(exc),
            )

        price_snapshot = self._build_snapshot(
            asset_symbol=asset_symbol,
            coingecko_id=coingecko_id,
            simple_data=simple_data,
            chart_data=chart_data,
        )

        if price_snapshot is None and self._binance_enabled:
            logger.info("尝试使用 Binance 行情降级: asset=%s", asset_symbol)
            binance_price = await self._fetch_binance_price(asset_symbol)
            if binance_price is not None:
                return self._build_binance_fallback(asset_symbol, binance_price)

        if price_snapshot is None:
            return ToolResult(
                source="CoinGecko",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="data_unavailable",
            )

        return price_snapshot

    async def _resolve_asset_id(self, symbol: str) -> Optional[str]:
        mapped = await self._registry.resolve(symbol)
        if mapped:
            return mapped

        # Attempt search via CoinGecko API
        query = re.sub(r"[^0-9a-zA-Z]+", " ", symbol)
        params = {"query": query}
        url = f"{self._base_url}{self.SEARCH_ENDPOINT}"
        headers = self._build_headers()

        logger.info("CoinGecko 搜索资产: symbol=%s query=%s", symbol, query)

        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                response = await client.get(url, params=params)
                if response.status_code == 429:
                    raise ToolRateLimitError("CoinGecko API rate limited during search")
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            logger.warning("CoinGecko 搜索超时: symbol=%s", symbol)
            raise
        except ToolRateLimitError:
            raise
        except Exception as exc:
            logger.error("CoinGecko 搜索失败: symbol=%s error=%s", symbol, exc)
            raise

        coins = payload.get("coins", [])
        if not coins:
            return None

        # Prefer exact symbol matches and highest market_cap_rank
        symbol_lower = symbol.lower()
        candidates = [
            coin for coin in coins if coin.get("symbol", "").lower() == symbol_lower
        ]
        if not candidates:
            candidates = coins

        def score(coin: dict) -> tuple[int, float]:
            rank = coin.get("market_cap_rank") or 9999
            score_exact = 0 if coin.get("symbol", "").lower() == symbol_lower else 1
            return score_exact, rank

        best = min(candidates, key=score)
        coingecko_id = best.get("id")

        if coingecko_id:
            await self._registry.store(symbol_lower, coingecko_id)

        return coingecko_id

    async def _fetch_simple_price(self, client: httpx.AsyncClient, coingecko_id: str) -> dict:
        params = {
            "ids": coingecko_id,
            "vs_currencies": "usd",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
        url = f"{self._base_url}{self.SIMPLE_PRICE_ENDPOINT}"
        response = await client.get(url, params=params)

        if response.status_code == 429:
            raise ToolRateLimitError("CoinGecko simple price rate limited")

        response.raise_for_status()
        payload = response.json()
        return payload.get(coingecko_id, {})

    async def _fetch_market_chart(self, client: httpx.AsyncClient, coingecko_id: str) -> dict:
        cached = self._market_chart_cache.get(coingecko_id)
        now = time.time()
        if cached and now - cached[1] < self._market_chart_cache_ttl:
            return cached[0]

        params = {"vs_currency": "usd", "days": 1, "interval": "hourly"}
        url = f"{self._base_url}{self.MARKET_CHART_ENDPOINT.format(id=coingecko_id)}"
        response = await client.get(url, params=params)

        if response.status_code == 429:
            raise ToolRateLimitError("CoinGecko market chart rate limited")

        response.raise_for_status()
        payload = response.json()
        self._market_chart_cache[coingecko_id] = (payload, now)
        return payload

    def _build_snapshot(
        self,
        *,
        asset_symbol: str,
        coingecko_id: str,
        simple_data: dict,
        chart_data: dict,
    ) -> Optional[ToolResult]:
        price_usd = simple_data.get("usd")

        if price_usd is None:
            # Attempt to derive price from market chart (last price)
            prices = chart_data.get("prices") or []
            if prices:
                price_usd = prices[-1][1]

        if price_usd is None:
            logger.warning("CoinGecko 缺少价格数据: asset=%s", asset_symbol)
            return None

        prices = chart_data.get("prices") or []
        price_change_1h_pct = self._calculate_change(prices, window_seconds=3600)
        price_change_24h_pct = simple_data.get("usd_24h_change")
        volume_24h_usd = simple_data.get("usd_24h_vol")

        deviation_pct = self._calculate_deviation(asset_symbol, price_usd)
        volatility_24h = self._calculate_volatility(prices)
        baseline = self._volatility_baseline.get(asset_symbol)
        if baseline is None:
            baseline = volatility_24h
        else:
            baseline = 0.7 * baseline + 0.3 * volatility_24h
        self._volatility_baseline[asset_symbol] = baseline

        volatility_spike = (
            baseline > 0
            and volatility_24h > 0
            and (volatility_24h / baseline) >= self._volatility_spike_multiplier
        )
        price_depeg = self._is_stablecoin(asset_symbol) and abs(deviation_pct) >= self._stablecoin_tolerance
        price_threshold_trigger = abs(deviation_pct) >= self._price_threshold
        triggered = price_threshold_trigger or price_depeg or volatility_spike

        confidence = self._calculate_confidence(
            deviation_pct=deviation_pct,
            price_trigger=price_threshold_trigger or price_depeg,
            volatility_spike=volatility_spike,
        )

        anomalies = {
            "price_depeg": price_depeg,
            "volatility_spike": volatility_spike,
            "funding_extreme": False,
        }

        metrics = {
            "price_usd": round(price_usd, 6),
            "deviation_pct": round(deviation_pct, 3),
            "price_change_1h_pct": round(price_change_1h_pct, 3) if price_change_1h_pct is not None else None,
            "price_change_24h_pct": round(price_change_24h_pct, 3) if price_change_24h_pct is not None else None,
            "volatility_24h": round(volatility_24h, 4),
            "volatility_avg": round(baseline, 4) if baseline is not None else None,
            "volume_24h_usd": round(volume_24h_usd, 3) if volume_24h_usd is not None else None,
            "liquidation_1h_usd": None,
            "liquidation_24h_avg": None,
            "funding_rate": None,
        }

        note_components = [
            f"价格 ${metrics['price_usd']}",
            f"偏离 {metrics['deviation_pct']}%",
        ]
        if metrics["price_change_24h_pct"] is not None:
            note_components.append(f"24h 变动 {metrics['price_change_24h_pct']}%")
        if volatility_spike:
            note_components.append("波动率异常")
        notes = ", ".join(note_components)

        return ToolResult(
            source="CoinGecko",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data={
                "asset": asset_symbol.upper(),
                "coingecko_id": coingecko_id,
                "metrics": metrics,
                "anomalies": anomalies,
                "notes": notes,
            },
            triggered=triggered,
            confidence=confidence,
        )

    def _calculate_change(self, prices: list[list[float]], *, window_seconds: int) -> Optional[float]:
        if not prices:
            return None

        latest_ts, latest_price = prices[-1]
        window_ms = window_seconds * 1000

        past_price = None
        for timestamp, price in reversed(prices):
            if latest_ts - timestamp >= window_ms:
                past_price = price
                break

        if past_price is None:
            # Use earliest price if window is larger than data length
            past_price = prices[0][1]

        if past_price <= 0:
            return None

        return ((latest_price - past_price) / past_price) * 100

    def _calculate_volatility(self, prices: list[list[float]]) -> float:
        if len(prices) < 2:
            return 0.0

        values = [price for _, price in prices]
        mean_price = sum(values) / len(values)
        if mean_price == 0:
            return 0.0

        variance = sum((price - mean_price) ** 2 for price in values) / len(values)
        std_dev = math.sqrt(variance)
        return (std_dev / mean_price) * 100

    def _calculate_deviation(self, symbol: str, price_usd: float) -> float:
        if price_usd <= 0:
            return 0.0

        if self._is_stablecoin(symbol):
            anchor = 1.0
        else:
            anchor = None

        if anchor is None:
            return 0.0

        return ((price_usd - anchor) / anchor) * 100

    def _calculate_confidence(
        self,
        *,
        deviation_pct: float,
        price_trigger: bool,
        volatility_spike: bool,
    ) -> float:
        base_confidence = 0.55
        if price_trigger:
            severity = min(abs(deviation_pct) / max(self._price_threshold, 0.1), 2.0)
            base_confidence += 0.25 + 0.1 * severity
        if volatility_spike:
            base_confidence += 0.1
        return min(1.0, round(base_confidence, 2))

    def _is_stablecoin(self, symbol: str) -> bool:
        symbol_lower = symbol.lower()
        if symbol_lower in self.STABLECOIN_SYMBOLS:
            return True
        return symbol_lower.endswith("usd")

    def _build_headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"x-cg-demo-api-key": self._api_key}

    async def _fetch_binance_price(self, symbol: str) -> Optional[float]:
        pair = f"{symbol.upper()}USDT"
        url = f"{self._binance_base_url}/api/v3/ticker/price"
        params = {"symbol": pair}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                return float(payload.get("price"))
        except Exception as exc:
            logger.warning("Binance 行情降级失败: symbol=%s error=%s", symbol, exc)
            return None

    def _build_binance_fallback(self, symbol: str, price_usd: float) -> ToolResult:
        metrics = {
            "price_usd": round(price_usd, 6),
            "deviation_pct": 0.0,
            "price_change_1h_pct": None,
            "price_change_24h_pct": None,
            "volatility_24h": 0.0,
            "volatility_avg": None,
            "volume_24h_usd": None,
            "liquidation_1h_usd": None,
            "liquidation_24h_avg": None,
            "funding_rate": None,
        }
        return ToolResult(
            source="CoinGecko",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data={
                "asset": symbol.upper(),
                "coingecko_id": None,
                "metrics": metrics,
                "anomalies": {
                    "price_depeg": False,
                    "volatility_spike": False,
                    "funding_extreme": False,
                },
                "notes": "CoinGecko 数据缺失, 使用 Binance 行情降级",
            },
            triggered=False,
            confidence=0.6,
        )
