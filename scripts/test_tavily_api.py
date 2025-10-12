#!/usr/bin/env python3
"""
Tavily 搜索 API 集成测试

既可作为 pytest 集成用例运行，也可直接执行脚本手动验证。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import httpx
import pytest

pytestmark = pytest.mark.integration

DEFAULT_TEST_CASES: List[Dict[str, Any]] = [
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
        "include_domains": None,
    },
]


@pytest.fixture(scope="session")
def tavily_api_key() -> str:
    """从环境变量读取 Tavily API Key，未配置时跳过测试。"""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        pytest.skip("TAVILY_API_KEY 未配置，跳过 Tavily 集成测试")
    return key


async def _call_tavily_search(
    *,
    api_key: str,
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_domains: Optional[Iterable[str]] = None,
    verbose: bool = False,
) -> Optional[Dict[str, Any]]:
    """调用 Tavily API，返回响应 JSON。"""
    endpoint = "https://api.tavily.com/search"
    payload: Dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": False,
    }
    if include_domains:
        payload["include_domains"] = list(include_domains)

    if verbose:
        print(f"\n{'=' * 80}")
        print(f"🔍 测试查询: {query}")
        print(f"{'=' * 80}")
        print("\n📤 请求参数:")
        print(json.dumps({k: v for k, v in payload.items() if k != 'api_key'}, indent=2, ensure_ascii=False))

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if verbose:
                print(f"\n⏳ 发送请求到 {endpoint}...")
            start_time = datetime.now(timezone.utc)
            response = await client.post(endpoint, json=payload)
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

            if verbose:
                print(f"\n✅ 响应状态: {response.status_code}")
                print(f"⏱️  耗时: {elapsed:.2f}s")

            if response.status_code == 200:
                data = response.json()
                if verbose:
                    _render_response_debug(data)
                return data

            if response.status_code == 429:
                pytest.skip("Tavily API 速率受限 (429)")
            if response.status_code == 401:
                pytest.fail("Tavily API Key 无效 (401)")

            pytest.fail(f"Tavily 请求失败: {response.status_code} -> {response.text}")
    except httpx.TimeoutException as exc:
        pytest.skip(f"Tavily 请求超时: {exc}")
    except Exception as exc:  # pragma: no cover - 防御性处理
        if verbose:
            print(f"\n❌ 异常: {exc}")
        pytest.fail(f"Tavily API 调用异常: {exc}")
    return None


def _render_response_debug(data: Dict[str, Any]) -> None:
    """以友好格式输出 Tavily 响应，方便手动调试。"""
    print("\n📥 完整响应结构:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"\n{'=' * 80}")
    print("📊 响应数据分析:")
    print(f"{'=' * 80}")

    print("\n🔑 顶层字段:")
    for key, value in data.items():
        print(f"  - {key}: {type(value).__name__}")

    results = data.get("results", [])
    print(f"\n📝 搜索结果数量: {len(results)}")

    if not results:
        return

    first = results[0]
    print("\n🔍 第一条结果字段:")
    for key, value in first.items():
        preview = str(value)[:100] if isinstance(value, str) else value
        print(f"  - {key}: {type(value).__name__} = {preview}")

    from urllib.parse import urlparse

    domains = [urlparse(r.get("url", "")).netloc for r in results]
    print("\n🌐 来源域名分布:")
    for domain in sorted(set(domains)):
        print(f"  - {domain}: {domains.count(domain)} 条")

    official_keywords = {
        "官方",
        "声明",
        "公告",
        "official",
        "statement",
        "announcement",
        "confirmed",
        "press release",
    }
    official_count = 0
    for result in results:
        content = (result.get("title", "") + " " + result.get("content", "")).lower()
        if any(keyword in content for keyword in official_keywords):
            official_count += 1
    print(f"\n📢 包含官方关键词的结果: {official_count}/{len(results)}")

    scores = [r.get("score", 0.0) for r in results if isinstance(r.get("score"), (int, float))]
    if scores:
        print("\n⭐ 结果评分:")
        print(f"  - 平均分: {sum(scores) / len(scores):.2f}")
        print(f"  - 最高分: {max(scores):.2f}")
        print(f"  - 最低分: {min(scores):.2f}")


@pytest.mark.asyncio
async def test_tavily_search_integration(tavily_api_key: str) -> None:
    """遍历默认测试用例，验证 Tavily API 响应结构。"""
    for case in DEFAULT_TEST_CASES:
        data = await _call_tavily_search(api_key=tavily_api_key, verbose=False, **case)
        assert data is not None, "Tavily 返回空响应"
        assert "results" in data, "响应缺少 results 字段"


async def _cli_main() -> int:
    """脚本入口，逐条运行默认测试用例。"""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("❌ 错误: 未设置 TAVILY_API_KEY 环境变量")
        print("\n使用方法:")
        print("  export TAVILY_API_KEY=your_api_key")
        print("  python scripts/test_tavily_api.py")
        return 1

    print(f"\n🔑 API Key: {api_key[:10]}...{api_key[-4:]}")

    for idx, case in enumerate(DEFAULT_TEST_CASES, 1):
        print(f"\n{'#' * 80}")
        print(f"# 测试用例 {idx}/{len(DEFAULT_TEST_CASES)}")
        print(f"{'#' * 80}")
        await _call_tavily_search(api_key=api_key, verbose=True, **case)
        if idx < len(DEFAULT_TEST_CASES):
            print("\n⏸️  等待 2 秒后执行下一个测试...")
            await asyncio.sleep(2)

    print(f"\n{'=' * 80}")
    print("✅ Tavily 集成测试全部完成")
    print(f"{'=' * 80}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli_main()))
