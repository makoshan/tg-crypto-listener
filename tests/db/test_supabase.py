#!/usr/bin/env python3
"""
Supabase 连接和功能测试脚本

测试项目:
1. 基础连接测试
2. 表结构验证
3. 向量搜索测试
4. 写入/读取测试
"""

import asyncio
import sys
from datetime import datetime, UTC
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.db.supabase_client import SupabaseClient
from src.memory.repository import SupabaseMemoryRepository, MemoryRepositoryConfig
from src.memory.types import MemoryEntry
from src.utils import setup_logger

logger = setup_logger(__name__)

# Embedding 生成器（简化版）
try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


async def test_1_basic_connection():
    """测试 1: 基础连接"""
    print("\n" + "="*60)
    print("测试 1: Supabase 基础连接")
    print("="*60)

    if not Config.SUPABASE_URL or not Config.SUPABASE_SERVICE_KEY:
        print("❌ SUPABASE_URL 或 SUPABASE_SERVICE_KEY 未配置")
        print(f"   SUPABASE_URL: {'已配置' if Config.SUPABASE_URL else '未配置'}")
        print(f"   SUPABASE_SERVICE_KEY: {'已配置' if Config.SUPABASE_SERVICE_KEY else '未配置'}")
        return False

    try:
        client = SupabaseClient(
            rest_url=Config.SUPABASE_URL,
            service_key=Config.SUPABASE_SERVICE_KEY,
            timeout=Config.SUPABASE_TIMEOUT_SECONDS
        )

        print(f"✅ Supabase 客户端初始化成功")
        print(f"   URL: {Config.SUPABASE_URL}")
        print(f"   Timeout: {Config.SUPABASE_TIMEOUT_SECONDS}s")
        return True

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


async def test_2_table_structure():
    """测试 2: 表结构验证（news_events + ai_signals）"""
    print("\n" + "="*60)
    print("测试 2: 表结构验证（news_events + ai_signals）")
    print("="*60)

    try:
        client = SupabaseClient(
            rest_url=Config.SUPABASE_URL,
            service_key=Config.SUPABASE_SERVICE_KEY,
            timeout=Config.SUPABASE_TIMEOUT_SECONDS
        )

        # 检查 news_events 表
        ne_response = await client._request("GET", "news_events", params={"select": "id,embedding", "limit": "0"})
        print(f"✅ 表 'news_events' 存在")

        # 统计有 embedding 的记录
        ne_with_emb = await client._request("GET", "news_events", params={"select": "id", "embedding": "not.is.null"})
        print(f"   有 embedding 的记录: {len(ne_with_emb) if isinstance(ne_with_emb, list) else 0} 条")

        # 检查 ai_signals 表
        ais_response = await client._request("GET", "ai_signals", params={"select": "id", "limit": "0"})
        print(f"✅ 表 'ai_signals' 存在")

        # 统计总记录数
        ais_count = await client._request("GET", "ai_signals", params={"select": "id", "limit": "1000"})
        print(f"   AI 信号记录: {len(ais_count) if isinstance(ais_count, list) else 0} 条")

        return True

    except Exception as e:
        print(f"❌ 表结构验证失败: {e}")
        print(f"   请确保已执行 supabase_migration.sql 创建表结构")
        return False


async def test_3_embedding_search():
    """测试 3: 向量搜索"""
    print("\n" + "="*60)
    print("测试 3: 向量搜索测试")
    print("="*60)

    if not Config.OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY 未配置，跳过向量搜索测试")
        return False

    if not HAS_OPENAI:
        print("❌ openai 模块未安装，跳过向量搜索测试")
        return False

    try:
        # 初始化 OpenAI 客户端
        openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

        # 生成测试查询的 embedding
        test_query = "Bitcoin price analysis"
        print(f"📝 测试查询: '{test_query}'")

        response = await openai_client.embeddings.create(
            input=test_query,
            model=Config.OPENAI_EMBEDDING_MODEL
        )
        embedding = response.data[0].embedding
        print(f"✅ Embedding 生成成功 (维度: {len(embedding)})")

        # 初始化 repository
        client = SupabaseClient(
            rest_url=Config.SUPABASE_URL,
            service_key=Config.SUPABASE_SERVICE_KEY,
            timeout=Config.SUPABASE_TIMEOUT_SECONDS
        )

        config = MemoryRepositoryConfig(
            max_notes=5,
            lookback_hours=72,
            min_confidence=0.5
        )

        repo = SupabaseMemoryRepository(client, config)

        # 执行向量搜索
        print(f"🔍 执行向量搜索...")
        context = await repo.fetch_memories(
            embedding=embedding,
            asset_codes=["BTC"]
        )

        print(f"✅ 搜索完成，找到 {len(context.entries)} 条相关记忆")

        if context.entries:
            print(f"\n📋 前 3 条记忆:")
            for i, entry in enumerate(context.entries[:3], 1):
                print(f"   [{i}] {entry.id[:8]}... | {entry.assets} | {entry.action}")
                print(f"       相似度={entry.similarity:.3f} | 置信度={entry.confidence:.2f}")
                print(f"       {entry.summary[:80]}...")
        else:
            print(f"⚠️  未找到相关记忆（这可能是正常的，如果数据库为空）")

        return True

    except Exception as e:
        print(f"❌ 向量搜索失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_4_write_read():
    """测试 4: 写入和读取"""
    print("\n" + "="*60)
    print("测试 4: 写入和读取测试")
    print("="*60)

    if not Config.OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY 未配置，跳过写入测试")
        return False

    if not HAS_OPENAI:
        print("❌ openai 模块未安装，跳过写入测试")
        return False

    try:
        # 初始化 OpenAI 客户端
        openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

        client = SupabaseClient(
            rest_url=Config.SUPABASE_URL,
            service_key=Config.SUPABASE_SERVICE_KEY,
            timeout=Config.SUPABASE_TIMEOUT_SECONDS
        )

        # 创建测试记忆条目
        test_summary = f"Test memory entry created at {datetime.now(UTC).isoformat()}"

        response = await openai_client.embeddings.create(
            input=test_summary,
            model=Config.OPENAI_EMBEDDING_MODEL
        )
        test_embedding = response.data[0].embedding

        test_entry = MemoryEntry(
            id=f"test_{datetime.now(UTC).timestamp()}",
            created_at=datetime.now(UTC),
            assets=["BTC", "TEST"],
            action="buy",
            confidence=0.85,
            similarity=0.0,  # 写入时不需要
            summary=test_summary,
            embedding=test_embedding
        )

        print(f"📝 创建测试记忆: {test_entry.id}")
        print(f"   Assets: {test_entry.assets}")
        print(f"   Summary: {test_entry.summary}")

        # 写入数据库
        print(f"💾 写入 Supabase...")

        insert_data = {
            "id": test_entry.id,
            "created_at": test_entry.created_at.isoformat(),
            "assets": test_entry.assets,
            "action": test_entry.action,
            "confidence": test_entry.confidence,
            "summary": test_entry.summary,
            "embedding": test_entry.embedding,
        }

        await client.insert("memory_entries", insert_data)
        print(f"✅ 写入成功")

        # 读取验证
        print(f"🔍 读取验证...")
        read_response = await client._request("GET", "memory_entries", params={"select": "*", "id": f"eq.{test_entry.id}"})

        if read_response and len(read_response) > 0:
            retrieved = read_response[0]
            print(f"✅ 读取成功")
            print(f"   ID: {retrieved['id']}")
            print(f"   Assets: {retrieved['assets']}")
            print(f"   Action: {retrieved['action']}")
            print(f"   Confidence: {retrieved['confidence']}")

            # 清理测试数据
            print(f"🗑️  清理测试数据...")
            await client._request("DELETE", "memory_entries", params={"id": f"eq.{test_entry.id}"})
            print(f"✅ 测试数据已清理")

            return True
        else:
            print(f"❌ 读取失败：未找到刚写入的记录")
            return False

    except Exception as e:
        print(f"❌ 写入/读取测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有测试"""
    print("🧪 Supabase 功能测试")
    print(f"⏰ 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = []

    # 测试 1: 基础连接
    results.append(("基础连接", await test_1_basic_connection()))

    # 测试 2: 表结构
    if results[-1][1]:  # 仅当连接成功时继续
        results.append(("表结构验证", await test_2_table_structure()))

    # 测试 3: 向量搜索
    if results[-1][1]:  # 仅当表结构验证成功时继续
        results.append(("向量搜索", await test_3_embedding_search()))

    # 测试 4: 写入读取
    if results[-1][1]:  # 仅当向量搜索成功时继续
        results.append(("写入读取", await test_4_write_read()))

    # 汇总结果
    print("\n" + "="*60)
    print("测试汇总")
    print("="*60)

    for test_name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{status} - {test_name}")

    total_passed = sum(1 for _, passed in results if passed)
    print(f"\n总计: {total_passed}/{len(results)} 项测试通过")

    if total_passed == len(results):
        print("🎉 所有测试通过！Supabase 配置正确。")
        return 0
    else:
        print("⚠️  部分测试失败，请检查配置和网络连接。")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
