from types import SimpleNamespace

import pytest

from src.ai.tools.protocol.fetcher import ProtocolTool


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


PROTOCOL_PAYLOAD = {
    "name": "Test Protocol",
    "slug": "test-protocol",
    "url": "https://test.example",
    "symbol": "TEST",
    "chains": ["Ethereum", "Arbitrum"],
    "currentChainTvls": {
        "Ethereum": 700.0,
        "Arbitrum": 200.0,
        "Ethereum-borrowed": 60.0,
    },
    "tvl": [
        {"date": 1, "totalLiquidityUSD": 1000.0},
        {"date": 2, "totalLiquidityUSD": 900.0},
        {"date": 3, "totalLiquidityUSD": 850.0},
        {"date": 4, "totalLiquidityUSD": 800.0},
        {"date": 5, "totalLiquidityUSD": 780.0},
        {"date": 6, "totalLiquidityUSD": 760.0},
        {"date": 7, "totalLiquidityUSD": 700.0},
        {"date": 8, "totalLiquidityUSD": 500.0},
    ],
}


@pytest.mark.asyncio
async def test_protocol_tool_detects_tvl_drop(monkeypatch):
    calls = []

    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient(PROTOCOL_PAYLOAD, calls=calls, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.protocol.providers.defillama.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        PROTOCOL_CACHE_TTL_SECONDS=0,
        PROTOCOL_TVL_DROP_THRESHOLD_PCT=10.0,
        PROTOCOL_TVL_DROP_THRESHOLD_USD=200.0,
        PROTOCOL_TOP_CHAIN_LIMIT=2,
        DEEP_ANALYSIS_PROTOCOL_PROVIDER="defillama",
    )

    tool = ProtocolTool(config)
    result = await tool.snapshot(slug="test-protocol", force_refresh=True)

    assert result.success is True
    assert result.triggered is True
    anomalies = result.data["anomalies"]
    assert "tvl_drop_24h_pct" in anomalies
    metrics = result.data["metrics"]
    assert metrics["tvl_usd"] == pytest.approx(500.0)
    assert metrics["top_chains"][0]["chain"] == "Ethereum"


@pytest.mark.asyncio
async def test_protocol_tool_cache(monkeypatch):
    calls = []

    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient(PROTOCOL_PAYLOAD, calls=calls, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.protocol.providers.defillama.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        PROTOCOL_CACHE_TTL_SECONDS=999,
        DEEP_ANALYSIS_PROTOCOL_PROVIDER="defillama",
    )

    tool = ProtocolTool(config)
    first = await tool.snapshot(slug="test-protocol", force_refresh=True)
    second = await tool.snapshot(slug="test-protocol")

    assert first.success is True
    assert second.success is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_protocol_tool_not_found(monkeypatch):
    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient({}, status_code=404, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.protocol.providers.defillama.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        PROTOCOL_CACHE_TTL_SECONDS=0,
        DEEP_ANALYSIS_PROTOCOL_PROVIDER="defillama",
    )

    tool = ProtocolTool(config)
    result = await tool.snapshot(slug="unknown", force_refresh=True)

    assert result.success is False
    assert result.error == "protocol_not_found"
