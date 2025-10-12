# Phase 1 模块化架构设计

**日期**: 2025-10-11
**问题**: LangGraph 节点方法代码量大（573行），是否需要拆分模块？
**结论**: ✅ **建议拆分** - 提高可维护性和可测试性

---

## 🤔 为什么需要拆分？

### 当前问题

1. **gemini.py 文件过大**:
   - 现有代码: ~271 行
   - LangGraph 节点: +573 行
   - **总计**: ~844 行（单个类）

2. **职责混杂**:
   - Function Calling 深度分析（现有）
   - LangGraph 工具编排（新增）
   - 记忆检索逻辑
   - Prompt 构建
   - 工具执行

3. **测试困难**:
   - 节点方法难以独立测试
   - Mock 依赖复杂

4. **可读性下降**:
   - 类定义过长
   - 逻辑跳跃频繁

---

## ✅ 推荐方案：模块化拆分

### 架构设计

```
src/ai/deep_analysis/
├── gemini.py                    # 主引擎（保持简洁）
├── base.py                      # 现有基类
├── factory.py                   # 现有工厂
├── nodes/                       # 🆕 LangGraph 节点模块
│   ├── __init__.py
│   ├── base.py                  # 节点基类
│   ├── context_gather.py        # Context Gather 节点
│   ├── tool_planner.py          # Tool Planner 节点
│   ├── tool_executor.py         # Tool Executor 节点
│   └── synthesis.py             # Synthesis 节点
├── helpers/                     # 🆕 辅助方法模块
│   ├── __init__.py
│   ├── memory.py                # 记忆检索逻辑
│   ├── prompts.py               # Prompt 构建
│   └── formatters.py            # 格式化工具
└── graph.py                     # 🆕 LangGraph 图构建
```

### 优势

✅ **职责分离**: 每个模块职责单一
✅ **可测试性**: 节点可独立测试
✅ **可维护性**: 修改某个节点不影响其他部分
✅ **可复用性**: Helper 方法可在多处复用
✅ **可扩展性**: 添加新节点（Phase 2）更容易

---

## 📦 详细模块设计

### 1. `src/ai/deep_analysis/nodes/base.py`

**职责**: 节点基类，定义通用接口

```python
"""Base class for LangGraph nodes."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseNode(ABC):
    """Abstract base class for LangGraph nodes."""

    def __init__(self, engine):
        """
        Args:
            engine: GeminiDeepAnalysisEngine instance for accessing shared resources
        """
        self._engine = engine
        self._client = engine._client
        self._memory = engine._memory
        self._config = engine._config

    @abstractmethod
    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute node logic and return state updates."""
        pass
```

---

### 2. `src/ai/deep_analysis/nodes/context_gather.py`

**职责**: 记忆收集节点（~50 行）

```python
"""Context Gather node for retrieving historical memory."""

import logging
from typing import Any, Dict

from .base import BaseNode
from ..helpers.memory import fetch_memory_entries, format_memory_evidence

logger = logging.getLogger(__name__)


class ContextGatherNode(BaseNode):
    """Node for gathering historical memory context."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Gather memory context from historical events."""
        logger.info("🧠 Context Gather: 获取历史记忆")

        entries = await fetch_memory_entries(
            engine=self._engine,
            payload=state["payload"],
            preliminary=state["preliminary"],
        )

        memory_text = format_memory_evidence(entries)
        logger.info("🧠 Context Gather: 找到 %d 条历史事件", len(entries))

        return {
            "memory_evidence": {
                "entries": entries,
                "formatted": memory_text,
                "count": len(entries),
            }
        }
```

---

### 3. `src/ai/deep_analysis/nodes/tool_planner.py`

**职责**: AI 工具决策 + 关键词生成（~120 行）

```python
"""Tool Planner node for deciding which tools to call."""

import logging
from typing import Any, Dict

from .base import BaseNode
from ..helpers.prompts import build_planner_prompt

logger = logging.getLogger(__name__)

# Event type filters
NEVER_SEARCH_EVENT_TYPES = {"macro", "governance", "airdrop", "celebrity"}
FORCE_SEARCH_EVENT_TYPES = {"hack", "regulation", "partnership"}


class ToolPlannerNode(BaseNode):
    """Node for AI-powered tool planning and keyword generation."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Decide which tools to call and generate search keywords."""
        logger.info("🤖 Tool Planner: 决策下一步工具")

        preliminary = state["preliminary"]

        # Blacklist check
        if preliminary.event_type in NEVER_SEARCH_EVENT_TYPES:
            logger.info("🤖 Tool Planner: 事件类型 '%s' 在黑名单", preliminary.event_type)
            return {"next_tools": []}

        # Whitelist check (first turn only)
        if preliminary.event_type in FORCE_SEARCH_EVENT_TYPES and state["tool_call_count"] == 0:
            logger.info("🤖 Tool Planner: 事件类型 '%s' 在白名单，强制搜索", preliminary.event_type)
            keyword = await self._generate_keywords_ai(state)
            return {"next_tools": ["search"], "search_keywords": keyword}

        # Already have search results
        if state.get("search_evidence"):
            logger.info("🤖 Tool Planner: 已有搜索结果")
            return {"next_tools": []}

        # AI decision using Function Calling
        return await self._decide_with_function_calling(state)

    async def _decide_with_function_calling(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Use Gemini Function Calling for structured decision."""
        prompt = build_planner_prompt(state, self._engine)

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
                        "description": "搜索关键词（中英文混合）",
                    },
                    "reason": {"type": "STRING", "description": "决策理由"},
                },
                "required": ["tools", "reason"],
            },
        }

        try:
            response = await self._client.generate_content_with_tools(
                messages=[{"role": "user", "content": prompt}],
                tools=[tool_definition],
            )

            if response and response.function_calls:
                decision = response.function_calls[0].args
                tools = decision.get("tools", [])
                keywords = decision.get("search_keywords", "")
                reason = decision.get("reason", "")

                logger.info(
                    "🤖 Tool Planner 决策: tools=%s, keywords='%s', 理由: %s",
                    tools,
                    keywords,
                    reason,
                )

                return {"next_tools": tools, "search_keywords": keywords}

            logger.warning("Tool Planner 未返回工具调用")
            return {"next_tools": []}

        except Exception as exc:
            logger.error("Tool Planner 执行失败: %s", exc)
            return {"next_tools": []}

    async def _generate_keywords_ai(self, state: Dict[str, Any]) -> str:
        """Generate keywords using AI for whitelist events."""
        # ... (keyword generation logic)
        pass
```

---

### 4. `src/ai/deep_analysis/nodes/tool_executor.py`

**职责**: 工具执行 + 配额检查（~80 行）

```python
"""Tool Executor node for executing planned tools."""

import logging
from typing import Any, Dict, Optional

from .base import BaseNode

logger = logging.getLogger(__name__)


class ToolExecutorNode(BaseNode):
    """Node for executing tools decided by planner."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute planned tools with quota checking."""
        tools_to_call = state.get("next_tools", [])
        logger.info("🔧 Tool Executor: 调用工具: %s", tools_to_call)

        # Check daily quota
        if not self._check_quota():
            logger.warning("⚠️ 超出每日配额")
            return {"tool_call_count": state["tool_call_count"] + 1}

        updates = {"tool_call_count": state["tool_call_count"] + 1}

        for tool_name in tools_to_call:
            if tool_name == "search":
                result = await self._execute_search(state)
                if result:
                    updates["search_evidence"] = result
            else:
                logger.warning("未知工具: %s", tool_name)

        return updates

    async def _execute_search(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Execute search tool with domain whitelisting."""
        if not self._engine._search_tool:
            logger.warning("搜索工具未初始化")
            return None

        preliminary = state["preliminary"]
        keyword = state.get("search_keywords", "").strip()

        # Fallback to hardcoded keywords
        if not keyword:
            keyword = f"{preliminary.asset} {preliminary.event_type}"
            logger.info("使用硬编码关键词: '%s'", keyword)
        else:
            logger.info("使用 AI 生成关键词: '%s'", keyword)

        # Get domain whitelist
        include_domains = None
        if hasattr(self._config, "HIGH_PRIORITY_EVENT_DOMAINS"):
            include_domains = self._config.HIGH_PRIORITY_EVENT_DOMAINS.get(
                preliminary.event_type
            )

        try:
            result = await self._engine._search_tool.fetch(
                keyword=keyword,
                max_results=5,
                include_domains=include_domains,
            )

            if result.success:
                logger.info(
                    "🔧 搜索返回 %d 条结果 (multi_source=%s)",
                    result.data.get("source_count", 0),
                    result.data.get("multi_source"),
                )
                return {
                    "success": True,
                    "data": result.data,
                    "triggered": result.triggered,
                    "confidence": result.confidence,
                }

            logger.warning("搜索失败: %s", result.error)
            return None

        except Exception as exc:
            logger.error("搜索工具异常: %s", exc)
            return None

    def _check_quota(self) -> bool:
        """Check if daily quota is exceeded."""
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date()

        if today != self._engine._tool_call_reset_date:
            self._engine._tool_call_count_today = 0
            self._engine._tool_call_reset_date = today

        if self._engine._tool_call_count_today >= self._engine._tool_call_daily_limit:
            return False

        self._engine._tool_call_count_today += 1
        return True
```

---

### 5. `src/ai/deep_analysis/nodes/synthesis.py`

**职责**: 证据综合（~60 行）

```python
"""Synthesis node for generating final signal."""

import json
import logging
from typing import Any, Dict

from .base import BaseNode
from ..helpers.prompts import build_synthesis_prompt

logger = logging.getLogger(__name__)


class SynthesisNode(BaseNode):
    """Node for synthesizing all evidence into final signal."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesize evidence and generate final signal."""
        logger.info("📊 Synthesis: 生成最终分析")

        prompt = build_synthesis_prompt(state, self._engine)
        final_json = await self._invoke_text_model(prompt)

        try:
            parsed = json.loads(final_json)
            final_conf = parsed.get("confidence", 0.0)
            prelim_conf = state["preliminary"].confidence
            logger.info("📊 Synthesis: 最终置信度 %.2f (初步 %.2f)", final_conf, prelim_conf)
        except Exception:
            logger.warning("📊 Synthesis: 无法解析最终 JSON")

        return {"final_response": final_json}

    async def _invoke_text_model(self, prompt: str) -> str:
        """Invoke Gemini for text generation."""
        messages = [{"role": "user", "content": prompt}]
        response = await self._client.generate_content_with_tools(messages, tools=None)

        if not response or not response.text:
            raise Exception("Gemini 返回空响应")

        return response.text.strip()
```

---

### 6. `src/ai/deep_analysis/helpers/memory.py`

**职责**: 记忆检索逻辑（~80 行）

```python
"""Memory retrieval helpers."""

import inspect
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def fetch_memory_entries(
    *,
    engine,
    payload: Any,
    preliminary: Any,
    limit: int = None,
) -> List[Dict[str, Any]]:
    """Independent memory retrieval helper.

    Reused in both:
    1. Context Gather node (LangGraph)
    2. _tool_fetch_memories (Function Calling)
    """
    if not engine._memory or not engine._memory.enabled:
        return []

    limit = limit or engine._memory_limit
    keywords = list(payload.keywords_hit or [])

    # Import helper functions
    from ...deep_analysis.gemini import _normalise_asset_codes, _memory_entries_to_prompt
    from src.memory.types import MemoryContext

    asset_codes = _normalise_asset_codes(preliminary.asset)

    repo = engine._memory.repository
    if repo is None:
        return []

    entries = []

    # Async repository
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
        else:
            entries = list(context) if context else []

    # Sync repository
    elif hasattr(repo, "load_entries"):
        try:
            entries = repo.load_entries(
                keywords=keywords,
                limit=limit,
                min_confidence=engine._memory_min_confidence,
            )
        except Exception as exc:
            logger.warning("本地记忆检索失败: %s", exc)
            return []

    prompt_entries = _memory_entries_to_prompt(entries)[:limit] if entries else []
    return prompt_entries


def format_memory_evidence(entries: List[Dict[str, Any]]) -> str:
    """Format memory entries for AI consumption."""
    if not entries:
        return "无历史相似事件"

    lines = []
    for i, entry in enumerate(entries, 1):
        confidence = entry.get("confidence", "N/A")
        similarity = entry.get("similarity", "N/A")
        summary = entry.get("summary", "N/A")
        lines.append(f"{i}. {summary} (置信度: {confidence}, 相似度: {similarity})")

    return "\n".join(lines)
```

---

### 7. `src/ai/deep_analysis/helpers/prompts.py`

**职责**: Prompt 构建（~200 行）

```python
"""Prompt builders for nodes."""

from typing import Any, Dict


def build_planner_prompt(state: Dict[str, Any], engine: Any) -> str:
    """Build prompt for Tool Planner node."""
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
- 搜索结果: {_format_search_evidence(search_ev)}

【决策规则】
0. ⚠️ 成本意识：每次搜索消耗配额，请谨慎决策
1. 如果已有搜索结果且 multi_source=true → 证据充分,无需再搜索
2. 如果事件类型是 hack/regulation/partnership → 需要搜索验证
3. 如果 tool_call_count >= 2 → 证据充分,无需再搜索
4. 如果记忆中已有高相似度案例 (similarity > 0.8) → 优先使用记忆，减少搜索

【关键词生成规则】（仅当决定搜索时）
1. **中英文混合**: 如果消息是中文,生成中英文混合关键词
2. **包含关键实体**: 提取具体公司名、协议名、金额等
3. **官方来源标识**: 添加 "official statement 官方声明"
4. **事件类型关键词**:
   - hack → "黑客攻击 hack exploit breach"
   - regulation → "监管政策 regulation SEC CFTC"
   - listing → "上线 listing announce"
5. **避免泛化词**: 不要使用 "新闻" "消息" 等

【当前状态】
- 已调用工具次数: {state['tool_call_count']}
- 最大调用次数: {state['max_tool_calls']}

请调用 decide_next_tools 函数返回决策和关键词。"""


def build_synthesis_prompt(state: Dict[str, Any], engine: Any) -> str:
    """Build prompt for Synthesis node."""
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
{_format_search_detail(search_ev)}

【置信度调整规则】
- 基准: Gemini Flash 初判置信度 = {preliminary.confidence}
- 搜索多源确认 (multi_source=true) AND 官方确认 (official_confirmed=true):
  → 提升 +0.15 to +0.20
- 搜索多源确认但无官方确认:
  → 提升 +0.05 to +0.10
- 搜索结果 < 3 条或无官方确认:
  → 降低 -0.10 to -0.20
- 历史记忆存在高相似度案例 (similarity > 0.8):
  → 参考历史案例最终置信度,调整 ±0.10

返回 JSON（与 SignalResult 格式一致）:
{{
  "summary": "中文摘要",
  "event_type": "{preliminary.event_type}",
  "asset": "{preliminary.asset}",
  "action": "buy|sell|observe",
  "confidence": 0.0-1.0,
  "notes": "推理依据,引用搜索来源和关键证据",
  "links": []
}}

只返回 JSON,不要其他文字。"""


def _format_search_evidence(search_ev: Dict[str, Any]) -> str:
    """Format search evidence briefly."""
    if not search_ev:
        return "无"
    data = search_ev.get("data", {})
    return f"找到 {data.get('source_count', 0)} 条结果, 多源确认={data.get('multi_source', False)}"


def _format_search_detail(search_ev: Dict[str, Any]) -> str:
    """Format search evidence in detail."""
    if not search_ev or not search_ev.get("success"):
        return "无搜索结果或搜索失败"

    data = search_ev.get("data", {})
    results = data.get("results", [])

    lines = [
        f"关键词: {data.get('keyword', 'N/A')}",
        f"结果数: {data.get('source_count', 0)}",
        f"多源确认: {data.get('multi_source', False)}",
        f"官方确认: {data.get('official_confirmed', False)}",
        "",
        "搜索结果:",
    ]

    for i, result in enumerate(results[:3], 1):
        lines.append(
            f"{i}. {result.get('title', 'N/A')} (来源: {result.get('source', 'N/A')}, 评分: {result.get('score', 0.0)})"
        )

    return "\n".join(lines)
```

---

### 8. `src/ai/deep_analysis/graph.py`

**职责**: LangGraph 图构建（~50 行）

```python
"""LangGraph graph builder for tool-enhanced deep analysis."""

import logging
from langgraph.graph import END, StateGraph

from .nodes import ContextGatherNode, ToolPlannerNode, ToolExecutorNode, SynthesisNode

logger = logging.getLogger(__name__)


def build_deep_graph(engine):
    """Build LangGraph for tool-enhanced deep analysis.

    Args:
        engine: GeminiDeepAnalysisEngine instance

    Returns:
        Compiled LangGraph
    """
    from .gemini import DeepAnalysisState

    graph = StateGraph(DeepAnalysisState)

    # Create node instances
    context_node = ContextGatherNode(engine)
    planner_node = ToolPlannerNode(engine)
    executor_node = ToolExecutorNode(engine)
    synthesis_node = SynthesisNode(engine)

    # Add nodes
    graph.add_node("context_gather", context_node.execute)
    graph.add_node("planner", planner_node.execute)
    graph.add_node("executor", executor_node.execute)
    graph.add_node("synthesis", synthesis_node.execute)

    # Define edges
    graph.set_entry_point("context_gather")
    graph.add_edge("context_gather", "planner")

    # Conditional routing
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {"executor": "executor", "synthesis": "synthesis"},
    )

    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"planner": "planner", "synthesis": "synthesis"},
    )

    graph.add_edge("synthesis", END)

    return graph.compile()


def _route_after_planner(state):
    """Router after Tool Planner."""
    if not state.get("next_tools"):
        return "synthesis"
    return "executor"


def _route_after_executor(state):
    """Router after Tool Executor."""
    if state["tool_call_count"] >= state["max_tool_calls"]:
        logger.info("达到最大工具调用次数，进入最终推理")
        return "synthesis"
    return "planner"
```

---

### 9. `src/ai/deep_analysis/gemini.py` (简化后)

**职责**: 主引擎协调器（~150 行）

```python
"""Gemini deep analysis engine with tool-enhanced flow."""

# ... (existing imports)
from .graph import build_deep_graph

class GeminiDeepAnalysisEngine(DeepAnalysisEngine):
    """Execute deep analysis via Gemini with optional tool enhancement."""

    def __init__(self, ...):
        # ... (existing init)

        # Tool-enhanced flow setup
        self._config = config or SimpleNamespace()
        self._search_tool = None

        # Daily quota tracking
        self._tool_call_daily_limit = getattr(config, "DEEP_ANALYSIS_TOOL_DAILY_LIMIT", 50)
        self._tool_call_count_today = 0
        self._tool_call_reset_date = datetime.now(timezone.utc).date()

        # Initialize search tool
        if config and getattr(config, "TOOL_SEARCH_ENABLED", False):
            from src.ai.tools import SearchTool
            try:
                self._search_tool = SearchTool(config)
                logger.info("🔍 搜索工具已初始化")
            except Exception as exc:
                logger.warning("⚠️ 搜索工具初始化失败: %s", exc)

    async def analyse(self, payload, preliminary):
        """Execute deep analysis with optional tool-enhanced flow."""
        tools_enabled = getattr(self._config, "DEEP_ANALYSIS_TOOLS_ENABLED", False)

        if not tools_enabled:
            logger.debug("工具增强流程未启用")
            return await self._analyse_with_function_calling(payload, preliminary)

        # Tool-enhanced flow with LangGraph
        max_calls = getattr(self._config, "DEEP_ANALYSIS_MAX_TOOL_CALLS", 3)

        try:
            logger.info("=== 启动 LangGraph 工具增强深度分析 ===")
            graph = build_deep_graph(self)  # Use external graph builder

            initial_state = DeepAnalysisState(
                payload=payload,
                preliminary=preliminary,
                search_evidence=None,
                memory_evidence=None,
                next_tools=[],
                search_keywords="",
                tool_call_count=0,
                max_tool_calls=max_calls,
                final_response="",
            )

            final_state = await graph.ainvoke(initial_state)
            final_payload = final_state.get("final_response")

            if not final_payload:
                raise DeepAnalysisError("LangGraph 未返回最终结果")

            result = self._parse_json(final_payload)
            logger.info("=== LangGraph 深度分析完成 ===")
            return result

        except Exception as exc:
            logger.error("LangGraph 工具编排失败，降级: %s", exc, exc_info=True)
            return await self._analyse_with_function_calling(payload, preliminary)

    async def _analyse_with_function_calling(self, payload, preliminary):
        """Traditional Function Calling implementation (backward compatible)."""
        # ... (existing implementation)
        pass

    # ... (keep existing methods: _run_tool_loop, _dispatch_tool, _tool_fetch_memories, _build_tools)
```

---

## 📊 代码量对比

| 文件 | 行数 | 职责 |
|------|------|------|
| **拆分前** | | |
| gemini.py | ~844 | 所有逻辑混杂 |
| **拆分后** | | |
| gemini.py | ~150 | 主引擎协调 |
| nodes/base.py | ~20 | 节点基类 |
| nodes/context_gather.py | ~40 | 记忆收集 |
| nodes/tool_planner.py | ~120 | AI 决策 |
| nodes/tool_executor.py | ~80 | 工具执行 |
| nodes/synthesis.py | ~60 | 证据综合 |
| helpers/memory.py | ~80 | 记忆逻辑 |
| helpers/prompts.py | ~200 | Prompt 构建 |
| graph.py | ~50 | 图构建 |
| **总计** | **~800** | **职责清晰** |

---

## ✅ 实施建议

### 方案选择

**推荐**: **模块化拆分方案**

**理由**:
1. ✅ 单个文件 < 200 行，可读性好
2. ✅ 职责分离，易于测试
3. ✅ 便于 Phase 2 扩展（添加新节点）
4. ✅ Helper 方法可复用（Function Calling + LangGraph）

### 实施步骤

1. **创建目录结构**:
   ```bash
   mkdir -p src/ai/deep_analysis/nodes
   mkdir -p src/ai/deep_analysis/helpers
   ```

2. **实施顺序**:
   - Day 1: 创建 base.py + helpers (memory, prompts)
   - Day 2: 实现 4 个节点类
   - Day 3: 创建 graph.py + 更新 gemini.py
   - Day 4: 单元测试
   - Day 5: 集成测试

3. **向后兼容**:
   - 保留 gemini.py 中的 Function Calling 逻辑
   - `DEEP_ANALYSIS_TOOLS_ENABLED=false` 时使用旧逻辑
   - 逐步迁移，降低风险

---

## 🧪 测试策略

### 单元测试

每个节点可独立测试：

```python
# tests/ai/deep_analysis/nodes/test_tool_planner.py
@pytest.mark.asyncio
async def test_tool_planner_whitelist():
    """Test whitelist event types force search."""
    engine = MockEngine()
    node = ToolPlannerNode(engine)

    state = {
        "payload": mock_payload,
        "preliminary": mock_preliminary(event_type="hack"),
        "tool_call_count": 0,
    }

    result = await node.execute(state)

    assert result["next_tools"] == ["search"]
    assert len(result["search_keywords"]) > 0
```

### 集成测试

```python
# tests/ai/deep_analysis/test_graph_integration.py
@pytest.mark.asyncio
async def test_full_langgraph_flow():
    """Test complete LangGraph flow."""
    engine = create_test_engine()
    graph = build_deep_graph(engine)

    initial_state = {...}
    final_state = await graph.ainvoke(initial_state)

    assert final_state["final_response"] is not None
```

---

## 📚 文档更新

需要更新以下文档：

1. ✅ 本文档 (`phase1_module_architecture.md`) - 模块化设计
2. 🔄 `phase1_search_tool_implementation_cn.md` - 更新实施任务
3. 🔄 `phase1_langgraph_integration_guide.md` - 更新集成步骤
4. 🔄 `README_PHASE1_IMPLEMENTATION.md` - 更新文件结构

---

## 🎯 总结

### ✅ 推荐使用模块化拆分

**优势**:
- 代码更清晰（单个文件 < 200 行）
- 职责分离，易于维护
- 可独立测试，提高质量
- 便于 Phase 2 扩展

**成本**:
- 需要创建更多文件（+9 个文件）
- 需要更新导入路径
- 需要编写更多单元测试

**ROI**: **值得投资** - 长期可维护性远超初期成本

---

**最后更新**: 2025-10-11
**决策**: ✅ 采用模块化拆分方案
**下一步**: 更新实施文档，按模块化方案重新组织代码
