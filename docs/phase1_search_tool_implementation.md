# Phase 1: Search Tool Integration - Implementation Guide

## Overview

This document provides a step-by-step implementation guide for integrating the Tavily search tool into the deep analysis pipeline. This is Phase 1 of the multi-tool integration plan, focusing on building the LangGraph foundation and adding news search verification capabilities.

**Timeline**: 1-2 weeks
**Goal**: Build LangGraph subgraph skeleton and implement Tavily search tool for news verification
**Status**: Not started

---

## Why Tavily Search?

- **Google Custom Search** free tier: Only 100 queries/day (insufficient for production)
- **Tavily advantages**:
  - Built for AI applications with structured responses
  - Free tier: 1,000 queries/month
  - Pro tier: $20/month unlimited
  - Returns title, summary, relevance score, and source credibility
  - Average latency: 1-2 seconds
  - Simple API with single-call multi-source results

---

## Architecture Changes

### Modification Scope

**Files to modify**:
- `src/ai/deep_analysis/gemini.py`: Add LangGraph subgraph to `analyse()` method
- New files in `src/ai/tools/`: Tool implementations

**Files unchanged**:
- Main pipeline (`src/listener.py`, `src/pipeline/langgraph_pipeline.py`, `src/ai/signal_engine.py`)
- All existing flow remains intact

### Trigger Conditions

Same as existing deep analysis logic:
- Gemini Flash preliminary analysis `confidence >= HIGH_VALUE_CONFIDENCE_THRESHOLD` (default 0.75)
- OR `event_type` in high-value types (depeg, liquidation, hack)
- EXCLUDE low-value types (macro, other, airdrop, governance, celebrity, scam_alert)

### Flow Diagram

```
Existing Flow (Unchanged):
listener → langgraph_pipeline → _node_ai_signal → AiSignalEngine.analyse()
                                                          ↓
                                              Gemini Flash preliminary analysis
                                                          ↓
                                    Check is_high_value_signal() (signal_engine.py:528-540)
                                                          ↓
                                          [NEW] DeepAnalysisGraph subgraph
                                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    ↓                                                     ↓
        Context Gather (memory) → Tool Planner (AI) → Tool Executor (API)
                    ↑                                                     ↓
                    └─────────────────── Router ←────────────────────────┘
                                          ↓ (max 3 rounds)
                                    Synthesis (final reasoning)
                                          ↓
                                    Output final signal
```

---

## LangGraph State Design

### State Object (DeepAnalysisState)

```python
from typing import TypedDict, Optional
from src.db.models import EventPayload
from src.ai.signal_engine import SignalResult

class DeepAnalysisState(TypedDict, total=False):
    # Input
    payload: EventPayload                # Original message payload
    preliminary: SignalResult             # Gemini Flash preliminary result

    # Evidence slots (Phase 1: only search + memory)
    search_evidence: Optional[dict]       # Search results
    memory_evidence: Optional[dict]       # Historical similar events

    # Control flow
    next_tools: list[str]                 # ["search"] or []
    tool_call_count: int                  # 0-3
    max_tool_calls: int                   # Fixed at 3

    # Output
    final_response: str                   # JSON string (final signal)
```

---

## Implementation Tasks

### Day 1: Tool Infrastructure

#### Task 1.1: Create Tool Directory Structure

```bash
mkdir -p src/ai/tools
touch src/ai/tools/__init__.py
touch src/ai/tools/base.py
touch src/ai/tools/search_fetcher.py
touch src/ai/tools/exceptions.py
```

#### Task 1.2: Implement Tool Base Class

**File**: `src/ai/tools/base.py`

```python
from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod
from datetime import datetime, timezone

@dataclass
class ToolResult:
    """Standardized tool result format"""
    source: str              # Tool source (e.g., "Tavily")
    timestamp: str           # ISO 8601 timestamp
    success: bool            # Whether call succeeded
    data: dict              # Structured data
    triggered: bool          # Whether anomaly threshold triggered
    confidence: float        # Result confidence (0.0-1.0)
    error: Optional[str] = None  # Error message if failed

    @staticmethod
    def _format_timestamp() -> str:
        """Return current UTC timestamp in ISO 8601 format"""
        return datetime.now(timezone.utc).isoformat()

class BaseTool(ABC):
    """Abstract base class for all tools"""

    def __init__(self, config):
        self._config = config
        self._timeout = getattr(config, 'DEEP_ANALYSIS_TOOL_TIMEOUT', 10)

    @abstractmethod
    async def fetch(self, **kwargs) -> ToolResult:
        """Fetch data from tool API"""
        pass

    def _handle_timeout(self, error: Exception) -> ToolResult:
        """Standard timeout error handling"""
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

**File**: `src/ai/tools/exceptions.py`

```python
class ToolFetchError(Exception):
    """Base exception for tool fetch errors"""
    pass

class ToolTimeoutError(ToolFetchError):
    """Tool API timeout"""
    pass

class ToolRateLimitError(ToolFetchError):
    """Tool API rate limit exceeded"""
    pass
```

#### Task 1.3: Implement Tavily Search Tool

**File**: `src/ai/tools/search_fetcher.py`

```python
import httpx
from typing import Optional
from .base import BaseTool, ToolResult
from .exceptions import ToolTimeoutError, ToolRateLimitError

class TavilySearchFetcher(BaseTool):
    """Tavily search API integration for news verification"""

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
            raise ValueError("TAVILY_API_KEY not configured")

    @staticmethod
    def _parse_domains(domains_str: str) -> list[str]:
        """Parse comma-separated domain list"""
        if not domains_str:
            return ["coindesk.com", "theblock.co", "cointelegraph.com"]
        return [d.strip() for d in domains_str.split(',') if d.strip()]

    async def fetch(self, keyword: str, max_results: Optional[int] = None) -> ToolResult:
        """
        Fetch search results from Tavily API

        Args:
            keyword: Search query
            max_results: Override default max results

        Returns:
            ToolResult with search data
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
                    raise ToolRateLimitError("Tavily API rate limit exceeded")

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
        """Parse Tavily API response into ToolResult"""
        results = data.get('results', [])

        # Check multi-source consistency
        multi_source = len(results) >= self._multi_source_threshold

        # Check for official confirmation
        official_confirmed = self._check_official_confirmation(results)

        # Simple sentiment analysis
        sentiment = self._analyze_sentiment(results)

        # Build structured data
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

        # Trigger if multi-source AND official confirmed
        triggered = multi_source and official_confirmed

        # Confidence based on result quality
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
        """Extract domain from URL"""
        from urllib.parse import urlparse
        return urlparse(url).netloc

    def _check_official_confirmation(self, results: list[dict]) -> bool:
        """Check if any result contains official statement keywords"""
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
        """Simple keyword-based sentiment analysis"""
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
        """Calculate confidence based on result quality"""
        if not results:
            return 0.0

        # Base confidence from average score
        avg_score = sum(r.get("score", 0.0) for r in results) / len(results)
        confidence = avg_score

        # Boost if multi-source
        if multi_source:
            confidence = min(1.0, confidence + 0.1)

        # Boost if official confirmed
        if official_confirmed:
            confidence = min(1.0, confidence + 0.15)

        return round(confidence, 2)
```

**File**: `src/ai/tools/__init__.py`

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

#### Task 1.4: Unit Tests

**File**: `tests/ai/tools/test_search_fetcher.py`

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
    """Test successful API call"""
    mock_response = {
        "results": [
            {
                "title": "USDC depegs to $0.98",
                "url": "https://coindesk.com/test",
                "content": "Circle official statement confirms...",
                "score": 0.95
            },
            {
                "title": "Market panic as USDC loses peg",
                "url": "https://theblock.co/test",
                "content": "暴跌 continues...",
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
    """Test API rate limit handling"""
    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 429

        result = await fetcher.fetch("test query")

        assert result.success is False
        assert result.error == "rate_limit"

@pytest.mark.asyncio
async def test_timeout_error(fetcher):
    """Test timeout handling"""
    import httpx

    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Connection timeout")

        result = await fetcher.fetch("test query")

        assert result.success is False
        assert "timeout" in result.error

@pytest.mark.asyncio
async def test_multi_source_detection(fetcher):
    """Test multi-source consistency logic"""
    mock_response = {
        "results": [
            {"title": f"News {i}", "url": f"https://source{i}.com/test", "score": 0.9}
            for i in range(5)
        ]
    }

    with patch('httpx.AsyncClient.post', new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_response

        result = await fetcher.fetch("test")

        assert result.data["multi_source"] is True  # >= 3 sources

@pytest.mark.asyncio
async def test_official_confirmation_detection(fetcher):
    """Test official statement detection"""
    mock_response = {
        "results": [
            {
                "title": "Official announcement from Circle",
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

### Day 2: LangGraph State and Node Skeleton

#### Task 2.1: Define State Object

Add to `src/ai/deep_analysis/gemini.py` (near top of file):

```python
from typing import TypedDict, Optional

class DeepAnalysisState(TypedDict, total=False):
    # Input
    payload: 'EventPayload'
    preliminary: 'SignalResult'

    # Evidence slots (Phase 1: only search + memory)
    search_evidence: Optional[dict]
    memory_evidence: Optional[dict]

    # Control flow
    next_tools: list[str]        # ["search"] or []
    tool_call_count: int         # 0-3
    max_tool_calls: int          # Fixed at 3

    # Output
    final_response: str          # JSON string
```

#### Task 2.2: Implement Node Method Skeletons

Add to `GeminiDeepAnalysisEngine` class:

```python
def _node_context_gather(self, state: DeepAnalysisState) -> dict:
    """
    Node 1: Gather historical memory context
    Reuses existing _tool_fetch_memories logic
    """
    # TODO: Implement in Day 3
    pass

def _node_tool_planner(self, state: DeepAnalysisState) -> dict:
    """
    Node 2: AI decides which tools to call next
    Simplified prompt for Phase 1 (only search decision)
    """
    # TODO: Implement in Day 3
    pass

def _node_tool_executor(self, state: DeepAnalysisState) -> dict:
    """
    Node 3: Execute tools decided by planner
    Phase 1: Only handles search tool
    """
    # TODO: Implement in Day 3
    pass

def _node_synthesis(self, state: DeepAnalysisState) -> dict:
    """
    Node 4: Synthesize all evidence into final signal
    """
    # TODO: Implement in Day 3
    pass
```

#### Task 2.3: Implement Router Methods

```python
def _route_after_planner(self, state: DeepAnalysisState) -> str:
    """
    Route after Tool Planner:
    - If next_tools is empty → "synthesis"
    - Otherwise → "executor"
    """
    if not state.get("next_tools"):
        return "synthesis"
    return "executor"

def _route_after_executor(self, state: DeepAnalysisState) -> str:
    """
    Route after Tool Executor:
    - If tool_call_count >= max_tool_calls → "synthesis"
    - Otherwise → "planner" (for next round)
    """
    if state["tool_call_count"] >= state["max_tool_calls"]:
        logger.info("Reached max tool calls (3), proceeding to synthesis")
        return "synthesis"
    return "planner"
```

---

### Day 3-4: Implement LangGraph Subgraph

#### Task 3.1: Build Graph Structure

Add to `GeminiDeepAnalysisEngine`:

```python
def _build_deep_graph(self):
    """Build LangGraph for deep analysis with tools"""
    from langgraph.graph import StateGraph, END

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

#### Task 3.2: Implement Context Gather Node

```python
def _node_context_gather(self, state: DeepAnalysisState) -> dict:
    """
    Gather historical memory context
    Reuses existing _tool_fetch_memories logic
    """
    logger.info("🧠 Context Gather: Fetching historical memories")

    # Reuse existing memory fetch logic
    memory_entries = self._tool_fetch_memories({
        "payload": state["payload"],
        "preliminary": state["preliminary"]
    })

    # Format as simple text
    memory_text = self._format_memory_evidence(memory_entries)

    logger.info(f"🧠 Context Gather: Found {len(memory_entries)} historical events")

    return {
        "memory_evidence": {
            "entries": memory_entries,
            "formatted": memory_text,
            "count": len(memory_entries)
        }
    }

def _format_memory_evidence(self, entries: list) -> str:
    """Format memory entries for AI consumption"""
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

#### Task 3.3: Implement Tool Planner Node

```python
def _node_tool_planner(self, state: DeepAnalysisState) -> dict:
    """
    AI decides whether to call search tool
    Phase 1: Simplified decision (only search)
    """
    logger.info("🤖 Tool Planner: Deciding next tools")

    # Build decision prompt
    prompt = self._build_planner_prompt(state)

    # Call Gemini for decision
    response = self._client.generate_content(prompt)
    decision_text = response.text.strip()

    # Parse JSON decision
    import json
    try:
        decision = json.loads(decision_text)
        tools = decision.get("tools", [])
        reason = decision.get("reason", "")

        logger.info(f"🤖 Tool Planner decision: {tools}, reason: {reason}")

        return {"next_tools": tools}

    except json.JSONDecodeError:
        logger.warning(f"Failed to parse planner decision: {decision_text}")
        return {"next_tools": []}

def _build_planner_prompt(self, state: DeepAnalysisState) -> str:
    """Build prompt for tool planning"""
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
4. 如果是数值类事件 (depeg/liquidation) → 暂不需要搜索 (Phase 1 限制)

【当前状态】
- 已调用工具次数: {state['tool_call_count']}
- 最大调用次数: {state['max_tool_calls']}

返回 JSON:
- 需要搜索: {{"tools": ["search"], "reason": "传闻类事件需多源验证"}}
- 无需搜索: {{"tools": [], "reason": "已有充分证据"}}

只返回 JSON,不要其他文字。"""

def _format_search_evidence(self, search_ev: dict) -> str:
    """Format search evidence for display"""
    if not search_ev:
        return "无"

    data = search_ev.get("data", {})
    return f"找到 {data.get('source_count', 0)} 条结果, 多源确认={data.get('multi_source', False)}, 官方确认={data.get('official_confirmed', False)}"
```

#### Task 3.4: Implement Tool Executor Node

```python
def _node_tool_executor(self, state: DeepAnalysisState) -> dict:
    """
    Execute tools decided by planner
    Phase 1: Only handles search tool
    """
    tools_to_call = state.get("next_tools", [])
    logger.info(f"🔧 Tool Executor: Calling tools: {tools_to_call}")

    updates = {"tool_call_count": state["tool_call_count"] + 1}

    for tool_name in tools_to_call:
        if tool_name == "search":
            result = await self._execute_search_tool(state)
            if result:
                updates["search_evidence"] = result
        else:
            logger.warning(f"Unknown tool: {tool_name}")

    return updates

async def _execute_search_tool(self, state: DeepAnalysisState) -> Optional[dict]:
    """Execute Tavily search tool"""
    preliminary = state["preliminary"]

    # Build search keyword
    keyword = f"{preliminary.asset} {preliminary.event_type}"
    if preliminary.event_type in ["hack", "regulation"]:
        keyword += " news official"

    logger.info(f"🔧 Calling Tavily search: keyword='{keyword}'")

    try:
        result = await self._search_fetcher.fetch(keyword=keyword, max_results=5)

        if result.success:
            logger.info(f"🔧 Tavily returned {result.data.get('source_count', 0)} results")
            return {
                "success": True,
                "data": result.data,
                "triggered": result.triggered,
                "confidence": result.confidence
            }
        else:
            logger.warning(f"🔧 Tavily search failed: {result.error}")
            return None

    except Exception as e:
        logger.error(f"Search tool execution failed: {e}")
        return None
```

#### Task 3.5: Implement Synthesis Node

```python
def _node_synthesis(self, state: DeepAnalysisState) -> dict:
    """
    Synthesize all evidence into final signal
    """
    logger.info("📊 Synthesis: Generating final analysis")

    # Build synthesis prompt
    prompt = self._build_synthesis_prompt(state)

    # Call Gemini for final reasoning
    response = self._client.generate_content(prompt)
    final_json = response.text.strip()

    # Extract confidence for logging
    try:
        import json
        parsed = json.loads(final_json)
        final_conf = parsed.get("confidence", 0.0)
        prelim_conf = state["preliminary"].confidence
        logger.info(f"📊 Synthesis: Final confidence {final_conf:.2f} (preliminary {prelim_conf:.2f})")
    except:
        pass

    return {"final_response": final_json}

def _build_synthesis_prompt(self, state: DeepAnalysisState) -> str:
    """Build prompt for final synthesis"""
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
1. 搜索结果是否确认事件真实性 (multi_source + official_confirmed)
2. 结合历史案例调整置信度
3. 如果搜索结果冲突或不足,降低置信度并标记 data_incomplete

返回 JSON (与 SignalResult 格式一致):
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
    """Format search evidence in detail"""
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

    for i, result in enumerate(results[:3], 1):  # Show top 3
        lines.append(f"{i}. {result.get('title', 'N/A')} (来源: {result.get('source', 'N/A')}, 评分: {result.get('score', 0.0)})")

    return "\n".join(lines)
```

---

### Day 5: Integration into analyse() Method

#### Task 5.1: Modify analyse() Method

In `src/ai/deep_analysis/gemini.py`, modify the `analyse()` method:

```python
async def analyse(self, payload, preliminary):
    """
    Deep analysis with optional tool integration

    Args:
        payload: EventPayload
        preliminary: SignalResult from fast analysis

    Returns:
        SignalResult (enhanced with tool evidence)
    """
    # Check if tool-enhanced analysis is enabled
    tools_enabled = getattr(self._config, 'DEEP_ANALYSIS_TOOLS_ENABLED', False)

    if not tools_enabled:
        # Fallback: Use existing Function Calling flow
        logger.info("Tools disabled, using legacy Function Calling flow")
        return await self._analyse_with_function_calling(payload, preliminary)

    # [NEW] LangGraph tool orchestration flow
    try:
        logger.info("=== Starting LangGraph Deep Analysis with Tools ===")

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

        # Parse final response JSON
        result = self._parse_json(final_state["final_response"])

        logger.info("=== LangGraph Deep Analysis Complete ===")
        return result

    except Exception as exc:
        logger.error(f"LangGraph tool orchestration failed, falling back to legacy flow: {exc}", exc_info=True)
        return await self._analyse_with_function_calling(payload, preliminary)

async def _analyse_with_function_calling(self, payload, preliminary):
    """
    Legacy Function Calling implementation (for backward compatibility)
    This is the existing analyse() logic, refactored into a separate method
    """
    # Move all existing analyse() code here
    # ... (existing implementation)
    pass
```

#### Task 5.2: Initialize Tools in __init__

```python
def __init__(self, *, client, memory_bundle, parse_json_callback, config):
    # ... existing initialization

    self._config = config  # Save config reference

    # Initialize search tool if enabled
    from src.ai.tools import TavilySearchFetcher

    if getattr(config, 'TOOL_SEARCH_ENABLED', False):
        try:
            self._search_fetcher = TavilySearchFetcher(config)
            logger.info("Tavily search tool initialized")
        except ValueError as e:
            logger.warning(f"Failed to initialize search tool: {e}")
            self._search_fetcher = None
    else:
        self._search_fetcher = None
```

---

### Day 6-7: Testing and Tuning

#### Task 6.1: Functional Testing

**Test messages**:

```python
# Rumor type
test_rumor = "Coinbase 即将上线 XYZ 代币,内部人士透露下周公布"

# Policy type
test_policy = "SEC 批准比特币现货 ETF,将于下周开始交易"

# Hack type
test_hack = "XXX DeFi 协议遭受闪电贷攻击,损失超过 $100M USDC"
```

**Verification steps**:
1. Message triggers deep analysis
2. Context Gather fetches memories
3. Tool Planner decides to call search
4. Tool Executor calls Tavily API
5. Synthesis combines evidence → final signal

**Log checkpoints**:
```
[INFO] 🧠 Context Gather: Found 2 historical events
[INFO] 🤖 Tool Planner decision: ['search'], reason: 传闻类事件需多源验证
[INFO] 🔧 Calling Tavily search: keyword='XYZ listing'
[INFO] 🔧 Tavily returned 4 results
[INFO] 📊 Synthesis: Final confidence 0.65 (preliminary 0.80)
```

#### Task 6.2: Edge Case Testing

**Test scenarios**:
- [ ] Tavily API timeout → verify fallback to legacy flow
- [ ] Tavily rate limit (429) → verify error handling
- [ ] No search results → verify Synthesis handles empty evidence
- [ ] Conflicting results (different sources contradict) → verify confidence downgrade

#### Task 6.3: Cost and Latency Testing

**Metrics to collect** (for 10 test messages):
- [ ] Average total latency
- [ ] Context Gather latency
- [ ] Tool Planner latency
- [ ] Tool Executor latency
- [ ] Synthesis latency
- [ ] Tavily API calls per message
- [ ] Average cost per message:
  - Tool Planner (Gemini): ~$0.01
  - Tavily API: ~$0.002 (assuming $20/month unlimited)
  - Synthesis (Gemini): ~$0.02
  - **Total**: ~$0.032/message

**Performance targets**:
- Average latency < 8s (Context 1s + Planner 2s + Executor 2s + Synthesis 3s)
- Tavily success rate > 95%
- Tool calls per message ≤ 1 (Phase 1 simple scenario)

#### Task 6.4: Prompt Tuning

Based on test results:
- [ ] If Tool Planner over-calls search → strengthen prompt constraints
- [ ] If Synthesis confidence adjustments unreasonable → optimize evidence weighting
- [ ] If search result quality low → adjust Tavily parameters (include_domains, search_depth)

#### Task 6.5: Observability Enhancement

Add detailed logging to each node:

```python
# In _node_context_gather
logger.info(f"🧠 Context Gather: Found {len(memory_entries)} memories, top similarity: {top_similarity:.2f}")

# In _node_tool_planner
logger.info(f"🤖 Tool Planner: Decision={next_tools}, Reason={reason}, Round={tool_call_count+1}/3")

# In _node_tool_executor
logger.info(f"🔧 Tool Executor: Tavily keyword='{keyword}', results={len(results)}, triggered={triggered}")

# In _node_synthesis
logger.info(f"📊 Synthesis: Confidence {final_conf:.2f} (Δ {final_conf - prelim_conf:+.2f}), Evidence: memory={mem_count}, search={search_count}")
```

Optional: Record tool calls to database:

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

## Configuration

### Environment Variables (.env)

Add to `.env`:

```bash
# ==================== Deep Analysis Tools (Phase 1) ====================

# Tool feature toggle
DEEP_ANALYSIS_TOOLS_ENABLED=false        # Default off, enable after testing

# Tool call limits
DEEP_ANALYSIS_MAX_TOOL_CALLS=3           # Max tool rounds
DEEP_ANALYSIS_TOOL_TIMEOUT=10            # Timeout per tool (seconds)

# Tavily search configuration
TAVILY_API_KEY=                          # Required: Tavily API key
TOOL_SEARCH_ENABLED=true                 # Search tool toggle
SEARCH_MAX_RESULTS=5                     # Max search results
SEARCH_MULTI_SOURCE_THRESHOLD=3          # Multi-source threshold (number of sources)
SEARCH_INCLUDE_DOMAINS=coindesk.com,theblock.co,cointelegraph.com  # Preferred domains (comma-separated)

# Phase 2+ tools (disabled for now)
TOOL_PRICE_ENABLED=false
TOOL_MACRO_ENABLED=false
TOOL_ONCHAIN_ENABLED=false
```

### Config Class Updates

Add to `src/config.py`:

```python
# Deep Analysis Tools (Phase 1)
DEEP_ANALYSIS_TOOLS_ENABLED: bool = False
DEEP_ANALYSIS_MAX_TOOL_CALLS: int = 3
DEEP_ANALYSIS_TOOL_TIMEOUT: int = 10

# Tavily Search
TAVILY_API_KEY: str = ""
TOOL_SEARCH_ENABLED: bool = True
SEARCH_MAX_RESULTS: int = 5
SEARCH_MULTI_SOURCE_THRESHOLD: int = 3
SEARCH_INCLUDE_DOMAINS: str = "coindesk.com,theblock.co,cointelegraph.com"

# Future tools
TOOL_PRICE_ENABLED: bool = False
TOOL_MACRO_ENABLED: bool = False
TOOL_ONCHAIN_ENABLED: bool = False
```

---

## Acceptance Criteria

### Functionality
- [ ] Rumor/policy/hack messages trigger Tavily search
- [ ] Search results populate `search_evidence` (multi_source, official_confirmed, sentiment)
- [ ] Synthesis combines search + memory evidence to adjust confidence
- [ ] Search failures fallback to legacy flow without blocking message processing

### Performance
- [ ] Average latency < 8s
- [ ] Tavily API success rate > 95%
- [ ] Tool calls per message ≤ 1 (Phase 1 simplified scenario)

### Cost
- [ ] Average cost < $0.05/message
- [ ] Tavily monthly quota within limits (1,000 free or $20 unlimited)

### Quality
- [ ] Rumor message confidence accuracy improved (vs manual labels)
- [ ] False positive rate decreased (multi-source verification filters false rumors)
- [ ] Synthesis `notes` field includes search source citations

### Maintainability
- [ ] Code has complete comments and type hints
- [ ] Tool logic decoupled from LangGraph logic (easy to add more tools)
- [ ] `DEEP_ANALYSIS_TOOLS_ENABLED` toggle can disable feature anytime

---

## Tavily API Reference

### Endpoint

```
POST https://api.tavily.com/search
```

### Request Example

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

### Response Example

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

### Pricing

- **Free tier**: 1,000 requests/month
- **Pro tier**: $20/month, unlimited requests, faster response
- **Average latency**: 1-2 seconds

### Error Handling

- **401**: Invalid API key → Check configuration
- **429**: Quota exceeded → Wait for monthly reset or upgrade to Pro
- **503**: Service unavailable → Retry 3 times then fallback

---

## Next Steps After Phase 1

After completing Phase 1, evaluate based on production data:

### 1. Search Quality Optimization
- If Tavily result quality poor → Adjust `include_domains` or `search_depth`
- If false positive rate high → Enhance multi-source consistency logic (check source authority)

### 2. Cost Optimization
- If Tavily quota exceeded → Implement result caching (10min cache for same keyword)
- If Planner over-calls → Optimize prompt or add event type whitelist

### 3. Expand to Phase 2
- If search tool effective → Prioritize price tool (depeg scenarios)
- If rumor verification demand low → Skip Phase 2, focus on optimizing existing flow

---

## References

### API Documentation
- [Tavily API](https://docs.tavily.com/)
- [Google Custom Search API](https://developers.google.com/custom-search/v1/overview) (alternative)

### Related Documents
- Main plan: `docs/deep_analysis_tools_integration_plan.md`
- Architecture: `docs/memory_architecture.md`
- AI Signal Engine: `docs/aisignalengine_implementation.md`

---

## Changelog

- **2025-10-11**: Initial Phase 1 implementation guide created
