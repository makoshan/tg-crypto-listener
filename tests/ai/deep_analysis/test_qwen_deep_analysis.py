"""
千问深度分析引擎集成测试

测试内容：
1. 引擎初始化
2. 基础深度分析（无工具）
3. 工具增强深度分析（搜索、价格等）
4. enable_search 内置搜索功能
5. 与 Gemini 深度分析的输出一致性

运行方式：
    pytest tests/ai/deep_analysis/test_qwen_deep_analysis.py -v
    pytest tests/ai/deep_analysis/test_qwen_deep_analysis.py::test_qwen_basic_analysis -v
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from src.ai.deep_analysis.factory import create_deep_analysis_engine
from src.ai.signal_engine import EventPayload, SignalResult
from src.config import Config


@pytest.fixture
def mock_config():
    """Mock configuration for Qwen"""
    config = Mock(spec=Config)

    # Qwen configuration
    config.DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "sk-test")
    config.QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    config.QWEN_DEEP_MODEL = "qwen-plus"
    config.QWEN_DEEP_TIMEOUT_SECONDS = 30.0
    config.QWEN_DEEP_MAX_FUNCTION_TURNS = 6
    config.QWEN_ENABLE_SEARCH = False  # Default: disable search

    # Tools configuration (disabled by default)
    config.TOOL_SEARCH_ENABLED = False
    config.TOOL_PRICE_ENABLED = False
    config.TOOL_MACRO_ENABLED = False
    config.TOOL_ONCHAIN_ENABLED = False
    config.TOOL_PROTOCOL_ENABLED = False

    def get_deep_analysis_config():
        return {
            "enabled": True,
            "provider": "qwen",
            "qwen": {
                "api_key": config.DASHSCOPE_API_KEY,
                "base_url": config.QWEN_BASE_URL,
                "model": config.QWEN_DEEP_MODEL,
                "timeout": config.QWEN_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": config.QWEN_DEEP_MAX_FUNCTION_TURNS,
                "enable_search": config.QWEN_ENABLE_SEARCH,
            },
        }

    config.get_deep_analysis_config = get_deep_analysis_config
    return config


@pytest.fixture
def mock_payload():
    """Mock event payload"""
    return EventPayload(
        text="Binance 宣布上线 ABC 代币，明天开盘交易",
        translated_text="Binance announces ABC token listing, trading starts tomorrow",
        source="@test_channel",
        timestamp=datetime.now(timezone.utc),
        historical_reference={
            "entries": [
                {
                    "summary": "历史上线案例：XYZ 代币上线后涨幅 50%",
                    "action": "buy",
                    "confidence": 0.8,
                }
            ]
        },
    )


@pytest.fixture
def mock_preliminary():
    """Mock preliminary analysis result"""
    return SignalResult(
        status="success",
        summary="币安宣布上线 ABC 代币",
        event_type="listing",
        asset="ABC",
        action="buy",
        confidence=0.7,
        risk_flags=["data_incomplete"],
        notes="初步分析：交易所上线事件",
        links=[],
    )


def parse_json_callback(raw_text: str) -> SignalResult:
    """Mock JSON parser (same logic as AiSignalEngine)"""
    # Remove markdown code blocks
    if "```json" in raw_text:
        raw_text = raw_text.split("```json")[1].split("```")[0].strip()
    elif "```" in raw_text:
        raw_text = raw_text.split("```")[1].split("```")[0].strip()

    data = json.loads(raw_text)

    return SignalResult(
        status="success",
        summary=data.get("summary", ""),
        event_type=data.get("event_type", "other"),
        asset=data.get("asset", "NONE"),
        asset_names=data.get("asset_name", ""),
        action=data.get("action", "observe"),
        direction=data.get("direction", "neutral"),
        confidence=float(data.get("confidence", 0.5)),
        strength=data.get("strength", "low"),
        risk_flags=data.get("risk_flags", []),
        notes=data.get("notes", ""),
        links=data.get("links", []),
        timeframe=data.get("timeframe", "short"),
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY not set",
)
async def test_qwen_engine_initialization(mock_config):
    """测试 1: 千问引擎初始化"""
    engine = create_deep_analysis_engine(
        provider="qwen",
        config=mock_config,
        parse_callback=parse_json_callback,
        memory_bundle=None,
    )

    assert engine is not None
    assert engine.provider_name == "qwen"
    assert engine.model == "qwen-plus"
    assert engine.max_function_turns == 6
    print("✅ 千问引擎初始化成功")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY not set",
)
async def test_qwen_basic_analysis(mock_config, mock_payload, mock_preliminary):
    """测试 2: 基础深度分析（无工具）"""
    engine = create_deep_analysis_engine(
        provider="qwen",
        config=mock_config,
        parse_callback=parse_json_callback,
        memory_bundle=None,
    )

    result = await engine.analyse(
        payload=mock_payload,
        preliminary=mock_preliminary,
    )

    # Verify result structure
    assert result is not None
    assert result.summary
    assert result.event_type in [
        "listing",
        "delisting",
        "hack",
        "regulation",
        "funding",
        "whale",
        "liquidation",
        "partnership",
        "product_launch",
        "governance",
        "macro",
        "celebrity",
        "airdrop",
        "scam_alert",
        "other",
    ]
    assert result.asset
    assert result.action in ["buy", "sell", "observe"]
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.risk_flags, list)
    assert result.notes

    print(f"✅ 基础深度分析成功")
    print(f"   Summary: {result.summary}")
    print(f"   Event Type: {result.event_type}")
    print(f"   Asset: {result.asset}")
    print(f"   Action: {result.action}")
    print(f"   Confidence: {result.confidence}")
    print(f"   Notes: {result.notes[:100]}...")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY not set",
)
async def test_qwen_with_enable_search(mock_config, mock_payload, mock_preliminary):
    """测试 3: enable_search 内置搜索功能（千问特色）"""
    # Enable search
    mock_config.QWEN_ENABLE_SEARCH = True

    def get_deep_analysis_config():
        return {
            "enabled": True,
            "provider": "qwen",
            "qwen": {
                "api_key": mock_config.DASHSCOPE_API_KEY,
                "base_url": mock_config.QWEN_BASE_URL,
                "model": mock_config.QWEN_DEEP_MODEL,
                "timeout": mock_config.QWEN_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": mock_config.QWEN_DEEP_MAX_FUNCTION_TURNS,
                "enable_search": True,  # Enable search
            },
        }

    mock_config.get_deep_analysis_config = get_deep_analysis_config

    engine = create_deep_analysis_engine(
        provider="qwen",
        config=mock_config,
        parse_callback=parse_json_callback,
        memory_bundle=None,
    )

    assert engine.enable_search is True

    # Use a real-world event for search validation
    mock_payload.text = "Binance 是否在 2024 年 10 月宣布上线 PENGU 代币？"
    mock_payload.translated_text = "Did Binance announce PENGU token listing in October 2024?"

    result = await engine.analyse(
        payload=mock_payload,
        preliminary=mock_preliminary,
    )

    assert result is not None
    assert result.summary
    assert result.notes

    print(f"✅ enable_search 测试成功")
    print(f"   Summary: {result.summary}")
    print(f"   Notes: {result.notes[:200]}...")

    # Check if search results are mentioned in notes
    # (enable_search should auto-search and include results)
    assert len(result.notes) > 100, "Notes should contain search evidence"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY not set",
)
async def test_qwen_output_consistency(mock_config, mock_payload, mock_preliminary):
    """测试 4: 输出一致性（与 Gemini 相同的字段）"""
    engine = create_deep_analysis_engine(
        provider="qwen",
        config=mock_config,
        parse_callback=parse_json_callback,
        memory_bundle=None,
    )

    result = await engine.analyse(
        payload=mock_payload,
        preliminary=mock_preliminary,
    )

    # Verify all required fields exist (same as Gemini)
    required_fields = [
        "summary",
        "event_type",
        "asset",
        "action",
        "confidence",
        "risk_flags",
        "notes",
        "links",
    ]

    for field in required_fields:
        assert hasattr(result, field), f"Missing field: {field}"
        value = getattr(result, field)
        assert value is not None, f"Field {field} is None"

    print(f"✅ 输出一致性验证成功")
    print(f"   所有必需字段: {required_fields}")


if __name__ == "__main__":
    """手动运行测试（用于调试）"""
    import sys

    if not os.getenv("DASHSCOPE_API_KEY"):
        print("❌ DASHSCOPE_API_KEY 未设置")
        sys.exit(1)

    print("=" * 60)
    print("千问深度分析引擎集成测试")
    print("=" * 60)

    # Create mock instances manually (not using pytest fixtures)
    config = Mock(spec=Config)
    config.DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
    config.QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    config.QWEN_DEEP_MODEL = "qwen-plus"
    config.QWEN_DEEP_TIMEOUT_SECONDS = 30.0
    config.QWEN_DEEP_MAX_FUNCTION_TURNS = 6
    config.QWEN_ENABLE_SEARCH = False
    config.TOOL_SEARCH_ENABLED = False
    config.TOOL_PRICE_ENABLED = False
    config.TOOL_MACRO_ENABLED = False
    config.TOOL_ONCHAIN_ENABLED = False
    config.TOOL_PROTOCOL_ENABLED = False

    def get_deep_analysis_config():
        return {
            "enabled": True,
            "provider": "qwen",
            "qwen": {
                "api_key": config.DASHSCOPE_API_KEY,
                "base_url": config.QWEN_BASE_URL,
                "model": config.QWEN_DEEP_MODEL,
                "timeout": config.QWEN_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": config.QWEN_DEEP_MAX_FUNCTION_TURNS,
                "enable_search": config.QWEN_ENABLE_SEARCH,
            },
        }

    config.get_deep_analysis_config = get_deep_analysis_config

    payload = EventPayload(
        text="Binance 宣布上线 ABC 代币，明天开盘交易",
        translated_text="Binance announces ABC token listing, trading starts tomorrow",
        source="@test_channel",
        timestamp=datetime.now(timezone.utc),
        historical_reference={
            "entries": [
                {
                    "summary": "历史上线案例：XYZ 代币上线后涨幅 50%",
                    "action": "buy",
                    "confidence": 0.8,
                }
            ]
        },
    )

    preliminary = SignalResult(
        status="success",
        summary="币安宣布上线 ABC 代币",
        event_type="listing",
        asset="ABC",
        action="buy",
        confidence=0.7,
        risk_flags=["data_incomplete"],
        notes="初步分析：交易所上线事件",
        links=[],
    )

    # Run tests
    asyncio.run(test_qwen_engine_initialization(config))
    asyncio.run(test_qwen_basic_analysis(config, payload, preliminary))

    print("\n" + "=" * 60)
    print("✅ 基础测试通过")
    print("=" * 60)
