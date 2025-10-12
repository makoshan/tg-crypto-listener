# 深度分析工具集成方案 (LangGraph + Multi-Tool)

## 概述

本方案在现有 `GeminiDeepAnalysisEngine` 基础上,引入 LangGraph 状态机编排多个外部工具 (价格API、搜索、宏观数据、链上数据),让 Gemini 2.5 Flash 自主决策调用时机,提升深度分析的准确性和可操作性。

**核心设计哲学**: AI 负责推理和决策,工具只提供客观事实 + 异常标记。

---

## 整体架构

### 改动位置

**仅修改**: `src/ai/deep_analysis/gemini.py` 的 `analyse()` 方法内部
**不改动**: 主流程 (listener.py → langgraph_pipeline.py → signal_engine.py) 保持原样

### 触发条件

保持现有逻辑:
- Gemini Flash 快速分析后 `confidence >= 0.75` (可配置 `HIGH_VALUE_CONFIDENCE_THRESHOLD`)
- 或 `event_type` 属于高价值类型 (depeg/liquidation/hack)
- 排除低价值类型 (macro/other/airdrop/governance/celebrity/scam_alert)

### 流程图

```
现有流程 (不变):
listener → langgraph_pipeline → _node_ai_signal → AiSignalEngine.analyse()
                                                          ↓
                                              Gemini Flash 快速分析
                                                          ↓
                                    判断 is_high_value_signal() (signal_engine.py:528-540)
                                                          ↓
                                          [NEW] DeepAnalysisGraph 子图
                                                          ↓
                    ┌──────────────────────────────────────────────────────┐
                    ↓                                                      ↓
        Context Gather (读记忆) → Tool Planner (AI决策) → Tool Executor (调API)
                    ↑                                                      ↓
                    └────────────────── 路由节点 ←───────────────────────┘
                                          ↓ (最多3轮)
                                    Synthesis (综合推理)
                                          ↓
                                      输出最终信号
```

---

## LangGraph 子图设计

### 状态对象 (DeepAnalysisState)

```python
DeepAnalysisState(TypedDict):
    # 输入
    payload: EventPayload                # 原始消息载荷
    preliminary: SignalResult             # Gemini Flash 初步结果

    # 证据槽位 (由工具填充)
    price_evidence: Optional[dict]        # 价格/清算/资金费率数据
    search_evidence: Optional[dict]       # 新闻搜索/多源验证结果
    macro_evidence: Optional[dict]        # 宏观经济数据 (CPI/利率)
    onchain_evidence: Optional[dict]      # 链上数据 (流动性/赎回)
    memory_evidence: Optional[dict]       # 历史相似事件

    # 控制流
    next_tools: list[str]                 # Planner 填充,待调用工具列表
    tool_call_count: int                  # 已调用工具次数 (限制 ≤ 3)

    # 输出
    final_response: str                   # Synthesis 填充,最终 JSON 结果
```

### 节点定义

#### 1. Context Gather (读记忆)

**职责**: 初始化状态,从记忆系统拉取历史参照

**执行逻辑**:
1. 复用现有 `_tool_fetch_memories` 逻辑 (gemini.py:122-193)
2. **并行查询**:
   - 本地记忆 (LocalMemoryStore): 关键词快速匹配
   - Supabase 记忆: 向量语义相似搜索
3. 合并策略:
   ```python
   # 本地关键词匹配
   local_entries = local_store.load_entries(
       keywords=payload.keywords_hit,
       limit=5,
       min_confidence=0.6
   )

   # Supabase 向量检索
   supabase_entries = await repo.fetch_memories(
       embedding=embedding,
       asset_codes=[preliminary.asset],
       limit=5
   )

   # 合并去重,按相似度降序排序
   all_entries = local_entries + supabase_entries
   sorted_entries = sorted(
       all_entries,
       key=lambda x: getattr(x, 'similarity', 0.5),
       reverse=True
   )[:3]  # 取 top 3
   ```

**输出**: 填充 `state["memory_evidence"]`

**触发时机**: 深度分析启动时立即执行

---

#### 2. Tool Planner (AI 决策)

**职责**: Gemini 根据消息内容和已有证据,动态决定需要哪些工具

**执行逻辑**:
1. **不使用 Function Calling**,采用文本 JSON 返回
2. Prompt 设计:
   ```
   你是工具调度专家,根据消息内容和已有证据决定调用哪些工具。

   【消息内容】{payload.text}
   【事件类型】{preliminary.event_type}
   【资产】{preliminary.asset}
   【Gemini Flash 初判】confidence={preliminary.confidence}, action={preliminary.action}

   【已掌握证据】
   - 价格数据: {format_evidence(state["price_evidence"])}
   - 搜索结果: {format_evidence(state["search_evidence"])}
   - 宏观数据: {format_evidence(state["macro_evidence"])}
   - 链上数据: {format_evidence(state["onchain_evidence"])}
   - 历史记忆: {format_evidence(state["memory_evidence"])}

   【决策规则】
   1. 数值问题 (脱锚/清算/暴跌) → 优先调用 "price"
   2. 叙事问题 (传闻/政策/黑客) → 优先调用 "search"
   3. 宏观事件 (加息/CPI/美联储) → 调用 "macro"
   4. 如果 price_evidence 显示 triggered=true → 追加 "search" 验证
   5. 如果 search_evidence 显示 multi_source=true → 追加 "price" 看市场反应
   6. 如果证据充分可做最终判断 → 返回空数组 []

   【可用工具】
   - "price": 获取价格/清算量/资金费率 (CoinGecko/Binance)
   - "search": 搜索新闻/验证多源一致性 (Google Search)
   - "macro": 获取宏观经济数据 (FRED API)
   - "onchain": 获取链上流动性/赎回数据 (DeFiLlama)

   请判断下一步需要哪些工具,返回 JSON:
   {
     "tools": ["price", "macro"],
     "search_keywords": "USDC depeg Circle official statement",
     "macro_indicators": ["CPI"],
     "reason": "需要验证价格偏离度，并补充通胀数据解释市场情绪"
   }

   如果证据已充分,返回: {"tools": [], "macro_indicators": [], "reason": "证据充分,可进行最终判断"}
   ```

**输出**: 更新 `state["next_tools"]`

**触发时机**:
- 初次: Context Gather 后
- 循环: Tool Executor 执行后回到此节点 (最多 3 轮)

---

#### 3. Tool Executor (并行调用 API)

**职责**: 根据 Planner 决策并行调用外部工具,填充证据槽位

**工具定义**:

##### price_snapshot (价格工具)
- **文件**: `src/ai/tools/price_fetcher.py`
- **函数**: `async def get_price_snapshot(asset: str, config: Config) -> dict`
- **数据源**: CoinGecko API / Binance API
- **返回格式**:
  ```json
  {
    "source": "CoinGecko",
    "timestamp": "2025-10-11T10:30:00Z",
    "asset": "USDC",
    "metrics": {
      "price_usd": 0.987,
      "deviation_pct": -1.3,
      "volatility_24h": 2.1,
      "volatility_avg": 0.3,
      "funding_rate": 0.002,
      "liquidation_1h_usd": 1200000000,
      "liquidation_24h_avg": 400000000
    },
    "triggered": true,  # 偏离 > 2% 或清算量 > 均值 3 倍
    "confidence": 0.95
  }
  ```

##### consensus_check (搜索工具)
- **文件**: `src/ai/tools/search_fetcher.py`
- **函数**: `async def search_news(keyword: str, max_results: int, config: Config) -> dict`
- **数据源**: Google Custom Search API / Tavily API
- **返回格式**:
  ```json
  {
    "source": "GoogleSearch",
    "timestamp": "2025-10-11T10:30:00Z",
    "keyword": "USDC depeg",
    "results": [
      {"title": "Circle: USDC reserves safe", "source": "Coindesk", "time": "2h ago"},
      {"title": "USDC depegs to $0.98", "source": "TheBlock", "time": "1h ago"}
    ],
    "multi_source": true,        # >= 3 个独立来源
    "official_confirmed": true,  # 是否有项目方/交易所声明
    "sentiment": {
      "panic": 0.6,
      "neutral": 0.3,
      "optimistic": 0.1
    },
    "triggered": true,  # multi_source=true 且 official_confirmed=true
    "confidence": 0.9
  }
  ```

##### macro_snapshot (宏观数据工具)
- **文件**: `src/ai/tools/macro/fetcher.py`
- **入口**: `MacroTool.snapshot(indicator: str, force_refresh: bool = False)`
- **Provider**: `src/ai/tools/macro/providers/fred.py` (默认使用 FRED，可扩展 Trading Economics)
- **核心能力**:
  - 支持指标枚举: `CPI`, `CORE_CPI`, `FED_FUNDS`, `UNEMPLOYMENT`, `DXY`, `VIX`
  - 计算月环比/年同比、相对移动均线偏离、市场预期偏差(`MACRO_EXPECTATIONS_JSON`)
  - 依据配置阈值输出异常标签 `anomalies`，生成 `triggered` 标记
  - 结果缓存 (`MACRO_CACHE_TTL_SECONDS`, 默认 30 分钟)
- **返回格式**:
  ```json
  {
    "source": "FRED",
    "timestamp": "2025-10-11T10:30:00Z",
    "indicator": "CPI",
    "indicator_name": "美国CPI(城市居民消费价格指数,季调)",
    "metrics": {
      "value": 3.2,
      "previous": 3.0,
      "year_ago": 2.1,
      "change_abs": 0.2,
      "change_mom_pct": 0.35,
      "change_yoy_pct": 1.10,
      "moving_average": 3.05,
      "deviation_from_ma_pct": 1.64,
      "expectation": 3.0,
      "surprise": 0.2,
      "surprise_pct": 0.67,
      "release_time": "2025-10-10T00:00:00"
    },
    "anomalies": {
      "mom_spike": true,
      "consensus_surprise": true
    },
    "thresholds": {
      "mom_pct_threshold": 0.3,
      "yoy_pct_threshold": 0.5,
      "surprise_pct_threshold": 0.2
    },
    "notes": "衡量美国城市居民消费品与服务价格的平均变动",
    "triggered": true,
    "confidence": 1.0
  }
  ```

##### onchain_monitor (链上数据工具)
- **文件**: `src/ai/tools/onchain_fetcher.py`
- **函数**: `async def get_liquidity_stats(asset: str, config: Config) -> dict`
- **数据源**: DeFiLlama API / Etherscan
- **返回格式**:
  ```json
  {
    "source": "DeFiLlama",
    "timestamp": "2025-10-11T10:30:00Z",
    "asset": "USDC",
    "metrics": {
      "tvl_usd": 350000000,
      "tvl_change_1h_pct": -30,
      "redemption_24h_usd": 2000000000,
      "redemption_24h_avg": 500000000,
      "bridge_status": "normal",
      "oracle_status": "normal"
    },
    "triggered": true,  # 流动性下降 > 20% 或赎回量 > 均值 3 倍
    "confidence": 0.85
  }
  ```

**执行逻辑**:
```python
async def _node_executor(state: DeepAnalysisState) -> dict:
    tools_to_call = state["next_tools"]

    # 并行调用工具
    tasks = []
    for tool_name in tools_to_call:
        if tool_name == "price":
            tasks.append(self._price_fetcher.get_price_snapshot(
                state["preliminary"].asset
            ))
        elif tool_name == "search":
            keyword = f"{state['preliminary'].asset} {state['preliminary'].event_type}"
            tasks.append(self._search_fetcher.search_news(keyword, max_results=5))
        # ... 其他工具

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 填充证据槽位
    for tool_name, result in zip(tools_to_call, results):
        if isinstance(result, Exception):
            logger.warning(f"工具 {tool_name} 执行失败: {result}")
            continue

        if tool_name == "price":
            state["price_evidence"] = result
        elif tool_name == "search":
            state["search_evidence"] = result
        # ...

    state["tool_call_count"] += 1
    return state
```

**输出**: 填充对应证据槽位,更新 `state["tool_call_count"]`

**触发时机**: Tool Planner 返回非空 tools 列表时

---

#### 4. 路由节点 (条件分支)

**职责**: 决定下一步流向 (继续调用工具 or 进入最终推理)

**路由逻辑**:
```python
def _route_after_executor(state: DeepAnalysisState) -> str:
    # 达到最大调用次数限制
    if state["tool_call_count"] >= 3:
        logger.info("达到工具调用次数限制 (3 次),进入最终推理")
        return "synthesis"

    # Planner 判断证据充分
    if not state["next_tools"]:
        logger.info("证据充分,进入最终推理")
        return "synthesis"

    # 继续调用工具
    return "planner"
```

**输出**: 路由到 "planner" 或 "synthesis"

---

#### 5. Synthesis (综合推理)

**职责**: 聚合所有证据,输出最终交易信号

**执行逻辑**:
1. 整理所有证据为结构化文本
2. 调用 Gemini (不使用 Function Calling)
3. Prompt 设计:
   ```
   你是加密交易台资深分析师,已掌握完整证据,请给出最终交易建议。

   【原始消息】
   {payload.text}

   【Gemini Flash 初步判断】
   - 摘要: {preliminary.summary}
   - 事件类型: {preliminary.event_type}
   - 资产: {preliminary.asset}
   - 操作: {preliminary.action}
   - 置信度: {preliminary.confidence}

   【价格证据】
   {format_evidence_detail(state["price_evidence"])}

   【搜索证据】
   {format_evidence_detail(state["search_evidence"])}

   【宏观证据】
   {format_evidence_detail(state["macro_evidence"])}

   【链上证据】
   {format_evidence_detail(state["onchain_evidence"])}

   【历史相似事件】
   {format_evidence_detail(state["memory_evidence"])}

   请综合所有证据,给出最终判断:
   1. 验证初步判断的准确性
   2. 调整置信度 (考虑证据一致性/多源确认/历史参照)
   3. 评估风险点 (流动性/监管/市场情绪)
   4. 给出操作建议和理由

   返回 JSON 格式:
   {
     "summary": "中文摘要",
     "event_type": "...",
     "asset": "...",
     "asset_name": "...",
     "action": "buy|sell|observe",
     "direction": "long|short|neutral",
     "confidence": 0.0-1.0,
     "strength": "low|medium|high",
     "timeframe": "short|medium|long",
     "risk_flags": [],
     "notes": "推理依据,引用关键证据",
     "links": []
   }

   【关键要求】
   - 数值异常 + 多源确认一致 → 高置信度 (≥0.8)
   - 数值异常但无多源确认 → 中等置信度 (0.5-0.7)
   - 证据冲突或不足 → 低置信度 (≤0.4),标记 data_incomplete
   - 在 notes 中说明使用了哪些工具证据及其关键数值
   ```

**输出**: 填充 `state["final_response"]`

**触发时机**: 路由节点判定进入最终推理

---

## 记忆协调策略

### 本地记忆 vs Supabase

**本地记忆 (LocalMemoryStore)**:
- 优势: 速度快 (无网络延迟),适合高频关键词匹配
- 劣势: 覆盖范围小 (仅加载到内存的条目),依赖关键词精确匹配
- 使用场景: "脱锚"/"清算"/"黑客"等高频明确事件

**Supabase 记忆 (SupabaseMemoryRepository)**:
- 优势: 覆盖全量历史,向量语义搜索 (找相似但关键词不同的事件)
- 劣势: 速度慢 (网络 + 数据库查询),依赖 embedding 质量
- 使用场景: 复杂叙事事件,需要历史相似模式参照

**混合策略 (Context Gather 节点)**:
```
1. 并行查询两个数据源 (避免串行等待)
2. 本地记忆: 关键词 in ["脱锚", "depeg", "清算", "hack"] → 快速匹配
3. Supabase 记忆: 向量相似度搜索,asset_codes 过滤
4. 合并结果:
   - 去重 (by event_id or summary hash)
   - 按 similarity 降序排序
   - 取 top 3 (配置 MEMORY_MAX_NOTES)
5. 如果 Supabase 查询超时 (>2s) → 仅使用本地结果,不阻塞流程
```

### 网络搜索时机

**优先级策略**:
1. **数值主导事件** (depeg/liquidation/whale):
   - 第1轮: 调用 price (验证数值真实性)
   - 如果 price.triggered=true → 第2轮: 调用 search (验证事件背景)

2. **叙事主导事件** (hack/regulation/partnership):
   - 第1轮: 调用 search (验证消息真实性和多源一致性)
   - 如果 search.multi_source=true → 第2轮: 调用 price (观察市场反应)

3. **宏观事件** (macro):
   - 第1轮: 调用 macro (获取最新数据)
   - 第2轮: 调用 search (获取市场解读方向)
   - 第3轮: 调用 price (观察 Crypto 市场反应)

**动态补拉逻辑** (Tool Planner 决策):
```
if price_evidence and price_evidence["triggered"]:
    # 价格异常 → 补拉搜索验证
    next_tools.append("search")
    reason = "价格数据显示异常,需验证事件背景"

if search_evidence and search_evidence["multi_source"] and search_evidence["official_confirmed"]:
    # 多源确认 → 补拉价格看反应
    next_tools.append("price")
    reason = "新闻多源确认,需观察市场反应程度"
```

---

## 配置参数

在 `.env` 新增:

```bash
# ==================== 深度分析工具配置 ====================

# 工具总开关
DEEP_ANALYSIS_TOOLS_ENABLED=true

# 工具调用限制
DEEP_ANALYSIS_MAX_TOOL_CALLS=3           # 最大工具调用轮次
DEEP_ANALYSIS_TOOL_TIMEOUT=10            # 单个工具超时 (秒)

# 价格工具配置
COINGECKO_API_KEY=                       # CoinGecko API Key (可选,免费版限流)
BINANCE_API_KEY=                         # Binance API Key (可选)
PRICE_DEVIATION_THRESHOLD=2.0            # 价格偏离阈值 (%)
LIQUIDATION_MULTIPLIER=3.0               # 清算量异常倍数

# 搜索工具配置
GOOGLE_SEARCH_API_KEY=                   # Google Custom Search API Key
GOOGLE_SEARCH_CX=                        # Google Custom Search Engine ID
TAVILY_API_KEY=                          # Tavily API Key (备选)
SEARCH_MAX_RESULTS=5                     # 最大搜索结果数
SEARCH_MULTI_SOURCE_THRESHOLD=3          # 多源一致性阈值 (来源数)

# 宏观数据工具配置
FRED_API_KEY=                            # FRED API Key
MACRO_EXPECTATION_THRESHOLD=0.2          # 宏观数据超预期阈值 (%)

# 链上数据工具配置
DEFILLAMA_API_KEY=                       # DeFiLlama API Key (可选)
ETHERSCAN_API_KEY=                       # Etherscan API Key (可选)
LIQUIDITY_CHANGE_THRESHOLD=20.0          # 流动性变化阈值 (%)
REDEMPTION_MULTIPLIER=3.0                # 赎回量异常倍数

# 工具开关 (可单独禁用)
TOOL_PRICE_ENABLED=true
TOOL_SEARCH_ENABLED=true
TOOL_MACRO_ENABLED=false                 # 默认关闭 (可选工具)
TOOL_ONCHAIN_ENABLED=false               # 默认关闭 (可选工具)
```

- **现有 Demo Key**: `CG-jqfVyg8KDjKCcKRkpkg1Bc3p` (可直接写入 `.env` 的 `COINGECKO_API_KEY`, 如后续轮换请在此记录最新值)
- 需要同时为生产环境配置安全存储 (如 Supabase Secrets / GCP Secret Manager),避免硬编码

---

## 实现路径 (分步迭代)

### Phase 1: 基础框架 + Tavily 搜索工具 (第 1-2 周)

**目标**: 搭建 LangGraph 子图骨架,实现 Tavily 搜索工具,验证流程可行性

**为什么选择 Tavily**:
- Google Custom Search 免费配额仅 100 次/天,不适合生产环境
- Tavily 专为 AI 应用优化,返回结构化数据 (标题/摘要/相关性评分)
- 免费层 1000 次/月,付费层 $20/月 无限量
- API 简单,单次调用返回多源结果 + 可信度评分

---

#### 任务清单

##### Day 1: 工具基础架构

**1.1 创建工具目录结构**
```
src/ai/tools/
├── __init__.py              # 导出所有工具
├── base.py                  # 工具基类和统一返回格式
├── search_fetcher.py        # Tavily 搜索工具实现
└── exceptions.py            # 工具异常定义
```

**1.2 实现工具基类** (`base.py`)
- [ ] 定义 `ToolResult` 数据类:
  ```python
  @dataclass
  class ToolResult:
      source: str              # 工具来源 (如 "Tavily")
      timestamp: str           # ISO 8601 时间戳
      success: bool            # 调用是否成功
      data: dict              # 结构化数据
      triggered: bool          # 是否触发异常阈值
      confidence: float        # 结果可信度 (0.0-1.0)
      error: Optional[str]     # 错误信息
  ```
- [ ] 定义 `BaseTool` 抽象类:
  - 抽象方法: `async def fetch(self, **kwargs) -> ToolResult`
  - 通用方法: `_format_timestamp()`, `_handle_timeout()`
- [ ] 实现工具异常类 (`ToolFetchError`, `ToolTimeoutError`)

**1.3 实现 Tavily 搜索工具** (`search_fetcher.py`)
- [ ] 实现 `TavilySearchFetcher(BaseTool)` 类
- [ ] 初始化配置:
  - API Key (from `config.TAVILY_API_KEY`)
  - 超时时间 (from `config.DEEP_ANALYSIS_TOOL_TIMEOUT`)
  - 最大结果数 (from `config.SEARCH_MAX_RESULTS`)
- [ ] 实现 `fetch()` 方法:
  - 输入参数: `keyword: str`, `max_results: int = 5`
  - 调用 Tavily API: `POST https://api.tavily.com/search`
  - 请求体:
    ```json
    {
      "api_key": "...",
      "query": "USDC depeg",
      "max_results": 5,
      "search_depth": "basic",
      "include_domains": ["coindesk.com", "theblock.co", "cointelegraph.com"],
      "include_answer": false
    }
    ```
  - 解析响应,提取关键字段:
    - `results`: 搜索结果列表 (title/url/content/score)
    - `answer`: 简短摘要 (如果 include_answer=true)
- [ ] 实现多源一致性判断:
  - 规则: `len(results) >= SEARCH_MULTI_SOURCE_THRESHOLD` (默认 3)
  - 遍历结果,检测是否有官方来源 (domain 包含 "official"/"gov"/"项目名")
- [ ] 实现官方确认检测:
  - 关键词匹配: title/content 包含 "官方"/"声明"/"公告"/"Official"/"Statement"
- [ ] 实现情绪分析 (简化版):
  - 统计负面词频率 (暴跌/崩盘/恐慌/hack) → panic
  - 统计中性词频率 (观察/等待/监控) → neutral
  - 统计正面词频率 (恢复/稳定/反弹) → optimistic
- [ ] 返回 `ToolResult`:
  ```python
  ToolResult(
      source="Tavily",
      timestamp="2025-10-11T10:30:00Z",
      success=True,
      data={
          "keyword": "USDC depeg",
          "results": [
              {"title": "...", "source": "Coindesk", "url": "...", "score": 0.95},
              {"title": "...", "source": "TheBlock", "url": "...", "score": 0.89}
          ],
          "multi_source": True,
          "official_confirmed": True,
          "sentiment": {"panic": 0.6, "neutral": 0.3, "optimistic": 0.1},
          "source_count": 5
      },
      triggered=True,  # multi_source=True 且 official_confirmed=True
      confidence=0.9
  )
  ```
- [ ] 异常处理:
  - API 超时 → 返回 `success=False, error="timeout"`
  - API 限流 (429) → 返回 `success=False, error="rate_limit"`
  - 无结果 → 返回 `success=True, triggered=False, data={"results": []}`

**1.4 单元测试** (`tests/ai/tools/test_search_fetcher.py`)
- [ ] 测试真实 API 调用 (需要 API Key)
- [ ] 测试超时场景 (mock httpx.AsyncClient)
- [ ] 测试限流场景 (mock 429 响应)
- [ ] 测试多源一致性判断逻辑
- [ ] 测试官方确认检测逻辑

---

##### Day 2: LangGraph 状态对象与节点骨架

**2.1 定义状态对象** (在 `gemini.py` 顶部)
```python
from typing import TypedDict, Optional

class DeepAnalysisState(TypedDict, total=False):
    # 输入
    payload: EventPayload
    preliminary: SignalResult

    # 证据槽位 (Phase 1 只有 search)
    search_evidence: Optional[dict]
    memory_evidence: Optional[dict]

    # 控制流
    next_tools: list[str]        # ["search"] or []
    tool_call_count: int         # 0-3
    max_tool_calls: int          # 固定为 3

    # 输出
    final_response: str          # JSON 字符串
```

**2.2 实现节点方法骨架**
- [ ] `_node_context_gather(self, state: DeepAnalysisState) -> dict`
  - 复用现有 `_tool_fetch_memories` 逻辑
  - 返回 `{"memory_evidence": {...}}`
- [ ] `_node_tool_planner(self, state: DeepAnalysisState) -> dict`
  - 简化 prompt,只决策是否调用 search
  - 返回 `{"next_tools": ["search"] or []}`
- [ ] `_node_tool_executor(self, state: DeepAnalysisState) -> dict`
  - 只处理 search 工具
  - 返回 `{"search_evidence": {...}, "tool_call_count": state["tool_call_count"] + 1}`
- [ ] `_node_synthesis(self, state: DeepAnalysisState) -> dict`
  - 综合记忆和搜索结果
  - 返回 `{"final_response": "..."}`

**2.3 实现路由方法**
- [ ] `_route_after_planner(self, state: DeepAnalysisState) -> str`
  - 如果 `next_tools` 为空 → 返回 "synthesis"
  - 否则 → 返回 "executor"
- [ ] `_route_after_executor(self, state: DeepAnalysisState) -> str`
  - 如果 `tool_call_count >= max_tool_calls` → 返回 "synthesis"
  - 否则 → 返回 "planner"

---

##### Day 3-4: 实现 LangGraph 子图

**3.1 实现 `_build_deep_graph()` 方法**
```python
def _build_deep_graph(self) -> CompiledGraph:
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

**3.2 实现 Context Gather 节点**
- [ ] 调用现有 `_tool_fetch_memories` 获取记忆
- [ ] 格式化为简洁文本 (标题 + 置信度 + 相似度)
- [ ] 填充 `state["memory_evidence"]`

**3.3 实现 Tool Planner 节点**
- [ ] 构建简化 prompt:
  ```
  你是工具调度专家,判断是否需要搜索新闻验证。

  【消息内容】{payload.text}
  【事件类型】{preliminary.event_type}
  【资产】{preliminary.asset}

  【已有证据】
  - 历史记忆: {format_memory(state["memory_evidence"])}
  - 搜索结果: {format_search(state["search_evidence"])}

  【决策规则】
  1. 如果事件类型是 hack/regulation/partnership/celebrity → 需要搜索验证
  2. 如果已有搜索结果且 multi_source=true → 证据充分,无需再搜索
  3. 如果 tool_call_count >= 2 → 证据充分,无需再搜索

  返回 JSON:
  - 需要搜索: {"tools": ["search"], "reason": "传闻类事件需多源验证"}
  - 无需搜索: {"tools": [], "reason": "已有充分证据"}
  ```
- [ ] 调用 Gemini 获取决策 JSON
- [ ] 解析 `tools` 字段,更新 `state["next_tools"]`

**3.4 实现 Tool Executor 节点**
- [ ] 初始化 `TavilySearchFetcher`
- [ ] 构建搜索关键词:
  ```python
  keyword = f"{state['preliminary'].asset} {state['preliminary'].event_type}"
  if state["preliminary"].event_type in ["hack", "regulation"]:
      keyword += " news official"
  ```
- [ ] 调用 `fetcher.fetch(keyword=keyword, max_results=5)`
- [ ] 将 `ToolResult.data` 填充到 `state["search_evidence"]`
- [ ] 递增 `state["tool_call_count"]`

**3.5 实现 Synthesis 节点**
- [ ] 构建综合推理 prompt:
  ```
  你是加密交易台资深分析师,已掌握完整证据,请给出最终判断。

  【原始消息】{payload.text}

  【Gemini Flash 初步判断】
  - 事件类型: {preliminary.event_type}
  - 资产: {preliminary.asset}
  - 操作: {preliminary.action}
  - 置信度: {preliminary.confidence}

  【历史记忆】
  {format_memory_detail(state["memory_evidence"])}

  【搜索验证】
  {format_search_detail(state["search_evidence"])}

  请综合判断:
  1. 搜索结果是否确认事件真实性 (multi_source + official_confirmed)
  2. 结合历史案例调整置信度
  3. 如果搜索结果冲突或不足,降低置信度并标记 data_incomplete

  返回 JSON (与现有 SignalResult 格式一致):
  {
    "summary": "中文摘要",
    "event_type": "...",
    "asset": "...",
    "action": "buy|sell|observe",
    "confidence": 0.0-1.0,
    "risk_flags": [],
    "notes": "推理依据,引用搜索来源"
  }
  ```
- [ ] 调用 Gemini 获取最终 JSON
- [ ] 填充 `state["final_response"]`

---

##### Day 5: 集成到 analyse() 方法

**5.1 修改 `analyse()` 方法**
- [ ] 在方法开头添加特性开关判断:
  ```python
  async def analyse(self, payload, preliminary):
      # 检查是否启用工具增强
      tools_enabled = getattr(self._config, "DEEP_ANALYSIS_TOOLS_ENABLED", False)

      if not tools_enabled:
          # Fallback: 使用现有 Function Calling 流程
          return await self._analyse_with_function_calling(payload, preliminary)

      # [NEW] LangGraph 工具编排流程
      try:
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
          return self._parse_json(final_state["final_response"])

      except Exception as exc:
          logger.error("LangGraph 工具编排失败,降级到现有流程: %s", exc)
          return await self._analyse_with_function_calling(payload, preliminary)
  ```
- [ ] 重构现有逻辑为 `_analyse_with_function_calling()` 方法 (保持向后兼容)

**5.2 在 `__init__` 初始化工具**
```python
def __init__(self, *, client, memory_bundle, parse_json_callback, ...):
    # ... 现有初始化
    self._config = config  # 保存配置引用
    self._search_fetcher = TavilySearchFetcher(config) if getattr(config, "TOOL_SEARCH_ENABLED", False) else None
```

---

##### Day 6-7: 测试与调优

**6.1 功能测试**
- [ ] 准备测试消息:
  - 传闻类: "Coinbase 即将上线 XYZ 代币"
  - 政策类: "SEC 批准 BTC ETF"
  - 黑客类: "XXX 协议被攻击,损失 $100M"
- [ ] 验证流程:
  1. 消息触发深度分析
  2. Context Gather 拉取记忆
  3. Tool Planner 决策调用 search
  4. Tool Executor 调用 Tavily API
  5. Synthesis 综合证据输出最终信号
- [ ] 检查日志:
  - LangGraph 节点执行顺序
  - Tavily API 请求/响应
  - 最终置信度变化 (vs Gemini Flash 初判)

**6.2 边界测试**
- [ ] Tavily API 超时 → 验证降级到现有流程
- [ ] Tavily API 限流 → 验证错误处理
- [ ] 无搜索结果 → 验证 Synthesis 能处理空证据
- [ ] 搜索结果冲突 (不同来源说法矛盾) → 验证置信度下调

**6.3 成本与延迟测试**
- [ ] 统计 10 条消息的平均延迟
- [ ] 统计 Tool Planner/Executor/Synthesis 各自耗时
- [ ] 统计 Tavily API 调用次数 (应 ≤ 1 次/条)
- [ ] 计算平均成本:
  - Tool Planner (Gemini): $0.01
  - Tavily API: $0.002 (按 $20/月无限量估算)
  - Synthesis (Gemini): $0.02
  - 总计: $0.032/条

**6.4 Prompt 调优**
- [ ] 如果 Tool Planner 过度调用 search → 增强 prompt 约束条件
- [ ] 如果 Synthesis 置信度调整不合理 → 优化证据权重描述
- [ ] 如果搜索结果质量低 → 调整 Tavily 请求参数 (include_domains/search_depth)

**6.5 可观测性增强**
- [ ] 在每个节点添加详细日志:
  ```python
  logger.info("🧠 Context Gather: 找到 %d 条历史记忆", len(memory_entries))
  logger.info("🤖 Tool Planner 决策: %s, 理由: %s", next_tools, reason)
  logger.info("🔧 Tool Executor: 调用 Tavily, 关键词='%s', 结果数=%d", keyword, len(results))
  logger.info("📊 Synthesis: 最终置信度 %.2f (初判 %.2f)", final_conf, preliminary.confidence)
  ```
- [ ] 记录工具调用到数据库 (可选):
  - 表: `deep_analysis_tool_calls`
  - 字段: event_id, tool_name, request_params, response_data, latency_ms, success

---

#### 验收标准

- [ ] **功能完整性**:
  - 传闻/政策/黑客类消息能触发 Tavily 搜索
  - 搜索结果正确填充 `search_evidence` (multi_source/official_confirmed/sentiment)
  - Synthesis 能综合搜索结果和记忆调整置信度
  - 搜索失败时能降级到现有流程,不阻塞消息处理

- [ ] **性能指标**:
  - 平均延迟 < 8s (Context 1s + Planner 2s + Executor 2s + Synthesis 3s)
  - Tavily API 调用成功率 > 95%
  - 工具调用次数 ≤ 1 次/条 (Phase 1 简化场景)

- [ ] **成本控制**:
  - 平均成本 < $0.05/条 (Planner $0.01 + Tavily $0.002 + Synthesis $0.02 + 缓冲 $0.018)
  - Tavily 月度配额不超限 (1000 次免费 or $20 无限量)

- [ ] **质量提升**:
  - 传闻类消息的置信度准确性提升 (对比人工标注)
  - 误报率下降 (通过多源验证过滤虚假传闻)
  - Synthesis 的 notes 字段包含引用搜索来源

- [ ] **可维护性**:
  - 代码有完整注释和类型标注
  - 工具逻辑与 LangGraph 逻辑解耦 (方便后续扩展其他工具)
  - 配置开关 `DEEP_ANALYSIS_TOOLS_ENABLED` 可随时关闭新功能

---

#### 配置参数 (Phase 1)

在 `.env` 新增:
```bash
# ==================== 深度分析工具配置 (Phase 1) ====================

# 工具总开关
DEEP_ANALYSIS_TOOLS_ENABLED=false        # 默认关闭,测试通过后开启

# 工具调用限制
DEEP_ANALYSIS_MAX_TOOL_CALLS=3           # 最大工具调用轮次
DEEP_ANALYSIS_TOOL_TIMEOUT=10            # 单个工具超时 (秒)

# Tavily 搜索配置
TAVILY_API_KEY=                          # Tavily API Key (必填)
TOOL_SEARCH_ENABLED=true                 # 搜索工具开关
SEARCH_MAX_RESULTS=5                     # 最大搜索结果数
SEARCH_MULTI_SOURCE_THRESHOLD=3          # 多源一致性阈值 (来源数)
SEARCH_INCLUDE_DOMAINS=coindesk.com,theblock.co,cointelegraph.com  # 优先域名 (逗号分隔)

# 价格工具 (Phase 2)
TOOL_PRICE_ENABLED=true
DEEP_ANALYSIS_PRICE_PROVIDER=coingecko
COINGECKO_API_KEY=xxx
PRICE_CACHE_TTL_SECONDS=60

# 宏观工具 (Phase 3)
TOOL_MACRO_ENABLED=true
DEEP_ANALYSIS_MACRO_PROVIDER=fred
FRED_API_KEY=xxx
MACRO_CACHE_TTL_SECONDS=1800
# 可选: 提前写入市场预期, 例如 {"CPI":3.0,"FED_FUNDS":5.50}
MACRO_EXPECTATIONS_JSON=

# 其他工具 (Phase 3+/可选)
TOOL_ONCHAIN_ENABLED=false
```

---

#### Tavily API 使用说明

**API 端点**: `POST https://api.tavily.com/search`

**请求示例**:
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

**响应示例**:
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

**配额与定价**:
- 免费层: 1000 次/月
- Pro 层: $20/月,无限量,更快响应
- 单次调用平均延迟: 1-2s

**错误处理**:
- 401: API Key 无效 → 检查配置
- 429: 超过配额 → 等待下月重置或升级 Pro
- 503: 服务不可用 → 重试 3 次后降级

---

#### Phase 1 后续优化方向

完成 Phase 1 后,根据实际运行数据评估:

1. **搜索质量优化**:
   - 如果 Tavily 结果质量不佳 → 调整 `include_domains` 或 `search_depth`
   - 如果误报率高 → 增强多源一致性判断逻辑 (检查来源权威性)

2. **成本优化**:
   - 如果 Tavily 月度配额超限 → 实现结果缓存 (10 分钟内相同关键词复用)
   - 如果 Planner 过度调用 → 优化 prompt 或增加事件类型白名单

3. **扩展到 Phase 2**:
   - 如果搜索工具效果显著 → 优先实现 price 工具 (脱锚场景)
   - 如果传闻验证需求不高 → 跳过 Phase 2,专注优化现有流程

---

### Phase 2: 价格工具 (第 3 周)

**目标**: 实现价格工具,支持脱锚/清算场景

**任务清单**:

1. **实现价格工具** (Day 1-2)
   - [ ] `src/ai/tools/price_fetcher.py`
   - [ ] 集成 CoinGecko API (`/simple/price`)
   - [ ] 集成 Binance API (`/api/v3/ticker/24hr`)
   - [ ] 实现价格偏离度计算 (vs 锚定价或历史均值)
   - [ ] 实现清算量获取 (Coinglass API)
   - [ ] 实现资金费率获取 (Binance API)
   - [ ] 实现异常判断逻辑 (偏离 > 2% 或清算量 > 均值 3 倍)
   - [ ] 单元测试

2. **扩展 Tool Executor** (Day 2-3)
   - [ ] 在 `_node_executor` 添加 price 工具处理
   - [ ] 实现并行调用 (price + search)

3. **优化 Tool Planner** (Day 3-4)
   - [ ] 扩展 prompt,支持 price/search 双工具决策
   - [ ] 实现动态补拉逻辑:
     - [ ] price.triggered=true → 追加 search
     - [ ] search.multi_source=true → 追加 price

4. **测试脱锚场景** (Day 5)
   - [ ] 测试 USDC/USDT 脱锚消息
   - [ ] 验证 price → search 补拉流程
   - [ ] 验证 Synthesis 能综合价格和搜索证据

**验收标准**:
- [ ] 脱锚消息能触发 price 工具
- [ ] price 异常时自动补拉 search
- [ ] 最终置信度符合预期 (异常+多源确认 → ≥0.8)

#### price_fetcher.py 设计细节 (CoinGecko)

**核心职责**: 针对单个资产返回"是否出现价格异常"的结构化判断,为 Synthesis 提供客观数值证据。

**数据源选择**:
- **主源**: CoinGecko API (免费,覆盖 12k+ 资产,支持 1 分钟级别价格历史)
- **备源**: Binance 公开行情 (`/api/v3/ticker/24hr`) —— 仅当资产存在现货交易对时触发,用于交叉验证
- **扩展**: Coinglass Liquidation API、Binance Funding Rate API (Phase 2.5,可选)

**认证方式**:
- 在请求 Header 中附加 `x-cg-demo-api-key: {config.COINGECKO_API_KEY}`
- 若升级 Pro,Header 改为 `x-cg-pro-api-key`
- 免费版速率限制: 10-30 次/分钟 (按资产),需加缓存

**调用组合** _(单资产一次调用不超过 2 个 HTTP 请求)_:
1. `GET /api/v3/simple/price`
   - 参数: `ids={coingecko_id}&vs_currencies=usd&include_24hr_vol=true&include_24hr_change=true`
   - 获得: `price_usd`, `volume_24h`, `price_change_24h_pct`
2. `GET /api/v3/coins/{coingecko_id}/market_chart`
   - 参数: `vs_currency=usd&days=1&interval=hourly`
   - 获得: 最近 24 小时每小时价格,用于计算 `volatility_24h`、`volatility_avg`
3. (可选) `GET /api/v3/coins/{coingecko_id}`
   - 仅当资产首度出现或缺少 `market_data` 时,补充市值、市值占比等信息

**资产 ID 映射**:
- 在 `data/asset_registry.json` (新建) 维护 `{symbol: coingecko_id}`
- 若未命中映射:
  1. 调用 `/api/v3/search?query={symbol}`
  2. 根据 `symbol` + `market_cap_rank` 选择权重最高的条目
  3. 结果写回缓存 (`.cache/coingecko_ids.json`) 减少重复查询
- 支持别名 (如 `USDC.e`, `WETH`) → 通过正则清洗后匹配

**指标计算**:
```python
price_usd = simple_price["usd"]
historical_prices = market_chart["prices"]  # [[timestamp, price], ...]

# 与锚定价比较 (稳定币)
anchor_price = 1.0 if asset in STABLECOIN_SET else price_usd_baseline(asset)
deviation_pct = ((price_usd - anchor_price) / anchor_price) * 100

# 波动率: 24h 标准差,再与 7 日均值比较
volatility_24h = np.std([p for _, p in historical_prices])
volatility_avg = rolling_volatility_cache.get(asset, default=volatility_24h)

# 清算/资金费率 (Phase 2.5)
liquidation_1h_usd = coinglass_client.fetch_liquidation(asset, window="1h")
funding_rate = binance_client.fetch_funding_rate(asset)
```
- 若缺少清算/资金费率数据 → 字段留空 (None),不影响触发判断

**异常判定规则**:
- `triggered = abs(deviation_pct) >= PRICE_DEVIATION_THRESHOLD`
- 稳定币额外规则: `price_usd < 0.995` 或 `price_usd > 1.005`
- 衍生指标:
  - `volatility_spike = volatility_24h / max(volatility_avg, 1e-6)`
  - `volatility_spike >= 3` 视为异常 → 补拉搜索工具
  - 若资金费率 > 0.05 (5%) 或 < -0.05 → 标记风险

**返回结构 (更新版)**:
```json
{
  "source": "CoinGecko",
  "timestamp": "2025-10-11T10:30:00Z",
  "asset": "USDC",
  "metrics": {
    "price_usd": 0.987,
    "deviation_pct": -1.3,
    "price_change_1h_pct": -0.8,
    "price_change_24h_pct": -1.6,
    "volatility_24h": 1.9,
    "volatility_avg": 0.4,
    "volume_24h_usd": 1200000000,
    "liquidation_1h_usd": null,
    "liquidation_24h_avg": null,
    "funding_rate": null
  },
  "anomalies": {
    "price_depeg": true,
    "volatility_spike": true,
    "funding_extreme": false
  },
  "triggered": true,
  "confidence": 0.9,
  "notes": "USDC 价格跌至 $0.987, 偏离锚定 1.3%, 24h 波动率为 1.9"
}
```

**错误与降级策略**:
- 429/5xx → 重试 2 次,退避间隔 0.5s/1s
- 超时 (≥ config.DEEP_ANALYSIS_TOOL_TIMEOUT) → 记录警告,返回 `success=False`
- 若 CoinGecko 不可用:
  1. 尝试 Binance `/ticker/price` 获取现价
  2. 缺少历史波动数据 → `volatility_*` 置为 None,降低 `confidence` 至 0.6
- 针对稳定币增加人工兜底: 价格缺失时使用上一次缓存值 (有效期 2 分钟)

**缓存与配额控制**:
- 使用 `functools.lru_cache(maxsize=128, ttl=60)` or 简易内存缓存:
  - 相同资产 60s 内直接复用
  - `market_chart` 结果缓存 5 分钟 (成本高,数据刷新频率低)
- 在 `GeminiDeepAnalysisEngine` 层记录每日调用次数,超出 `DEEP_ANALYSIS_TOOL_DAILY_LIMIT` 时自动降级到搜索工具

**单元测试建议**:
1. `test_price_fetcher_happy_path` —— Mock CoinGecko 响应,验证指标计算
2. `test_price_fetcher_stablecoin_depeg` —— 输入价格 0.98,确保触发
3. `test_price_fetcher_timeout` —— 模拟超时,检查错误处理
4. `test_price_fetcher_cache` —— 连续调用同一资产,确保命中缓存
5. `test_price_fetcher_liquidation_optional` —— 缺失清算数据时字段为空但不触发异常

**后续扩展路线**:
- Phase 2.5: 接入 Coinglass (清算) + Binance Funding Rate,补齐高级指标
- Phase 3: 引入 Kaiko/Amberdata 作为机构级数据备选,提升可靠性
- Phase 4: 在 Tool Planner 中记录价格异常类型 (脱锚/暴涨/暴跌),用于历史对比

---

### Phase 3: 宏观和链上工具 (第 4 周,可选)

**目标**: 补充宏观和链上工具,覆盖更多场景

**任务清单**:

1. **实现宏观工具** (Day 1-2)
   - [x] `src/ai/tools/macro/fetcher.py`
   - [x] 集成 FRED API (`/series/observations`)
   - [x] 支持 CPI/利率/美债等指标
   - [x] 实现超预期判断 (基于 `MACRO_EXPECTATIONS_JSON` 与阈值)

2. **实现链上工具** (Day 3-4)
   - [ ] `src/ai/tools/onchain_fetcher.py`
   - [ ] 集成 DeFiLlama API (`/tvl`, `/protocol`)
   - [ ] 集成 Etherscan API (可选)
   - [ ] 实现流动性变化/赎回量异常判断

3. **扩展 Tool Planner** (Day 5)
   - [ ] 支持 4 工具动态编排
   - [ ] 针对 macro 事件类型优化决策逻辑

**验收标准**:
- [ ] CPI/加息消息能触发 macro 工具
- [ ] 流动性异常消息能触发 onchain 工具
- [ ] 工具调用仍在预算内 (≤ 3 轮)

---

### Phase 4: 优化与监控 (第 5 周)

**目标**: 性能优化,成本控制,可观测性增强

**任务清单**:

1. **工具缓存** (Day 1-2)
   - [ ] 实现工具结果缓存 (Redis or 内存)
   - [ ] 相同资产 5 分钟内复用价格数据
   - [ ] 相同关键词 10 分钟内复用搜索结果

2. **成本优化** (Day 2-3)
   - [ ] 统计每条消息的工具调用次数和成本
   - [ ] 优化 Tool Planner prompt (减少过度调用)
   - [ ] 针对低价值事件类型跳过工具调用

3. **可观测性** (Day 3-4)
   - [ ] LangGraph 节点日志增强
   - [ ] 记录每个工具的响应时间
   - [ ] 记录证据一致性 (price vs search 是否冲突)
   - [ ] Dashboard: 工具调用分布/成功率/平均延迟

4. **A/B 测试** (Day 5)
   - [ ] 对比启用工具 vs 不启用的准确率
   - [ ] 统计高置信度信号的正确率

**验收标准**:
- [ ] 平均延迟 < 10s
- [ ] 平均成本 < $0.08/条
- [ ] 工具调用成功率 > 95%

---

## 成本与性能预估

### 延迟

| 场景 | 工具调用 | LangGraph 轮次 | 预估延迟 | 说明 |
|------|---------|---------------|---------|------|
| 简单 (只调 price) | 1 次 | 3 轮 | 5-8s | Planner → Executor → Synthesis |
| 中等 (price + search) | 2 次 | 5 轮 | 8-12s | Planner → Executor → Planner → Executor → Synthesis |
| 复杂 (3 个工具) | 3 次 | 7 轮 | 12-15s | 最多 3 轮工具调用 |

### 成本

| 组件 | 单次成本 | 备注 |
|------|---------|------|
| Tool Planner (Gemini) | $0.01 | 每轮决策 |
| Tool Executor (API) | $0.001-0.01 | 取决于 API (CoinGecko 免费,Google Search $5/1000 次) |
| Synthesis (Gemini) | $0.02 | 综合推理 |
| **总成本** | **$0.03-0.10** | 简单场景 $0.03,复杂场景 $0.10 |

### 优化目标

- 通过缓存降低 API 调用: 50% 命中率 → 成本减半
- 通过 Planner prompt 优化: 减少不必要工具调用 → 延迟降低 20%

---

## 风险与缓解

### 风险 1: API 配额耗尽

**场景**: Google Search API 免费配额 100 次/天,高并发场景下快速耗尽

**缓解**:
- 实现多 API 轮询 (Google Search → Tavily → Bing Search)
- 工具缓存 (10 分钟内相同关键词复用)
- 降级策略: API 失败时使用 Gemini 内部知识推理

### 风险 2: 工具响应慢导致超时

**场景**: DeFiLlama API 偶尔响应超过 10s

**缓解**:
- 设置工具超时 (`DEEP_ANALYSIS_TOOL_TIMEOUT=10`)
- 超时后记录异常但不阻塞流程
- Synthesis 能处理部分证据缺失场景

### 风险 3: 证据冲突导致判断失误

**场景**: price 显示异常但 search 无多源确认

**缓解**:
- Tool Planner 识别冲突 → 标记 "需要人工/延迟"
- Synthesis 降低置信度 (≤0.4) + 添加 `data_incomplete` 风险标志
- 在 notes 中说明冲突原因

### 风险 4: 成本超预算

**场景**: 每条消息调用 3 轮工具 → 成本 $0.10 × 1000 条/天 = $100/天

**缓解**:
- 设置每日工具调用配额 (如 500 次)
- 优先级策略: 高价值事件 (depeg/hack) 优先使用工具
- 实时监控成本,超阈值时自动降级

---

## 监控指标

### 工具调用指标

- `deep_analysis_tool_calls_total`: 工具调用总次数 (按工具类型分组)
- `deep_analysis_tool_success_rate`: 工具调用成功率
- `deep_analysis_tool_latency`: 工具响应延迟 (P50/P95/P99)
- `deep_analysis_tool_cost`: 工具调用成本

### 决策质量指标

- `deep_analysis_confidence_distribution`: 最终置信度分布
- `deep_analysis_evidence_consistency`: 证据一致性 (price vs search)
- `deep_analysis_planner_rounds`: Planner 决策轮次分布

### 业务指标

- `deep_analysis_high_confidence_signals`: 高置信度信号数 (confidence ≥ 0.8)
- `deep_analysis_evidence_conflicts`: 证据冲突数 (需要人工介入)

---

## 参考资料

### API 文档

- [CoinGecko API](https://www.coingecko.com/en/api/documentation)
- [Binance API](https://binance-docs.github.io/apidocs/spot/en/)
- [Google Custom Search API](https://developers.google.com/custom-search/v1/overview)
- [FRED API](https://fred.stlouisfed.org/docs/api/fred/)
- [DeFiLlama API](https://defillama.com/docs/api)

### 工具选型

- **搜索 API**: Google Search (主) + Tavily (备) + Bing (备)
- **价格 API**: CoinGecko (主,免费但限流) + Binance (备,需 API Key)
- **宏观 API**: FRED (美国数据,免费) + Trading Economics (全球数据,付费)
- **链上 API**: DeFiLlama (TVL/协议数据,免费) + Glassnode (高级链上指标,付费)

---

## 变更日志

- 2025-10-11: 初版方案,定义整体架构和 5 节点设计
- 2025-10-11: 新增分步实现路径,优先级为 search → price → macro/onchain
