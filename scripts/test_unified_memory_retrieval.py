#!/usr/bin/env python3
"""测试统一检索方案（retrieval_augmentation.md）是否正确生效。

测试目标：
1. SupabaseMemoryRepository.fetch_memories() 使用 search_memory RPC
2. fetch_memory_evidence() 协调器正常工作
3. 向量优先 + 关键词降级逻辑
4. 日志输出验证
"""

import os
import json
import asyncio
import argparse
from typing import List, Optional

from dotenv import load_dotenv

from src.db.supabase_client import get_supabase_client
from src.memory.repository import SupabaseMemoryRepository, MemoryRepositoryConfig
from src.memory.coordinator import fetch_memory_evidence
from src.utils import setup_logger


async def test_supabase_memory_repository(
    embedding: Optional[List[float]] = None,
    keywords: Optional[List[str]] = None,
    asset_codes: Optional[List[str]] = None,
    config: Optional[MemoryRepositoryConfig] = None,
) -> None:
    """测试 SupabaseMemoryRepository.fetch_memories() 是否使用统一检索方案。"""
    logger = setup_logger("test.unified.retrieval")

    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not supabase_key:
        logger.error("❌ 缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY")
        return

    logger.info("=" * 80)
    logger.info("🧪 测试 1: SupabaseMemoryRepository.fetch_memories()")
    logger.info("=" * 80)

    client = get_supabase_client(url=supabase_url, service_key=supabase_key)
    repo_config = config or MemoryRepositoryConfig(
        max_notes=5,
        similarity_threshold=0.85,
        lookback_hours=72,
        min_confidence=0.6,
    )
    repo = SupabaseMemoryRepository(client, repo_config)

    logger.info(f"📝 测试参数:")
    logger.info(f"   - embedding: {'有' if embedding else '无'} (维度={len(embedding) if embedding else 0})")
    logger.info(f"   - keywords: {keywords or []}")
    logger.info(f"   - asset_codes: {asset_codes or []}")
    logger.info(f"   - match_threshold: {repo_config.similarity_threshold}")
    logger.info(f"   - match_count: {repo_config.max_notes}")

    try:
        context = await repo.fetch_memories(
            embedding=embedding,
            keywords=keywords,
            asset_codes=asset_codes,
        )

        logger.info(f"\n✅ 检索结果:")
        logger.info(f"   - 找到 {len(context.entries)} 条记忆")
        for idx, entry in enumerate(context.entries, 1):
            logger.info(
                f"   [{idx}] id={entry.id[:8]}..., "
                f"similarity={entry.similarity:.3f}, "
                f"confidence={entry.confidence:.3f}, "
                f"assets={entry.assets}, "
                f"action={entry.action}"
            )
            logger.info(f"       摘要: {entry.summary[:100]}...")

        return context

    except Exception as exc:
        logger.error(f"❌ 测试失败: {exc}", exc_info=True)
        return None


async def test_fetch_memory_evidence(
    embedding: Optional[List[float]] = None,
    keywords: Optional[List[str]] = None,
    asset_codes: Optional[List[str]] = None,
) -> None:
    """测试 fetch_memory_evidence() 协调器。"""
    logger = setup_logger("test.coordinator")

    logger.info("\n" + "=" * 80)
    logger.info("🧪 测试 2: fetch_memory_evidence() 协调器")
    logger.info("=" * 80)

    # 创建一个简单的 config 对象
    class TestConfig:
        SUPABASE_URL = os.getenv("SUPABASE_URL", "")
        SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

    config = TestConfig()

    logger.info(f"📝 测试参数:")
    logger.info(f"   - embedding: {'有' if embedding else '无'}")
    logger.info(f"   - keywords: {keywords or []}")
    logger.info(f"   - asset_codes: {asset_codes or []}")

    try:
        result = await fetch_memory_evidence(
            config=config,
            embedding_1536=embedding,
            keywords=keywords,
            asset_codes=asset_codes,
            match_threshold=0.85,
            min_confidence=0.6,
            time_window_hours=72,
            match_count=5,
        )

        logger.info(f"\n✅ 协调器结果:")
        logger.info(f"   - supabase_hits: {len(result.get('supabase_hits', []))} 条")
        logger.info(f"   - local_keyword: {result.get('local_keyword', [])}")
        logger.info(f"   - notes: {result.get('notes', 'N/A')}")

        if result.get("supabase_hits"):
            logger.info(f"\n   Supabase 命中详情:")
            for idx, hit in enumerate(result["supabase_hits"][:3], 1):
                logger.info(
                    f"   [{idx}] match_type={hit.get('match_type')}, "
                    f"news_event_id={hit.get('news_event_id')}, "
                    f"similarity={hit.get('similarity')}, "
                    f"combined_score={hit.get('combined_score')}"
                )

        return result

    except Exception as exc:
        logger.error(f"❌ 测试失败: {exc}", exc_info=True)
        return None


async def main() -> None:
    """主测试流程。"""
    load_dotenv()

    parser = argparse.ArgumentParser(description="测试统一检索方案（retrieval_augmentation.md）")
    parser.add_argument("--keywords", nargs="*", default=None, help="关键词列表，例如: bitcoin etf")
    parser.add_argument(
        "--embedding-file",
        type=str,
        default=None,
        help="包含 1536 维 embedding 的 JSON 文件路径",
    )
    parser.add_argument("--assets", nargs="*", default=None, help="资产代码过滤，例如: BTC ETH SOL")
    parser.add_argument("--test-mode", choices=["repo", "coordinator", "all"], default="all", help="测试模式")
    args = parser.parse_args()

    logger = setup_logger("test.main")

    # 加载 embedding（如果提供）
    embedding: Optional[List[float]] = None
    if args.embedding_file:
        try:
            with open(args.embedding_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    embedding = [float(x) for x in data]
                    logger.info(f"✅ 已加载 embedding，维度: {len(embedding)}")
        except Exception as exc:
            logger.error(f"❌ 加载 embedding 文件失败: {exc}")
            return

    keywords = args.keywords if args.keywords else None
    asset_codes = args.assets if args.assets else None

    logger.info("\n" + "🚀" * 40)
    logger.info("开始测试统一检索方案")
    logger.info("🚀" * 40 + "\n")

    results = {}

    # 测试 1: SupabaseMemoryRepository
    if args.test_mode in ["repo", "all"]:
        repo_result = await test_supabase_memory_repository(
            embedding=embedding,
            keywords=keywords,
            asset_codes=asset_codes,
        )
        results["repo"] = repo_result

    # 测试 2: fetch_memory_evidence 协调器
    if args.test_mode in ["coordinator", "all"]:
        coordinator_result = await test_fetch_memory_evidence(
            embedding=embedding,
            keywords=keywords,
            asset_codes=asset_codes,
        )
        results["coordinator"] = coordinator_result

    # 总结
    logger.info("\n" + "=" * 80)
    logger.info("📊 测试总结")
    logger.info("=" * 80)

    if args.test_mode in ["repo", "all"]:
        repo_ctx = results.get("repo")
        if repo_ctx:
            logger.info(f"✅ SupabaseMemoryRepository: 找到 {len(repo_ctx.entries)} 条记忆")
        else:
            logger.warning("⚠️  SupabaseMemoryRepository: 测试未完成或失败")

    if args.test_mode in ["coordinator", "all"]:
        coord_result = results.get("coordinator")
        if coord_result:
            supabase_hits = len(coord_result.get("supabase_hits", []))
            local_kw = coord_result.get("local_keyword", [])
            logger.info(f"✅ fetch_memory_evidence: supabase_hits={supabase_hits}, local_keyword={len(local_kw)}")
        else:
            logger.warning("⚠️  fetch_memory_evidence: 测试未完成或失败")

    logger.info("\n✅ 测试完成！")
    logger.info("\n提示：检查日志输出，应该看到 '统一检索开始 (search_memory RPC)' 而不是 'search_memory_events'")


if __name__ == "__main__":
    asyncio.run(main())
