from datetime import datetime, timezone, timedelta

import pytest

from src.memory.multi_source_repository import MultiSourceMemoryRepository
from src.memory.repository import MemoryRepositoryConfig
from src.memory.types import MemoryContext, MemoryEntry


class DummyPrimaryRepository:
    def __init__(self) -> None:
        now = datetime.now(tz=timezone.utc)
        self._context = MemoryContext(
            entries=[
                MemoryEntry(
                    id="news:1",
                    created_at=now - timedelta(hours=1),
                    assets=["BTC"],
                    action="inform",
                    confidence=0.8,
                    summary="Primary memory entry",
                    similarity=0.8,
                )
            ]
        )

    async def fetch_memories(self, *, embedding, asset_codes=None):
        return self._context


class DummySecondaryClient:
    async def rpc(self, function_name, params):
        assert function_name == "match_documents"
        return [
            {
                "id": 101,
                "ai_summary_cn": "Secondary summary",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "tags": {"entities": ["ETH"]},
                "canonical_url": "https://example.com/doc",
                "source": "docs-feed",
                "source_author": "analyst",
                "similarity": 0.9,
            },
            {
                "id": 102,
                "content_text": "Backup summary text for dedupe",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "tags": {"entities": ["BTC"]},
                "similarity": 0.5,
            },
        ]


@pytest.mark.asyncio
async def test_multi_source_merges_primary_and_secondary():
    config = MemoryRepositoryConfig(max_notes=2, similarity_threshold=0.5)
    primary = DummyPrimaryRepository()
    secondary_client = DummySecondaryClient()

    repo = MultiSourceMemoryRepository(
        primary=primary,  # type: ignore[arg-type]
        secondary_client=secondary_client,
        secondary_table="docs",
        config=config,
        secondary_similarity_threshold=0.7,
        secondary_max_results=5,
    )

    context = await repo.fetch_memories(embedding=[0.1, 0.2, 0.3], asset_codes=None)

    assert len(context.entries) == 2
    # Secondary entry should be first due to higher similarity
    assert context.entries[0].id.startswith("docs:")
    assert "Secondary summary" in context.entries[0].summary
