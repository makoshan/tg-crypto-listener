"""测试统一检索方案是否正确实现。

验证点：
1. SupabaseMemoryRepository.fetch_memories() 调用 search_memory RPC（不是 search_memory_events）
2. 支持关键词参数
3. 正确处理返回的 news_event_id 并查询完整信号信息
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.repository import SupabaseMemoryRepository, MemoryRepositoryConfig
from src.memory.types import MemoryContext


class MockSupabaseClient:
    """模拟 SupabaseClient，记录调用的 RPC 名称。"""

    def __init__(self):
        self.rpc_calls = []
        self.select_one_results = {}

    async def rpc(self, name: str, params: dict):
        """记录 RPC 调用。"""
        self.rpc_calls.append({"name": name, "params": params})
        # 返回模拟的 search_memory 结果
        if name == "search_memory":
            return [
                {
                    "match_type": "vector",
                    "news_event_id": 1,
                    "created_at": "2025-01-15T10:00:00Z",
                    "content_text": "Test content",
                    "translated_text": "测试内容",
                    "similarity": 0.9,
                    "keyword_score": None,
                    "combined_score": 0.9,
                },
                {
                    "match_type": "keyword",
                    "news_event_id": 2,
                    "created_at": "2025-01-14T10:00:00Z",
                    "content_text": "Another test",
                    "translated_text": "另一个测试",
                    "similarity": None,
                    "keyword_score": 0.8,
                    "combined_score": 0.75,
                },
            ]
        return []

    async def select_one(self, table: str, filters: dict, columns: str = None, order_by: str = None):
        """模拟查询单个记录。"""
        key = f"{table}_{filters.get('news_event_id') or filters.get('id')}"
        return self.select_one_results.get(key, None)


@pytest.mark.asyncio
async def test_supabase_memory_repo_uses_search_memory_rpc():
    """验证 SupabaseMemoryRepository 使用 search_memory RPC。"""
    # 创建模拟的 UnifiedMemoryRepository
    mock_unified_repo = AsyncMock()
    mock_unified_repo.search_memory.return_value = {
        "hits": [
            {
                "match_type": "vector",
                "news_event_id": 1,
                "created_at": "2025-01-15T10:00:00Z",
                "content_text": "Test",
                "translated_text": "测试",
                "similarity": 0.9,
                "combined_score": 0.9,
            }
        ],
        "stats": {"total": 1, "vector": 1, "keyword": 0},
    }

    # 创建模拟的 SupabaseClient（用于查询信号信息）
    mock_client = MockSupabaseClient()
    mock_client.select_one_results = {
        "ai_signals_1": {
            "id": 100,
            "summary_cn": "测试摘要",
            "assets": "BTC,ETH",
            "action": "buy",
            "confidence": 0.85,
            "created_at": "2025-01-15T10:00:00Z",
        }
    }

    # 替换 UnifiedMemoryRepository
    from src.memory import repository
    original_init = SupabaseMemoryRepository.__init__
    original_unified_repo_attr = None

    def mock_init(self, client, config=None):
        original_init(self, client, config)
        self._unified_repo = mock_unified_repo

    SupabaseMemoryRepository.__init__ = mock_init

    try:
        repo = SupabaseMemoryRepository(mock_client, MemoryRepositoryConfig(max_notes=5))
        repo._client = mock_client  # 确保使用我们的 mock client

        # 测试调用
        embedding = [0.1] * 1536
        context = await repo.fetch_memories(embedding=embedding, keywords=["bitcoin"])

        # 验证：应该调用 search_memory，不是 search_memory_events
        assert mock_unified_repo.search_memory.called, "应该调用 unified_repo.search_memory()"
        call_args = mock_unified_repo.search_memory.call_args
        assert call_args is not None
        kwargs = call_args.kwargs

        # 验证参数
        assert kwargs.get("embedding_1536") == embedding
        assert kwargs.get("keywords") == ["bitcoin"]
        assert kwargs.get("match_threshold") == 0.85

        # 验证返回结果
        assert isinstance(context, MemoryContext)
        assert len(context.entries) == 1
        entry = context.entries[0]
        assert entry.id == "100"
        assert entry.summary == "测试摘要"
        assert entry.assets == ["BTC", "ETH"]
        assert entry.action == "buy"
        assert entry.confidence == 0.85

    finally:
        # 恢复原始实现
        SupabaseMemoryRepository.__init__ = original_init


@pytest.mark.asyncio
async def test_supabase_memory_repo_supports_keywords_only():
    """验证仅使用关键词（无 embedding）时也能工作。"""
    mock_unified_repo = AsyncMock()
    mock_unified_repo.search_memory.return_value = {
        "hits": [
            {
                "match_type": "keyword",
                "news_event_id": 3,
                "created_at": "2025-01-13T10:00:00Z",
                "content_text": "Keyword match",
                "translated_text": "关键词匹配",
                "similarity": None,
                "keyword_score": 0.7,
                "combined_score": 0.7,
            }
        ],
        "stats": {"total": 1, "vector": 0, "keyword": 1},
    }

    mock_client = MockSupabaseClient()
    mock_client.select_one_results = {
        "ai_signals_3": {
            "id": 200,
            "summary_cn": "关键词匹配摘要",
            "assets": "SOL",
            "action": "observe",
            "confidence": 0.7,
            "created_at": "2025-01-13T10:00:00Z",
        }
    }

    from src.memory import repository

    def mock_init(self, client, config=None):
        SupabaseMemoryRepository.__init__.__wrapped__(self, client, config)
        self._unified_repo = mock_unified_repo

    SupabaseMemoryRepository.__init__ = mock_init

    try:
        repo = SupabaseMemoryRepository(mock_client, MemoryRepositoryConfig(max_notes=5))
        repo._client = mock_client

        # 仅使用关键词，无 embedding
        context = await repo.fetch_memories(embedding=None, keywords=["etf", "bitcoin"])

        # 验证调用参数
        assert mock_unified_repo.search_memory.called
        call_args = mock_unified_repo.search_memory.call_args
        kwargs = call_args.kwargs

        assert kwargs.get("embedding_1536") is None
        assert kwargs.get("keywords") == ["etf", "bitcoin"]

        # 验证结果
        assert len(context.entries) == 1
        assert context.entries[0].summary == "关键词匹配摘要"

    finally:
        # 恢复
        import importlib
        importlib.reload(repository)


@pytest.mark.asyncio
async def test_coordinator_fetch_memory_evidence():
    """测试 fetch_memory_evidence 协调器。"""
    from src.memory.coordinator import fetch_memory_evidence

    class MockConfig:
        SUPABASE_URL = "https://test.supabase.co"
        SUPABASE_SERVICE_KEY = "test-key"

    # Mock get_supabase_client 和 MemoryRepository
    import src.memory.coordinator as coordinator_module
    original_get_client = coordinator_module.get_supabase_client
    original_repo_class = coordinator_module.MemoryRepository

    mock_client = MagicMock()
    mock_repo = AsyncMock()
    mock_repo.search_memory.return_value = {
        "hits": [{"match_type": "vector", "news_event_id": 1}],
        "stats": {"total": 1, "vector": 1, "keyword": 0},
    }

    def mock_get_client(url, service_key):
        return mock_client

    def mock_repo_init(self, client):
        pass

    async def mock_search_memory(self, **kwargs):
        return mock_repo.search_memory.return_value

    MemoryRepositoryMock = type("MemoryRepository", (), {"__init__": mock_repo_init, "search_memory": mock_search_memory})

    coordinator_module.get_supabase_client = mock_get_client
    coordinator_module.MemoryRepository = MemoryRepositoryMock

    try:
        result = await fetch_memory_evidence(
            config=MockConfig(),
            embedding_1536=[0.1] * 1536,
            keywords=["bitcoin"],
            asset_codes=["BTC"],
            match_count=5,
        )

        # 验证结果结构
        assert "supabase_hits" in result or "local_keyword" in result
        if "supabase_hits" in result:
            assert len(result["supabase_hits"]) == 1
            assert "notes" in result

    finally:
        coordinator_module.get_supabase_client = original_get_client
        coordinator_module.MemoryRepository = original_repo_class
