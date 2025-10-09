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
        "4. 评估信号质量（时间线明确性、内容具体性、可验证性）\n"
        "5. 调整置信度并给出操作建议\n\n"
        "置信度（confidence）语义：\n"
        "- confidence 表示**该信号作为交易建议的可靠性**，而非事件真实性\n"
        "- 0.7-1.0：高质量买入/卖出信号，有充分数据支撑\n"
        "- 0.4-0.7：中等质量信号，需结合其他信息判断\n"
        "- 0.0-0.4：低质量信号或风险警告，不建议交易\n"
        "**关键**：即使事件真实（如确实有人暴富），但若不是好的交易机会（如高风险投机、无法复制），应设为低置信度（≤0.4）。\n\n"
        "信号质量评估准则（Signal vs Noise）：\n"
        "- 时间线模糊（"即将"、"近期"、"不久"）→ 添加 vague_timeline，降低 confidence 至 ≤0.5\n"
        "- 内容含糊（"大事件"、"重要更新"）无具体细节 → 添加 speculative，降低 confidence 至 ≤0.4\n"
        "- 无法验证的声明或预期 → 添加 unverifiable\n"
        "- 仅基于社交媒体发言但无实质内容 → 标记为 speculative，即使发言者是知名人士\n"
        "- "一夜暴富"等故事 → 识别为 scam_alert，confidence ≤0.4，添加 speculative 和 liquidity_risk\n\n"
        "输出：JSON 格式，包含 summary、event_type、asset、asset_name、action、direction、"
        "confidence、strength、risk_flags、notes、links。"
        "event_type 可选值：listing、delisting、hack、regulation、funding、whale、liquidation、partnership、product_launch、governance、macro、celebrity、airdrop、scam_alert、other。"
        "risk_flags 可选值：price_volatility、liquidity_risk、regulation_risk、confidence_low、data_incomplete、vague_timeline、speculative、unverifiable。"
        "若初步分析有误（如误判资产、置信度过高、忽视噪音特征、将风险警告标记为高置信度），在 notes 中说明修正理由。"
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
