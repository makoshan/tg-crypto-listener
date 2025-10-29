import asyncio
from typing import Any, Dict

import pytest

from src.ai.tools.search.providers.brave import BraveSearchProvider


class _DummyConfig:
    BRAVE_API_KEY = "test-key"
    SEARCH_MULTI_SOURCE_THRESHOLD = 2
    DEEP_ANALYSIS_TOOL_TIMEOUT = 5


class _MockResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any]):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _MockAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers: Dict[str, str], params: Dict[str, Any]):
        assert headers.get("X-Subscription-Token") == _DummyConfig.BRAVE_API_KEY
        assert "q" in params and "count" in params
        payload = {
            "web": {
                "results": [
                    {
                        "title": "A",
                        "url": "https://coindesk.com/a",
                        "meta_url": {"host": "coindesk.com"},
                        "description": "official statement",
                    },
                    {
                        "title": "B",
                        "url": "https://reuters.com/b",
                        "meta_url": {"host": "reuters.com"},
                        "description": "news summary",
                    },
                ]
            }
        }
        return _MockResponse(200, payload)


@pytest.mark.asyncio
async def test_brave_provider_basic_mapping(monkeypatch):
    # Patch httpx.AsyncClient with our mock
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)

    provider = BraveSearchProvider(_DummyConfig)
    result = await provider.search(keyword="bitcoin etf", max_results=5)

    assert result.success is True
    assert result.source == "Brave"
    data = result.data
    assert data.get("source_count") == 2
    assert data.get("unique_domains") == 2
    assert any(r["source"] == "coindesk.com" for r in data.get("results", []))
    assert any(r["source"] == "reuters.com" for r in data.get("results", []))
    # multi-source threshold = 2 → triggered=True
    assert result.triggered is True
    assert 0.5 <= result.confidence <= 1.0


@pytest.mark.asyncio
async def test_brave_provider_domain_filter(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)

    provider = BraveSearchProvider(_DummyConfig)
    result = await provider.search(
        keyword="bitcoin etf",
        max_results=5,
        include_domains=["coindesk.com"],
    )

    assert result.success is True
    data = result.data
    assert data.get("source_count") == 1
    assert data.get("unique_domains") == 1
    assert all(r["source"] == "coindesk.com" for r in data.get("results", []))
    # unique_domains=1 < threshold=2 → not triggered
    assert result.triggered is False

