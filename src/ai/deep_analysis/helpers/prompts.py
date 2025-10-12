"""Prompt Builder Helper"""
from typing import Any, Mapping

from .formatters import format_memory_evidence, format_search_detail, format_search_evidence

def build_planner_prompt(state: Mapping[str, Any], _engine: Any | None = None) -> str:
    """
    构建 Tool Planner 使用的 prompt

    Args:
        state: LangGraph 状态字典

    Returns:
        str: 格式化的 Planner prompt
    """
    payload = state["payload"]
    preliminary = state["preliminary"]
    memory_ev = state.get("memory_evidence", {})
    search_ev = state.get("search_evidence", {})

    # 获取消息语言属性
    language = getattr(payload, "language", "未知")

    # 格式化证据
    memory_text = memory_ev.get("formatted", "无") if memory_ev else "无"
    search_text = format_search_evidence(search_ev)

    return f"""你是工具调度专家,判断是否需要搜索新闻验证,并生成最优搜索关键词。

【消息内容】
{payload.text}

【消息语言】{language}

【事件类型】{preliminary.event_type}

【资产】{preliminary.asset}

【初步置信度】{preliminary.confidence}

【已有证据】
- 历史记忆: {memory_text}
- 搜索结果: {search_text}

【决策规则】成本意识优先
0. ⚠️ 成本意识：每次搜索消耗配额，请谨慎决策
1. 如果已有搜索结果且 multi_source=true → 证据充分,无需再搜索
2. 如果事件类型是 hack/regulation/partnership → 需要搜索验证
3. 如果 tool_call_count >= 2 → 证据充分,无需再搜索
4. 如果记忆中已有高相似度案例 (similarity > 0.8) → 优先使用记忆，减少搜索
5. 如果是数值类事件 (depeg/liquidation) → 暂不需要搜索

【关键词生成规则】（仅当决定搜索时）
1. **中英文混合**: 如果消息是中文,生成中英文混合关键词
2. **包含关键实体**: 提取消息中的具体公司名、协议名、金额等
3. **官方来源标识**: 添加 "official statement 官方声明"
4. **事件类型关键词**:
   - hack → "黑客攻击 hack exploit breach"
   - regulation → "监管政策 regulation SEC CFTC"
   - listing → "上线 listing announce"
   - partnership → "合作 partnership collaboration"
5. **避免泛化词**: 不要使用 "新闻" "消息" 等

【示例】
- 消息: "Coinbase 宣布上线 XYZ 代币"
  关键词: "XYZ listing Coinbase official announcement 上线 官方公告"

- 消息: "XXX DeFi 协议遭受闪电贷攻击,损失 $100M USDC"
  关键词: "XXX DeFi hack exploit flash loan attack official statement 黑客攻击 官方声明"

【当前状态】
- 已调用工具次数: {state['tool_call_count']}
- 最大调用次数: {state['max_tool_calls']}

请调用 decide_next_tools 函数返回决策和关键词。"""


def build_synthesis_prompt(state: Mapping[str, Any], _engine: Any | None = None) -> str:
    """
    构建 Synthesis 使用的 prompt

    Args:
        state: LangGraph 状态字典

    Returns:
        str: 格式化的 Synthesis prompt
    """
    payload = state["payload"]
    preliminary = state["preliminary"]
    memory_ev = state.get("memory_evidence", {})
    search_ev = state.get("search_evidence", {})

    # 格式化证据
    memory_text = memory_ev.get("formatted", "无历史相似事件") if memory_ev else "无历史相似事件"
    search_text = format_search_detail(search_ev)

    return f"""你是加密交易台资深分析师,已掌握完整证据,请给出最终判断。

【原始消息】
{payload.text}

【Gemini Flash 初步判断】
- 事件类型: {preliminary.event_type}
- 资产: {preliminary.asset}
- 操作: {preliminary.action}
- 置信度: {preliminary.confidence}
- 摘要: {preliminary.summary}

【历史记忆】
{memory_text}

【搜索验证】
{search_text}

【置信度调整规则】
- 基准: Gemini Flash 初判置信度 = {preliminary.confidence}

- 搜索多源确认 (multi_source=true) AND 官方确认 (official_confirmed=true):
  → 提升 +0.15 to +0.20

- 搜索多源确认但无官方确认:
  → 提升 +0.05 to +0.10

- 搜索结果 < 3 条或无官方确认:
  → 降低 -0.10 to -0.20

- 搜索结果冲突 (不同来源说法矛盾):
  → 降低 -0.20 并标记 data_incomplete

- 历史记忆存在高相似度案例 (similarity > 0.8):
  → 参考历史案例最终置信度,调整 ±0.10

【最终约束】
- 置信度范围: 0.0 - 1.0
- 如果最终置信度 < 0.4, 必须添加 confidence_low 风险标志
- 在 notes 中说明: "初判 {preliminary.confidence:.2f} → 最终 [final_confidence:.2f], 依据: [搜索/记忆/冲突]"

返回 JSON（与 SignalResult 格式一致）:
{{
  "summary": "中文摘要,简明扼要描述事件核心",
  "event_type": "{preliminary.event_type}",
  "asset": "{preliminary.asset}",
  "action": "buy|sell|observe",
  "confidence": 0.0-1.0,
  "notes": "推理依据,引用搜索来源和关键证据",
  "links": [],
  "risk_flags": []
}}

只返回 JSON,不要其他文字。"""
