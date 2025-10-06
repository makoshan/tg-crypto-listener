#!/usr/bin/env python3
"""测试实际的记忆检索效果"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.db.supabase_client import get_supabase_client
from src.memory import SupabaseMemoryRepository, MemoryRepositoryConfig

async def main():
    print("🧪 测试实际记忆检索（使用新配置）\n")

    if not Config.OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY 未配置")
        return 1

    try:
        from openai import AsyncOpenAI

        # 初始化
        openai_client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
        supabase_client = get_supabase_client(
            Config.SUPABASE_URL,
            Config.SUPABASE_SERVICE_KEY
        )

        memory_config = MemoryRepositoryConfig(
            max_notes=Config.MEMORY_MAX_NOTES,
            similarity_threshold=Config.MEMORY_SIMILARITY_THRESHOLD,
            lookback_hours=Config.MEMORY_LOOKBACK_HOURS,
            min_confidence=Config.MEMORY_MIN_CONFIDENCE
        )

        repo = SupabaseMemoryRepository(supabase_client, memory_config)

        # 测试查询
        test_queries = [
            "Bitcoin price analysis",
            "比特币价格分析",
            "Ethereum listing on exchange",
            "PEPE meme coin"
        ]

        print(f"📋 配置:")
        print(f"   相似度阈值: {memory_config.similarity_threshold}")
        print(f"   最小置信度: {memory_config.min_confidence}")
        print(f"   时间窗口: {memory_config.lookback_hours}h")
        print(f"   最大返回: {memory_config.max_notes} 条\n")

        for query in test_queries:
            print(f"🔍 查询: '{query}'")

            # 生成 embedding
            response = await openai_client.embeddings.create(
                input=query,
                model=Config.OPENAI_EMBEDDING_MODEL
            )
            embedding = response.data[0].embedding

            # 检索记忆
            context = await repo.fetch_memories(
                embedding=embedding,
                asset_codes=None
            )

            if context.entries:
                print(f"   ✅ 找到 {len(context.entries)} 条记忆:")
                for i, entry in enumerate(context.entries, 1):
                    print(f"      [{i}] {entry.assets} | {entry.action}")
                    print(f"          相似度={entry.similarity:.3f} | 置信度={entry.confidence:.2f}")
                    print(f"          {entry.summary[:60]}...")
            else:
                print(f"   ❌ 未找到相关记忆")

            print()

        return 0

    except ImportError:
        print("❌ openai 模块未安装")
        return 1
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
