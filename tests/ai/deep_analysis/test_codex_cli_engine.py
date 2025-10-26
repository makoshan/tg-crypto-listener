import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.ai.deep_analysis.codex_cli import CodexCliDeepAnalysisEngine
from src.ai.deep_analysis.base import DeepAnalysisError
from src.ai.signal_engine import EventPayload, SignalResult


@pytest.fixture
def payload() -> EventPayload:
    return EventPayload(
        text="Binance 宣布上线 TEST 代币",
        source="binance_cn",
        timestamp=datetime.now(timezone.utc),
        translated_text="Binance announced listing TEST.",
    )


@pytest.fixture
def preliminary() -> SignalResult:
    return SignalResult(
        status="success",
        summary="初步：TEST 代币上线",
        event_type="listing",
        asset="TEST",
        action="observe",
        confidence=0.5,
    )


def _build_engine(parse_callback, **overrides) -> CodexCliDeepAnalysisEngine:
    return CodexCliDeepAnalysisEngine(
        cli_path="codex",
        timeout=5.0,
        parse_json_callback=parse_callback,
        context_refs=["@docs/codex_cli_integration_plan.md"],
        extra_cli_args=["--sandbox", "workspace-write"],
        max_retries=overrides.get("max_retries", 0),
        working_directory=overrides.get("working_directory"),
        disable_after_failures=overrides.get("disable_after_failures", 2),
        failure_cooldown=overrides.get("failure_cooldown", 60.0),
    )


@pytest.mark.asyncio
async def test_codex_cli_engine_success(payload: EventPayload, preliminary: SignalResult):
    captured: dict[str, str] = {}

    def _parse(text: str) -> SignalResult:
        captured["text"] = text
        return SignalResult(
            status="success",
            summary="最终分析",
            event_type="listing",
            asset="TEST",
            action="buy",
            confidence=0.8,
        )

    engine = _build_engine(_parse)

    stdout = (
        "```json\n"
        "{\n"
        '  "summary": "TEST 上线，流动性提升",\n'
        '  "event_type": "listing",\n'
        '  "asset": "TEST",\n'
        '  "action": "buy",\n'
        '  "confidence": 0.82\n'
        "}\n"
        "```"
    ).encode("utf-8")

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.communicate = AsyncMock(return_value=(stdout, b""))
        proc_mock.returncode = 0
        mock_exec.return_value = proc_mock

        result = await engine.analyse(payload, preliminary)

    assert captured["text"].startswith("{")
    assert result.summary == "最终分析"
    assert result.raw_response.startswith("```json")

    args, kwargs = mock_exec.call_args
    assert args[0] == "codex"
    assert args[1] == "exec"
    assert "--sandbox" in args
    # Prompt is last positional argument
    prompt = args[-1]
    assert "@docs/codex_cli_integration_plan.md" in prompt


@pytest.mark.asyncio
async def test_codex_cli_engine_non_zero_exit(payload: EventPayload, preliminary: SignalResult):
    def _parse(text: str) -> SignalResult:
        return SignalResult(status="skip")

    engine = _build_engine(_parse)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.communicate = AsyncMock(return_value=(b"", b"permission denied"))
        proc_mock.returncode = 1
        mock_exec.return_value = proc_mock

        with pytest.raises(DeepAnalysisError):
            await engine.analyse(payload, preliminary)


@pytest.mark.asyncio
async def test_codex_cli_engine_missing_binary(payload: EventPayload, preliminary: SignalResult):
    def _parse(text: str) -> SignalResult:
        return SignalResult(status="skip")

    engine = _build_engine(_parse)

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("codex")):
        with pytest.raises(DeepAnalysisError):
            await engine.analyse(payload, preliminary)


@pytest.mark.asyncio
async def test_codex_cli_engine_enters_cooldown(payload: EventPayload, preliminary: SignalResult):
    def _parse(text: str) -> SignalResult:
        return SignalResult(status="skip")

    engine = _build_engine(
        _parse,
        max_retries=0,
        disable_after_failures=1,
        failure_cooldown=120.0,
    )

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.communicate = AsyncMock(return_value=(b"", b"fatal error"))
        proc_mock.returncode = 2
        mock_exec.return_value = proc_mock

        with pytest.raises(DeepAnalysisError):
            await engine.analyse(payload, preliminary)

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        with pytest.raises(DeepAnalysisError):
            await engine.analyse(payload, preliminary)
        mock_exec.assert_not_called()

    # Cooldown expired -> engine should attempt to invoke CLI again
    engine._cooldown_until = 0.0  # type: ignore[attr-defined]

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("codex")) as mock_exec:
        with pytest.raises(DeepAnalysisError):
            await engine.analyse(payload, preliminary)
        mock_exec.assert_called_once()
