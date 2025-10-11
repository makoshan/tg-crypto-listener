#!/usr/bin/env python3
"""
Tavily API 测试脚本
测试真实 API 调用并验证返回数据格式

使用方法:
    export TAVILY_API_KEY=your_api_key
    python scripts/test_tavily_api.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import httpx


async def test_tavily_search(
    api_key: str,
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_domains: list[str] | None = None,
):
    """测试 Tavily 搜索 API"""

    endpoint = "https://api.tavily.com/search"

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": False,
    }

    if include_domains:
        payload["include_domains"] = include_domains

    print(f"\n{'='*80}")
    print(f"🔍 测试查询: {query}")
    print(f"{'='*80}")
    print(f"\n📤 请求参数:")
    print(json.dumps({k: v for k, v in payload.items() if k != "api_key"}, indent=2, ensure_ascii=False))

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"\n⏳ 发送请求到 {endpoint}...")
            start_time = datetime.now(timezone.utc)

            response = await client.post(endpoint, json=payload)

            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

            print(f"\n✅ 响应状态: {response.status_code}")
            print(f"⏱️  耗时: {elapsed:.2f}s")

            if response.status_code == 200:
                data = response.json()
                print(f"\n📥 完整响应结构:")
                print(json.dumps(data, indent=2, ensure_ascii=False))

                # 分析响应结构
                print(f"\n{'='*80}")
                print("📊 响应数据分析:")
                print(f"{'='*80}")

                print(f"\n🔑 顶层字段:")
                for key in data.keys():
                    print(f"  - {key}: {type(data[key]).__name__}")

                results = data.get("results", [])
                print(f"\n📝 搜索结果数量: {len(results)}")

                if results:
                    print(f"\n🔍 第一条结果字段:")
                    for key, value in results[0].items():
                        value_preview = str(value)[:100] if isinstance(value, str) else value
                        print(f"  - {key}: {type(value).__name__} = {value_preview}")

                    # 统计域名分布
                    from urllib.parse import urlparse
                    domains = [urlparse(r.get("url", "")).netloc for r in results]
                    print(f"\n🌐 来源域名分布:")
                    for domain in set(domains):
                        count = domains.count(domain)
                        print(f"  - {domain}: {count} 条")

                    # 检查是否有官方关键词
                    official_keywords = [
                        "官方", "声明", "公告", "official", "statement",
                        "announcement", "confirmed", "press release",
                    ]

                    official_count = 0
                    for result in results:
                        title = result.get("title", "").lower()
                        content = result.get("content", "").lower()
                        if any(kw in title or kw in content for kw in official_keywords):
                            official_count += 1

                    print(f"\n📢 包含官方关键词的结果: {official_count}/{len(results)}")

                    # 评分分析
                    scores = [r.get("score", 0.0) for r in results]
                    avg_score = sum(scores) / len(scores) if scores else 0.0
                    print(f"\n⭐ 结果评分:")
                    print(f"  - 平均分: {avg_score:.2f}")
                    print(f"  - 最高分: {max(scores):.2f}")
                    print(f"  - 最低分: {min(scores):.2f}")

                return data

            elif response.status_code == 429:
                print(f"\n❌ 速率限制: API 超出配额")
                print(f"响应: {response.text}")
                return None

            elif response.status_code == 401:
                print(f"\n❌ 认证失败: API Key 无效")
                print(f"响应: {response.text}")
                return None

            else:
                print(f"\n❌ 请求失败: {response.status_code}")
                print(f"响应: {response.text}")
                return None

    except httpx.TimeoutException:
        print(f"\n❌ 请求超时")
        return None

    except Exception as exc:
        print(f"\n❌ 异常: {exc}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """主测试函数"""

    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        print("❌ 错误: 未设置 TAVILY_API_KEY 环境变量")
        print("\n使用方法:")
        print("  export TAVILY_API_KEY=your_api_key")
        print("  python scripts/test_tavily_api.py")
        sys.exit(1)

    print(f"\n🔑 API Key: {api_key[:10]}...{api_key[-4:]}")

    # 测试用例
    test_cases = [
        {
            "query": "USDC Circle depeg official statement 脱锚 官方声明",
            "max_results": 5,
            "include_domains": ["coindesk.com", "theblock.co", "cointelegraph.com"],
        },
        {
            "query": "Bitcoin spot ETF SEC approval 比特币 现货 批准",
            "max_results": 5,
            "include_domains": ["coindesk.com", "theblock.co"],
        },
        {
            "query": "Binance hack exploit $50M 黑客攻击",
            "max_results": 3,
            "include_domains": None,  # 测试不限制域名
        },
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n\n{'#'*80}")
        print(f"# 测试用例 {i}/{len(test_cases)}")
        print(f"{'#'*80}")

        await test_tavily_search(
            api_key=api_key,
            query=test_case["query"],
            max_results=test_case["max_results"],
            include_domains=test_case.get("include_domains"),
        )

        if i < len(test_cases):
            print(f"\n⏸️  等待 2 秒后执行下一个测试...")
            await asyncio.sleep(2)

    print(f"\n\n{'='*80}")
    print("✅ 所有测试完成")
    print(f"{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
