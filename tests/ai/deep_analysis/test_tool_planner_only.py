#!/usr/bin/env python3
"""
快速测试: Tool Planner 决策验证 (不执行实际工具调用)

验证 AI 是否能正确决策应该调用哪些工具
"""

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    stream=sys.stdout
)
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest

from src.ai.deep_analysis.nodes.tool_planner import ToolPlannerNode

logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("src.memory").setLevel(logging.WARNING)
logging.getLogger("src.ai.gemini_function_client").setLevel(logging.WARNING)

SCENARIOS = [
    ("场景1: 稳定币脱锚", "USDC 跌至 $0.88，Circle 储备金出现问题", "depeg", "USDC", ["price"]),
    ("场景2: 交易所上线", "Coinbase 刚刚宣布上线 XYZ 代币", "listing", "XYZ", ["price", "search"]),
    ("场景3: 低价值空投", "某小型空投活动开始", "airdrop", "NONE", []),
]

@dataclass
class EventPayload:
    """Minimal EventPayload for testing."""
    text: str
    source: str = "test"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    language: str = "zh"
    keywords_hit: list[str] = field(default_factory=lambda: ["test"])
    translated_text: Optional[str] = None
    translation_confidence: float = 0.0
    historical_reference: Dict[str, Any] = field(default_factory=dict)
    media: list[Dict[str, Any]] = field(default_factory=list)
    is_priority_kol: bool = False


@dataclass
class SignalResult:
    """Minimal SignalResult for testing."""
    status: str = "success"
    event_type: str = "other"
    asset: str = ""
    confidence: float = 0.5
    summary: str = ""
    action: str = "observe"
    asset_names: str = ""
    direction: str = "neutral"
    strength: str = "low"
    timeframe: str = "medium"
    risk_flags: list[str] = field(default_factory=list)
    raw_response: str = ""
    notes: str = ""
    error: Optional[str] = None
    links: list[str] = field(default_factory=list)
    alert: str = ""
    severity: str = ""


def _build_state(message: str, event_type: str, asset: str) -> dict[str, Any]:
    """Construct the minimal planner state used by tests and manual runs."""
    return {
        "payload": EventPayload(text=message),
        "preliminary": SignalResult(
            event_type=event_type,
            asset=asset,
            confidence=0.75,
        ),
        "memory_evidence": {},
        "search_evidence": {},
        "price_evidence": {},
        "tool_call_count": 0,
        "max_tool_calls": 3,
    }


async def _run_scenario(
    name: str,
    message: str,
    event_type: str,
    asset: str,
    expected_tools: list[str],
    planner: Optional[ToolPlannerNode] = None,
):
    """Execute a single scenario and return whether the planner chose the expected tools."""
    print(f"\n{'='*80}")
    print(f"🧪 {name}")
    print(f"消息: {message}")
    print(f"预期工具: {expected_tools if expected_tools else '无'}")
    print(f"{'='*80}")

    if planner is None:
        from src.config import Config
        from src.ai.gemini_function_client import GeminiFunctionCallingClient
        from src.ai.deep_analysis.gemini import GeminiDeepAnalysisEngine
        from src.memory.factory import create_memory_backend

        config = Config()
        client = GeminiFunctionCallingClient(
            api_key=config.GEMINI_API_KEY,
            model_name=config.GEMINI_DEEP_MODEL,
            timeout=config.GEMINI_DEEP_TIMEOUT_SECONDS,
        )

        memory_bundle = create_memory_backend(config)

        # Create minimal engine for tool planner
        def dummy_parse(text):
            return SignalResult(summary=text)

        engine = GeminiDeepAnalysisEngine(
            client=client,
            memory_bundle=memory_bundle,
            parse_json_callback=dummy_parse,
            max_function_turns=1,
            memory_limit=config.MEMORY_MAX_NOTES,
            memory_min_confidence=config.MEMORY_MIN_CONFIDENCE,
            config=config,
        )

        planner = ToolPlannerNode(engine)

    # Create state
    state = _build_state(message, event_type, asset)

    try:
        result = await planner.execute(state)
        actual_tools = sorted(result.get("next_tools", []))
        keywords = result.get("search_keywords", "")

        success = actual_tools == sorted(expected_tools)

        print(f"✅ AI 决策: tools={actual_tools}")
        if keywords:
            print(f"   搜索关键词: {keywords}")
        print(f"✅ 结果: {'✅ 通过' if success else '❌ 失败'}")
        if not success:
            print(f"   预期: {sorted(expected_tools)}")
            print(f"   实际: {actual_tools}")

        return success

    except Exception as e:
        print(f"❌ 错误: {e}")
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,message,event_type,asset,expected_tools",
    SCENARIOS,
)
async def test_tool_planner_scenarios(name: str, message: str, event_type: str, asset: str, expected_tools: list[str]):
    """pytest entry point exercising the tool planner scenarios."""
    decision_args = {
        "tools": expected_tools,
        "search_keywords": "stub keywords 官方声明" if "search" in expected_tools else "",
        "macro_indicators": [],
        "onchain_assets": [asset] if "onchain" in expected_tools and asset != "NONE" else [],
        "protocol_slugs": [],
        "reason": "stubbed tool planner response",
    }

    class StubClient:
        def __init__(self, args):
            self._args = args

        async def generate_content_with_tools(self, messages, tools):
            return SimpleNamespace(function_calls=[SimpleNamespace(args=self._args)])

    class StubEngine:
        def __init__(self, args):
            self._client = StubClient(args)

    planner = ToolPlannerNode(StubEngine(decision_args))

    state = _build_state(message, event_type, asset)
    result = await planner.execute(state)

    assert sorted(result.get("next_tools", [])) == sorted(expected_tools)
    if "search" in expected_tools:
        assert result.get("search_keywords"), "search scenario should yield keywords"
    else:
        assert not result.get("search_keywords"), "non-search scenarios should not set keywords"

    success = await _run_scenario(name, message, event_type, asset, expected_tools, planner=planner)
    assert success, f"Unexpected tool selection for {name}"


async def main():
    """Run all tests."""
    print("🚀 Tool Planner 决策测试")
    print("测试 AI 是否能智能决策调用哪些工具\n")

    results = []
    for name, message, event_type, asset, expected in SCENARIOS:
        success = await _run_scenario(name, message, event_type, asset, expected)
        results.append((name, success))

    # Summary
    print(f"\n{'='*80}")
    print("📈 测试总结")
    print(f"{'='*80}")

    passed = sum(1 for _, s in results if s)
    total = len(results)

    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} - {name}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("🎉 所有测试通过！AI Tool Planner 智能决策正常工作")
        return 0
    else:
        print(f"⚠️  {total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
