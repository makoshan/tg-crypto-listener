import pytest

from src.ai.tools.price.providers.coinmarketcap import CoinMarketCapPriceProvider


class DummyConfig:
    DEEP_ANALYSIS_TOOL_TIMEOUT = 5
    COINMARKETCAP_API_KEY = ""
    PRICE_DEVIATION_THRESHOLD = 2.0
    PRICE_STABLECOIN_TOLERANCE = 0.5
    PRICE_VOLATILITY_SPIKE_MULTIPLIER = 3.0
    PRICE_BINANCE_FALLBACK_ENABLED = False
    PRICE_CRASH_ALERT_THRESHOLD = 5.0


@pytest.mark.asyncio
async def test_coinmarketcap_snapshot_includes_context_on_large_drop(monkeypatch):
    """Ensure crash context is attached when 24h 跌幅超过阈值。"""

    provider = CoinMarketCapPriceProvider(DummyConfig())

    async def fake_resolve(symbol: str):
        return 999

    async def fake_fetch_quote(client, cmc_id: int):
        return {
            "quote": {
                "USD": {
                    "price": 10.0,
                    "percent_change_1h": -3.2,
                    "percent_change_24h": -12.5,
                    "volume_24h": 1_500_000,
                    "market_cap": 10_000_000,
                }
            }
        }

    async def fake_context(**kwargs):
        return {
            "detected_drop_pct": -12.5,
            "threshold_pct": 5.0,
            "checks": {"btc_market": {"status": "ok", "also_down": True}},
        }

    monkeypatch.setattr(provider, "_resolve_asset_id", fake_resolve)
    monkeypatch.setattr(provider, "_fetch_quote", fake_fetch_quote)
    monkeypatch.setattr(provider, "_gather_crash_context", fake_context)

    result = await provider.snapshot(asset="UNI")

    assert result.success is True
    assert "context_checks" in result.data
    context = result.data["context_checks"]
    assert context["checks"]["btc_market"]["status"] == "ok"
    assert result.data["notes"].endswith("已执行大跌情境检查")
