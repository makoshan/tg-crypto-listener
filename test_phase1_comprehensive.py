"""Phase 1 综合测试（pytest 版本）。"""

from __future__ import annotations

import importlib
from typing import Iterable
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytestmark = pytest.mark.integration

IMPORT_CASES = [
    ("src.ai.tools.base", ("BaseTool", "ToolResult")),
    ("src.ai.tools.exceptions", ("ToolFetchError", "ToolTimeoutError", "ToolRateLimitError")),
    ("src.ai.tools.search.providers.base", ("SearchProvider",)),
    ("src.ai.tools.search.providers.tavily", ("TavilySearchProvider",)),
    ("src.ai.tools.search.fetcher", ("SearchTool",)),
    ("src.ai.deep_analysis.nodes.base", ("BaseNode",)),
    ("src.ai.deep_analysis.nodes.context_gather", ("ContextGatherNode",)),
    ("src.ai.deep_analysis.nodes.tool_planner", ("ToolPlannerNode",)),
    ("src.ai.deep_analysis.nodes.tool_executor", ("ToolExecutorNode",)),
    ("src.ai.deep_analysis.nodes.synthesis", ("SynthesisNode",)),
    ("src.ai.deep_analysis.helpers.memory", ("fetch_memory_entries", "format_memory_evidence")),
    ("src.ai.deep_analysis.helpers.prompts", ("build_planner_prompt", "build_synthesis_prompt")),
    ("src.ai.deep_analysis.helpers.formatters", ("format_search_evidence",)),
    ("src.ai.deep_analysis.graph", ("build_deep_analysis_graph",)),
]


@pytest.mark.parametrize(("module_name", "attributes"), IMPORT_CASES)
def test_phase1_module_imports(module_name: str, attributes: Iterable[str]) -> None:
    """验证 Phase 1 关键模块均可成功导入。"""
    module = importlib.import_module(module_name)
    for attr in attributes:
        assert hasattr(module, attr), f"{module_name}.{attr} 未定义"


def test_toolresult_basic_usage() -> None:
    """ToolResult 基础创建与属性访问。"""
    from src.ai.tools.base import ToolResult

    result = ToolResult(
        source="TestTool",
        timestamp=ToolResult._format_timestamp(),
        success=True,
        data={"key": "value"},
        triggered=False,
        confidence=0.8,
    )

    assert result.source == "TestTool"
    assert result.success is True
    assert result.data == {"key": "value"}
    assert result.confidence == 0.8
    assert result.error is None


def test_format_search_evidence() -> None:
    """搜索证据格式化函数应包含关键信息。"""
    from src.ai.deep_analysis.helpers.formatters import format_search_evidence

    search_ev = {
        "success": True,
        "data": {
            "keyword": "Bitcoin ETF",
            "source_count": 5,
            "multi_source": True,
            "official_confirmed": True,
        },
    }

    formatted = format_search_evidence(search_ev)
    assert "Bitcoin ETF" in formatted
    assert "5" in formatted


def test_format_memory_evidence() -> None:
    """记忆证据格式化函数应包含摘要与置信度。"""
    from src.ai.deep_analysis.helpers.memory import format_memory_evidence

    entries = [
        {"confidence": 0.85, "similarity": 0.92, "summary": "历史案例1"},
        {"confidence": 0.78, "similarity": 0.88, "summary": "历史案例2"},
    ]

    formatted = format_memory_evidence(entries)
    assert "历史案例1" in formatted
    assert "0.85" in formatted


def test_build_planner_prompt() -> None:
    """工具规划 Prompt 构造应包含事件核心要素。"""
    from src.ai.deep_analysis.helpers.prompts import build_planner_prompt

    mock_state = {
        "payload": Mock(text="测试消息", language="zh"),
        "preliminary": Mock(event_type="hack", asset="BTC", confidence=0.8, action="sell"),
        "memory_evidence": {"formatted": "历史记忆：案例1...", "count": 2},
        "search_evidence": None,
        "tool_call_count": 0,
        "max_tool_calls": 3,
    }

    prompt = build_planner_prompt(mock_state, Mock())

    assert "hack" in prompt
    assert "BTC" in prompt
    assert "0.8" in prompt
    assert "decide_next_tools" in prompt


def test_build_synthesis_prompt() -> None:
    """综合节点 Prompt 构造应包含记忆与搜索证据。"""
    from src.ai.deep_analysis.helpers.prompts import build_synthesis_prompt

    mock_state = {
        "payload": Mock(text="测试消息"),
        "preliminary": Mock(
            event_type="hack",
            asset="BTC",
            confidence=0.8,
            action="sell",
            summary="初步分析",
        ),
        "memory_evidence": {"formatted": "历史记忆：案例1...", "count": 2},
        "search_evidence": {
            "success": True,
            "data": {
                "keyword": "BTC hack",
                "results": [],
                "multi_source": True,
                "official_confirmed": False,
                "source_count": 3,
            },
        },
    }

    prompt = build_synthesis_prompt(mock_state, Mock())
    assert "hack" in prompt
    assert "BTC" in prompt
    assert "BTC hack" in prompt


def test_node_initialisation_context_gather() -> None:
    from src.ai.deep_analysis.nodes.context_gather import ContextGatherNode

    engine = Mock()
    node = ContextGatherNode(engine)
    assert node.engine is engine


def test_node_initialisation_tool_planner() -> None:
    from src.ai.deep_analysis.nodes.tool_planner import ToolPlannerNode

    engine = Mock()
    node = ToolPlannerNode(engine)
    assert node.engine is engine


def test_node_initialisation_tool_executor() -> None:
    from src.ai.deep_analysis.nodes.tool_executor import ToolExecutorNode

    engine = Mock()
    node = ToolExecutorNode(engine)
    assert node.engine is engine


def test_node_initialisation_synthesis() -> None:
    from src.ai.deep_analysis.nodes.synthesis import SynthesisNode

    engine = Mock()
    node = SynthesisNode(engine)
    assert node.engine is engine


@pytest.mark.asyncio
async def test_search_tool_with_mock_provider() -> None:
    """SearchTool fetch 应能正确处理 Mock Provider。"""
    from src.ai.tools.base import ToolResult
    from src.ai.tools.search.fetcher import SearchTool

    mock_config = Mock()
    mock_config.DEEP_ANALYSIS_SEARCH_PROVIDER = "tavily"
    mock_config.TAVILY_API_KEY = "test-key"
    mock_config.SEARCH_MAX_RESULTS = 5
    mock_config.SEARCH_MULTI_SOURCE_THRESHOLD = 3
    mock_config.SEARCH_INCLUDE_DOMAINS = "coindesk.com,theblock.co"
    mock_config.DEEP_ANALYSIS_TOOL_TIMEOUT = 10

    mock_provider = AsyncMock()
    mock_provider.search = AsyncMock(
        return_value=ToolResult(
            source="MockProvider",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data={
                "keyword": "test",
                "results": [{"title": "Test Article", "url": "https://example.com", "score": 0.9}],
                "multi_source": False,
                "official_confirmed": False,
                "sentiment": {"panic": 0.2, "neutral": 0.5, "optimistic": 0.3},
                "source_count": 1,
            },
            triggered=False,
            confidence=0.8,
        )
    )

    with patch("src.ai.tools.search.create_search_provider", return_value=mock_provider):
        search_tool = SearchTool(mock_config)
        result = await search_tool.fetch(keyword="test", max_results=5)

    assert result.success is True
    assert result.data["keyword"] == "test"
    assert len(result.data["results"]) == 1


def test_config_has_required_fields() -> None:
    """Config 应包含工具增强相关字段。"""
    from src.config import Config

    config = Config()
    required_attrs = [
        "DEEP_ANALYSIS_TOOLS_ENABLED",
        "DEEP_ANALYSIS_MAX_TOOL_CALLS",
        "DEEP_ANALYSIS_TOOL_TIMEOUT",
        "TOOL_SEARCH_ENABLED",
        "DEEP_ANALYSIS_SEARCH_PROVIDER",
        "SEARCH_MAX_RESULTS",
        "SEARCH_MULTI_SOURCE_THRESHOLD",
    ]

    missing = [attr for attr in required_attrs if not hasattr(config, attr)]
    assert not missing, f"Config 缺少字段: {', '.join(missing)}"


def test_deep_analysis_state_has_fields() -> None:
    """DeepAnalysisState TypedDict 应包含 Phase 1 定义字段。"""
    from src.ai.deep_analysis.gemini import DeepAnalysisState

    annotations = getattr(DeepAnalysisState, "__annotations__", {})
    required_fields = [
        "payload",
        "preliminary",
        "search_evidence",
        "memory_evidence",
        "next_tools",
        "search_keywords",
        "tool_call_count",
        "max_tool_calls",
        "final_response",
    ]

    missing = [field for field in required_fields if field not in annotations]
    assert not missing, f"DeepAnalysisState 缺少字段: {', '.join(missing)}"
