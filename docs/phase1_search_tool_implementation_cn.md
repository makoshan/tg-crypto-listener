# 第一阶段：搜索工具集成 - 实施指南

## 概述

本文档提供将 Tavily 搜索工具集成到深度分析流程的分步实施指南。这是多工具集成方案的第一阶段，重点是构建 LangGraph 基础框架并添加新闻搜索验证能力。

**时间线**: 1-2 周
**目标**: 搭建 LangGraph 子图骨架，实现 Tavily 搜索工具用于新闻验证
**状态**: 未开始

---

## 为什么选择 Tavily 搜索？

- **Google Custom Search** 免费配额：仅 100 次查询/天（生产环境不够用）
- **Tavily 优势**：
  - 专为 AI 应用优化，返回结构化响应
  - 免费层：1,000 次查询/月
  - Pro 层：$20/月 无限量
  - 返回标题、摘要、相关性评分和来源可信度
  - 平均延迟：1-2 秒
  - 简单 API，单次调用返回多源结果

---

## 架构变更

### 改动范围

**需修改的文件**：
- `src/ai/deep_analysis/gemini.py`: 在 `analyse()` 方法中添加 LangGraph 子图
- `src/ai/tools/` 目录下的新文件：工具实现

**不改动的文件**：
- 主流程（`src/listener.py`, `src/pipeline/langgraph_pipeline.py`, `src/ai/signal_engine.py`）
- 所有现有流程保持不变

### 触发条件

与现有深度分析逻辑相同：
- Gemini Flash 初步分析 `confidence >= HIGH_VALUE_CONFIDENCE_THRESHOLD`（默认 0.75）
- 或 `event_type` 属于高价值类型（depeg、liquidation、hack）
- 排除低价值类型（macro、other、airdrop、governance、celebrity、scam_alert）

### 流程图

```
现有流程（不变）:
listener → langgraph_pipeline → _node_ai_signal → AiSignalEngine.analyse()
                                                          ↓
                                              Gemini Flash 初步分析
                                                          ↓
                                    检查 is_high_value_signal() (signal_engine.py:528-540)
                                                          ↓
                                          [新增] DeepAnalysisGraph 子图
                                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    ↓                                                     ↓
        Context Gather (记忆) → Tool Planner (AI决策) → Tool Executor (调API)
                    ↑                                                     ↓
                    └─────────────────── 路由器 ←────────────────────────┘
                                          ↓ (最多 3 轮)
                                    Synthesis (最终推理)
                                          ↓
                                    输出最终信号
```

---

## LangGraph 状态设计

### 状态对象 (DeepAnalysisState)

```python
from typing import TypedDict, Optional
from src.db.models import EventPayload
from src.ai.signal_engine import SignalResult

class DeepAnalysisState(TypedDict, total=False):
    # 输入
    payload: EventPayload                # 原始消息载荷
    preliminary: SignalResult             # Gemini Flash 初步结果

    # 证据槽位（第一阶段：仅搜索 + 记忆）
    search_evidence: Optional[dict]       # 搜索结果
    memory_evidence: Optional[dict]       # 历史相似事件

    # 控制流
    next_tools: list[str]                 # ["search"] 或 []
    tool_call_count: int                  # 0-3
    max_tool_calls: int                   # 固定为 3

    # 输出
    final_response: str                   # JSON 字符串（最终信号）
```

---

## 实施任务

### 第 1 天：工具基础架构

#### 任务 1.1：创建工具目录结构

```bash
mkdir -p src/ai/tools
touch src/ai/tools/__init__.py
touch src/ai/tools/base.py
touch src/ai/tools/search_fetcher.py
touch src/ai/tools/exceptions.py
```

#### 任务 1.2：实现工具基类

**文件**: `src/ai/tools/base.py`

```python
from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod
from datetime import datetime, timezone

@dataclass
class ToolResult:
    """标准化工具结果格式"""
    source: str              # 工具来源（如 "Tavily"）
    timestamp: str           # ISO 8601 时间戳
    success: bool            # 调用是否成功
    data: dict              # 结构化数据
    triggered: bool          # 是否触发异常阈值
    confidence: float        # 结果可信度（0.0-1.0）
    error: Optional[str] = None  # 错误信息（如果失败）

    @staticmethod
    def _format_timestamp() -> str:
        """返回当前 UTC 时间戳（ISO 8601 格式）"""
        return datetime.now(timezone.utc).isoformat()

class BaseTool(ABC):
    """所有工具的抽象基类"""

    def __init__(self, config):
        self._config = config
        self._timeout = getattr(config, 'DEEP_ANALYSIS_TOOL_TIMEOUT', 10)

    @abstractmethod
    async def fetch(self, **kwargs) -> ToolResult:
        """从工具 API 获取数据"""
        pass

    def _handle_timeout(self, error: Exception) -> ToolResult:
        """标准超时错误处理"""
        return ToolResult(
            source=self.__class__.__name__,
            timestamp=ToolResult._format_timestamp(),
            success=False,
            data={},
            triggered=False,
            confidence=0.0,
            error=f"timeout: {str(error)}"
        )
```

**文件**: `src/ai/tools/exceptions.py`

```python
class ToolFetchError(Exception):
    """工具获取错误的基类"""
    pass

class ToolTimeoutError(ToolFetchError):
    """工具 API 超时"""
    pass

class ToolRateLimitError(ToolFetchError):
    """工具 API 超出速率限制"""
    pass
```

#### 任务 1.3：实现 Tavily 搜索工具

**文件**: `src/ai/tools/search_fetcher.py`

```python
import httpx
from typing import Optional
from .base import BaseTool, ToolResult
from .exceptions import ToolTimeoutError, ToolRateLimitError

class TavilySearchFetcher(BaseTool):
    """Tavily 搜索 API 集成，用于新闻验证"""

    API_ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, config):
        super().__init__(config)
        self._api_key = getattr(config, 'TAVILY_API_KEY', None)
        self._max_results = getattr(config, 'SEARCH_MAX_RESULTS', 5)
        self._multi_source_threshold = getattr(config, 'SEARCH_MULTI_SOURCE_THRESHOLD', 3)
        self._include_domains = self._parse_domains(
            getattr(config, 'SEARCH_INCLUDE_DOMAINS', '')
        )

        if not self._api_key:
            raise ValueError("TAVILY_API_KEY 未配置")

    @staticmethod
    def _parse_domains(domains_str: str) -> list[str]:
        """解析逗号分隔的域名列表"""
        if not domains_str:
            return ["coindesk.com", "theblock.co", "cointelegraph.com"]
        return [d.strip() for d in domains_str.split(',') if d.strip()]

    async def fetch(self, keyword: str, max_results: Optional[int] = None) -> ToolResult:
        """
        从 Tavily API 获取搜索结果

        参数:
            keyword: 搜索查询词
            max_results: 覆盖默认最大结果数

        返回:
            包含搜索数据的 ToolResult
        """
        max_results = max_results or self._max_results

        request_body = {
            "api_key": self._api_key,
            "query": keyword,
            "max_results": max_results,
            "search_depth": "basic",
            "include_domains": self._include_domains,
            "include_answer": False
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self.API_ENDPOINT,
                    json=request_body
                )

                if response.status_code == 429:
                    raise ToolRateLimitError("Tavily API 超出速率限制")

                response.raise_for_status()
                data = response.json()

                return self._parse_response(data, keyword)

        except httpx.TimeoutException as e:
            return self._handle_timeout(e)
        except ToolRateLimitError as e:
            return ToolResult(
                source="Tavily",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="rate_limit"
            )
        except Exception as e:
            return ToolResult(
                source="Tavily",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=str(e)
            )

    def _parse_response(self, data: dict, keyword: str) -> ToolResult:
        """将 Tavily API 响应解析为 ToolResult"""
        results = data.get('results', [])

        # 检查多源一致性
        multi_source = len(results) >= self._multi_source_threshold

        # 检查官方确认
        official_confirmed = self._check_official_confirmation(results)

        # 简单情绪分析
        sentiment = self._analyze_sentiment(results)

        # 构建结构化数据
        formatted_results = [
            {
                "title": r.get("title", ""),
                "source": self._extract_domain(r.get("url", "")),
                "url": r.get("url", ""),
                "score": r.get("score", 0.0)
            }
            for r in results
        ]

        tool_data = {
            "keyword": keyword,
            "results": formatted_results,
            "multi_source": multi_source,
            "official_confirmed": official_confirmed,
            "sentiment": sentiment,
            "source_count": len(results)
        }

        # 如果多源确认且官方确认，则触发
        triggered = multi_source and official_confirmed

        # 基于结果质量的置信度
        confidence = self._calculate_confidence(results, multi_source, official_confirmed)

        return ToolResult(
            source="Tavily",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data=tool_data,
            triggered=triggered,
            confidence=confidence
        )

    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 中提取域名"""
        from urllib.parse import urlparse
        return urlparse(url).netloc

    def _check_official_confirmation(self, results: list[dict]) -> bool:
        """检查是否有结果包含官方声明关键词"""
        official_keywords = [
            "官方", "声明", "公告", "official", "statement",
            "announcement", "confirmed", "press release"
        ]

        for result in results:
            title = result.get("title", "").lower()
            content = result.get("content", "").lower()

            for keyword in official_keywords:
                if keyword in title or keyword in content:
                    return True

        return False

    def _analyze_sentiment(self, results: list[dict]) -> dict:
        """基于关键词的简单情绪分析"""
        panic_keywords = ["暴跌", "崩盘", "恐慌", "hack", "exploit", "crash", "dump"]
        neutral_keywords = ["观察", "等待", "监控", "watch", "monitor", "observe"]
        optimistic_keywords = ["恢复", "稳定", "反弹", "recovery", "stable", "bounce"]

        panic_count = 0
        neutral_count = 0
        optimistic_count = 0

        for result in results:
            text = (result.get("title", "") + " " + result.get("content", "")).lower()

            for keyword in panic_keywords:
                if keyword in text:
                    panic_count += 1
            for keyword in neutral_keywords:
                if keyword in text:
                    neutral_count += 1
            for keyword in optimistic_keywords:
                if keyword in text:
                    optimistic_count += 1

        total = panic_count + neutral_count + optimistic_count
        if total == 0:
            return {"panic": 0.33, "neutral": 0.34, "optimistic": 0.33}

        return {
            "panic": round(panic_count / total, 2),
            "neutral": round(neutral_count / total, 2),
            "optimistic": round(optimistic_count / total, 2)
        }

    def _calculate_confidence(
        self,
        results: list[dict],
        multi_source: bool,
        official_confirmed: bool
    ) -> float:
        """基于结果质量计算置信度"""
        if not results:
            return 0.0

        # 从平均分数得出基础置信度
        avg_score = sum(r.get("score", 0.0) for r in results) / len(results)
        confidence = avg_score

        # 如果多源确认则提升
        if multi_source:
            confidence = min(1.0, confidence + 0.1)

        # 如果官方确认则提升
        if official_confirmed:
            confidence = min(1.0, confidence + 0.15)

        return round(confidence, 2)
```

**文件**: `src/ai/tools/__init__.py`

```python
from .base import BaseTool, ToolResult
from .search_fetcher import TavilySearchFetcher
from .exceptions import ToolFetchError, ToolTimeoutError, ToolRateLimitError

__all__ = [
    'BaseTool',
    'ToolResult',
    'TavilySearchFetcher',
    'ToolFetchError',
    'ToolTimeoutError',
    'ToolRateLimitError',
]
```

#### 任务 1.4：单元测试

**文件**: `tests/ai/tools/test_search_fetcher.py`

```python
import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.ai.tools.search_fetcher import TavilySearchFetcher
from src.ai.tools.exceptions import ToolRateLimitError

@pytest.fixture
def mock_config():
    config = Mock()
    config.TAVILY_API_KEY = "test-api-key"
    config.SEARCH_MAX_RESULTS = 5
    config.SEARCH_MULTI_SOURCE_THRESHOLD = 3
    config.SEARCH_INCLUDE_DOMAINS = "coindesk.com,theblock.co"
    config.DEEP_ANALYSIS_TOOL_TIMEOUT = 10
    return config

@pytest.fixture
def fetcher(mock_config):
    return TavilySearchFetcher(mock_config)

@pytest.mark.asyncio
async def test_successful_search(fetcher):
    """测试成功的 API 调用"""
    mock_response = {
        "results": [
            {
                "title": "USDC 脱锚至 $0.98",
                "url": "https://coindesk.com/test",
                "content": "Circle 官方声明确认...",
                "score": 0.95
            },
            {
                "title": "市场恐慌，USDC 失去锚定",
                "url": "https://theblock.co/test",
                "content": "暴跌持续...",
                "score": 0.88
            }
        ]
    }

    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_response

        result = await fetcher.fetch("USDC depeg")

        assert result.success is True
        assert result.source == "Tavily"
        assert result.data["keyword"] == "USDC depeg"
        assert result.data["source_count"] == 2
        assert result.data["official_confirmed"] is True
        assert result.confidence > 0.8

@pytest.mark.asyncio
async def test_rate_limit_error(fetcher):
    """测试 API 速率限制处理"""
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 429

        result = await fetcher.fetch("test query")

        assert result.success is False
        assert result.error == "rate_limit"

@pytest.mark.asyncio
async def test_timeout_error(fetcher):
    """测试超时处理"""
    import httpx

    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("连接超时")

        result = await fetcher.fetch("test query")

        assert result.success is False
        assert "timeout" in result.error

@pytest.mark.asyncio
async def test_multi_source_detection(fetcher):
    """测试多源一致性逻辑"""
    mock_response = {
        "results": [
            {"title": f"新闻 {i}", "url": f"https://source{i}.com/test", "score": 0.9}
            for i in range(5)
        ]
    }

    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_response

        result = await fetcher.fetch("test")

        assert result.data["multi_source"] is True  # >= 3 个来源

@pytest.mark.asyncio
async def test_official_confirmation_detection(fetcher):
    """测试官方声明检测"""
    mock_response = {
        "results": [
            {
                "title": "Circle 官方公告",
                "url": "https://coindesk.com/test",
                "content": "Circle 官方声明...",
                "score": 0.95
            }
        ]
    }

    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_response

        result = await fetcher.fetch("test")

        assert result.data["official_confirmed"] is True
```

---

### 第 2 天：LangGraph 状态对象与节点骨架

#### 任务 2.1：定义状态对象

在 `src/ai/deep_analysis/gemini.py` 文件顶部添加：

```python
from typing import TypedDict, Optional

class DeepAnalysisState(TypedDict, total=False):
    # 输入
    payload: 'EventPayload'
    preliminary: 'SignalResult'

    # 证据槽位（第一阶段：仅搜索 + 记忆）
    search_evidence: Optional[dict]
    memory_evidence: Optional[dict]

    # 控制流
    next_tools: list[str]        # ["search"] 或 []
    tool_call_count: int         # 0-3
    max_tool_calls: int          # 固定为 3

    # 输出
    final_response: str          # JSON 字符串
```

#### 任务 2.2：实现节点方法骨架

添加到 `GeminiDeepAnalysisEngine` 类：

```python
def _node_context_gather(self, state: DeepAnalysisState) -> dict:
    """
    节点 1：收集历史记忆上下文
    复用现有的 _tool_fetch_memories 逻辑
    """
    # TODO: 在第 3 天实现
    pass

def _node_tool_planner(self, state: DeepAnalysisState) -> dict:
    """
    节点 2：AI 决定接下来调用哪些工具
    第一阶段的简化 prompt（仅搜索决策）
    """
    # TODO: 在第 3 天实现
    pass

def _node_tool_executor(self, state: DeepAnalysisState) -> dict:
    """
    节点 3：执行 planner 决定的工具
    第一阶段：仅处理搜索工具
    """
    # TODO: 在第 3 天实现
    pass

def _node_synthesis(self, state: DeepAnalysisState) -> dict:
    """
    节点 4：综合所有证据生成最终信号
    """
    # TODO: 在第 3 天实现
    pass
```

#### 任务 2.3：实现路由器方法

```python
def _route_after_planner(self, state: DeepAnalysisState) -> str:
    """
    Tool Planner 之后的路由：
    - 如果 next_tools 为空 → "synthesis"
    - 否则 → "executor"
    """
    if not state.get("next_tools"):
        return "synthesis"
    return "executor"

def _route_after_executor(self, state: DeepAnalysisState) -> str:
    """
    Tool Executor 之后的路由：
    - 如果 tool_call_count >= max_tool_calls → "synthesis"
    - 否则 → "planner"（下一轮）
    """
    if state["tool_call_count"] >= state["max_tool_calls"]:
        logger.info("达到最大工具调用次数 (3)，进入最终推理")
        return "synthesis"
    return "planner"
```

---

### 第 3-4 天：实现 LangGraph 子图

#### 任务 3.1：构建图结构

添加到 `GeminiDeepAnalysisEngine`：

```python
def _build_deep_graph(self):
    """构建用于工具增强深度分析的 LangGraph"""
    from langgraph.graph import StateGraph, END

    graph = StateGraph(DeepAnalysisState)

    # 添加节点
    graph.add_node("context_gather", self._node_context_gather)
    graph.add_node("planner", self._node_tool_planner)
    graph.add_node("executor", self._node_tool_executor)
    graph.add_node("synthesis", self._node_synthesis)

    # 定义边
    graph.set_entry_point("context_gather")
    graph.add_edge("context_gather", "planner")

    # 条件路由
    graph.add_conditional_edges(
        "planner",
        self._route_after_planner,
        {
            "executor": "executor",
            "synthesis": "synthesis"
        }
    )

    graph.add_conditional_edges(
        "executor",
        self._route_after_executor,
        {
            "planner": "planner",
            "synthesis": "synthesis"
        }
    )

    graph.add_edge("synthesis", END)

    return graph.compile()
```

#### 任务 3.2：实现 Context Gather 节点

```python
def _node_context_gather(self, state: DeepAnalysisState) -> dict:
    """
    收集历史记忆上下文
    复用现有的 _tool_fetch_memories 逻辑
    """
    logger.info("🧠 Context Gather: 获取历史记忆")

    # 复用现有的记忆获取逻辑
    memory_entries = self._tool_fetch_memories({
        "payload": state["payload"],
        "preliminary": state["preliminary"]
    })

    # 格式化为简单文本
    memory_text = self._format_memory_evidence(memory_entries)

    logger.info(f"🧠 Context Gather: 找到 {len(memory_entries)} 条历史事件")

    return {
        "memory_evidence": {
            "entries": memory_entries,
            "formatted": memory_text,
            "count": len(memory_entries)
        }
    }

def _format_memory_evidence(self, entries: list) -> str:
    """格式化记忆条目供 AI 使用"""
    if not entries:
        return "无历史相似事件"

    lines = []
    for i, entry in enumerate(entries, 1):
        confidence = getattr(entry, 'confidence', 'N/A')
        similarity = getattr(entry, 'similarity', 'N/A')
        summary = getattr(entry, 'summary', 'N/A')
        lines.append(f"{i}. {summary} (置信度: {confidence}, 相似度: {similarity})")

    return "\n".join(lines)
```

#### 任务 3.3：实现 Tool Planner 节点

```python
def _node_tool_planner(self, state: DeepAnalysisState) -> dict:
    """
    AI 决定是否调用搜索工具
    第一阶段：简化决策（仅搜索）
    """
    logger.info("🤖 Tool Planner: 决策下一步工具")

    # 构建决策 prompt
    prompt = self._build_planner_prompt(state)

    # 调用 Gemini 做决策
    response = self._client.generate_content(prompt)
    decision_text = response.text.strip()

    # 解析 JSON 决策
    import json
    try:
        decision = json.loads(decision_text)
        tools = decision.get("tools", [])
        reason = decision.get("reason", "")

        logger.info(f"🤖 Tool Planner 决策: {tools}, 理由: {reason}")

        return {"next_tools": tools}

    except json.JSONDecodeError:
        logger.warning(f"无法解析 planner 决策: {decision_text}")
        return {"next_tools": []}

def _build_planner_prompt(self, state: DeepAnalysisState) -> str:
    """构建工具规划 prompt"""
    payload = state["payload"]
    preliminary = state["preliminary"]
    memory_ev = state.get("memory_evidence", {})
    search_ev = state.get("search_evidence", {})

    return f"""你是工具调度专家,判断是否需要搜索新闻验证。

【消息内容】{payload.text}
【事件类型】{preliminary.event_type}
【资产】{preliminary.asset}
【初步置信度】{preliminary.confidence}

【已有证据】
- 历史记忆: {memory_ev.get('formatted', '无')}
- 搜索结果: {self._format_search_evidence(search_ev)}

【决策规则】
1. 如果事件类型是 hack/regulation/partnership/celebrity → 需要搜索验证
2. 如果已有搜索结果且 multi_source=true → 证据充分,无需再搜索
3. 如果 tool_call_count >= 2 → 证据充分,无需再搜索
4. 如果是数值类事件 (depeg/liquidation) → 暂不需要搜索（第一阶段限制）

【当前状态】
- 已调用工具次数: {state['tool_call_count']}
- 最大调用次数: {state['max_tool_calls']}

返回 JSON:
- 需要搜索: {{"tools": ["search"], "reason": "传闻类事件需多源验证"}}
- 无需搜索: {{"tools": [], "reason": "已有充分证据"}}

只返回 JSON,不要其他文字。"""

def _format_search_evidence(self, search_ev: dict) -> str:
    """格式化搜索证据用于显示"""
    if not search_ev:
        return "无"

    data = search_ev.get("data", {})
    return f"找到 {data.get('source_count', 0)} 条结果, 多源确认={data.get('multi_source', False)}, 官方确认={data.get('official_confirmed', False)}"
```

#### 任务 3.4：实现 Tool Executor 节点

```python
def _node_tool_executor(self, state: DeepAnalysisState) -> dict:
    """
    执行 planner 决定的工具
    第一阶段：仅处理搜索工具
    """
    tools_to_call = state.get("next_tools", [])
    logger.info(f"🔧 Tool Executor: 调用工具: {tools_to_call}")

    updates = {"tool_call_count": state["tool_call_count"] + 1}

    for tool_name in tools_to_call:
        if tool_name == "search":
            result = await self._execute_search_tool(state)
            if result:
                updates["search_evidence"] = result
        else:
            logger.warning(f"未知工具: {tool_name}")

    return updates

async def _execute_search_tool(self, state: DeepAnalysisState) -> Optional[dict]:
    """执行 Tavily 搜索工具"""
    preliminary = state["preliminary"]

    # 构建搜索关键词
    keyword = f"{preliminary.asset} {preliminary.event_type}"
    if preliminary.event_type in ["hack", "regulation"]:
        keyword += " news official"

    logger.info(f"🔧 调用 Tavily 搜索: keyword='{keyword}'")

    try:
        result = await self._search_fetcher.fetch(keyword=keyword, max_results=5)

        if result.success:
            logger.info(f"🔧 Tavily 返回 {result.data.get('source_count', 0)} 条结果")
            return {
                "success": True,
                "data": result.data,
                "triggered": result.triggered,
                "confidence": result.confidence
            }
        else:
            logger.warning(f"🔧 Tavily 搜索失败: {result.error}")
            return None

    except Exception as e:
        logger.error(f"搜索工具执行失败: {e}")
        return None
```

#### 任务 3.5：实现 Synthesis 节点

```python
def _node_synthesis(self, state: DeepAnalysisState) -> dict:
    """
    综合所有证据生成最终信号
    """
    logger.info("📊 Synthesis: 生成最终分析")

    # 构建综合推理 prompt
    prompt = self._build_synthesis_prompt(state)

    # 调用 Gemini 做最终推理
    response = self._client.generate_content(prompt)
    final_json = response.text.strip()

    # 提取置信度用于日志
    try:
        import json
        parsed = json.loads(final_json)
        final_conf = parsed.get("confidence", 0.0)
        prelim_conf = state["preliminary"].confidence
        logger.info(f"📊 Synthesis: 最终置信度 {final_conf:.2f} (初步 {prelim_conf:.2f})")
    except:
        pass

    return {"final_response": final_json}

def _build_synthesis_prompt(self, state: DeepAnalysisState) -> str:
    """构建最终综合推理 prompt"""
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

请综合判断:
1. 搜索结果是否确认事件真实性（multi_source + official_confirmed）
2. 结合历史案例调整置信度
3. 如果搜索结果冲突或不足,降低置信度并标记 data_incomplete

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

【关键要求】
- 搜索多源确认 + 官方确认 → 提升置信度 (+0.1 to +0.2)
- 搜索结果冲突或无官方确认 → 降低置信度 (-0.1 to -0.2)
- 证据不足 → 标记 data_incomplete 风险
- 在 notes 中明确说明使用了哪些证据及关键发现

只返回 JSON,不要其他文字。"""

def _format_search_detail(self, search_ev: dict) -> str:
    """详细格式化搜索证据"""
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
        "搜索结果:"
    ]

    for i, result in enumerate(results[:3], 1):  # 显示前 3 条
        lines.append(f"{i}. {result.get('title', 'N/A')} (来源: {result.get('source', 'N/A')}, 评分: {result.get('score', 0.0)})")

    return "\n".join(lines)
```

---

### 第 5 天：集成到 analyse() 方法

#### 任务 5.1：修改 analyse() 方法

在 `src/ai/deep_analysis/gemini.py` 中修改 `analyse()` 方法：

```python
async def analyse(self, payload, preliminary):
    """
    带可选工具集成的深度分析

    参数:
        payload: EventPayload
        preliminary: 快速分析的 SignalResult

    返回:
        SignalResult（用工具证据增强）
    """
    # 检查是否启用工具增强分析
    tools_enabled = getattr(self._config, 'DEEP_ANALYSIS_TOOLS_ENABLED', False)

    if not tools_enabled:
        # 降级：使用现有 Function Calling 流程
        logger.info("工具未启用，使用传统 Function Calling 流程")
        return await self._analyse_with_function_calling(payload, preliminary)

    # [新增] LangGraph 工具编排流程
    try:
        logger.info("=== 启动 LangGraph 工具增强深度分析 ===")

        graph = self._build_deep_graph()

        initial_state = DeepAnalysisState(
            payload=payload,
            preliminary=preliminary,
            search_evidence=None,
            memory_evidence=None,
            next_tools=[],
            tool_call_count=0,
            max_tool_calls=3,
            final_response=""
        )

        final_state = await graph.ainvoke(initial_state)

        # 解析最终响应 JSON
        result = self._parse_json(final_state["final_response"])

        logger.info("=== LangGraph 深度分析完成 ===")
        return result

    except Exception as exc:
        logger.error(f"LangGraph 工具编排失败，降级到传统流程: {exc}", exc_info=True)
        return await self._analyse_with_function_calling(payload, preliminary)

async def _analyse_with_function_calling(self, payload, preliminary):
    """
    传统 Function Calling 实现（用于向后兼容）
    这是现有 analyse() 逻辑，重构为单独方法
    """
    # 将所有现有 analyse() 代码移到这里
    # ... (现有实现)
    pass
```

#### 任务 5.2：在 __init__ 中初始化工具

```python
def __init__(self, *, client, memory_bundle, parse_json_callback, config):
    # ... 现有初始化

    self._config = config  # 保存配置引用

    # 如果启用则初始化搜索工具
    from src.ai.tools import TavilySearchFetcher

    if getattr(config, 'TOOL_SEARCH_ENABLED', False):
        try:
            self._search_fetcher = TavilySearchFetcher(config)
            logger.info("Tavily 搜索工具已初始化")
        except ValueError as e:
            logger.warning(f"搜索工具初始化失败: {e}")
            self._search_fetcher = None
    else:
        self._search_fetcher = None
```

---

### 第 6-7 天：测试与调优

#### 任务 6.1：功能测试

**测试消息**：

```python
# 传闻类
test_rumor = "Coinbase 即将上线 XYZ 代币,内部人士透露下周公布"

# 政策类
test_policy = "SEC 批准比特币现货 ETF,将于下周开始交易"

# 黑客类
test_hack = "XXX DeFi 协议遭受闪电贷攻击,损失超过 $100M USDC"
```

**验证步骤**：
1. 消息触发深度分析
2. Context Gather 获取记忆
3. Tool Planner 决定调用搜索
4. Tool Executor 调用 Tavily API
5. Synthesis 综合证据 → 最终信号

**日志检查点**：
```
[INFO] 🧠 Context Gather: 找到 2 条历史事件
[INFO] 🤖 Tool Planner 决策: ['search'], 理由: 传闻类事件需多源验证
[INFO] 🔧 调用 Tavily 搜索: keyword='XYZ listing'
[INFO] 🔧 Tavily 返回 4 条结果
[INFO] 📊 Synthesis: 最终置信度 0.65 (初步 0.80)
```

#### 任务 6.2：边界测试

**测试场景**：
- [ ] Tavily API 超时 → 验证降级到传统流程
- [ ] Tavily 速率限制（429）→ 验证错误处理
- [ ] 无搜索结果 → 验证 Synthesis 能处理空证据
- [ ] 结果冲突（不同来源矛盾）→ 验证置信度下调

#### 任务 6.3：成本和延迟测试

**收集指标**（10 条测试消息）：
- [ ] 平均总延迟
- [ ] Context Gather 延迟
- [ ] Tool Planner 延迟
- [ ] Tool Executor 延迟
- [ ] Synthesis 延迟
- [ ] 每条消息的 Tavily API 调用次数
- [ ] 每条消息的平均成本：
  - Tool Planner (Gemini): ~$0.01
  - Tavily API: ~$0.002（按 $20/月无限量计算）
  - Synthesis (Gemini): ~$0.02
  - **总计**: ~$0.032/条

**性能目标**：
- 平均延迟 < 8s（Context 1s + Planner 2s + Executor 2s + Synthesis 3s）
- Tavily 成功率 > 95%
- 每条消息工具调用 ≤ 1 次（第一阶段简单场景）

#### 任务 6.4：Prompt 调优

根据测试结果：
- [ ] 如果 Tool Planner 过度调用搜索 → 加强 prompt 约束
- [ ] 如果 Synthesis 置信度调整不合理 → 优化证据权重
- [ ] 如果搜索结果质量低 → 调整 Tavily 参数（include_domains, search_depth）

#### 任务 6.5：可观测性增强

为每个节点添加详细日志：

```python
# 在 _node_context_gather 中
logger.info(f"🧠 Context Gather: 找到 {len(memory_entries)} 条记忆, 最高相似度: {top_similarity:.2f}")

# 在 _node_tool_planner 中
logger.info(f"🤖 Tool Planner: 决策={next_tools}, 理由={reason}, 轮次={tool_call_count+1}/3")

# 在 _node_tool_executor 中
logger.info(f"🔧 Tool Executor: Tavily keyword='{keyword}', 结果数={len(results)}, 触发={triggered}")

# 在 _node_synthesis 中
logger.info(f"📊 Synthesis: 置信度 {final_conf:.2f} (Δ {final_conf - prelim_conf:+.2f}), 证据: 记忆={mem_count}, 搜索={search_count}")
```

可选：将工具调用记录到数据库：

```sql
CREATE TABLE deep_analysis_tool_calls (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT REFERENCES news_events(id),
    tool_name TEXT NOT NULL,
    request_params JSONB,
    response_data JSONB,
    latency_ms INTEGER,
    success BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 配置

### 环境变量（.env）

添加到 `.env`：

```bash
# ==================== 深度分析工具（第一阶段）====================

# 工具特性开关
DEEP_ANALYSIS_TOOLS_ENABLED=false        # 默认关闭，测试通过后启用

# 工具调用限制
DEEP_ANALYSIS_MAX_TOOL_CALLS=3           # 最大工具轮次
DEEP_ANALYSIS_TOOL_TIMEOUT=10            # 每个工具超时（秒）

# Tavily 搜索配置
TAVILY_API_KEY=                          # 必填：Tavily API key
TOOL_SEARCH_ENABLED=true                 # 搜索工具开关
SEARCH_MAX_RESULTS=5                     # 最大搜索结果数
SEARCH_MULTI_SOURCE_THRESHOLD=3          # 多源一致性阈值（来源数）
SEARCH_INCLUDE_DOMAINS=coindesk.com,theblock.co,cointelegraph.com  # 优先域名（逗号分隔）

# 第二阶段+ 工具（暂时禁用）
TOOL_PRICE_ENABLED=false
TOOL_MACRO_ENABLED=false
TOOL_ONCHAIN_ENABLED=false
```

### Config 类更新

添加到 `src/config.py`：

```python
# 深度分析工具（第一阶段）
DEEP_ANALYSIS_TOOLS_ENABLED: bool = False
DEEP_ANALYSIS_MAX_TOOL_CALLS: int = 3
DEEP_ANALYSIS_TOOL_TIMEOUT: int = 10

# Tavily 搜索
TAVILY_API_KEY: str = ""
TOOL_SEARCH_ENABLED: bool = True
SEARCH_MAX_RESULTS: int = 5
SEARCH_MULTI_SOURCE_THRESHOLD: int = 3
SEARCH_INCLUDE_DOMAINS: str = "coindesk.com,theblock.co,cointelegraph.com"

# 未来的工具
TOOL_PRICE_ENABLED: bool = False
TOOL_MACRO_ENABLED: bool = False
TOOL_ONCHAIN_ENABLED: bool = False
```

---

## 验收标准

### 功能性
- [ ] 传闻/政策/黑客消息触发 Tavily 搜索
- [ ] 搜索结果填充 `search_evidence`（multi_source、official_confirmed、sentiment）
- [ ] Synthesis 结合搜索 + 记忆证据调整置信度
- [ ] 搜索失败时降级到传统流程，不阻塞消息处理

### 性能
- [ ] 平均延迟 < 8s
- [ ] Tavily API 成功率 > 95%
- [ ] 每条消息工具调用 ≤ 1 次（第一阶段简化场景）

### 成本
- [ ] 平均成本 < $0.05/条
- [ ] Tavily 月度配额在限制内（1,000 免费或 $20 无限量）

### 质量
- [ ] 传闻消息置信度准确性提升（对比人工标注）
- [ ] 误报率降低（多源验证过滤虚假传闻）
- [ ] Synthesis 的 `notes` 字段包含搜索来源引用

### 可维护性
- [ ] 代码有完整注释和类型提示
- [ ] 工具逻辑与 LangGraph 逻辑解耦（便于添加更多工具）
- [ ] `DEEP_ANALYSIS_TOOLS_ENABLED` 开关可随时禁用功能

---

## Tavily API 参考

### 端点

```
POST https://api.tavily.com/search
```

### 请求示例

```bash
curl -X POST https://api.tavily.com/search \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "tvly-xxxxx",
    "query": "USDC depeg Circle official statement",
    "max_results": 5,
    "search_depth": "basic",
    "include_domains": ["coindesk.com", "theblock.co"],
    "include_answer": false
  }'
```

### 响应示例

```json
{
  "query": "USDC depeg Circle official statement",
  "results": [
    {
      "title": "Circle: USDC reserves are safe amid depeg concerns",
      "url": "https://coindesk.com/...",
      "content": "Circle CEO Jeremy Allaire stated that all USDC reserves...",
      "score": 0.95,
      "published_date": "2025-10-11"
    },
    {
      "title": "USDC briefly depegs to $0.98 on Binance",
      "url": "https://theblock.co/...",
      "content": "The USD Coin (USDC) stablecoin briefly lost its peg...",
      "score": 0.89,
      "published_date": "2025-10-11"
    }
  ]
}
```

### 定价

- **免费层**: 1,000 次请求/月
- **Pro 层**: $20/月，无限请求，更快响应
- **平均延迟**: 1-2 秒

### 错误处理

- **401**: API key 无效 → 检查配置
- **429**: 超出配额 → 等待月度重置或升级到 Pro
- **503**: 服务不可用 → 重试 3 次后降级

---

## 第一阶段后的下一步

完成第一阶段后，根据生产数据评估：

### 1. 搜索质量优化
- 如果 Tavily 结果质量差 → 调整 `include_domains` 或 `search_depth`
- 如果误报率高 → 增强多源一致性逻辑（检查来源权威性）

### 2. 成本优化
- 如果 Tavily 配额超限 → 实现结果缓存（相同关键词 10 分钟内复用）
- 如果 Planner 过度调用 → 优化 prompt 或添加事件类型白名单

### 3. 扩展到第二阶段
- 如果搜索工具效果显著 → 优先实现价格工具（脱锚场景）
- 如果传闻验证需求不高 → 跳过第二阶段，专注优化现有流程

---

## 参考资料

### API 文档
- [Tavily API](https://docs.tavily.com/)
- [Google Custom Search API](https://developers.google.com/custom-search/v1/overview)（备选）

### 相关文档
- 主方案：`docs/deep_analysis_tools_integration_plan.md`
- 架构：`docs/memory_architecture.md`
- AI 信号引擎：`docs/aisignalengine_implementation.md`

---

## 变更日志

- **2025-10-11**: 创建第一阶段实施指南（中文版）
