#!/usr/bin/env python3
"""诊断 Supabase Memory 为什么返回空结果"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.db.supabase_client import get_supabase_client

async def main():
    print("🔍 诊断 Supabase Memory 检索问题\n")
    print("=" * 60)

    # 1. 检查配置
    print("\n📋 当前配置:")
    print(f"   MEMORY_ENABLED: {Config.MEMORY_ENABLED}")
    print(f"   MEMORY_BACKEND: {Config.MEMORY_BACKEND}")
    print(f"   MEMORY_MAX_NOTES: {Config.MEMORY_MAX_NOTES}")
    print(f"   MEMORY_LOOKBACK_HOURS: {Config.MEMORY_LOOKBACK_HOURS}")
    print(f"   MEMORY_MIN_CONFIDENCE: {Config.MEMORY_MIN_CONFIDENCE}")
    print(f"   MEMORY_SIMILARITY_THRESHOLD: {Config.MEMORY_SIMILARITY_THRESHOLD}")

    try:
        client = get_supabase_client(
            Config.SUPABASE_URL,
            Config.SUPABASE_SERVICE_KEY
        )

        # 2. 检查数据库中的记录数量和时间范围
        print("\n" + "=" * 60)
        print("📊 数据库统计:\n")

        # 检查 news_events
        news = await client._request("GET", "news_events", params={
            "select": "id,created_at,embedding",
            "order": "created_at.desc",
            "limit": "10"
        })

        total_news = len(news) if isinstance(news, list) else 0
        print(f"   news_events 表: {total_news} 条记录")

        if total_news > 0:
            # 检查 embedding
            has_embedding = sum(1 for n in news if n.get('embedding'))
            print(f"   └─ 有 embedding: {has_embedding}/{total_news} 条")

            # 时间范围
            oldest = news[-1]['created_at'] if news else None
            newest = news[0]['created_at'] if news else None
            print(f"   └─ 时间范围: {oldest} 到 {newest}")

            # 检查是否在时间窗口内
            if oldest:
                oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
                cutoff = datetime.now(timezone.utc) - timedelta(hours=Config.MEMORY_LOOKBACK_HOURS)
                in_window = oldest_dt >= cutoff
                print(f"   └─ 是否在 {Config.MEMORY_LOOKBACK_HOURS}h 时间窗口内: {'✅ 是' if in_window else '❌ 否'}")
                if not in_window:
                    hours_ago = (datetime.now(timezone.utc) - oldest_dt).total_seconds() / 3600
                    print(f"      ⚠️  最早记录是 {hours_ago:.1f} 小时前，超出时间窗口")

        # 检查 ai_signals
        signals = await client._request("GET", "ai_signals", params={
            "select": "id,confidence,action,assets",
            "order": "created_at.desc",
            "limit": "10"
        })

        total_signals = len(signals) if isinstance(signals, list) else 0
        print(f"\n   ai_signals 表: {total_signals} 条记录")

        if total_signals > 0:
            # 置信度统计
            confidences = [s.get('confidence', 0) for s in signals]
            avg_conf = sum(confidences) / len(confidences) if confidences else 0
            high_conf = sum(1 for c in confidences if c >= Config.MEMORY_MIN_CONFIDENCE)

            print(f"   └─ 平均置信度: {avg_conf:.2f}")
            print(f"   └─ 满足最小置信度 ({Config.MEMORY_MIN_CONFIDENCE}): {high_conf}/{total_signals} 条")

            if high_conf == 0:
                print(f"      ⚠️  没有记录满足最小置信度要求！")
                print(f"      💡 建议降低 MEMORY_MIN_CONFIDENCE")

            # 资产统计
            assets_list = [s.get('assets', '') for s in signals]
            print(f"   └─ 资产: {', '.join(set(assets_list[:5]))}")

        # 3. 测试向量搜索
        print("\n" + "=" * 60)
        print("🔍 测试向量搜索:\n")

        if not Config.OPENAI_API_KEY:
            print("   ⚠️  OPENAI_API_KEY 未配置，无法测试向量搜索")
        else:
            try:
                from openai import AsyncOpenAI

                openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

                # 生成测试 embedding
                test_query = "Bitcoin price analysis"
                response = await openai_client.embeddings.create(
                    input=test_query,
                    model=Config.OPENAI_EMBEDDING_MODEL
                )
                embedding = response.data[0].embedding

                print(f"   测试查询: '{test_query}'")
                print(f"   Embedding 维度: {len(embedding)}")

                # 尝试不同的阈值
                thresholds = [0.5, 0.7, 0.85, 0.9]

                for threshold in thresholds:
                    result = await client.rpc("search_memory_events", {
                        "query_embedding": embedding,
                        "match_threshold": threshold,
                        "match_count": 5,
                        "min_confidence": Config.MEMORY_MIN_CONFIDENCE,
                        "time_window_hours": Config.MEMORY_LOOKBACK_HOURS
                    })

                    count = len(result) if isinstance(result, list) else 0
                    status = "✅" if count > 0 else "❌"
                    print(f"   {status} 阈值 {threshold}: 找到 {count} 条记录")

                    if count > 0 and isinstance(result, list):
                        for i, item in enumerate(result[:2], 1):
                            sim = item.get('similarity', 0)
                            conf = item.get('confidence', 0)
                            print(f"      [{i}] 相似度={sim:.3f}, 置信度={conf:.2f}")

            except ImportError:
                print("   ⚠️  openai 模块未安装")
            except Exception as e:
                print(f"   ❌ 向量搜索测试失败: {e}")

        # 4. 给出建议
        print("\n" + "=" * 60)
        print("💡 优化建议:\n")

        suggestions = []

        if total_news == 0 or total_signals == 0:
            suggestions.append("❗ 数据库为空，需要运行监听器积累数据")

        if total_news > 0 and has_embedding == 0:
            suggestions.append("❗ 所有记录都没有 embedding，检查 embedding 生成是否正常")

        if total_signals > 0 and high_conf == 0:
            suggestions.append(f"📉 降低 MEMORY_MIN_CONFIDENCE（当前 {Config.MEMORY_MIN_CONFIDENCE}）")
            suggestions.append(f"   建议值: 0.3 - 0.5")

        if total_news > 0 and oldest and not in_window:
            suggestions.append(f"⏰ 增加 MEMORY_LOOKBACK_HOURS（当前 {Config.MEMORY_LOOKBACK_HOURS}h）")
            suggestions.append(f"   建议值: 168 (7天) 或更大")

        if Config.MEMORY_SIMILARITY_THRESHOLD > 0.8:
            suggestions.append(f"📊 降低 MEMORY_SIMILARITY_THRESHOLD（当前 {Config.MEMORY_SIMILARITY_THRESHOLD}）")
            suggestions.append(f"   建议值: 0.6 - 0.75")

        if not suggestions:
            suggestions.append("✅ 配置看起来合理，可能需要更多数据积累")

        for suggestion in suggestions:
            print(f"   {suggestion}")

        print("\n" + "=" * 60)
        return 0

    except Exception as e:
        print(f"\n❌ 诊断失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
