"""CoinMarketCap-backed price provider implementation."""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Dict, Optional, TYPE_CHECKING

import httpx

from ...base import ToolResult
from ...exceptions import ToolRateLimitError
from .base import PriceProvider
from src.config import PROJECT_ROOT
from src.utils import setup_logger

logger = setup_logger(__name__)

if TYPE_CHECKING:
    from src.ai.tools.search.fetcher import SearchTool


class CoinMarketCapAssetRegistry:
    """Resolve asset symbols to CoinMarketCap IDs with caching support."""

    STATIC_PATH = PROJECT_ROOT / "data" / "asset_registry_cmc.json"
    CACHE_PATH = PROJECT_ROOT / ".cache" / "coinmarketcap_ids.json"

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._ids: Dict[str, int] = {}
        self._load_static()
        self._load_cache()

    def _load_static(self) -> None:
        if not self.STATIC_PATH.exists():
            logger.warning("CoinMarketCap 资产映射文件缺失: %s", self.STATIC_PATH)
            return

        try:
            data = json.loads(self.STATIC_PATH.read_text(encoding="utf-8"))
            for symbol, cmc_id in data.items():
                self._ids[symbol.lower()] = int(cmc_id)
        except Exception as exc:
            logger.warning("读取 CoinMarketCap 资产映射失败: %s", exc)

    def _load_cache(self) -> None:
        if not self.CACHE_PATH.exists():
            return

        try:
            data = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            for symbol, cmc_id in data.items():
                self._ids.setdefault(symbol.lower(), int(cmc_id))
        except Exception as exc:
            logger.warning("读取 CoinMarketCap 资产缓存失败: %s", exc)

    async def resolve(self, symbol: str) -> Optional[int]:
        """Return CoinMarketCap ID if known."""
        async with self._lock:
            return self._ids.get(symbol.lower())

    async def store(self, symbol: str, cmc_id: int) -> None:
        """Persist newly discovered mapping for future lookups."""
        async with self._lock:
            updated = False
            if symbol.lower() not in self._ids:
                self._ids[symbol.lower()] = cmc_id
                updated = True

            if updated:
                cache_dir = self.CACHE_PATH.parent
                cache_dir.mkdir(parents=True, exist_ok=True)
                self.CACHE_PATH.write_text(
                    json.dumps(self._ids, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )


class CoinMarketCapPriceProvider(PriceProvider):
    """Fetch price snapshots using the CoinMarketCap API."""

    API_BASE_URL = "https://pro-api.coinmarketcap.com"
    QUOTE_LATEST_ENDPOINT = "/v2/cryptocurrency/quotes/latest"
    MAP_ENDPOINT = "/v1/cryptocurrency/map"
    OHLCV_ENDPOINT = "/v2/cryptocurrency/ohlcv/historical"

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
        self._api_key = getattr(config, "COINMARKETCAP_API_KEY", "").strip()
        self._base_url = getattr(config, "COINMARKETCAP_API_BASE_URL", self.API_BASE_URL).rstrip("/")
        self._price_threshold = float(getattr(config, "PRICE_DEVIATION_THRESHOLD", 2.0))
        self._stablecoin_tolerance = float(getattr(config, "PRICE_STABLECOIN_TOLERANCE", 0.5))
        self._volatility_spike_multiplier = float(
            getattr(config, "PRICE_VOLATILITY_SPIKE_MULTIPLIER", 3.0)
        )
        self._binance_enabled = bool(getattr(config, "PRICE_BINANCE_FALLBACK_ENABLED", True))
        self._crash_threshold = float(getattr(config, "PRICE_CRASH_ALERT_THRESHOLD", 7.0))
        self._btc_correlation_threshold = float(
            getattr(config, "PRICE_BTC_CORRELATION_THRESHOLD", 2.0)
        )

        self._registry = CoinMarketCapAssetRegistry()
        self._volatility_baseline: Dict[str, float] = {}
        self._binance_base_url = getattr(
            config, "BINANCE_REST_BASE_URL", "https://api.binance.com"
        ).rstrip("/")
        self._search_tool: Optional["SearchTool"] = None
        self._search_tool_disabled = False

    async def snapshot(self, *, asset: str) -> ToolResult:
        asset_symbol = asset.lower()
        try:
            cmc_id = await self._resolve_asset_id(asset_symbol)
        except Exception as exc:
            logger.error("无法解析资产 ID: asset=%s error=%s", asset_symbol, exc)
            return ToolResult(
                source="CoinMarketCap",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_resolution_failed",
            )

        if not cmc_id:
            logger.warning("未找到资产的 CoinMarketCap ID: asset=%s", asset_symbol)
            return ToolResult(
                source="CoinMarketCap",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="asset_not_supported",
            )

        headers = self._build_headers()

        # Fetch quote data
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                quote_data = await self._fetch_quote(client, cmc_id)
        except httpx.TimeoutException as exc:
            logger.warning("CoinMarketCap quote 请求超时: asset=%s", asset_symbol)
            return self._handle_timeout(exc)
        except ToolRateLimitError:
            logger.warning("CoinMarketCap quote rate limited: asset=%s", asset_symbol)
            if self._binance_enabled:
                logger.info("尝试使用 Binance 行情降级: asset=%s", asset_symbol)
                binance_price = await self._fetch_binance_price(asset_symbol)
                if binance_price is not None:
                    return self._build_binance_fallback(asset_symbol, binance_price)
            return ToolResult(
                source="CoinMarketCap",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="rate_limit",
            )
        except Exception as exc:
            logger.error("CoinMarketCap quote 请求失败: asset=%s error=%s", asset_symbol, exc)
            if self._binance_enabled:
                logger.info("尝试使用 Binance 行情降级: asset=%s", asset_symbol)
                binance_price = await self._fetch_binance_price(asset_symbol)
                if binance_price is not None:
                    return self._build_binance_fallback(asset_symbol, binance_price)
            return ToolResult(
                source="CoinMarketCap",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=str(exc),
            )

        price_snapshot = self._build_snapshot(
            asset_symbol=asset_symbol,
            cmc_id=cmc_id,
            quote_data=quote_data,
        )

        if price_snapshot is None and self._binance_enabled:
            logger.info("尝试使用 Binance 行情降级: asset=%s", asset_symbol)
            binance_price = await self._fetch_binance_price(asset_symbol)
            if binance_price is not None:
                return self._build_binance_fallback(asset_symbol, binance_price)

        if price_snapshot is None:
            return ToolResult(
                source="CoinMarketCap",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="data_unavailable",
            )

        await self._maybe_attach_context(
            snapshot=price_snapshot,
            asset_symbol=asset_symbol,
        )

        return price_snapshot

    async def _resolve_asset_id(self, symbol: str) -> Optional[int]:
        mapped = await self._registry.resolve(symbol)
        if mapped:
            return mapped

        # Attempt search via CoinMarketCap API
        params = {"symbol": symbol.upper(), "limit": 10}
        url = f"{self._base_url}{self.MAP_ENDPOINT}"
        headers = self._build_headers()

        logger.info("CoinMarketCap 搜索资产: symbol=%s", symbol)

        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                response = await client.get(url, params=params)
                if response.status_code == 429:
                    raise ToolRateLimitError("CoinMarketCap API rate limited during search")
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException:
            logger.warning("CoinMarketCap 搜索超时: symbol=%s", symbol)
            raise
        except ToolRateLimitError:
            raise
        except Exception as exc:
            logger.error("CoinMarketCap 搜索失败: symbol=%s error=%s", symbol, exc)
            raise

        data = payload.get("data", [])
        if not data:
            return None

        # Prefer exact symbol matches with is_active=1 and highest rank
        symbol_upper = symbol.upper()
        candidates = [
            coin for coin in data
            if coin.get("symbol", "").upper() == symbol_upper and coin.get("is_active") == 1
        ]
        if not candidates:
            candidates = [coin for coin in data if coin.get("is_active") == 1]
        if not candidates:
            candidates = data

        def score(coin: dict) -> tuple[int, int]:
            rank = coin.get("rank") or 999999
            exact_match = 0 if coin.get("symbol", "").upper() == symbol_upper else 1
            return exact_match, rank

        best = min(candidates, key=score)
        cmc_id = best.get("id")

        if cmc_id:
            await self._registry.store(symbol.lower(), cmc_id)

        return cmc_id

    async def _fetch_quote(self, client: httpx.AsyncClient, cmc_id: int) -> dict:
        params = {
            "id": cmc_id,
            "convert": "USD",
        }
        url = f"{self._base_url}{self.QUOTE_LATEST_ENDPOINT}"
        response = await client.get(url, params=params)

        if response.status_code == 429:
            raise ToolRateLimitError("CoinMarketCap quote rate limited")

        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {})
        return data.get(str(cmc_id), {})

    def _build_snapshot(
        self,
        *,
        asset_symbol: str,
        cmc_id: int,
        quote_data: dict,
    ) -> Optional[ToolResult]:
        quote = quote_data.get("quote", {}).get("USD", {})
        price_usd = quote.get("price")

        if price_usd is None:
            logger.warning("CoinMarketCap 缺少价格数据: asset=%s", asset_symbol)
            return None

        price_change_1h_pct = quote.get("percent_change_1h")
        price_change_24h_pct = quote.get("percent_change_24h")
        volume_24h_usd = quote.get("volume_24h")
        market_cap_usd = quote.get("market_cap")

        deviation_pct = self._calculate_deviation(asset_symbol, price_usd)

        # Estimate volatility from price changes
        volatility_24h = 0.0
        if price_change_1h_pct is not None and price_change_24h_pct is not None:
            # Simple volatility estimate based on price changes
            volatility_24h = abs(price_change_24h_pct)

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
            "market_cap_usd": round(market_cap_usd, 3) if market_cap_usd is not None else None,
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
            source="CoinMarketCap",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data={
                "asset": asset_symbol.upper(),
                "coinmarketcap_id": cmc_id,
                "metrics": metrics,
                "anomalies": anomalies,
                "notes": notes,
            },
            triggered=triggered,
            confidence=confidence,
        )

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
        return {"X-CMC_PRO_API_KEY": self._api_key}

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
            "market_cap_usd": None,
            "liquidation_1h_usd": None,
            "liquidation_24h_avg": None,
            "funding_rate": None,
        }
        return ToolResult(
            source="CoinMarketCap",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data={
                "asset": symbol.upper(),
                "coinmarketcap_id": None,
                "metrics": metrics,
                "anomalies": {
                    "price_depeg": False,
                    "volatility_spike": False,
                    "funding_extreme": False,
                },
                "notes": "CoinMarketCap 数据缺失, 使用 Binance 行情降级",
            },
            triggered=False,
            confidence=0.6,
        )

    async def _maybe_attach_context(self, *, snapshot: ToolResult, asset_symbol: str) -> None:
        metrics = snapshot.data.get("metrics") if isinstance(snapshot.data, dict) else {}
        if not isinstance(metrics, dict):
            return

        drop_value = metrics.get("price_change_24h_pct")
        try:
            drop_value = float(drop_value)
        except (TypeError, ValueError):
            return

        if drop_value > -self._crash_threshold:
            return

        context = await self._gather_crash_context(
            asset_symbol=asset_symbol,
            drop_pct=drop_value,
        )
        if not context:
            return

        snapshot.data["context_checks"] = context  # type: ignore[index]
        notes = snapshot.data.get("notes") if isinstance(snapshot.data, dict) else None
        suffix = "已执行大跌情境检查"
        if isinstance(notes, str) and notes:
            snapshot.data["notes"] = f"{notes}；{suffix}"  # type: ignore[index]
        else:
            snapshot.data["notes"] = suffix  # type: ignore[index]

    async def _gather_crash_context(self, *, asset_symbol: str, drop_pct: float) -> dict:
        checks: dict[str, dict] = {}
        tasks: list[tuple[str, asyncio.Task]] = []

        symbol_lower = asset_symbol.lower()
        if symbol_lower != "btc":
            tasks.append(("btc_market", asyncio.create_task(self._check_bitcoin_correlation())))

        search_topics = [
            ("us_trade_sanctions", ["美国 制裁 加密", "US sanctions crypto market"]),
            ("trump_commentary", ["特朗普 加密 言论", "Trump crypto statement"]),
            ("war_risk", ["战争 升级 crypto 市场", "geopolitical war crypto plunge"]),
        ]

        for label, queries in search_topics:
            tasks.append((label, asyncio.create_task(self._search_topic(queries))))

        if not tasks:
            return {}

        results = await asyncio.gather(
            *(task for _, task in tasks),
            return_exceptions=True,
        )

        for (label, _), result in zip(tasks, results, strict=False):
            if isinstance(result, Exception):
                logger.warning("大跌情境检查异常: %s error=%s", label, result)
                checks[label] = {"status": "error", "reason": str(result)}
            else:
                checks[label] = result

        if not any(
            isinstance(value, dict) and value.get("status") not in {"unavailable", "error"}
            for value in checks.values()
        ):
            return {}

        return {
            "detected_drop_pct": round(drop_pct, 3),
            "threshold_pct": self._crash_threshold,
            "checks": checks,
        }

    async def _check_bitcoin_correlation(self) -> dict:
        try:
            cmc_id = await self._resolve_asset_id("btc")
        except Exception as exc:
            logger.warning("比特币行情解析失败: %s", exc)
            return {"status": "error", "reason": str(exc)}

        if not cmc_id:
            return {"status": "unavailable", "reason": "btc_id_not_found"}

        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                btc_quote = await self._fetch_quote(client, cmc_id)
        except Exception as exc:
            logger.warning("比特币行情请求失败: %s", exc)
            return {"status": "error", "reason": str(exc)}

        quote = btc_quote.get("quote", {}).get("USD", {}) if isinstance(btc_quote, dict) else {}
        change_raw = quote.get("percent_change_24h")
        price_raw = quote.get("price")

        try:
            change = float(change_raw)
        except (TypeError, ValueError):
            return {"status": "unavailable", "reason": "percent_change_missing"}

        btc_price = None
        try:
            btc_price = round(float(price_raw), 2)
        except (TypeError, ValueError):
            btc_price = None

        also_down = change <= -self._btc_correlation_threshold

        return {
            "status": "ok",
            "percent_change_24h": round(change, 3),
            "price_usd": btc_price,
            "also_down": also_down,
            "threshold_pct": self._btc_correlation_threshold,
        }

    async def _search_topic(self, queries: list[str]) -> dict:
        tool = self._ensure_search_tool()
        if not tool:
            return {"status": "unavailable", "reason": "search_tool_unavailable"}

        for query in queries:
            try:
                result = await tool.fetch(keyword=query, max_results=3)
            except Exception as exc:
                logger.warning("关键字搜索失败: query='%s' error=%s", query, exc)
                return {"status": "error", "reason": str(exc)}

            if not result.success:
                continue

            data = result.data or {}
            raw_results = data.get("results") or []
            if not raw_results:
                continue

            formatted_results = [
                {
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "url": item.get("url", ""),
                }
                for item in raw_results[:3]
            ]

            return {
                "status": "ok",
                "query": query,
                "hits": len(raw_results),
                "multi_source": data.get("multi_source"),
                "official": data.get("official_confirmed"),
                "triggered": result.triggered,
                "top_results": formatted_results,
            }

        return {"status": "ok", "query": queries[-1] if queries else "", "hits": 0}

    def _ensure_search_tool(self) -> Optional["SearchTool"]:
        if self._search_tool_disabled:
            return None
        if self._search_tool is not None:
            return self._search_tool

        try:
            from src.ai.tools.search.fetcher import SearchTool as SearchToolFetcher

            self._search_tool = SearchToolFetcher(self._config)
            logger.info("搜索工具初始化成功，用于大跌情境分析")
            return self._search_tool
        except Exception as exc:
            logger.warning("搜索工具不可用，将跳过新闻上下文检查: %s", exc)
            self._search_tool_disabled = True
            return None
