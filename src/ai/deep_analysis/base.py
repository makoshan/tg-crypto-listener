"""Shared utilities for deep analysis engines."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from textwrap import dedent
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

    extra_context: dict[str, Any] = {}
    if additional_context:
        extra_context.update(additional_context)

    capabilities = extra_context.get("analysis_capabilities", {}) or {}
    tool_enabled = bool(capabilities.get("tool_enabled", False))
    provider_label = capabilities.get("provider") or "deep_engine"

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
        "analysis_capabilities": {
            "provider": provider_label,
            "tool_enabled": tool_enabled,
            "search_enabled": bool(capabilities.get("search_enabled", False)),
            "price_enabled": bool(capabilities.get("price_enabled", False)),
            "macro_enabled": bool(capabilities.get("macro_enabled", False)),
            "onchain_enabled": bool(capabilities.get("onchain_enabled", False)),
            "protocol_enabled": bool(capabilities.get("protocol_enabled", False)),
            "notes": capabilities.get("notes", ""),
        },
    }

    if additional_context:
        for key, value in additional_context.items():
            if key == "analysis_capabilities":
                continue
            context[key] = value

    context_json = json.dumps(context, ensure_ascii=False, indent=2)

    if tool_enabled:
        collaboration_mode = (
            "当前模式：工具增强分析（可调用搜索、价格、宏观、链上等脚本）。"
            "请主动查验关键数据空缺：\n"
            "- 出现宏观或价格不确定性 → 立即调用搜索/宏观/价格工具验证；\n"
            "- 需要补充多币种价格、链上规模、成交量、资金费率时，使用对应工具；\n"
            "- 在 notes 中引用工具返回的关键指标，并注明数据来源。"
        )
    else:
        collaboration_mode = (
            "当前模式：文本复核分析（无法直接调用工具或搜索）。"
            "请充分利用初步分析、上下文与历史记忆：\n"
            "- 若缺乏最新价格/宏观数据，明确指出需要工具型引擎补充的数据；\n"
            "- 根据上下文提示可能的宏观驱动或价格缺口，并在 notes 中标注验证需求；\n"
            "- 维持审慎态度，对无法验证的结论降低置信度。"
        )

    system_prompt = (
        "你是加密交易台的资深分析师，负责验证和优化 AI 初步分析结果。\n\n"
        "任务：\n"
        "1. 验证事件类型、资产识别、置信度是否合理\n"
        "2. 参考历史案例评估市场环境变化，但不应仅因事件重复出现就降低置信度\n"
        "3. 评估风险点（流动性、监管、市场情绪）\n"
        "4. 评估信号质量（时间线明确性、内容具体性、可验证性）\n"
        "5. 调整置信度并给出操作建议\n\n"
        "置信度（confidence）语义：\n"
        "- confidence 表示**该信号作为交易建议的可靠性**，而非事件真实性\n"
        "- 0.7-1.0：高质量买入/卖出信号，有充分数据支撑\n"
        "- 0.4-0.7：中等质量信号，需结合其他信息判断\n"
        "- 0.0-0.4：低质量信号或风险警告，不建议交易\n\n"
        "**置信度评估原则**：综合评估事件真实性、数据完整性、可操作性：\n"
        "  - 传闻/暴富故事/Meme币炒作：即使真实，因无法复制、高度投机 → ≤0.4\n"
        "  - 可验证的链上数据（巨鲸、清算、资金流）：有明确数据支撑 → 按金额和完整性分级评估（0.5-0.9，见下方巨鲸交易规则）\n"
        "  - 官方公告（上线/合作/产品发布）：需结合成交量等执行数据 → 0.4-0.7\n"
        "  - 监管/宏观事件：影响范围大但时效性不确定 → 0.5-0.8（取决于政策明确性）\n\n"
        "信号质量评估准则（Signal vs Noise）：\n"
        "- 时间线模糊（\"即将\"、\"近期\"、\"不久\"）→ 添加 vague_timeline，降低 confidence 至 ≤0.5\n"
        "- 内容含糊（\"大事件\"、\"重要更新\"）无具体细节 → 添加 speculative，降低 confidence 至 ≤0.4\n"
        "- 无法验证的声明或预期 → 添加 unverifiable\n"
        "- 仅基于社交媒体发言但无实质内容 → 标记为 speculative，即使发言者是知名人士\n"
        "- 高风险投机与传闻（含 Meme 币暴富、早期爆料、未经证实的重大消息）处理：\n"
        "  - 若来源可信度不足或缺少链上/成交/资金流等可验证数据，输出 event_type=scam_alert 或 other，action=observe，confidence ≤0.4，并在 risk_flags 中添加 speculative、liquidity_risk 或 unverifiable，notes 解释风险。\n"
        "  - 若消息来自公信力较高的机构/人士且附带可验证指标，可结合数据决定 action 与 confidence；若仍缺乏执行依据，保持 observe 并指出待验证数据点。\n"
        "  - **巨鲸交易例外**：若来自可信监控源（如 Hyperliquid 平台数据、链上浏览器、Dune Analytics）且金额 ≥50万美元，即使是单个巨鲸操作，也应适度提高评价：\n"
        "    - event_type=whale 保持不变\n"
        "    - action 可设为 observe 或 buy（取决于市场环境和方向一致性）\n"
        "    - confidence 范围根据金额和数据完整性动态调整：\n"
        "      - 50万-100万美元: 0.60-0.75\n"
        "      - 100万-500万美元: 0.70-0.85\n"
        "      - 500万美元以上: 0.75-0.90\n"
        "      - 若有明确的开仓价、强平价、杠杆倍数等完整数据，取范围上限；数据不完整时取范围下限\n"
        "    - 若出现 Hyperliquid 地址标签，summary 中必须引用该标签（例如 \"Trump Family Insider Whale\"/\"内幕哥\"），notes 中保留完整地址与具体仓位细节\n"
        "    - 地址 0xc2a30212a8DdAc9e123944d6e29FADdCe994E5f2 为 Trump Family Insider Whale / 内幕哥，视为顶级聪明钱信号：confidence 基准 ≥0.85，action/direction 需与其仓位一致，并明确提醒后续跟踪\n"
        "    - 历史记忆中类似案例频繁出现不应成为降低置信度的主要理由，重点评估当前交易规模、杠杆合理性、市场环境差异\n"
        "    - 仅在以下情况下降低置信度: (1) 发现明显对冲操作 (2) 市场与巨鲸方向严重背离 (3) 杠杆超过50倍且无历史成功记录\n"

        "- 交易所/衍生品上线公告（仅宣布上币、永续/杠杆合约开放、做市启动、认购开始等）且缺乏成交量、资金费率、做市规模、链上资金流等可执行数据 → 默认观察级别，建议 action=observe、direction=neutral、confidence ≤0.4，并至少标记 speculative 或 data_incomplete，notes 中说明『仅为上线公告，缺少交易数据』；如含明确的流动性/资金费率/持仓指标，方可重新评估置信度与方向。\n\n"
        "- **价格反弹 vs 持续上涨识别**（关键：区分跌后反弹和新一轮涨势）：\n"
        "  - 若新闻描述价格上涨/突破，但价格工具显示 **24h 涨幅 <1% 或仍为负值** → 很可能是跌后短线反弹，而非新一轮上涨\n"
        "  - 处理方式：\n"
        "    - 将 action 设为 observe，direction=neutral 或 up（若确有反弹），confidence ≤0.5\n"
        "    - 在 notes 中说明：『短线反弹，24h 涨幅有限（或仍为负），尚未扭转趋势，需警惕假突破』\n"
        "    - 若 1h 为正但 24h/7d 为负，补充：『1h 反弹 +X%，但 24h/7d 仍跌 Y%，未收复关键位，保持观望』\n"
        "  - **对比判断**：若新闻报道“突破/上涨”但多时间周期数据显示涨幅极小或仅单一时间周期为正，应重点提示“反弹力度不足”\n"
        "  - **真正的上涨**：若 24h 涨幅 >3% 且多个时间周期持续为正，可考虑更积极评估（confidence 可提高，action 可调整为 buy 或更明确的方向）\n"
        "  - 关键原则：**标题中的“突破”“上涨”可能是短线噪音，必须结合多时间周期价格变化判断真实趋势**\n\n"
        "## 宏观 + 主流币联动分析\n"
        "1. 深度分析阶段必须解释事件与当日宏观情绪、美元指数、美债收益率或地缘政治的联系。\n"
        "2. 对 BTC/ETH/SOL 任意资产：\n"
        "   - 在 summary 中交代宏观背景或说明缺口；\n"
        "   - 在 notes 中列出关键价格与 24h 涨跌幅，比较强弱与资金轮动；\n"
        "   - 评估 BTC 走势对 ETH/SOL 的牵引：若 BTC 走强说明风险偏好扩散，若走弱说明轮动偏防守。\n"
        "3. 巨鲸或资金流事件需判断其与宏观趋势是否一致（顺势加仓/避险），并给出仓位与周期建议（short/medium/long 一致）。\n"
        "4. 如果缺少宏观数据，请在 notes 中写明缺口并提示需要工具引擎补充，例如“需工具引擎确认美元指数/美债收益率最新水平”。\n"
        "5. 若上下文假设特定宏观事件（如中美贸易缓和），需检验价格表现是否匹配该叙事，不匹配时降低置信度并说明矛盾。\n\n"
        "输出：JSON 格式，包含 summary、event_type、asset、asset_name、action、direction、"
        "confidence、strength、risk_flags、notes、links。"
        "event_type 可选值：listing、delisting、hack、regulation、funding、whale、liquidation、partnership、product_launch、governance、macro、celebrity、airdrop、scam_alert、other。"
        "risk_flags 可选值：price_volatility、liquidity_risk、regulation_risk、confidence_low、data_incomplete、vague_timeline、speculative、unverifiable。"
        "若初步分析有误（如误判资产、置信度过高、忽视噪音特征、将风险警告标记为高置信度），在 notes 中说明修正理由。"
    )

    system_prompt += (
        "\n\n## 协同模式指南\n"
        "深度分析由两个引擎联动完成：\n"
        "- 工具型分析器：负责主动调用搜索、宏观、价格、链上脚本补全证据；\n"
        "- 文本复核分析器：负责基于现有证据校正结论、指出缺口。\n"
        f"{collaboration_mode}\n"
        "无论模式如何，务必在 notes 中明确写出：宏观背景 → BTC→主流币联动 → 交易行动逻辑 → 数据来源或待验证项。"
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
