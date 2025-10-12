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
        "- 时间线模糊（\"即将\"、\"近期\"、\"不久\"）→ 添加 vague_timeline，降低 confidence 至 ≤0.5\n"
        "- 内容含糊（\"大事件\"、\"重要更新\"）无具体细节 → 添加 speculative，降低 confidence 至 ≤0.4\n"
        "- 无法验证的声明或预期 → 添加 unverifiable\n"
        "- 仅基于社交媒体发言但无实质内容 → 标记为 speculative，即使发言者是知名人士\n"
        "- 高风险投机与传闻（含 Meme 币暴富、早期爆料、未经证实的重大消息）处理：\n"
        "  - 若来源可信度不足或缺少链上/成交/资金流等可验证数据，输出 event_type=scam_alert 或 other，action=observe，confidence ≤0.4，并在 risk_flags 中添加 speculative、liquidity_risk 或 unverifiable，notes 解释风险。\n"
        "  - 若消息来自公信力较高的机构/人士且附带可验证指标，可结合数据决定 action 与 confidence；若仍缺乏执行依据，保持 observe 并指出待验证数据点。\n"
        "- 交易所/衍生品上线公告（仅宣布上币、永续/杠杆合约开放、做市启动、认购开始等）且缺乏成交量、资金费率、做市规模、链上资金流等可执行数据 → 默认观察级别，建议 action=observe、direction=neutral、confidence ≤0.4，并至少标记 speculative 或 data_incomplete，notes 中说明『仅为上线公告，缺少交易数据』；如含明确的流动性/资金费率/持仓指标，方可重新评估置信度与方向。\n\n"
        "- **价格反弹 vs 持续上涨识别**（关键：区分跌后反弹和新一轮涨势）：\n"
        "  - 若新闻描述价格上涨/突破，但价格工具显示 **24h 涨幅 <1% 或仍为负值** → 很可能是跌后短线反弹，而非新一轮上涨\n"
        "  - 处理方式：\n"
        "    - 将 action 设为 observe，direction=neutral 或 up（若确有反弹），confidence ≤0.5\n"
        "    - 在 notes 中说明：『短线反弹，24h 涨幅有限（或仍为负），尚未扭转趋势，需警惕假突破』\n"
        "    - 若 1h 为正但 24h/7d 为负，补充：『1h 反弹 +X%，但 24h/7d 仍跌 Y%，未收复关键位，保持观望』\n"
        "  - **对比判断**：若新闻报道"突破/上涨"但多时间周期数据显示涨幅极小或仅单一时间周期为正，应重点提示"反弹力度不足"\n"
        "  - **真正的上涨**：若 24h 涨幅 >3% 且多个时间周期持续为正，可考虑更积极评估（confidence 可提高，action 可调整为 buy 或更明确的方向）\n"
        "  - 关键原则：**标题中的"突破""上涨"可能是短线噪音，必须结合多时间周期价格变化判断真实趋势**\n\n"
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
