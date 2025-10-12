from types import SimpleNamespace

import pytest

from src.ai.tools.macro.fetcher import MacroTool


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

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return DummyResponse(self._payload, self._status)


@pytest.mark.asyncio
async def test_macro_tool_parses_fred_series(monkeypatch):
    payload = {
        "observations": [
            {"date": "2025-10-01", "value": "3.2"},
            {"date": "2025-09-01", "value": "2.9"},
            {"date": "2025-08-01", "value": "2.7"},
            {"date": "2024-10-01", "value": "2.4"},
        ]
    }

    calls = []

    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient(payload, calls=calls, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.macro.providers.fred.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        MACRO_CACHE_TTL_SECONDS=0,
        DEEP_ANALYSIS_MACRO_PROVIDER="fred",
        FRED_API_KEY="test",
        MACRO_EXPECTATIONS={"CPI": 3.0},
    )

    tool = MacroTool(config)
    result = await tool.snapshot(indicator="CPI", force_refresh=True)

    assert result.success is True
    assert result.data["indicator"] == "CPI"
    metrics = result.data["metrics"]
    assert metrics["value"] == pytest.approx(3.2)
    assert metrics["change_mom_pct"] == pytest.approx((3.2 - 2.9) / 2.9 * 100)
    assert metrics["expectation"] == 3.0
    assert metrics["surprise"] == pytest.approx(0.2)
    assert result.triggered is True  # mom_spike triggered
    assert "mom_spike" in result.data["anomalies"]


@pytest.mark.asyncio
async def test_macro_tool_uses_cache(monkeypatch):
    payload = {
        "observations": [
            {"date": "2025-10-01", "value": "5.0"},
            {"date": "2025-09-30", "value": "4.9"},
            {"date": "2025-09-29", "value": "4.8"},
        ]
    }
    calls = []

    def dummy_client_constructor(*args, **kwargs):
        return DummyAsyncClient(payload, calls=calls, **kwargs)

    monkeypatch.setattr(
        "src.ai.tools.macro.providers.fred.httpx.AsyncClient",
        dummy_client_constructor,
    )

    config = SimpleNamespace(
        DEEP_ANALYSIS_TOOL_TIMEOUT=5,
        MACRO_CACHE_TTL_SECONDS=999,
        DEEP_ANALYSIS_MACRO_PROVIDER="fred",
        FRED_API_KEY="test",
        MACRO_EXPECTATIONS={},
    )

    tool = MacroTool(config)
    first = await tool.snapshot(indicator="VIX", force_refresh=True)
    second = await tool.snapshot(indicator="VIX", force_refresh=False)

    assert first.success is True
    assert second.success is True
    assert len(calls) == 1  # second call should hit cache
