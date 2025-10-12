import pytest

from src.ai.tools.price.fetcher import PriceTool
from src.ai.tools.price.providers.coingecko import CoinGeckoPriceProvider


class DummyConfig:
    """Lightweight config stub for price tool tests."""

    DEEP_ANALYSIS_TOOL_TIMEOUT = 5
    PRICE_CACHE_TTL_SECONDS = 60
    COINGECKO_API_KEY = "demo-key"
    COINGECKO_API_BASE_URL = "https://api.coingecko.com/api/v3"
    PRICE_DEVIATION_THRESHOLD = 2.0
    PRICE_STABLECOIN_TOLERANCE = 0.5
    PRICE_VOLATILITY_SPIKE_MULTIPLIER = 3.0
    PRICE_MARKET_CHART_CACHE_SECONDS = 300
    PRICE_BINANCE_FALLBACK_ENABLED = True
    BINANCE_REST_BASE_URL = "https://api.binance.com"
    DEEP_ANALYSIS_PRICE_PROVIDER = "coingecko"


def _sample_chart(prices: list[float]) -> dict:
    base_ts = 1_700_000_000_000
    interval = 3_600_000  # 1 hour in ms
    entries = []
    for index, price in enumerate(prices):
        entries.append([base_ts + index * interval, price])
    return {"prices": entries}


@pytest.mark.asyncio
async def test_price_tool_detects_stablecoin_depeg(monkeypatch):
    """Ensure CoinGecko snapshot flags stablecoin deviations and uses cache."""
    config = DummyConfig()
    tool = PriceTool(config)

    call_counter = {"simple": 0, "chart": 0}

    async def fake_simple(self, client, coingecko_id):
        call_counter["simple"] += 1
        return {
            "usd": 0.987,
            "usd_24h_change": -1.6,
            "usd_24h_vol": 1_200_000_000,
        }

    async def fake_chart(self, client, coingecko_id):
        call_counter["chart"] += 1
        return _sample_chart([1.0, 0.998, 0.995, 0.99, 0.987])

    monkeypatch.setattr(CoinGeckoPriceProvider, "_fetch_simple_price", fake_simple)
    monkeypatch.setattr(CoinGeckoPriceProvider, "_fetch_market_chart", fake_chart)

    result = await tool.snapshot(asset="USDC")

    assert result.success is True
    assert result.triggered is True
    assert result.confidence >= 0.8
    assert result.data["anomalies"]["price_depeg"] is True
    metrics = result.data["metrics"]
    assert metrics["price_usd"] == pytest.approx(0.987, rel=0.0001)
    assert metrics["deviation_pct"] < 0
    assert metrics["volume_24h_usd"] == pytest.approx(1_200_000_000, rel=0.0001)

    # Second call should hit cache and not re-invoke provider fetches
    cached = await tool.snapshot(asset="USDC")
    assert cached is result
    assert call_counter["simple"] == 1
    assert call_counter["chart"] == 1


@pytest.mark.asyncio
async def test_price_tool_binance_fallback(monkeypatch):
    """If CoinGecko data is missing, tool should fall back to Binance ticker."""
    config = DummyConfig()
    tool = PriceTool(config)

    async def fake_simple(self, client, coingecko_id):
        return {}

    async def fake_chart(self, client, coingecko_id):
        return {"prices": []}

    async def fake_binance(self, symbol):
        return 1.01

    def fake_build_snapshot(self, **kwargs):
        return None

    monkeypatch.setattr(CoinGeckoPriceProvider, "_fetch_simple_price", fake_simple)
    monkeypatch.setattr(CoinGeckoPriceProvider, "_fetch_market_chart", fake_chart)
    monkeypatch.setattr(CoinGeckoPriceProvider, "_fetch_binance_price", fake_binance)
    monkeypatch.setattr(CoinGeckoPriceProvider, "_build_snapshot", fake_build_snapshot)

    result = await tool.snapshot(asset="USDC")

    assert result.success is True
    assert result.triggered is False
    assert "Binance 行情降级" in result.data["notes"]
    assert result.data["metrics"]["price_usd"] == pytest.approx(1.01, rel=0.0001)
