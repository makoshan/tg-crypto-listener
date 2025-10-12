"""Unit tests for Tavily search provider and SearchTool facade."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from src.ai.tools.base import ToolResult
from src.ai.tools.search.fetcher import SearchTool
from src.ai.tools.search.providers.tavily import TavilySearchProvider


class MockResponse:
    """Simple HTTPX response mock that mimics minimal behaviour we rely on."""

    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600 and self.status_code != 429:
            raise httpx.HTTPStatusError(
                message="HTTP error",
                request=Mock(spec=httpx.Request),
                response=Mock(spec=httpx.Response),
            )


def _make_tool_result(**data) -> ToolResult:
    return ToolResult(
        source="Tavily",
        timestamp=ToolResult._format_timestamp(),
        success=True,
        data=data or {},
        triggered=False,
        confidence=0.7,
    )


@pytest.fixture
def mock_config() -> Mock:
    config = Mock()
    config.TAVILY_API_KEY = "test-api-key"
    config.SEARCH_MAX_RESULTS = 5
    config.SEARCH_MULTI_SOURCE_THRESHOLD = 2
    config.SEARCH_CACHE_TTL_SECONDS = 600
    config.SEARCH_INCLUDE_DOMAINS = "coindesk.com,theblock.co"
    config.DEEP_ANALYSIS_TOOL_TIMEOUT = 10
    config.DEEP_ANALYSIS_SEARCH_PROVIDER = "tavily"
    return config


@pytest.fixture
def provider(mock_config: Mock) -> TavilySearchProvider:
    return TavilySearchProvider(mock_config)


@pytest.fixture
def search_tool(mock_config: Mock) -> SearchTool:
    return SearchTool(mock_config)


def test_provider_requires_api_key() -> None:
    config = Mock()
    config.TAVILY_API_KEY = ""
    config.SEARCH_MULTI_SOURCE_THRESHOLD = 3
    config.DEEP_ANALYSIS_TOOL_TIMEOUT = 10

    with pytest.raises(ValueError):
        TavilySearchProvider(config)


@pytest.mark.asyncio
async def test_successful_search(provider: TavilySearchProvider) -> None:
    mock_payload = {
        "results": [
            {
                "title": "Circle releases official statement on USDC",
                "url": "https://coindesk.com/article",
                "content": "This is an official statement confirming stability.",
                "score": 0.92,
            },
            {
                "title": "USDC peg holds according to exchange data",
                "url": "https://theblock.co/update",
                "content": "Markets react calmly.",
                "score": 0.88,
            },
        ]
    }

    with patch(
        "src.ai.tools.search.providers.tavily.httpx.AsyncClient",
        autospec=True,
    ) as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = MockResponse(200, mock_payload)
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__.return_value = False

        result = await provider.search(keyword="USDC depeg", max_results=5)

    assert result.success is True
    assert result.source == "Tavily"
    assert result.data["keyword"] == "USDC depeg"
    assert result.data["source_count"] == 2
    assert result.data["official_confirmed"] is True
    assert result.data["multi_source"] is True
    assert result.triggered is True
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_rate_limit_error(provider: TavilySearchProvider) -> None:
    with patch(
        "src.ai.tools.search.providers.tavily.httpx.AsyncClient",
        autospec=True,
    ) as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = MockResponse(status_code=429)
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__.return_value = False

        result = await provider.search(keyword="rate limit", max_results=5)

    assert result.success is False
    assert result.error == "rate_limit"


@pytest.mark.asyncio
async def test_timeout_handled(provider: TavilySearchProvider) -> None:
    with patch(
        "src.ai.tools.search.providers.tavily.httpx.AsyncClient",
        autospec=True,
    ) as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("timeout")
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__.return_value = False

        result = await provider.search(keyword="slow response", max_results=5)

    assert result.success is False
    assert isinstance(result.error, str)
    assert result.error.startswith("timeout:")


@pytest.mark.asyncio
async def test_http_error_returns_failure(provider: TavilySearchProvider) -> None:
    with patch(
        "src.ai.tools.search.providers.tavily.httpx.AsyncClient",
        autospec=True,
    ) as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = MockResponse(status_code=500)
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__.return_value = False

        result = await provider.search(keyword="server error", max_results=5)

    assert result.success is False
    assert result.error


@pytest.mark.asyncio
async def test_search_tool_respects_max_results(search_tool: SearchTool) -> None:
    tool_result = _make_tool_result(results=[])
    with patch.object(
        search_tool._provider,
        "search",
        new=AsyncMock(return_value=tool_result),
    ) as mock_search:
        await search_tool.fetch(keyword="anything")

    mock_search.assert_awaited_once()
    _, kwargs = mock_search.call_args
    assert kwargs["max_results"] == search_tool._max_results


@pytest.mark.asyncio
async def test_search_tool_cache_hit(search_tool: SearchTool) -> None:
    tool_result = _make_tool_result(keyword="cached")
    with patch.object(
        search_tool._provider,
        "search",
        new=AsyncMock(return_value=tool_result),
    ) as mock_search:
        first = await search_tool.fetch(keyword="cached query")
        second = await search_tool.fetch(keyword="cached query")

    assert first is tool_result
    assert second is tool_result
    assert mock_search.await_count == 1


@pytest.mark.asyncio
async def test_search_tool_cache_distinguishes_domains(search_tool: SearchTool) -> None:
    first_result = _make_tool_result(keyword="alpha")
    second_result = _make_tool_result(keyword="alpha", domains=["coindesk.com"])

    with patch.object(
        search_tool._provider,
        "search",
        new=AsyncMock(side_effect=[first_result, second_result]),
    ) as mock_search:
        await search_tool.fetch(keyword="domain test")
        await search_tool.fetch(
            keyword="domain test",
            include_domains=["coindesk.com"],
        )

    assert mock_search.await_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tavily_real_api() -> None:
    api_key = os.getenv("TAVILY_API_KEY") or ""
    if not api_key:
        pytest.skip("TAVILY_API_KEY not set, skipping integration test")

    config = Mock()
    config.TAVILY_API_KEY = api_key
    config.SEARCH_MAX_RESULTS = 3
    config.SEARCH_MULTI_SOURCE_THRESHOLD = 2
    config.SEARCH_CACHE_TTL_SECONDS = 0
    config.DEEP_ANALYSIS_TOOL_TIMEOUT = 15
    config.DEEP_ANALYSIS_SEARCH_PROVIDER = "tavily"

    provider = TavilySearchProvider(config)
    result = await provider.search(keyword="Bitcoin price news", max_results=3)

    assert result.success is True
    assert result.data["results"]
