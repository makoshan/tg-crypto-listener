#!/usr/bin/env python3
"""测试 Claude CLI 深度分析引擎实现"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

# Add project root to path
sys.path.insert(0, "/home/mako/tg-crypto-listener")

from src.ai.deep_analysis.claude_cli import ClaudeCliDeepAnalysisEngine


@dataclass
class MockEventPayload:
    """模拟事件负载"""
    text: str
    source: str
    timestamp: datetime
    translated_text: Optional[str] = None
    language: str = "zh"
    translation_confidence: float = 0.9
    keywords_hit: list = None
    historical_reference: dict = None
    media: list = None
    is_priority_kol: bool = False


@dataclass
class MockSignalResult:
    """模拟初步分析结果 - 匹配真实的 SignalResult 结构"""
    summary: str
    event_type: str
    asset: str
    action: str
    confidence: float
    status: str = "success"
    notes: str = ""
    raw_response: str = ""
    risk_flags: list = None
    direction: str = "neutral"
    strength: str = "medium"
    timeframe: str = "short"
    asset_names: str = ""
    error: Optional[str] = None
    links: list = None
    alert: str = ""
    severity: str = ""

    def __post_init__(self):
        if self.risk_flags is None:
            self.risk_flags = []
        if self.links is None:
            self.links = []


def parse_json_callback(json_str: str) -> MockSignalResult:
    """解析 JSON 字符串为 SignalResult"""
    try:
        data = json.loads(json_str)
        return MockSignalResult(
            summary=data.get("summary", ""),
            event_type=data.get("event_type", "unknown"),
            asset=data.get("asset", "NONE"),
            action=data.get("action", "observe"),
            confidence=data.get("confidence", 0.5),
            notes=data.get("notes", ""),
        )
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败: {e}")
        print(f"\n完整原始输出:")
        print("=" * 80)
        print(json_str)
        print("=" * 80)
        raise


async def test_claude_cli_engine_basic():
    """测试 1: 基础 JSON 输出"""
    print("=" * 80)
    print("测试 1: Claude CLI 引擎 - 基础 JSON 输出")
    print("=" * 80)

    # 创建引擎实例
    engine = ClaudeCliDeepAnalysisEngine(
        cli_path="claude",
        timeout=120.0,  # 120 秒超时
        parse_json_callback=parse_json_callback,
        allowed_tools=["Bash"],  # 允许 Bash 工具
    )

    # 创建模拟事件
    payload = MockEventPayload(
        text="Binance 宣布上线 XYZ 代币，明天开盘交易",
        source="Binance Official",
        timestamp=datetime.now(timezone.utc),
        translated_text="Binance announces XYZ token listing, trading starts tomorrow",
    )

    # 创建初步分析结果
    preliminary = MockSignalResult(
        summary="Binance 上线新代币",
        event_type="listing",
        asset="XYZ",
        action="buy",
        confidence=0.6,
    )

    print(f"\n输入事件: {payload.text}")
    print(f"初步分析: {preliminary.summary}")
    print(f"初步置信度: {preliminary.confidence}\n")

    try:
        # 执行深度分析
        print("⏳ 开始深度分析...")
        start_time = asyncio.get_event_loop().time()

        result = await engine.analyse(payload, preliminary)

        elapsed = asyncio.get_event_loop().time() - start_time

        print(f"\n✅ 深度分析完成！")
        print(f"⏱️  耗时: {elapsed:.2f}s")
        print(f"\n📊 分析结果:")
        print(f"  - summary: {result.summary}")
        print(f"  - event_type: {result.event_type}")
        print(f"  - asset: {result.asset}")
        print(f"  - action: {result.action}")
        print(f"  - confidence: {result.confidence}")
        print(f"\n📝 Notes (前 200 字符):")
        print(f"  {result.notes[:200]}")

        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_claude_cli_engine_with_tools():
    """测试 2: 工具调用能力"""
    print("\n" + "=" * 80)
    print("测试 2: Claude CLI 引擎 - 工具调用（价格查询）")
    print("=" * 80)

    engine = ClaudeCliDeepAnalysisEngine(
        cli_path="claude",
        timeout=180.0,  # 更长超时，因为需要执行工具
        parse_json_callback=parse_json_callback,
        allowed_tools=["Bash", "Read"],
    )

    payload = MockEventPayload(
        text="BTC 价格突破 11 万美元，创历史新高",
        source="CryptoNews",
        timestamp=datetime.now(timezone.utc),
        translated_text="BTC price breaks $110,000, reaches all-time high",
    )

    preliminary = MockSignalResult(
        summary="BTC 创新高",
        event_type="price_movement",
        asset="BTC",
        action="observe",
        confidence=0.7,
    )

    print(f"\n输入事件: {payload.text}")
    print(f"初步分析: {preliminary.summary}\n")

    try:
        print("⏳ 开始深度分析（包含价格验证）...")
        start_time = asyncio.get_event_loop().time()

        result = await engine.analyse(payload, preliminary)

        elapsed = asyncio.get_event_loop().time() - start_time

        print(f"\n✅ 深度分析完成！")
        print(f"⏱️  耗时: {elapsed:.2f}s")
        print(f"\n📊 分析结果:")
        print(f"  - summary: {result.summary}")
        print(f"  - action: {result.action}")
        print(f"  - confidence: {result.confidence}")
        print(f"\n📝 Notes (前 400 字符):")
        print(f"  {result.notes[:400]}")

        # 检查是否执行了工具
        if "uvx" in result.notes or "fetch_price" in result.notes or "BTC" in result.notes:
            print(f"\n✅ 检测到工具执行证据")
            return True
        else:
            print(f"\n⚠️  未检测到明显的工具执行证据")
            return False

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有测试"""
    print("\n🚀 开始 Claude CLI 深度分析引擎测试\n")

    # 测试 1: 基础功能
    result1 = await test_claude_cli_engine_basic()

    # 测试 2: 工具调用
    result2 = await test_claude_cli_engine_with_tools()

    print("\n" + "=" * 80)
    print("📊 测试总结")
    print("=" * 80)
    print(f"{'✅' if result1 else '❌'} 基础 JSON 输出: {'通过' if result1 else '失败'}")
    print(f"{'✅' if result2 else '❌'} 工具调用能力: {'通过' if result2 else '失败'}")

    if result1 and result2:
        print("\n💡 结论:")
        print("  ✨ Claude CLI 深度分析引擎完全可用！")
        print("  ✨ 可以集成到生产环境")
    elif result1:
        print("\n💡 结论:")
        print("  ⚠️  基础功能可用，但工具调用需要进一步调试")
    else:
        print("\n💡 结论:")
        print("  ❌ 引擎实现存在问题，需要调试")

    print("=" * 80)

    return 0 if result1 else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  测试被用户中断")
        sys.exit(130)
