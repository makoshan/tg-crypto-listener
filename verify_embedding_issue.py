#!/usr/bin/env python3
"""
验证 Supabase Embedding 存储格式问题

这个脚本会：
1. 检查数据库中 embedding 的实际存储格式
2. 测试向量查询是否能工作
3. 验证新旧数据格式的差异
"""

import asyncio
import json
from src.db.supabase_client import get_supabase_client
from src.config import Config

async def main():
    print("="*80)
    print("Supabase Embedding 格式验证工具")
    print("="*80)

    # 初始化客户端
    client = get_supabase_client(
        url=Config.SUPABASE_URL,
        service_key=Config.SUPABASE_SERVICE_KEY
    )

    print("\n[步骤 1] 检查数据库中的 embedding 类型")
    print("-"*80)

    # 使用 SQL 查询检查 embedding 列的实际类型
    sql_query = """
    SELECT
        id,
        created_at,
        pg_typeof(embedding) as embedding_type,
        CASE
            WHEN embedding IS NULL THEN 'NULL'
            WHEN pg_typeof(embedding)::text = 'vector' THEN 'vector'
            WHEN pg_typeof(embedding)::text = 'text' THEN 'text'
            ELSE pg_typeof(embedding)::text
        END as type_name,
        CASE
            WHEN embedding IS NOT NULL AND pg_typeof(embedding)::text = 'vector'
            THEN vector_dims(embedding)
            ELSE NULL
        END as vector_dimensions
    FROM news_events
    WHERE embedding IS NOT NULL
    ORDER BY id DESC
    LIMIT 5
    """

    try:
        # 注意：这需要直接 SQL 访问，Supabase REST API 可能不支持
        # 我们改用更简单的方式
        print("正在查询最新的 5 条记录...")

        # 方法 1：直接查询并检查返回的数据格式
        response = await client._request(
            "GET",
            "news_events",
            params={
                "select": "id,created_at,embedding",
                "order": "id.desc",
                "limit": "5",
                "embedding": "not.is.null"
            }
        )

        if not response:
            print("❌ 没有找到包含 embedding 的记录")
            return

        print(f"✅ 找到 {len(response)} 条记录\n")

        for idx, row in enumerate(response, 1):
            event_id = row.get('id')
            created_at = row.get('created_at', '')[:19]  # 截取到秒
            embedding = row.get('embedding')

            print(f"记录 #{idx} (ID: {event_id}, 时间: {created_at})")
            print(f"  类型: {type(embedding).__name__}")

            if isinstance(embedding, str):
                print(f"  ❌ 格式错误：存储为字符串")
                print(f"  字符串长度: {len(embedding)}")
                print(f"  前 100 字符: {embedding[:100]}")

                # 尝试解析为列表
                try:
                    parsed = json.loads(embedding)
                    if isinstance(parsed, list):
                        print(f"  解析后维度: {len(parsed)}")
                        print(f"  前 5 个值: {parsed[:5]}")
                except:
                    print(f"  无法解析为 JSON 列表")

            elif isinstance(embedding, list):
                print(f"  ✅ 格式正确：存储为 vector 类型（返回为列表）")
                print(f"  维度: {len(embedding)}")
                print(f"  前 5 个值: {embedding[:5]}")
            else:
                print(f"  ⚠️  未知格式: {type(embedding)}")

            print()

    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return

    print("\n[步骤 2] 测试向量查询功能")
    print("-"*80)

    # 创建一个测试 embedding
    test_embedding = [0.0] * 1536
    test_embedding[0] = 0.1
    test_embedding[1] = 0.2

    try:
        print("正在调用 search_memory_events RPC...")
        result = await client.rpc("search_memory_events", {
            "query_embedding": test_embedding,
            "match_threshold": 0.1,
            "match_count": 3,
            "min_confidence": 0.0,
            "time_window_hours": 168
        })

        print(f"✅ RPC 调用成功")
        print(f"返回结果数量: {len(result) if isinstance(result, list) else 'N/A'}")

        if not result or len(result) == 0:
            print("\n⚠️  警告：RPC 返回 0 条结果")
            print("   可能原因：")
            print("   1. embedding 存储格式不正确（字符串而非 vector）")
            print("   2. 数据库中没有满足条件的记录")
            print("   3. 向量索引未创建")
        else:
            print("\n✅ 成功检索到记忆！")
            for i, mem in enumerate(result[:3], 1):
                print(f"  [{i}] similarity={mem.get('similarity', 0):.3f}, "
                      f"confidence={mem.get('confidence', 0):.2f}, "
                      f"assets={mem.get('assets', [])}")

    except Exception as e:
        print(f"❌ RPC 调用失败: {e}")
        print("   这通常说明向量查询功能无法正常工作")

    print("\n[步骤 3] 诊断和建议")
    print("-"*80)

    # 统计字符串格式的记录数量
    try:
        # 这里我们无法直接用 SQL 统计，只能通过已查询的样本推断
        sample_records = response
        string_format_count = sum(1 for r in sample_records if isinstance(r.get('embedding'), str))
        total_sample = len(sample_records)

        if string_format_count > 0:
            print(f"⚠️  检测到问题：")
            print(f"   样本中 {string_format_count}/{total_sample} 条记录的 embedding 是字符串格式")
            print()
            print("📋 建议的修复步骤：")
            print()
            print("1. 【立即修复】代码已更新，新写入的数据将使用正确格式")
            print("   文件：src/db/repositories.py")
            print()
            print("2. 【迁移旧数据】需要在 Supabase SQL Editor 中执行以下 SQL：")
            print()
            print("   方案 A - 如果字段定义是 vector(1536)：")
            print("   ```sql")
            print("   UPDATE news_events ")
            print("   SET embedding = embedding::text::vector(1536)")
            print("   WHERE embedding IS NOT NULL")
            print("   AND pg_typeof(embedding)::text != 'vector';")
            print("   ```")
            print()
            print("   方案 B - 如果字段定义是 text：")
            print("   ```sql")
            print("   -- 先修改列类型")
            print("   ALTER TABLE news_events ")
            print("   ALTER COLUMN embedding TYPE vector(1536) ")
            print("   USING embedding::text::vector(1536);")
            print("   ```")
            print()
            print("3. 【创建索引】数据修复后，创建向量索引以提升性能：")
            print("   ```sql")
            print("   CREATE INDEX IF NOT EXISTS idx_news_events_embedding")
            print("   ON news_events USING ivfflat(embedding vector_cosine_ops)")
            print("   WITH (lists = 100);")
            print("   ```")
            print()
            print("4. 【验证修复】重新运行此脚本验证问题是否解决")
        else:
            print("✅ 所有样本记录的 embedding 格式正确！")
            if len(result) == 0:
                print("   但是向量查询返回 0 结果，可能需要：")
                print("   - 检查相似度阈值设置")
                print("   - 确认数据库中有足够的记录")
                print("   - 创建向量索引以提升性能")

    except Exception as e:
        print(f"⚠️  诊断过程出错: {e}")

    print("\n" + "="*80)
    print("验证完成")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(main())
