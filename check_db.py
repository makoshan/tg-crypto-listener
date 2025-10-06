#!/usr/bin/env python3
"""快速检查 Supabase 数据库内容"""

import asyncio
import os
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://woxbgotwkbbtiaerzrqu.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndveGJnb3R3a2JidGlhZXJ6cnF1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkzNTU3MzMsImV4cCI6MjA3NDkzMTczM30.oS0b-N1l7midTEZ1qlD8qovPB_IkeJM5cYele7AZ10M")

async def check_database():
    """检查数据库表内容"""

    print("🔍 连接到 Supabase...")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # 检查 news_events 表
    print("\n" + "="*60)
    print("📊 检查 news_events 表")
    print("="*60)

    try:
        result = client.table("news_events").select("id,created_at,source,embedding").limit(5).execute()
        print(f"✅ 找到 {len(result.data)} 条记录")
        for row in result.data:
            has_embedding = row.get('embedding') is not None
            print(f"  - ID: {row['id']}, 来源: {row.get('source')}, Embedding: {'✅' if has_embedding else '❌'}")

        # 统计总数
        count_result = client.table("news_events").select("id", count="exact").execute()
        print(f"\n📈 news_events 总记录数: {count_result.count}")

        # 统计有 embedding 的记录
        embedding_result = client.table("news_events").select("id", count="exact").not_.is_("embedding", "null").execute()
        print(f"📈 有 embedding 的记录数: {embedding_result.count}")

    except Exception as e:
        print(f"❌ 查询 news_events 失败: {e}")

    # 检查 ai_signals 表
    print("\n" + "="*60)
    print("📊 检查 ai_signals 表")
    print("="*60)

    try:
        result = client.table("ai_signals").select("id,created_at,action,confidence,assets").limit(5).execute()
        print(f"✅ 找到 {len(result.data)} 条记录")
        for row in result.data:
            print(f"  - ID: {row['id']}, Action: {row.get('action')}, Confidence: {row.get('confidence')}, Assets: {row.get('assets')}")

        # 统计总数
        count_result = client.table("ai_signals").select("id", count="exact").execute()
        print(f"\n📈 ai_signals 总记录数: {count_result.count}")

    except Exception as e:
        print(f"❌ 查询 ai_signals 失败: {e}")

    # 检查 RPC 函数是否存在
    print("\n" + "="*60)
    print("🔧 检查 RPC 函数")
    print("="*60)

    try:
        # 使用一个假的 embedding 测试
        fake_embedding = [0.0] * 1536
        result = client.rpc("search_memory_events", {
            "query_embedding": fake_embedding,
            "match_threshold": 0.3,
            "match_count": 3,
            "min_confidence": 0.5,
            "time_window_hours": 168
        }).execute()

        print(f"✅ RPC 函数 search_memory_events 存在")
        print(f"   返回 {len(result.data)} 条结果")

    except Exception as e:
        print(f"❌ RPC 函数测试失败: {e}")
        print(f"   可能原因: 1) 函数不存在, 2) 参数错误, 3) 权限问题")

if __name__ == "__main__":
    asyncio.run(check_database())
