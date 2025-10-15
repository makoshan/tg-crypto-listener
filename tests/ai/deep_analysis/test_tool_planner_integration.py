"""Integration test that exercises the Gemini-backed tool planner.

This test is intentionally skipped unless the environment enables real Gemini
calls to avoid accidental API usage during routine test runs.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.ai.deep_analysis.gemini import GeminiDeepAnalysisEngine
from src.ai.deep_analysis.nodes.tool_planner import ToolPlannerNode
from src.ai.gemini_function_client import GeminiFunctionCallingClient
from src.ai.signal_engine import EventPayload, SignalResult
from src.config import Config
from src.memory.factory import create_memory_backend

RUN_REAL_TESTS = os.getenv("RUN_REAL_GEMINI_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
]


@pytest.mark.skipif(not RUN_REAL_TESTS, reason="Set RUN_REAL_GEMINI_TESTS=1 to exercise real Gemini API calls.")
async def test_tool_planner_real_gemini_call():
    """Verify that the planner can call Gemini and return a structured response."""
    config = Config()
    if not config.GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY is not configured.")

    # Disable memory backends to avoid remote dependencies during integration runs.
    config.MEMORY_ENABLED = False
    config.DEEP_ANALYSIS_TOOLS_ENABLED = True
    config.TOOL_SEARCH_ENABLED = False
    config.TOOL_PRICE_ENABLED = False
    config.TOOL_MACRO_ENABLED = False
    config.TOOL_ONCHAIN_ENABLED = False
    config.TOOL_PROTOCOL_ENABLED = False

    client = GeminiFunctionCallingClient(
        api_key=config.GEMINI_API_KEY,
        model_name=config.GEMINI_DEEP_MODEL,
        timeout=config.GEMINI_DEEP_TIMEOUT_SECONDS,
    )

    memory_bundle = create_memory_backend(config)

    def dummy_parse(text: str) -> SignalResult:
        return SignalResult(status="success", summary=text)

    engine = GeminiDeepAnalysisEngine(
        client=client,
        memory_bundle=memory_bundle,
        parse_json_callback=dummy_parse,
        max_function_turns=config.GEMINI_DEEP_MAX_FUNCTION_TURNS,
        memory_limit=config.MEMORY_MAX_NOTES,
        memory_min_confidence=config.MEMORY_MIN_CONFIDENCE,
        config=config,
    )

    planner = ToolPlannerNode(engine)

    state = {
        "payload": EventPayload(
            text="USDC 跌至 $0.88，Circle 储备金出现问题",
            source="integration-test",
            timestamp=datetime.now(timezone.utc),
            language="zh",
        ),
        "preliminary": SignalResult(
            status="success",
            event_type="depeg",
            asset="USDC",
            confidence=0.6,
        ),
        "memory_evidence": {},
        "search_evidence": {},
        "price_evidence": {},
        "tool_call_count": 0,
        "max_tool_calls": 3,
    }

    result = await planner.execute(state)

    next_tools = result.get("next_tools")
    assert isinstance(next_tools, list)
    assert all(tool in {"search", "price", "macro", "onchain", "protocol"} for tool in next_tools)

    # If Gemini suggests a search, ensure keywords are provided.
    if "search" in next_tools:
        assert result.get("search_keywords"), "Gemini should supply search keywords when requesting search."
