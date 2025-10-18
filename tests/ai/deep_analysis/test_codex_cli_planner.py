"""
Tests for Codex CLI Planner.

Test organization:
- TestCodexCliInvocation: Basic CLI invocation tests
- TestCodexCliPlanner: Planner implementation tests
- TestCodexCliErrorHandling: Error scenario tests
- TestCodexCliIntegration: Real CLI integration tests (requires codex CLI)
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.deep_analysis.planners.codex_cli_planner import CodexCliPlanner
from src.ai.deep_analysis.planners.base import ToolPlan


# Mock configuration for testing
@dataclass
class MockConfig:
    """Mock configuration object."""
    CODEX_CLI_PATH: str = "codex"
    CODEX_CLI_TIMEOUT: float = 60.0
    CODEX_CLI_MAX_TOKENS: int = 4000
    CODEX_CLI_CONTEXT: str = "@docs/codex_cli_integration_plan.md"
    DEEP_ANALYSIS_PLANNER: str = "codex_cli"


class MockEngine:
    """Mock deep analysis engine."""
    def __init__(self):
        self._config = MockConfig()
        self._tools = {"search": {}, "price": {}, "macro": {}}


@pytest.fixture
def mock_config():
    """Mock configuration."""
    return MockConfig()


@pytest.fixture
def mock_engine():
    """Mock engine."""
    return MockEngine()


@pytest.fixture
def planner(mock_engine, mock_config):
    """Create CodexCliPlanner instance."""
    return CodexCliPlanner(mock_engine, mock_config)


@pytest.fixture
def sample_state():
    """Sample state for testing."""
    @dataclass
    class MockPayload:
        text: str = "BTC ETF 获批"
        language: str = "中文"

    @dataclass
    class MockPreliminary:
        event_type: str = "listing"
        asset: str = "BTC"
        action: str = "buy"
        confidence: float = 0.75
        summary: str = "BTC ETF 获得 SEC 批准"

    return {
        "payload": MockPayload(),
        "preliminary": MockPreliminary(),
        "evidence": {},
        "tool_call_count": 0,
        "max_tool_calls": 3,
    }


class TestCodexCliInvocation:
    """Test basic CLI invocation functionality."""

    @pytest.mark.asyncio
    async def test_codex_exec_basic(self, planner):
        """Test basic codex exec call with mocked subprocess."""
        mock_output = json.dumps({
            "tools": ["search", "price"],
            "search_keywords": "BTC ETF SEC approval",
            "reason": "需要搜索验证消息并获取价格数据"
        })

        with patch('asyncio.create_subprocess_exec') as mock_proc:
            # Mock process
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(
                return_value=(mock_output.encode(), b"")
            )
            proc_mock.returncode = 0
            mock_proc.return_value = proc_mock

            # Execute
            result = await planner._exec_codex("test prompt")

            # Assertions
            assert result == mock_output
            mock_proc.assert_called_once()
            call_args = mock_proc.call_args
            assert call_args[0][0] == "codex"
            assert call_args[0][1] == "exec"
            assert call_args[0][2] == "test prompt"

    @pytest.mark.asyncio
    async def test_codex_exec_with_context_file(self, planner, sample_state):
        """Test CLI invocation with context file reference."""
        mock_output = json.dumps({
            "tools": ["search"],
            "search_keywords": "BTC ETF",
            "reason": "验证消息"
        })

        with patch('asyncio.create_subprocess_exec') as mock_proc:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(
                return_value=(mock_output.encode(), b"")
            )
            proc_mock.returncode = 0
            mock_proc.return_value = proc_mock

            # Execute plan (which builds prompt with context)
            await planner.plan(sample_state, ["search", "price"])

            # Check that prompt was passed
            mock_proc.assert_called_once()
            prompt = mock_proc.call_args[0][2]
            assert "@docs/codex_cli_integration_plan.md" in prompt


class TestCodexCliPlanner:
    """Test Planner implementation."""

    @pytest.mark.asyncio
    async def test_plan_with_codex_exec(self, planner, sample_state):
        """Test complete planning flow."""
        mock_output = json.dumps({
            "tools": ["search", "price"],
            "search_keywords": "BTC ETF SEC approval",
            "macro_indicators": [],
            "onchain_assets": [],
            "protocol_slugs": [],
            "reason": "需要搜索验证消息并获取价格数据"
        })

        with patch.object(planner, '_exec_codex', return_value=mock_output):
            plan = await planner.plan(sample_state, ["search", "price", "macro"])

            # Assertions
            assert isinstance(plan, ToolPlan)
            assert plan.tools == ["search", "price"]
            assert "BTC ETF" in plan.search_keywords
            assert plan.reason != ""

    @pytest.mark.asyncio
    async def test_handle_markdown_wrapped_json(self, planner):
        """Test JSON extraction from markdown code blocks."""
        # Test markdown json block
        markdown_json = """
Here's the result:

```json
{
  "tools": ["search"],
  "reason": "test"
}
```
"""
        extracted = planner._extract_json(markdown_json)
        data = json.loads(extracted)
        assert data["tools"] == ["search"]

        # Test generic code block
        code_block = """
```
{
  "tools": ["price"],
  "reason": "test2"
}
```
"""
        extracted = planner._extract_json(code_block)
        data = json.loads(extracted)
        assert data["tools"] == ["price"]

        # Test raw JSON
        raw_json = '{"tools": ["macro"], "reason": "test3"}'
        extracted = planner._extract_json(raw_json)
        data = json.loads(extracted)
        assert data["tools"] == ["macro"]


class TestCodexCliErrorHandling:
    """Test error scenarios."""

    @pytest.mark.asyncio
    async def test_timeout_handling(self, planner):
        """Test CLI timeout handling."""
        with patch('asyncio.create_subprocess_exec') as mock_proc:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(
                side_effect=asyncio.TimeoutError()
            )
            proc_mock.kill = MagicMock()
            proc_mock.wait = AsyncMock()
            mock_proc.return_value = proc_mock

            with pytest.raises(TimeoutError):
                await planner._exec_codex("test prompt")

            proc_mock.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_zero_exit_code(self, planner):
        """Test handling of non-zero exit codes."""
        with patch('asyncio.create_subprocess_exec') as mock_proc:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(
                return_value=(b"", b"CLI error message")
            )
            proc_mock.returncode = 1
            mock_proc.return_value = proc_mock

            with pytest.raises(RuntimeError) as exc_info:
                await planner._exec_codex("test prompt")

            assert "exited with code 1" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_json_output(self, planner, sample_state):
        """Test handling of invalid JSON from CLI."""
        invalid_json = "This is not valid JSON"

        with patch.object(planner, '_exec_codex', return_value=invalid_json):
            with pytest.raises(RuntimeError) as exc_info:
                await planner.plan(sample_state, ["search"])

            assert "Invalid JSON" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cli_not_found(self):
        """Test handling when CLI executable is not found."""
        config = MockConfig()
        config.CODEX_CLI_PATH = "/nonexistent/path/to/codex"
        engine = MockEngine()
        planner = CodexCliPlanner(engine, config)

        with pytest.raises(RuntimeError) as exc_info:
            await planner._exec_codex("test prompt")

        assert "not found" in str(exc_info.value).lower()


@pytest.mark.integration
class TestCodexCliIntegration:
    """
    Integration tests requiring real CLI installation.

    Run with: pytest -m integration
    Skip with: pytest -m "not integration"
    """

    @pytest.mark.asyncio
    async def test_real_codex_exec_call(self, sample_state):
        """Test real CLI call (requires codex CLI installed)."""
        import shutil

        # Check if codex CLI is available
        if not shutil.which("codex"):
            pytest.skip("Codex CLI not found in PATH")

        config = MockConfig()
        engine = MockEngine()
        planner = CodexCliPlanner(engine, config)

        try:
            # This will make a real CLI call
            plan = await planner.plan(sample_state, ["search", "price"])

            # Basic assertions
            assert isinstance(plan, ToolPlan)
            assert isinstance(plan.tools, list)
            assert isinstance(plan.reason, str)

        except TimeoutError:
            pytest.skip("CLI call timed out")
        except RuntimeError as exc:
            pytest.skip(f"CLI execution failed: {exc}")


class TestCodexCliSynthesis:
    """Test synthesis functionality."""

    @pytest.mark.asyncio
    async def test_synthesize_with_codex(self, planner, sample_state):
        """Test evidence synthesis."""
        # Add some evidence
        sample_state["evidence"] = {
            "search_evidence": {"success": True, "summary": "找到 3 条确认消息"},
            "price_evidence": {"success": True, "current_price": 45000},
        }

        mock_output = json.dumps({
            "summary": "BTC ETF 获批，市场反应积极",
            "event_type": "listing",
            "asset": "BTC",
            "action": "buy",
            "confidence": 0.85,
            "notes": "多源确认，价格上涨",
            "links": [],
            "risk_flags": []
        })

        with patch.object(planner, '_exec_codex', return_value=mock_output):
            result = await planner.synthesize(sample_state)

            # Assertions
            assert isinstance(result, str)
            data = json.loads(result)
            assert data["asset"] == "BTC"
            assert data["action"] in ["buy", "sell", "observe"]
            assert 0.0 <= data["confidence"] <= 1.0


# Configuration for pytest markers
def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require external dependencies)"
    )
