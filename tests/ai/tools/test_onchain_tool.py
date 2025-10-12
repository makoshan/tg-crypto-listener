from types import SimpleNamespace

import pytest

from src.ai.tools.onchain.fetcher import OnchainTool


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class DummyAsyncClient:
    def __init__(self, payload, status_code=200, calls=None, **_kwargs):
        self._payload = payload
        self._status = status_code
        self.calls = calls if calls is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def get(self, url):
        self.calls.append(url)
        return DummyResponse(self._payload, self._status)


STABLECOIN_PAYLOAD = {
    "peggedAssets": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "circulating": {"peggedUSD": 900.0},
            "circulatingPrevDay": {"peggedUSD": 1100.0},
            "circulatingPrevWeek": {"peggedUSD": 1500.0},
            "chains": [
                {"chain": "ethereum", "circulating": 500.0},
                {"chain": "solana", "circulating": 200.0},
            ],
            "pegType": "fiat-backed",
        }
    ]
}


@pytest.mark.asyncio
async def test_onchain_tool_detects_redemption(monkeypatch):
    calls = []

    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient(STABLECOIN_PAYLOAD, calls=calls, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.onchain.providers.defillama.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        ONCHAIN_CACHE_TTL_SECONDS=0,
        ONCHAIN_TVL_DROP_THRESHOLD=10.0,
        ONCHAIN_REDEMPTION_USD_THRESHOLD=150.0,
        DEEP_ANALYSIS_ONCHAIN_PROVIDER="defillama",
    )

    tool = OnchainTool(config)
    result = await tool.snapshot(asset="USDC", force_refresh=True)

    assert result.success is True
    assert result.triggered is True
    anomalies = result.data["anomalies"]
    assert "tvl_drop_24h" in anomalies
    assert "redemption_spike_24h" in anomalies
    metrics = result.data["metrics"]
    assert metrics["redemption_24h_usd"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_onchain_tool_cache(monkeypatch):
    calls = []

    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient(STABLECOIN_PAYLOAD, calls=calls, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.onchain.providers.defillama.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        ONCHAIN_CACHE_TTL_SECONDS=999,
        DEEP_ANALYSIS_ONCHAIN_PROVIDER="defillama",
    )

    tool = OnchainTool(config)
    first = await tool.snapshot(asset="USDC", force_refresh=True)
    second = await tool.snapshot(asset="USDC")

    assert first.success is True
    assert second.success is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_onchain_tool_asset_not_found(monkeypatch):
    empty_payload = {"peggedAssets": []}

    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient(empty_payload, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.onchain.providers.defillama.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        ONCHAIN_CACHE_TTL_SECONDS=0,
        DEEP_ANALYSIS_ONCHAIN_PROVIDER="defillama",
    )

    tool = OnchainTool(config)
    result = await tool.snapshot(asset="UNKNOWN", force_refresh=True)

    assert result.success is False
    assert result.error == "asset_not_supported"
