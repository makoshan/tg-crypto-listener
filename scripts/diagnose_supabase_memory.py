#!/usr/bin/env python3
"""
诊断 Supabase search_memory RPC 函数问题
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import Config
from src.db.supabase_client import SupabaseClient
from src.db.repositories import MemoryRepository
from src.utils import setup_logger

logger = setup_logger(__name__)


async def test_search_memory():
    """测试 search_memory RPC 函数"""
    config = Config()
    
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        logger.error("❌ Supabase 配置缺失：SUPABASE_URL 或 SUPABASE_SERVICE_KEY")
        return
    
    client = SupabaseClient(
        rest_url=config.SUPABASE_URL,
        service_key=config.SUPABASE_SERVICE_KEY,
        timeout=30.0
    )
    
    repo = MemoryRepository(client)
    
    # 测试 1: 仅关键词检索（不使用 embedding）
    logger.info("=" * 80)
    logger.info("测试 1: 仅关键词检索 (keywords=['bnb'], 不使用 embedding)")
    logger.info("=" * 80)
    
    result1 = await repo.search_memory(
        embedding_1536=None,
        keywords=["bnb"],
        asset_codes=None,
        match_threshold=0.48,  # 使用较低的阈值
        min_confidence=0.50,
        time_window_hours=72,
        match_count=5,
    )
    
    logger.info(f"结果 1 - total={result1['stats']['total']}, vector={result1['stats']['vector']}, keyword={result1['stats']['keyword']}")
    if result1['stats']['total'] > 0:
        logger.info(f"✅ 找到 {result1['stats']['total']} 条匹配记录")
        for i, hit in enumerate(result1['hits'][:3], 1):
            logger.info(f"  [{i}] match_type={hit.get('match_type')}, event_id={hit.get('news_event_id')}, similarity={hit.get('similarity')}, combined_score={hit.get('combined_score')}")
    else:
        logger.warning("⚠️  没有找到匹配记录")
    
    # 测试 2: 检查数据库中是否有数据
    logger.info("\n" + "=" * 80)
    logger.info("测试 2: 检查数据库中是否有数据")
    logger.info("=" * 80)
    
    try:
        # 查询最近的 news_events（使用 RPC 或直接 HTTP GET）
        from src.db.supabase_client import SupabaseClient
        import httpx
        
        # 使用 _request 方法查询
        recent_events = await client._request(
            "GET",
            "news_events",
            params={
                "select": "id,created_at,content_text,translated_text",
                "order": "created_at.desc",
                "limit": "5"
            }
        )
        
        if isinstance(recent_events, list) and recent_events:
            logger.info(f"✅ 数据库中有 {len(recent_events)} 条最近的 news_events 记录")
            for i, event in enumerate(recent_events[:3], 1):
                created_at = event.get('created_at', 'N/A')
                content_preview = (event.get('translated_text') or event.get('content_text') or '')[:60]
                logger.info(f"  [{i}] id={event.get('id')}, created_at={created_at}, preview={content_preview}...")
        else:
            logger.warning("⚠️  数据库中没有 news_events 记录")
        
        # 查询最近的 ai_signals
        recent_signals = await client._request(
            "GET",
            "ai_signals",
            params={
                "select": "id,news_event_id,created_at,summary_cn,confidence",
                "order": "created_at.desc",
                "limit": "5"
            }
        )
        
        if isinstance(recent_signals, list) and recent_signals:
            logger.info(f"✅ 数据库中有 {len(recent_signals)} 条最近的 ai_signals 记录")
            for i, signal in enumerate(recent_signals[:3], 1):
                created_at = signal.get('created_at', 'N/A')
                confidence = signal.get('confidence', 0)
                logger.info(f"  [{i}] id={signal.get('id')}, event_id={signal.get('news_event_id')}, confidence={confidence}, created_at={created_at}")
        else:
            logger.warning("⚠️  数据库中没有 ai_signals 记录")
            
    except Exception as e:
        logger.error(f"❌ 查询数据库失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # 测试 3: 直接测试 RPC 函数
    logger.info("\n" + "=" * 80)
    logger.info("测试 3: 直接测试 RPC search_memory 函数")
    logger.info("=" * 80)
    
    try:
        # 测试关键词检索
        rpc_result = await client.rpc(
            "search_memory",
            {
                "query_embedding": None,
                "query_keywords": ["bnb"],
                "match_threshold": 0.48,
                "match_count": 5,
                "min_confidence": 0.50,
                "time_window_hours": 72,
                "asset_filter": None,
            }
        )
        
        if isinstance(rpc_result, list):
            logger.info(f"✅ RPC 调用成功，返回 {len(rpc_result)} 条记录")
            if rpc_result:
                for i, row in enumerate(rpc_result[:3], 1):
                    logger.info(f"  [{i}] match_type={row.get('match_type')}, event_id={row.get('news_event_id')}, similarity={row.get('similarity')}")
            else:
                logger.warning("⚠️  RPC 返回空列表")
        else:
            logger.warning(f"⚠️  RPC 返回非列表类型: {type(rpc_result)}")
            logger.warning(f"   内容: {rpc_result}")
            
    except Exception as e:
        logger.error(f"❌ RPC 调用失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # 测试 4: 检查 RPC 函数是否存在
    logger.info("\n" + "=" * 80)
    logger.info("测试 4: 检查 RPC 函数是否存在")
    logger.info("=" * 80)
    
    try:
        # 尝试查询 pg_proc 表（需要 PostgreSQL 权限）
        # 或者尝试调用一个不存在的函数来确认错误类型
        await client.rpc("nonexistent_function", {})
    except Exception as e:
        error_msg = str(e)
        if "does not exist" in error_msg.lower() or "function" in error_msg.lower():
            logger.warning(f"⚠️  函数不存在错误: {e}")
            logger.warning("   这表示 RPC 调用机制正常，但函数可能不存在")
        else:
            logger.info(f"✅ RPC 调用机制正常（预期的错误）: {e}")


if __name__ == "__main__":
    asyncio.run(test_search_memory())
