"""LangGraph node implementations for tool-enhanced deep analysis.

This module contains all LangGraph node methods that will be added to
GeminiDeepAnalysisEngine. These methods are separated for clarity during development
and will be integrated into the main gemini.py file.

Usage: Copy these methods into GeminiDeepAnalysisEngine class in gemini.py
"""

# ==================== LangGraph Node Methods ====================

async def _node_context_gather(self, state):
    """Node 1: Gather historical memory context (async)."""
    logger.info("🧠 Context Gather: 获取历史记忆")

    entries = await self._fetch_memory_entries(
        payload=state["payload"],
        preliminary=state["preliminary"],
    )

    memory_text = self._format_memory_evidence(entries)
    logger.info("🧠 Context Gather: 找到 %d 条历史事件", len(entries))

    return {
        "memory_evidence": {
            "entries": entries,
            "formatted": memory_text,
            "count": len(entries),
        }
    }


async def _node_tool_planner(self, state):
    """Node 2: AI decides which tools to call and generates search keywords (async)."""
    logger.info("🤖 Tool Planner: 决策下一步工具")

    preliminary = state["preliminary"]

    # Blacklist: Skip search for certain event types (低价值、噪音多的事件类型)
    # airdrop: 空投类活动通常市值小、投机性强，不适合深度搜索
    NEVER_SEARCH_EVENT_TYPES = {"macro", "governance", "airdrop", "celebrity"}
    if preliminary.event_type in NEVER_SEARCH_EVENT_TYPES:
        logger.info("🤖 Tool Planner: 事件类型 '%s' 在黑名单，跳过搜索", preliminary.event_type)
        return {"next_tools": []}

    # Whitelist: Force search for high-priority events (first turn only)
    FORCE_SEARCH_EVENT_TYPES = {"hack", "regulation", "partnership"}
    if preliminary.event_type in FORCE_SEARCH_EVENT_TYPES and state["tool_call_count"] == 0:
        logger.info("🤖 Tool Planner: 事件类型 '%s' 在白名单，强制搜索", preliminary.event_type)
        # Generate keywords using AI
        keyword = await self._generate_search_keywords(state)
        return {"next_tools": ["search"], "search_keywords": keyword}

    # Already have search results: No need to search again
    if state.get("search_evidence"):
        logger.info("🤖 Tool Planner: 已有搜索结果，无需再搜索")
        return {"next_tools": []}

    # Use Function Calling for AI decision
    prompt = self._build_planner_prompt(state)

    # Tool definition for decision making + keyword generation
    tool_definition = {
        "name": "decide_next_tools",
        "description": "根据已有证据决定下一步需要调用的工具，并为搜索生成最优关键词",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tools": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "需要调用的工具列表,可选值: search",
                },
                "search_keywords": {
                    "type": "STRING",
                    "description": "如果需要搜索，生成最优搜索关键词（中英文混合，包含关键实体、官方来源标识）。示例：'USDC Circle depeg official statement 脱锚 官方声明'",
                },
                "reason": {
                    "type": "STRING",
                    "description": "决策理由",
                },
            },
            "required": ["tools", "reason"],
        },
    }

    try:
        response = await self._client.generate_content_with_tools(
            messages=[{"role": "user", "content": prompt}],
            tools=[tool_definition],
        )

        # Parse Function Calling result
        if response and response.function_calls:
            decision = response.function_calls[0].args
            tools = decision.get("tools", [])
            search_keywords = decision.get("search_keywords", "")
            reason = decision.get("reason", "")

            logger.info(
                "🤖 Tool Planner 决策: tools=%s, keywords='%s', 理由: %s",
                tools,
                search_keywords,
                reason,
            )

            return {
                "next_tools": tools,
                "search_keywords": search_keywords,  # AI-generated keywords
            }
        else:
            logger.warning("Tool Planner 未返回工具调用")
            return {"next_tools": []}

    except Exception as exc:
        logger.error("Tool Planner 执行失败: %s", exc)
        return {"next_tools": []}


async def _node_tool_executor(self, state):
    """Node 3: Execute tools decided by planner (async, Phase 1: search only)."""
    tools_to_call = state.get("next_tools", [])
    logger.info("🔧 Tool Executor: 调用工具: %s", tools_to_call)

    # Check daily quota
    if not self._check_tool_quota():
        logger.warning("⚠️ 超出每日配额，跳过工具调用")
        return {"tool_call_count": state["tool_call_count"] + 1}

    updates = {"tool_call_count": state["tool_call_count"] + 1}

    for tool_name in tools_to_call:
        if tool_name != "search":
            logger.warning("未知工具: %s", tool_name)
            continue

        if not self._search_tool:
            logger.warning("搜索工具未初始化，跳过执行")
            continue

        result = await self._execute_search_tool(state)
        if result:
            updates["search_evidence"] = result

    return updates


async def _node_synthesis(self, state):
    """Node 4: Synthesize all evidence to generate final signal (async)."""
    logger.info("📊 Synthesis: 生成最终分析")

    prompt = self._build_synthesis_prompt(state)
    final_json = await self._invoke_text_model(prompt)

    try:
        import json
        import re

        # Try to extract JSON from markdown code blocks if present
        json_text = final_json
        if "```json" in final_json:
            match = re.search(r'```json\s*\n(.*?)\n```', final_json, re.DOTALL)
            if match:
                json_text = match.group(1)
        elif "```" in final_json:
            match = re.search(r'```\s*\n(.*?)\n```', final_json, re.DOTALL)
            if match:
                json_text = match.group(1)

        parsed = json.loads(json_text.strip())
        final_conf = parsed.get("confidence", 0.0)
        prelim_conf = state["preliminary"].confidence
        logger.info("📊 Synthesis: 最终置信度 %.2f (初步 %.2f)", final_conf, prelim_conf)
    except json.JSONDecodeError as exc:
        logger.error("📊 Synthesis: JSON 解析失败 - %s", exc)
        logger.error("📊 原始响应 (前500字符): %s", final_json[:500])
    except Exception as exc:  # pragma: no cover - tolerate parsing failures
        logger.error("📊 Synthesis: 其他错误 - %s", exc)

    return {"final_response": final_json}


# ==================== Router Methods ====================


def _route_after_planner(self, state):
    """Router after Tool Planner: synthesis if no tools, else executor."""
    if not state.get("next_tools"):
        return "synthesis"
    return "executor"


def _route_after_executor(self, state):
    """Router after Tool Executor: synthesis if max turns, else planner."""
    if state["tool_call_count"] >= state["max_tool_calls"]:
        logger.info("达到最大工具调用次数 (3)，进入最终推理")
        return "synthesis"
    return "planner"


# ==================== Graph Builder ====================


def _build_deep_graph(self):
    """Build LangGraph for tool-enhanced deep analysis."""
    from langgraph.graph import END, StateGraph

    graph = StateGraph(DeepAnalysisState)

    # Add nodes
    graph.add_node("context_gather", self._node_context_gather)
    graph.add_node("planner", self._node_tool_planner)
    graph.add_node("executor", self._node_tool_executor)
    graph.add_node("synthesis", self._node_synthesis)

    # Define edges
    graph.set_entry_point("context_gather")
    graph.add_edge("context_gather", "planner")

    # Conditional routing
    graph.add_conditional_edges(
        "planner",
        self._route_after_planner,
        {
            "executor": "executor",
            "synthesis": "synthesis",
        },
    )

    graph.add_conditional_edges(
        "executor",
        self._route_after_executor,
        {
            "planner": "planner",
            "synthesis": "synthesis",
        },
    )

    graph.add_edge("synthesis", END)

    return graph.compile()


# ==================== Helper Methods ====================


async def _fetch_memory_entries(
    self,
    *,
    payload,
    preliminary,
    limit=None,
):
    """Independent memory retrieval helper, reused in both Context Gather node and Function Calling tool."""
    if not self._memory or not self._memory.enabled:
        return []

    limit = limit or self._memory_limit
    keywords = list(payload.keywords_hit or [])
    asset_codes = _normalise_asset_codes(preliminary.asset)

    repo = self._memory.repository
    if repo is None:
        return []

    entries = []

    if hasattr(repo, "fetch_memories") and inspect.iscoroutinefunction(repo.fetch_memories):
        kwargs = {"embedding": None, "asset_codes": asset_codes}
        parameters = inspect.signature(repo.fetch_memories).parameters
        if "keywords" in parameters:
            kwargs["keywords"] = keywords
        try:
            context = await repo.fetch_memories(**kwargs)
        except Exception as exc:
            logger.warning("记忆检索失败: %s", exc)
            return []
        if isinstance(context, MemoryContext):
            entries = list(context.entries)
        elif isinstance(context, Iterable):
            entries = list(context)
    elif hasattr(repo, "fetch_memories"):
        kwargs = {"embedding": None, "asset_codes": asset_codes}
        parameters = inspect.signature(repo.fetch_memories).parameters
        if "keywords" in parameters:
            kwargs["keywords"] = keywords
        try:
            result = repo.fetch_memories(**kwargs)
        except Exception as exc:
            logger.warning("记忆检索失败: %s", exc)
            return []
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, MemoryContext):
            entries = list(result.entries)
        elif isinstance(result, Iterable):
            entries = list(result)
    elif hasattr(repo, "load_entries"):
        try:
            entries = repo.load_entries(
                keywords=keywords,
                limit=limit,
                min_confidence=self._memory_min_confidence,
            )
        except Exception as exc:
            logger.warning("本地记忆检索失败: %s", exc)
            return []

    prompt_entries = _memory_entries_to_prompt(entries)[:limit] if entries else []
    return prompt_entries


def _format_memory_evidence(self, entries):
    """Format memory entries for AI consumption."""
    if not entries:
        return "无历史相似事件"

    lines = []
    for i, entry in enumerate(entries, 1):
        confidence = entry.get("confidence", "N/A")
        similarity = entry.get("similarity", "N/A")
        summary = entry.get("summary", "N/A")
        lines.append(f"{i}. {summary} (置信度: {confidence}, 相似度: {similarity})")

    return "\\n".join(lines)


async def _generate_search_keywords(self, state):
    """Generate search keywords using AI for whitelist events."""
    payload = state["payload"]
    preliminary = state["preliminary"]

    prompt = f"""你是关键词生成专家，根据消息内容生成最优搜索关键词。

【消息内容】{payload.text}
【事件类型】{preliminary.event_type}
【资产】{preliminary.asset}

【关键词生成规则】
1. **中英文混合**: 中文消息生成中英文混合关键词
2. **包含关键实体**: 提取公司名、协议名、金额等
3. **官方来源标识**: 添加 "official statement 官方声明"
4. **事件类型关键词**: hack → "hack exploit 攻击", regulation → "regulation 监管", listing → "listing 上线"
5. **避免泛化词**: 不使用 "新闻" "消息" 等

直接返回关键词字符串，不要其他解释。"""

    try:
        text = await self._invoke_text_model(prompt)
        keyword = text.strip()
        logger.info("🤖 AI 生成关键词: '%s'", keyword)
        return keyword
    except Exception as exc:
        logger.warning("AI 关键词生成失败，使用降级方案: %s", exc)
        return f"{preliminary.asset} {preliminary.event_type}"


def _build_planner_prompt(self, state):
    """Build prompt for Tool Planner with keyword generation rules."""
    payload = state["payload"]
    preliminary = state["preliminary"]
    memory_ev = state.get("memory_evidence", {})
    search_ev = state.get("search_evidence", {})

    return f"""你是工具调度专家,判断是否需要搜索新闻验证,并生成最优搜索关键词。

【消息内容】{payload.text}
【消息语言】{getattr(payload, 'language', '未知')}
【事件类型】{preliminary.event_type}
【资产】{preliminary.asset}
【初步置信度】{preliminary.confidence}

【已有证据】
- 历史记忆: {memory_ev.get('formatted', '无')}
- 搜索结果: {self._format_search_evidence(search_ev)}

【决策规则】
0. ⚠️ 成本意识：每次搜索消耗配额，请谨慎决策
1. 如果已有搜索结果且 multi_source=true → 证据充分,无需再搜索
2. 如果事件类型是 hack/regulation/partnership → 需要搜索验证
3. 如果 tool_call_count >= 2 → 证据充分,无需再搜索
4. 如果是数值类事件 (depeg/liquidation) → 暂不需要搜索（第一阶段限制）
5. 如果记忆中已有高相似度案例 (similarity > 0.8) → 优先使用记忆，减少搜索

【关键词生成规则】（仅当决定搜索时）
1. **中英文混合**: 如果消息是中文,生成中英文混合关键词,提高搜索覆盖率
   示例: "比特币 Bitcoin ETF 批准 approval"

2. **包含关键实体**: 提取消息中的具体公司名、协议名、金额等
   示例: "Circle USDC $3B depeg"

3. **官方来源标识**: 对 hack/regulation/partnership 事件,添加官方关键词
   - 中文: "官方声明 官方公告"
   - 英文: "official statement announcement"

4. **事件类型关键词**:
   - hack → "黑客攻击 hack exploit breach"
   - regulation → "监管政策 regulation SEC CFTC"
   - listing → "上线 listing announce"
   - partnership → "合作 partnership collaboration"

5. **避免泛化词**: 不要使用 "新闻" "消息" "报道" 等低价值词

【示例】
- 消息: "Circle 确认 USDC 储备安全,脱锚已恢复"
  → 关键词: "USDC Circle depeg official statement 脱锚 官方声明"

- 消息: "XXX DeFi 协议遭受闪电贷攻击,损失 $50M"
  → 关键词: "XXX protocol flash loan hack exploit $50M 攻击"

- 消息: "SEC 批准比特币现货 ETF,将于下周开始交易"
  → 关键词: "Bitcoin spot ETF SEC approval 比特币 现货 批准"

【当前状态】
- 已调用工具次数: {state['tool_call_count']}
- 最大调用次数: {state['max_tool_calls']}

请调用 decide_next_tools 函数返回决策和关键词。"""


def _build_synthesis_prompt(self, state):
    """Build prompt for Synthesis node with quantified confidence adjustment rules."""
    payload = state["payload"]
    preliminary = state["preliminary"]
    memory_ev = state.get("memory_evidence", {})
    search_ev = state.get("search_evidence", {})

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
{memory_ev.get('formatted', '无历史相似事件')}

【搜索验证】
{self._format_search_detail(search_ev)}

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
- 在 notes 中说明: "初判 {preliminary.confidence:.2f} → 最终 {{final_confidence:.2f}}, 依据: [搜索/记忆/冲突]"

返回 JSON（与 SignalResult 格式一致）:
{{
  "summary": "中文摘要",
  "event_type": "{preliminary.event_type}",
  "asset": "{preliminary.asset}",
  "asset_name": "{getattr(preliminary, 'asset_name', '')}",
  "action": "buy|sell|observe",
  "direction": "long|short|neutral",
  "confidence": 0.0-1.0,
  "strength": "low|medium|high",
  "timeframe": "short|medium|long",
  "risk_flags": [],
  "notes": "推理依据,引用搜索来源和关键证据",
  "links": []
}}

只返回 JSON,不要其他文字。"""


async def _execute_search_tool(self, state):
    """Execute SearchTool and convert to LangGraph state format."""
    preliminary = state["preliminary"]

    # Prioritize AI-generated keywords
    keyword = state.get("search_keywords", "").strip()
    keyword_source = "AI生成"

    # Fallback: If AI didn't generate keywords, use basic concatenation
    if not keyword:
        keyword = f"{preliminary.asset} {preliminary.event_type}"
        if preliminary.event_type in ["hack", "regulation"]:
            keyword += " news official"
        keyword_source = "硬编码降级"

    # Get domain whitelist for high-priority events
    include_domains = None
    event_type = preliminary.event_type
    if hasattr(self._config, "HIGH_PRIORITY_EVENT_DOMAINS"):
        include_domains = self._config.HIGH_PRIORITY_EVENT_DOMAINS.get(event_type)

    logger.info(
        "🔧 调用搜索工具: keyword='%s' (来源: %s), domains=%s",
        keyword,
        keyword_source,
        include_domains,
    )

    try:
        result = await self._search_tool.fetch(
            keyword=keyword,
            max_results=5,
            include_domains=include_domains,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("搜索工具执行失败: %s", exc)
        return None

    if not result.success:
        logger.warning("🔧 搜索工具调用失败: %s", result.error)
        return None

    logger.info(
        "🔧 搜索返回 %d 条结果 (multi_source=%s, official=%s)",
        result.data.get("source_count", 0),
        result.data.get("multi_source"),
        result.data.get("official_confirmed"),
    )

    return {
        "success": True,
        "data": result.data,
        "triggered": result.triggered,
        "confidence": result.confidence,
    }


async def _invoke_text_model(self, prompt):
    """Unified text generation call, reusing Function Calling client."""
    messages = [{"role": "user", "content": prompt}]
    response = await self._client.generate_content_with_tools(messages, tools=None)

    if not response or not response.text:
        raise DeepAnalysisError("Gemini 返回空响应")

    return response.text.strip()


def _format_search_evidence(self, search_ev):
    """Format search evidence for display."""
    if not search_ev:
        return "无"

    data = search_ev.get("data", {})
    return f"找到 {data.get('source_count', 0)} 条结果, 多源确认={data.get('multi_source', False)}, 官方确认={data.get('official_confirmed', False)}"


def _format_search_detail(self, search_ev):
    """Format search evidence in detail for Synthesis."""
    if not search_ev or not search_ev.get("success"):
        return "无搜索结果或搜索失败"

    data = search_ev.get("data", {})
    results = data.get("results", [])

    lines = [
        f"关键词: {data.get('keyword', 'N/A')}",
        f"结果数: {data.get('source_count', 0)}",
        f"多源确认: {data.get('multi_source', False)}",
        f"官方确认: {data.get('official_confirmed', False)}",
        f"情绪分析: {data.get('sentiment', {})}",
        "",
        "搜索结果:",
    ]

    for i, result in enumerate(results[:3], 1):  # Show first 3 results
        lines.append(
            f"{i}. {result.get('title', 'N/A')} (来源: {result.get('source', 'N/A')}, 评分: {result.get('score', 0.0)})"
        )

    return "\\n".join(lines)


def _check_tool_quota(self):
    """Check if daily tool quota is exceeded."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()

    # Reset counter on new day
    if today != self._tool_call_reset_date:
        self._tool_call_count_today = 0
        self._tool_call_reset_date = today

    # Check quota
    if self._tool_call_count_today >= self._tool_call_daily_limit:
        logger.warning(
            "⚠️ 今日工具调用配额已用尽 (%d/%d)",
            self._tool_call_count_today,
            self._tool_call_daily_limit,
        )
        return False

    self._tool_call_count_today += 1
    return True
