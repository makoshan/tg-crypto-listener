"""Prompt Builder Helper"""
from typing import Any, Mapping

from .formatters import (
    format_macro_brief,
    format_macro_detail,
    format_memory_evidence,
    format_onchain_brief,
    format_onchain_detail,
    format_price_evidence,
    format_search_detail,
    format_search_evidence,
)

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
    price_ev = state.get("price_evidence", {})
    macro_ev = state.get("macro_evidence", {})
    protocol_ev = state.get("protocol_evidence", {})
    onchain_ev = state.get("onchain_evidence", {})
    protocol_ev = state.get("protocol_evidence", {})
    onchain_ev = state.get("onchain_evidence", {})
    onchain_ev = state.get("onchain_evidence", {})

    # 获取消息语言属性
    language = getattr(payload, "language", "未知")

    # 格式化证据
    memory_text = memory_ev.get("formatted", "无") if memory_ev else "无"
    search_text = format_search_evidence(search_ev)
    price_text = "已获取" if price_ev and price_ev.get("success") else "无"
    macro_text = format_macro_brief(macro_ev)
    protocol_text = format_protocol_brief(protocol_ev)
    onchain_text = format_onchain_brief(onchain_ev)

    return f"""你是工具调度专家，智能决策需要调用哪些工具来验证消息真实性。

【可用工具】
1. **search** - 搜索新闻验证事件真实性
2. **price** - 获取资产价格数据，验证价格异常
3. **macro** - 获取宏观经济指标（CPI、利率、就业、美元指数、VIX 等）
4. **onchain** - 获取链上流动性/赎回数据（TVL、赎回、桥接状态）
5. **protocol** - 获取协议级别 TVL/费用/链分布，验证协议资金是否异常流出

【消息内容】
{payload.text}

【消息语言】{language}

【事件类型】{preliminary.event_type}

【资产】{preliminary.asset}

【初步置信度】{preliminary.confidence}

【已有证据】
- 历史记忆: {memory_text}
- 搜索结果: {search_text}
- 价格数据: {price_text}
- 宏观数据: {macro_text}
- 协议数据: {protocol_text}
- 链上数据: {onchain_text}

【工具调用决策原则】⚠️ 问题驱动 + 成本意识

**核心问题：这个工具能否帮我做出更可靠的买入/卖出/观察判断？**

**决策原则**：
1. **证据充分性**：现有证据（消息内容、历史记忆、已获取数据）是否足够支持高置信度判断？
2. **信息互补性**：这个工具能否提供当前缺失的关键信息？
3. **成本效益比**：工具调用的成本 vs 可能带来的置信度提升

**search 工具的价值**：
- 验证消息的真实性（区分传闻 vs 官方确认）
- 获取多源确认（提升置信度）
- 发现消息中未提及的关键细节

**何时 search 价值高？**
→ 当你无法仅凭消息内容判断真伪时
→ 当消息涉及重大影响但缺乏来源时
→ 当需要更多细节来评估风险时

**何时 search 价值低？**
→ 已有搜索结果且多源确认
→ 历史记忆高度匹配（相似案例）
→ 消息本身就很明确且来自可信渠道

**price 工具的价值**：
- 验证价格异常（实际价格 vs 消息声称）
- 评估市场反应（事件对价格的影响）
- 发现隐藏风险（如波动率异常、清算风险）

**何时 price 价值高？**
→ 当消息声称价格异常但需要验证时
→ 当事件可能影响价格但不确定影响程度时
→ 当需要量化数据来支持交易决策时

**何时 price 价值低？**
→ 已有价格数据
→ 消息与价格/市场无关（纯新闻）
→ 资产无效或无法获取价格

**macro 工具的价值**：
- 识别宏观驱动（通胀、利率、美元、就业、避险情绪）对市场的影响
- 验证宏观叙事是否得到数据支持
- 提供“贸易战/战争/政策”类事件的客观指标（美元指数、VIX、就业等）

**何时 macro 价值高？**
→ 消息涉及宏观叙事（通胀、加息、就业、贸易战、战争、美元走势）
→ 需要量化宏观背景来解释价格波动
→ 搜索证据不足以判定宏观影响时

**宏观指标选择指南**：
- 通胀/物价 → `CPI`, `CORE_CPI`
- 加息/降息 → `FED_FUNDS`
- 就业/失业 → `UNEMPLOYMENT`
- 美元强弱/贸易战 → `DXY`
- 恐慌情绪/战争风险 → `VIX`

**onchain 工具的价值**：
- 判断稳定币/跨链资产是否出现大量赎回或流动性枯竭
- 验证链上资金流是否支持消息描述（如桥接暂停、赎回潮）
- 发现潜在系统性风险（脱锚、流动性断裂）

**何时 onchain 价值高？**
→ 脱锚、赎回、桥接暂停等消息  
→ 黑客/安全事件怀疑资金外流  
→ 需要量化链上资金变化支持决策

**onchain 资产选择指南**：
- 默认使用消息中的稳定币或桥接资产（如 USDC、USDT、USDE、WBETH）
- AI 可指定 `onchain_assets` 补充额外观测对象（如多资产事件）

**protocol 工具的价值**：
- 验证协议级 TVL 是否出现异常流出（被攻击、跑路、停止服务）
- 量化多链部署资金分布，判断风险是否集中在单链
- 补充 24h/7d TVL 变化，为“资金迁移/TVL 暴跌”消息提供证据

**何时 protocol 价值高？**
→ 协议被攻击、暂停、遭遇挤兑的传闻  
→ 官方公告重大参数调整、跨链部署迁移  
→ 市场讨论“TVL 暴跌/暴涨”但缺乏数据验证时

**protocol slug 选择指南**：
- 优先使用消息中给出的协议英文名或常用 slug（如 Aave → `aave`，Curve → `curve-dex`）
- 如涉及多个协议，可列出 1-2 个重点 slug（如 ["curve-dex","convex-finance"]）

**成本约束**：
- 工具调用消耗配额，优先级：成本 < 可靠性
- 优先使用免费证据：消息内容、历史记忆
- 如果置信度 >= 0.8 且有记忆支持 → 考虑跳过工具
- 如果 tool_call_count >= 2 → 证据应该足够了

【搜索关键词生成规则】（仅当 tools 包含 "search" 时生成）
1. **中英文混合**: 如果消息是中文,生成中英文混合关键词
2. **包含关键实体**: 提取消息中的具体公司名、协议名、金额等
3. **官方来源标识**: 添加 "official statement 官方声明"
4. **事件类型关键词**:
   - hack → "黑客攻击 hack exploit breach"
   - regulation → "监管政策 regulation SEC CFTC"
   - listing → "上线 listing announce"
   - partnership → "合作 partnership collaboration"
5. **避免泛化词**: 不要使用 "新闻" "消息" 等

【决策思考示例】- 展示思考过程，而非规则匹配

**示例 1: 思考"证据充分性"**
消息: "USDC 跌至 $0.88，Circle 储备金出现问题"
- 思考：消息声称价格异常，但我无法确认实际价格是否真的是 $0.88
- 思考：如果价格确实异常 → buy/sell 决策；如果价格正常 → 可能是假消息
- 思考：搜索新闻价值低（价格数据更直接）
- 决策: tools=["price"]
- 关键问题：实际价格是多少？是否真的脱锚？

**示例 2: 思考"信息互补性"**
消息: "据悉 Coinbase 将上线 XYZ 代币"
- 思考：消息是传闻（"据悉"），缺乏可信来源
- 思考：如果是真的 → buy 信号；如果是假的 → 避免损失
- 思考：search 能验证真实性，price 能查看是否已有价格波动
- 决策: tools=["search", "price"]
- 关键问题：这是真消息还是传闻？市场有反应吗？

**示例 3: 思考"成本效益比"**
消息: "某小型空投活动开始"
- 思考：初步置信度 0.85，历史记忆相似度 0.9（曾见过类似空投）
- 思考：即使调用工具，也不太可能提升置信度（空投类事件影响小）
- 思考：成本 > 收益，不值得调用工具
- 决策: tools=[]
- 关键问题：已有证据足够了吗？工具能提升多少置信度？

【宏观指标生成规则】（仅当 tools 包含 "macro" 时生成）
1. 从消息文本中识别关键宏观主题
2. 在返回的 `macro_indicators` 字段中列出 1-3 个指标代码（如 ["CPI","VIX"]）
3. 可选值：CPI, CORE_CPI, FED_FUNDS, UNEMPLOYMENT, DXY, VIX

【链上资产生成规则】（仅当 tools 包含 "onchain" 时生成）
1. 使用消息中提到的稳定币/桥接资产代号
2. 若消息涉及多资产赎回，可列出 1-2 个优先观察的资产（如 ["USDC","USDT"]）
3. 返回简洁大写代号；若无法判断，可留空让系统使用默认资产

【协议 slug 生成规则】（仅当 tools 包含 "protocol" 时生成）
1. 识别消息中的协议英文名或常用简称
2. 转换为 DeFiLlama slug（小写、空格换成破折号，例如 \"Curve Finance\" → \"curve-dex\"）
3. 如无法确定 slug，可留空让系统使用默认推断

【当前状态】
- 已调用工具次数: {state['tool_call_count']}
- 最大调用次数: {state['max_tool_calls']}

**你的任务**：
1. 分析当前证据是否足够做出可靠判断
2. 思考每个工具能否提供缺失的关键信息
3. 权衡成本和收益
4. 调用 decide_next_tools 函数返回决策

**记住核心问题**：这个工具能否帮我做出更可靠的买入/卖出/观察判断？"""


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
    price_ev = state.get("price_evidence", {})
    macro_ev = state.get("macro_evidence", {})

    # 格式化证据
    memory_text = memory_ev.get("formatted", "无历史相似事件") if memory_ev else "无历史相似事件"
    search_text = format_search_detail(search_ev)
    price_text = format_price_evidence(price_ev)
    macro_text = format_macro_detail(macro_ev)
    protocol_text = format_protocol_detail(protocol_ev)
    onchain_text = format_onchain_detail(onchain_ev)

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

【价格数据】
{price_text}

【宏观数据】
{macro_text}

【协议数据】
{protocol_text}

【链上数据】
{onchain_text}

【时效性判断】⚠️ 关键：区分"新机会"vs"事后回顾"

**时间维度分析**：
1. 消息是报道**正在发生**的事件 → 时效性高，机会存在
2. 消息是**事后总结/回顾** → 时效性低，市场可能已消化

**识别"事后回顾"的关键词**：
- "...事件后"、"...之后"、"回顾"、"总结"
- "作者认为"、"分析指出"、"观点"（分析类，非新闻类）
- "与历史...类似"、"历史上..."（历史对比）
- 消息发布时间 vs 事件发生时间（如果能判断）

**时效性对置信度的影响**：
- **正在发生的事件**（实时新闻）:
  → 保持或提升置信度，这是真正的交易机会

- **事后回顾/总结**（已发生的事件）:
  → **大幅降低置信度 -0.30 to -0.50**
  → 原因：市场可能已经反应完毕，追涨/追空风险高
  → 如果是长期趋势分析 → action 改为 "observe"

- **历史对比/观点分析**:
  → 这不是 actionable 信号，action 应该是 "observe"
  → 置信度降低 -0.40，标记 "analysis_not_news"

**示例判断**：
```
❌ 错误："1011黑天鹅事件后，USDe 脱锚"
   → 这是回顾，不是新机会
   → 应该：confidence -= 0.40, action = "observe", risk_flags += ["stale_event"]

✅ 正确："Coinbase 刚刚宣布上线 XYZ 代币"
   → 这是实时新闻，有交易机会
   → 保持或提升置信度
```

【置信度调整规则】
- 基准: Gemini Flash 初判置信度 = {preliminary.confidence}
- ⚠️ **优先检查时效性**（如果是事后回顾 → 大幅降低）

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

- 价格数据异常 (price_depeg=true 或 volatility_spike=true):
  → 提升 +0.10 to +0.15, 验证事件真实性

- 价格数据正常但事件类型为 depeg/liquidation:
  → 降低 -0.15 to -0.25, 标记 data_conflict

【最终约束】
- 置信度范围: 0.0 - 1.0
- 如果最终置信度 < 0.4, 必须添加 confidence_low 风险标志
- **必须在 notes 中说明时效性**：
  - "初判 {preliminary.confidence:.2f} → 最终 [final_confidence:.2f]"
  - "时效性: [实时新闻 / 事后回顾 / 观点分析]"
  - "依据: [搜索/价格/记忆/时效性调整]"

- **如果是事后回顾或观点分析**:
  - 必须添加 risk_flag: "stale_event" 或 "analysis_not_news"
  - action 通常应该是 "observe" 而不是 "buy/sell"
  - 在 summary 中明确说明 "这是事后总结，交易机会可能已过"

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
