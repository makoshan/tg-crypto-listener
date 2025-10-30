import asyncio
import types

import pytest

from src.db.repositories import MemoryRepository


class DummyClient:
    def __init__(self, payload):
        self._payload = payload

    async def rpc(self, name: str, params: dict):  # noqa: D401
        assert name == "search_memory"
        return self._payload


@pytest.mark.asyncio
async def test_memory_repo_counts_vector_and_keyword():
    rows = [
        {
            "match_type": "vector",
            "news_event_id": 1,
            "created_at": "2025-10-30T00:00:00Z",
            "content_text": "a",
            "translated_text": "a",
            "similarity": 0.9,
            "keyword_score": None,
            "combined_score": 0.9,
        },
        {
            "match_type": "keyword",
            "news_event_id": 2,
            "created_at": "2025-10-30T00:00:01Z",
            "content_text": "b",
            "translated_text": "b",
            "similarity": None,
            "keyword_score": 0.1,
            "combined_score": 0.4,
        },
    ]
    repo = MemoryRepository(DummyClient(rows))
    res = await repo.search_memory(keywords=["btc"], match_count=5)
    assert res["stats"]["total"] == 2
    assert res["stats"]["vector"] == 1
    assert res["stats"]["keyword"] == 1
    assert len(res["hits"]) == 2


