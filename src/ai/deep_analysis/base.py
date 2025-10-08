"""Shared utilities for deep analysis engines."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)


class DeepAnalysisError(RuntimeError):
    """Raised when a deep analysis provider fails."""


class DeepAnalysisEngine(ABC):
    """Abstract interface implemented by provider-specific deep analysis engines."""

    provider_name: str

    def __init__(
        self,
        *,
        provider_name: str,
        parse_json_callback: Callable[[str], Any],
    ) -> None:
        self.provider_name = provider_name
        self._parse_json_callback = parse_json_callback

    @abstractmethod
    async def analyse(  # pragma: no cover - interface definition
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        """Execute provider-specific deep analysis workflow."""

    def _parse_json(self, raw_text: str) -> "SignalResult":
        """Parse provider response using injected normaliser."""
        return self._parse_json_callback(raw_text)


def build_deep_analysis_messages(
    payload: "EventPayload",
    preliminary: "SignalResult",
    *,
    history_limit: int = 2,
    additional_context: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Create unified prompt messages for deep analysis providers."""

    historical_ref = payload.historical_reference or {}
    historical_entries = list(historical_ref.get("entries", []) or [])
    if history_limit > 0 and len(historical_entries) > history_limit:
        historical_ref = {"entries": historical_entries[:history_limit]}

    context: dict[str, Any] = {
        "text": payload.translated_text or payload.text,
        "source": payload.source,
        "timestamp": payload.timestamp.isoformat(),
        "historical_reference": historical_ref,
        "initial_analysis": {
            "summary": preliminary.summary,
            "event_type": preliminary.event_type,
            "asset": preliminary.asset,
            "action": preliminary.action,
            "confidence": preliminary.confidence,
            "risk_flags": preliminary.risk_flags,
            "notes": preliminary.notes,
            "links": preliminary.links,
        },
    }

    if additional_context:
        for key, value in additional_context.items():
            context[key] = value

    context_json = json.dumps(context, ensure_ascii=False, indent=2)

    system_prompt = (
        "你是加密交易台的资深分析师，负责验证和优化 AI 初步分析结果。\n\n"
        "任务：\n"
        "1. 验证事件类型、资产识别、置信度是否合理\n"
        "2. 结合历史案例判断当前事件的独特性\n"
        "3. 评估风险点（流动性、监管、市场情绪）\n"
        "4. 调整置信度并给出操作建议\n\n"
        "输出：JSON 格式，包含 summary、event_type、asset、asset_name、action、direction、"
        "confidence、strength、risk_flags、notes、links。"
        "若初步分析有误（如误判资产、置信度过高），在 notes 中说明修正理由。"
    )

    user_prompt = (
        "分析以下事件并返回优化后的 JSON：\n"
        f"```json\n{context_json}\n```"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# Deferred imports for type-checking only to avoid circular dependencies.
from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from src.ai.signal_engine import EventPayload, SignalResult
