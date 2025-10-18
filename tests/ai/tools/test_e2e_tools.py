#!/usr/bin/env python3
"""
端到端测试: Tool Planner 智能决策验证

测试场景:
1. 稳定币脱锚: "USDC 跌至 $0.88" → 应该只调用 price
2. 交易所上线: "Coinbase 上线 XYZ" → 应该调用 search + price
3. 低价值事件: "某空投活动" → 应该不调用工具
"""

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass
class EventPayload:
    """Minimal EventPayload for testing."""
    text: str
    source: str
    timestamp: datetime
    translated_text: Optional[str] = None
    language: str = "zh"
    translation_confidence: float = 0.0
    keywords_hit: list[str] = field(default_factory=list)
    historical_reference: Dict[str, Any] = field(default_factory=dict)
    media: list[Dict[str, Any]] = field(default_factory=list)
    is_priority_kol: bool = False


@dataclass
class SignalResult:
    """Minimal SignalResult for testing."""
    status: str = "success"
    summary: str = ""
    event_type: str = "other"
    asset: str = ""
    asset_names: str = ""
    action: str = "observe"
    direction: str = "neutral"
    confidence: float = 0.0
    strength: str = "low"
    timeframe: str = "medium"
    risk_flags: list[str] = field(default_factory=list)
    raw_response: str = ""
    notes: str = ""
    error: Optional[str] = None
    links: list[str] = field(default_factory=list)
    alert: str = ""
    severity: str = ""


class TestScenario:
    """Test scenario definition."""
    def __init__(self, name: str, message: str, event_type: str, asset: str,
                 confidence: float, expected_tools: list[str]):
        self.name = name
        self.message = message
        self.event_type = event_type
        self.asset = asset
        self.confidence = confidence
        self.expected_tools = sorted(expected_tools)

    def create_payload(self) -> EventPayload:
        """Create test payload."""
        return EventPayload(
            text=self.message,
            source="test_channel",
            timestamp=datetime.now(timezone.utc),
            language="zh",
            keywords_hit=["test"]
        )

    def create_preliminary(self) -> SignalResult:
        """Create preliminary signal (simulating Gemini Flash output)."""
        return SignalResult(
            status="success",
            summary=f"初步判断: {self.message[:30]}...",
            event_type=self.event_type,
            asset=self.asset,
            action="observe",
            confidence=self.confidence,
        )


async def run_test(scenario: TestScenario) -> dict:
    """Run a single test scenario."""
    logger.info("=" * 80)
    logger.info(f"🧪 测试场景: {scenario.name}")
    logger.info(f"消息: {scenario.message}")
    logger.info(f"预期工具调用: {scenario.expected_tools if scenario.expected_tools else '无'}")
    logger.info("=" * 80)

    # Import here to ensure .env is loaded
    from src.config import Config
    from src.ai.gemini_function_client import GeminiFunctionCallingClient
    from src.ai.deep_analysis.gemini import GeminiDeepAnalysisEngine
    from src.memory.factory import create_memory_backend

    # Load config
    config = Config()

    # Verify tools are enabled
    logger.info(f"配置检查:")
    logger.info(f"  DEEP_ANALYSIS_TOOLS_ENABLED: {config.DEEP_ANALYSIS_TOOLS_ENABLED}")
    logger.info(f"  TOOL_SEARCH_ENABLED: {config.TOOL_SEARCH_ENABLED}")
    logger.info(f"  TOOL_PRICE_ENABLED: {config.TOOL_PRICE_ENABLED}")

    # Create Gemini client
    client = GeminiFunctionCallingClient(
        api_key=config.GEMINI_API_KEY,
        model_name=config.GEMINI_DEEP_MODEL,
        timeout=config.GEMINI_DEEP_TIMEOUT_SECONDS,
    )

    # Create memory backend
    memory_bundle = create_memory_backend(config)

    # Create parse callback wrapper
    def parse_json_wrapper(text: str) -> SignalResult:
        """Simple JSON parser that returns SignalResult."""
        import json
        import re

        raw_text = (text or "").strip()

        # Extract JSON if wrapped in markdown
        if "```json" in raw_text:
            match = re.search(r'```json\s*\n(.*?)\n```', raw_text, re.DOTALL)
            if match:
                raw_text = match.group(1).strip()
        elif "```" in raw_text:
            match = re.search(r'```\s*\n(.*?)\n```', raw_text, re.DOTALL)
            if match:
                raw_text = match.group(1).strip()

        try:
            data = json.loads(raw_text)
            return SignalResult(
                status="success",
                summary=data.get("summary", ""),
                event_type=data.get("event_type", "other"),
                asset=data.get("asset", ""),
                action=data.get("action", "observe"),
                confidence=float(data.get("confidence", 0.0)),
                notes=data.get("notes", ""),
                links=data.get("links", []),
                risk_flags=data.get("risk_flags", []),
            )
        except Exception as e:
            logger.warning(f"Failed to parse JSON: {e}")
            return SignalResult(
                status="error",
                error=f"JSON parse error: {e}",
                summary="Failed to parse response",
            )

    parse_callback = parse_json_wrapper

    engine = GeminiDeepAnalysisEngine(
        client=client,
        memory_bundle=memory_bundle,
        parse_json_callback=parse_callback,
        max_function_turns=config.GEMINI_DEEP_MAX_FUNCTION_TURNS,
        memory_limit=config.MEMORY_MAX_NOTES,
        memory_min_confidence=config.MEMORY_MIN_CONFIDENCE,
        config=config,
    )

    # Create test data
    payload = scenario.create_payload()
    preliminary = scenario.create_preliminary()

    # Capture tool calls by patching the ToolExecutorNode
    tool_calls_made = []

    from src.ai.deep_analysis.nodes import tool_executor
    original_execute = tool_executor.ToolExecutorNode.execute

    async def patched_execute(self, state):
        # Capture tools from next_tools list
        next_tools = state.get("next_tools", [])
        for tool in next_tools:
            if tool and tool not in tool_calls_made:
                tool_calls_made.append(tool)
                logger.info(f"  🔧 捕获工具调用: {tool}")

        # Call original
        return await original_execute(self, state)

    tool_executor.ToolExecutorNode.execute = patched_execute

    try:
        # Run analysis
        result = await engine.analyse(payload, preliminary)

        # Restore original
        tool_executor.ToolExecutorNode.execute = original_execute

        # Sort for comparison
        actual_tools = sorted(tool_calls_made)

        # Compare with expected
        success = actual_tools == scenario.expected_tools

        logger.info("")
        logger.info("📊 结果:")
        logger.info(f"  预期工具: {scenario.expected_tools if scenario.expected_tools else '[]'}")
        logger.info(f"  实际工具: {actual_tools if actual_tools else '[]'}")
        logger.info(f"  ✅ 测试通过" if success else f"  ❌ 测试失败")
        logger.info(f"  最终置信度: {result.confidence:.2f}")
        logger.info(f"  最终操作: {result.action}")
        logger.info(f"  摘要: {result.summary[:100]}...")

        return {
            "scenario": scenario.name,
            "success": success,
            "expected_tools": scenario.expected_tools,
            "actual_tools": actual_tools,
            "confidence": result.confidence,
            "action": result.action,
        }

    except Exception as e:
        logger.error(f"❌ 测试执行失败: {e}", exc_info=True)
        tool_executor.ToolExecutorNode.execute = original_execute
        return {
            "scenario": scenario.name,
            "success": False,
            "expected_tools": scenario.expected_tools,
            "actual_tools": tool_calls_made if tool_calls_made else [],
            "error": str(e),
        }


async def main():
    """Run all test scenarios."""
    # Define test scenarios
    scenarios = [
        TestScenario(
            name="场景1: 稳定币脱锚 (仅price)",
            message="USDC 跌至 $0.88，Circle 储备金出现问题",
            event_type="depeg",
            asset="USDC",
            confidence=0.75,
            expected_tools=["price"]
        ),
        TestScenario(
            name="场景2: 交易所上线 (search+price)",
            message="Coinbase 刚刚宣布上线 XYZ 代币，预计将带来大量流动性",
            event_type="listing",
            asset="XYZ",
            confidence=0.80,
            expected_tools=["price", "search"]
        ),
        TestScenario(
            name="场景3: 低价值空投 (无工具)",
            message="某小型 DeFi 协议宣布空投活动开始，参与用户可获得少量代币奖励",
            event_type="airdrop",
            asset="NONE",
            confidence=0.60,
            expected_tools=[]
        ),
    ]

    logger.info("🚀 开始端到端测试")
    logger.info(f"总共 {len(scenarios)} 个测试场景\n")

    results = []
    for scenario in scenarios:
        result = await run_test(scenario)
        results.append(result)
        logger.info("")  # Empty line between tests

    # Summary
    logger.info("=" * 80)
    logger.info("📈 测试总结")
    logger.info("=" * 80)

    passed = sum(1 for r in results if r["success"])
    total = len(results)

    for result in results:
        status = "✅ PASS" if result["success"] else "❌ FAIL"
        logger.info(f"{status} - {result['scenario']}")
        if not result["success"]:
            logger.info(f"     预期: {result['expected_tools']}")
            logger.info(f"     实际: {result['actual_tools']}")
            if "error" in result:
                logger.info(f"     错误: {result['error']}")

    logger.info("")
    logger.info(f"总计: {passed}/{total} 测试通过")

    if passed == total:
        logger.info("🎉 所有测试通过！AI Tool Planner 工作正常")
        return 0
    else:
        logger.warning(f"⚠️ {total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
