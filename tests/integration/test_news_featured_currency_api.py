"""Integration test for the featured news-by-currency endpoint."""

from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

import pytest
import httpx


BASE_URL = "https://openapi.sosovalue.com/api/v1/news/featured/currency"
API_KEY_ENV_VAR = "SOSO_API_KEY"
# Fallback to the shared sandbox key if an env var is not provided.
DEFAULT_API_KEY = "SOSO-9ff2a05bb0b7494997db881fe2e49689"

# Default request scope focuses on BTC with a small page size for quick validation.
DEFAULT_PARAMS = {
    "pageNum": 1,
    "pageSize": 5,
    "currencyId": 1673723677362319866,
    "categoryList": "1,2,3,4,5,6,7,9,10",
}


@pytest.fixture
def anyio_backend() -> str:
    """Force AnyIO to use asyncio backend to avoid trio dependency."""
    return "asyncio"


def _assert_multilang_payload(multilang_entries: Sequence[Mapping[str, Any]]) -> None:
    """Validate multilanguage content entries contain language, title, and content."""
    assert multilang_entries, "Expected at least one multilanguage content entry"
    for entry in multilang_entries:
        assert isinstance(entry.get("language"), str) and entry["language"], \
            "Each multilanguage entry must include a language code"
        title = entry.get("title")
        assert title is None or isinstance(title, str), \
            "Multilanguage entry title must be a string when present"
        assert isinstance(entry.get("content"), str), \
            "Each multilanguage entry must include HTML content"


def _assert_media_payload(media_entries: Sequence[Mapping[str, Any]]) -> None:
    """Validate media payload structure."""
    for entry in media_entries:
        assert isinstance(entry.get("type"), str), "Media entry must expose a type"
        # URLs may be empty strings, but the fields should exist for consistency.
        assert "sosoUrl" in entry, "Media entry should expose sosoUrl"
        assert "originalUrl" in entry, "Media entry should expose originalUrl"


def _assert_currency_payload(currency_entries: Sequence[Mapping[str, Any]]) -> None:
    """Validate currency payload structure."""
    assert currency_entries, "Expected at least one matched currency in the payload"
    for currency in currency_entries:
        currency_id = currency.get("id")
        assert isinstance(currency_id, (str, int)), "Currency id should be a string or integer"
        assert isinstance(currency.get("fullName"), str) and currency["fullName"], \
            "Currency fullName must be a non-empty string"
        assert isinstance(currency.get("name"), str) and currency["name"], \
            "Currency symbol must be a non-empty string"


def _assert_tags_payload(tags: Sequence[Any]) -> None:
    """Validate tags payload includes string entries."""
    for tag in tags:
        assert isinstance(tag, str) and tag, "Tags must be non-empty strings"


@pytest.mark.integration
@pytest.mark.anyio("asyncio")
async def test_featured_news_currency_smoke() -> None:
    """Smoke test that verifies structure of the featured news feed by currency."""
    api_key = os.getenv(API_KEY_ENV_VAR, DEFAULT_API_KEY)
    if not api_key:
        pytest.skip(
            f"Missing required API key. Set {API_KEY_ENV_VAR} or provide a default key."
        )

    headers = {"x-soso-api-key": api_key}

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        response = await client.get(BASE_URL, params=DEFAULT_PARAMS, headers=headers)

    if response.status_code == 401:
        pytest.skip("API key rejected (401). Provide a valid SOSO API key to run the test.")

    assert response.status_code == 200, \
        f"Unexpected status code {response.status_code}: {response.text}"

    payload = response.json()
    assert payload.get("code") == 0, f"API returned error payload: {payload}"
    observed_trace_keys = {"traceId", "traceID", "trace_id", "tid", "TID"}
    assert observed_trace_keys & payload.keys(), \
        "Expected a trace identifier field in the response payload"

    data = payload.get("data")
    assert isinstance(data, dict), "Expected 'data' to be a dict"
    assert str(data.get("pageNum")) == str(DEFAULT_PARAMS["pageNum"]), \
        "pageNum should echo request"
    assert str(data.get("pageSize")) == str(DEFAULT_PARAMS["pageSize"]), \
        "pageSize should echo request"
    for numeric_meta_field in ("totalPages", "total"):
        if numeric_meta_field in data:
            try:
                int(data[numeric_meta_field])
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive path
                raise AssertionError(
                    f"{numeric_meta_field} should be numeric string/int. "
                    f"Observed value: {data[numeric_meta_field]}"
                ) from exc

    news_items = data.get("list")
    assert isinstance(news_items, list), "data.list should be a list"
    assert len(news_items) <= DEFAULT_PARAMS["pageSize"], \
        "Returned item count must not exceed requested pageSize"

    if not news_items:
        pytest.skip("No featured news returned for the given filters; cannot validate payload shape.")

    first_item = news_items[0]
    for field in (
        "id",
        "sourceLink",
        "releaseTime",
        "author",
        "category",
        "matchedCurrencies",
        "tags",
        "multilanguageContent",
        "mediaInfo",
        "nickName",
    ):
        assert field in first_item, f"Expected field '{field}' in news item payload"

    assert isinstance(first_item["releaseTime"], int), "releaseTime should be a millisecond epoch"
    assert isinstance(first_item["category"], int), "category should be an integer"
    assert isinstance(first_item["author"], str) and first_item["author"], \
        "author should be a non-empty string"
    assert isinstance(first_item["matchedCurrencies"], list), \
        "matchedCurrencies should be a list"
    assert isinstance(first_item["tags"], list), "tags should be a list"
    assert isinstance(first_item["mediaInfo"], list), "mediaInfo should be a list"

    _assert_multilang_payload(first_item["multilanguageContent"])
    _assert_media_payload(first_item["mediaInfo"])
    _assert_currency_payload(first_item["matchedCurrencies"])
    _assert_tags_payload(first_item["tags"])

    for optional_str_field in (
        "authorDescription",
        "authorAvatarUrl",
        "featureImage",
    ):
        value = first_item.get(optional_str_field)
        if value is not None:
            assert isinstance(value, str), f"{optional_str_field} should be a string when present"

    quote_info = first_item.get("quoteInfo")
    if quote_info:
        assert isinstance(quote_info, dict), "quoteInfo should be an object when present"
        if media := quote_info.get("mediaInfo", []):
            _assert_media_payload(media)
        if multilanguage := quote_info.get("multilanguageContent", []):
            _assert_multilang_payload(multilanguage)
        for metric in ("impressionCount", "likeCount", "replyCount", "retweetCount"):
            if metric in quote_info:
                assert isinstance(quote_info[metric], int), f"{metric} should be an int"
        if "originalUrl" in quote_info:
            assert isinstance(quote_info["originalUrl"], str), \
                "originalUrl should be a string when present on quoteInfo"
