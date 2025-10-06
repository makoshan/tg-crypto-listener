#!/usr/bin/env python3
"""快速验证 Supabase 配置是否正确"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.db.supabase_client import get_supabase_client

async def main():
    print("🔍 验证 Supabase 配置...\n")

    if not Config.SUPABASE_URL or not Config.SUPABASE_SERVICE_KEY:
        print("❌ SUPABASE_URL 或 SUPABASE_SERVICE_KEY 未配置")
        return 1

    try:
        client = get_supabase_client(
            Config.SUPABASE_URL,
            Config.SUPABASE_SERVICE_KEY
        )
        print(f"✅ Supabase 连接成功: {Config.SUPABASE_URL}\n")

        # 测试 news_events 表
        print("📋 检查 news_events 表...")
        result = await client._request("GET", "news_events", params={"select": "id", "limit": "1"})
        count = len(result) if isinstance(result, list) else 0
        print(f"   ✅ 表存在，当前有 {count} 条记录\n")

        # 测试 ai_signals 表
        print("📋 检查 ai_signals 表...")
        result = await client._request("GET", "ai_signals", params={"select": "id", "limit": "1"})
        count = len(result) if isinstance(result, list) else 0
        print(f"   ✅ 表存在，当前有 {count} 条记录\n")

        # 测试 RPC 函数
        print("🔧 检查 search_memory_events 函数...")
        try:
            result = await client.rpc("search_memory_events", {
                "query_embedding": [0.0] * 1536,
                "match_threshold": 0.5,
                "match_count": 1
            })
            print(f"   ✅ 函数存在且可调用\n")
        except Exception as e:
            print(f"   ❌ 函数测试失败: {e}")
            print(f"   ⚠️  请在 Supabase SQL Editor 中执行:")
            print(f"      docs/supabase_embedding_function.sql\n")
            return 1

        print("=" * 50)
        print("🎉 所有检查通过！Supabase 配置正确。")
        print("=" * 50)
        return 0

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        print(f"\n💡 请检查:")
        print(f"   1. Supabase URL 和 KEY 是否正确")
        print(f"   2. 是否已在 SQL Editor 中执行:")
        print(f"      - docs/supabase_migration.sql")
        print(f"      - docs/supabase_embedding_function.sql")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
