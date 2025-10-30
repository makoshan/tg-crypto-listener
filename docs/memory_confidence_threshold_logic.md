# 记忆搜索置信度阈值逻辑详解

## ⚠️ 重要澄清：记忆搜索的触发条件和 min_confidence 的作用

### 关键问题解答

**Q1: 在快速分析之前，还没有当前消息的 confidence，如何决定要不要查询历史记忆？**

**A: 记忆搜索的触发条件不依赖当前消息的 confidence！**

记忆搜索的触发条件：
```python
# src/listener.py:501
if self.config.MEMORY_ENABLED and self.memory_repository:
    # 只要配置开启且 repository 存在，就会触发记忆搜索
    # 与当前消息的 confidence 无关（因为此时还没有分析）
```

**触发条件清单：**
- ✅ `MEMORY_ENABLED=true`（配置开关）
- ✅ `memory_repository` 已初始化（存在）
- ✅ 有 `embedding` 或 `keywords`（查询条件）
- ❌ **不依赖当前消息的 confidence**（因为此时还没有分析结果）

**Q2: `min_confidence=0.5/0.6` 是用来比较什么的？**

**A: 用来过滤历史记录的 confidence，不是当前消息的 confidence！**

```python
# 流程说明：
# 1. 记忆搜索触发（不依赖当前消息的 confidence）
# 2. 查询数据库中的历史记录（news_events + ai_signals）
# 3. 数据库中的历史记录都有 confidence 字段（来自之前分析的 ai_signals 表）
# 4. min_confidence 用来过滤历史记录：只返回 confidence >= 0.5/0.6 的历史记录
```

**数据库层面的过滤：**
- `search_memory` RPC 函数会查询 `ai_signals` 表
- `ai_signals` 表中每条历史记录都有 `confidence` 字段（0.0-1.0）
- `min_confidence` 用于 SQL WHERE 条件：`WHERE ai_signals.confidence >= min_confidence`
- **比较的是历史记录的 confidence vs min_confidence，不是当前消息的 confidence**

### 完整的数据流

```
当前消息（无 confidence，还未分析）
    ↓
记忆搜索触发条件检查
    ├─ MEMORY_ENABLED=true? ✅
    ├─ memory_repository 存在? ✅
    ├─ 有 embedding 或 keywords? ✅
    └─ 当前消息的 confidence? ❌ 不需要（还没有）
    ↓
查询数据库（Supabase RPC: search_memory）
    ↓
数据库查询逻辑：
    SELECT news_events.*, ai_signals.confidence
    FROM news_events
    JOIN ai_signals ON news_events.id = ai_signals.news_event_id
    WHERE ai_signals.confidence >= min_confidence  ← 这里比较的是历史记录的 confidence
    AND similarity >= match_threshold
    AND created_at >= NOW() - INTERVAL 'X hours'
    ↓
返回过滤后的历史记录（只包含 confidence >= 0.5/0.6 的历史记录）
    ↓
注入到 payload.historical_reference
    ↓
传递给 AI 分析（快速分析）
```

### 关键区别对比

| 项目 | 当前消息的 confidence | 历史记录的 confidence |
|------|---------------------|---------------------|
| **存在时机** | 快速分析之后才产生 | 历史记录已存在（来自之前的分析） |
| **存储位置** | 还没有（分析后存到 `ai_signals`） | 已存在 `ai_signals` 表 |
| **比较对象** | 不参与记忆搜索判断 | 与 `min_confidence` 比较 |
| **用途** | 决定是否转发当前消息 | 决定是否返回该历史记录 |
| **阈值类型** | `AI_MIN_CONFIDENCE` (0.4) | `MEMORY_MIN_CONFIDENCE` (0.5/0.6) |

### 记忆搜索的完整逻辑

```python
# src/listener.py:501-550
# 步骤 1: 检查触发条件（不依赖当前消息的 confidence）
if self.config.MEMORY_ENABLED and self.memory_repository:
    # ✅ 触发条件满足
    # ❌ 不需要当前消息的 confidence（因为还没有）
    
    # 步骤 2: 查询历史记录
    memory_context = await self.memory_repository.fetch_memories(
        embedding=embedding_vector,  # 当前消息的 embedding
        keywords=keywords_hit,        # 当前消息的关键词
    )
    # ↑ 这里调用 RPC search_memory，传入 min_confidence=0.5/0.6
    
# src/db/repositories.py:177-201
# 步骤 3: RPC 函数接收到 min_confidence 参数
async def search_memory(..., min_confidence: float = 0.6):
    params = {
        "min_confidence": float(min_confidence),  # 0.5/0.6
        ...
    }
    # 调用 Supabase RPC，在数据库层面过滤历史记录
    
# 数据库 SQL 逻辑（伪代码）：
# SELECT news_events.*, ai_signals.confidence
# FROM news_events
# JOIN ai_signals ON news_events.id = ai_signals.news_event_id
# WHERE ai_signals.confidence >= min_confidence  ← 过滤历史记录
#   AND vector_similarity >= match_threshold
#   AND created_at >= NOW() - INTERVAL '72 hours'
# ORDER BY similarity DESC
# LIMIT 5
```

**总结：**
- ✅ 记忆搜索总是触发（如果配置开启），不依赖当前消息的 confidence
- ✅ `min_confidence` 用来过滤历史记录，比较的是历史记录的 confidence vs 阈值
- ✅ 当前消息的 confidence 在快速分析之后才产生，不参与记忆搜索的决策

## ⚠️ 重要澄清：记忆搜索在哪个 AI 分析之前？

**答案：记忆搜索在第一次 AI 分析（即代码中的"快速分析"）之前触发。**

### 关键概念说明

1. **"快速分析"**（Fast Analysis）：
   - 代码位置：`src/ai/signal_engine.py:516`，注释：`# Step 1: Gemini fast analysis (90%)`
   - 实际调用：`await self._client.generate_signal(messages, images=images)`
   - 使用的模型：根据配置可能是 Gemini Flash、OpenAI-compatible 等
   - 目的：对所有消息进行初步分析，产出初步的 confidence、action 等结果

2. **"深度分析"**（Deep Analysis）：
   - 代码位置：`src/ai/signal_engine.py:617`
   - 触发条件：快速分析的结果 `confidence >= 0.75`（`HIGH_VALUE_CONFIDENCE_THRESHOLD`）
   - 使用的模型：Claude、Gemini Function Calling 等
   - 目的：对高价值信号进行更深入的分析

3. **`min_confidence=0.5/0.6` 的来源**：
   - 配置来源：`src/config.py:235`
     ```python
     MEMORY_MIN_CONFIDENCE: float = float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.6"))
     ```
   - 默认值：`0.6`
   - 可以通过环境变量 `MEMORY_MIN_CONFIDENCE` 覆盖（你的日志显示 `0.50`，说明配置被改成了 `0.5`）
   - 用途：过滤历史记忆，只返回 `confidence >= min_confidence` 的历史记录

### 完整的 AI 分析流程

```
┌─────────────────────────────────────────────────────────┐
│ 1. 记忆搜索（MEMORY FETCH）                             │
│    代码位置：src/listener.py:501-550                    │
│    - 在第一次 AI 分析之前触发                            │
│    - 查询历史记忆（min_confidence=0.5/0.6）             │
│    - 注入到 payload.historical_reference                │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│ 2. 第一次 AI 分析（快速分析/Fast Analysis）            │
│    代码位置：src/ai/signal_engine.py:516-537            │
│    - 调用：ai_engine.analyse(payload)                   │
│    - 内部调用：client.generate_signal()                  │
│    - 使用包含历史记忆的 payload                         │
│    - 输出初步结果（confidence, action, etc.）          │
└─────────────────────────────────────────────────────────┘
                    │
                    ▼
        ┌───────────┴───────────┐
        │                       │
        ▼                       ▼
┌───────────────┐     ┌───────────────────────┐
│ confidence    │     │ confidence >= 0.75    │
│ < 0.75        │     │ is_high_value = true  │
│ 返回结果      │     │ 触发深度分析           │
└───────────────┘     └───────────────────────┘
                              │
                              ▼
                ┌─────────────────────────────┐
                │ 3. 深度分析（DEEP ANALYSIS） │
                │    代码位置：617            │
                │    - Claude / Gemini FC     │
                │    - 使用相同的 payload     │
                │    - 也包含历史记忆上下文   │
                │    - 输出最终结果           │
                └─────────────────────────────┘
```

### 关键代码位置

```python
# src/listener.py:501-612
# 步骤 1: 记忆搜索（在第一次 AI 分析之前）
if self.config.MEMORY_ENABLED and self.memory_repository:
    # 使用配置的 MEMORY_MIN_CONFIDENCE（默认 0.6，可通过环境变量覆盖）
    memory_context = await self.memory_repository.fetch_memories(
        embedding=embedding_vector,
        asset_codes=None,
        keywords=keywords_hit,
    )
    historical_reference_entries = memory_context.to_prompt_payload(...)

# 步骤 2: 构建 payload（包含历史记忆）
payload = EventPayload(
    ...
    historical_reference={
        "entries": historical_reference_entries,
        "enabled": True,
    },
    ...
)

# 步骤 3: 调用第一次 AI 分析（快速分析）
signal_result = await self.ai_engine.analyse(payload)  # ← 这里

# src/ai/signal_engine.py:491-617
# 步骤 4: 第一次 AI 分析（代码注释称为"快速分析"）
async def analyse(self, payload: EventPayload) -> SignalResult:
    messages = build_signal_prompt(payload)  # payload 包含 historical_reference
    
    # Step 1: Gemini fast analysis (90%) ← 代码注释
    response = await self._client.generate_signal(messages, images=images)
    gemini_result = self._parse_response(response)
    
    # Step 2: 判断是否触发深度分析
    is_high_value = gemini_result.is_high_value_signal(
        confidence_threshold=self._high_value_threshold,  # 默认 0.75
    )
    
    # Step 3: 如果高价值，触发深度分析（也使用相同的 payload）
    if is_high_value and self._deep_enabled:
        deep_result = await deep_engine.analyse(payload, gemini_result)
        # payload 中的 historical_reference 也会被深度分析使用
```

## 📊 完整流程逻辑图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Telegram 消息处理流程                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. 关键词过滤                                                           │
│    - 检查是否命中 keywords.txt                                         │
│    - 优先级 KOL 可跳过此步骤                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. 去重检查（三层）                                                      │
│    - 内存窗口去重（最近 N 小时）                                        │
│    - Hash 去重（完全相同的消息）                                        │
│    - 语义向量去重（相似度 >= threshold）                                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. 计算 Embedding                                                       │
│    - 使用 OpenAI text-embedding-3-small                               │
│    - 生成 1536 维向量                                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. 🔍 记忆搜索触发（AI 分析之前）                                       │
│    ┌─────────────────────────────────────────────────────────────┐     │
│    │ 触发条件（与置信度无关）：                                    │     │
│    │ ✅ MEMORY_ENABLED=true                                       │     │
│    │ ✅ memory_repository 已初始化                                 │     │
│    │ ✅ 有 embedding 或 keywords                                  │     │
│    │ ❌ 不依赖当前消息的置信度（此时还没有 AI 结果）                │     │
│    └─────────────────────────────────────────────────────────────┘     │
│                                                                         │
│    📥 查询历史记忆（Supabase RPC: search_memory）                       │
│    ┌─────────────────────────────────────────────────────────────┐     │
│    │ 参数：                                                        │     │
│    │ - query_embedding: 当前消息的 1536 维向量                    │     │
│    │ - query_keywords: 命中的关键词列表                           │     │
│    │ - match_threshold: 0.85 (相似度阈值)                        │     │
│    │ - min_confidence: 0.5/0.6 (历史信号置信度过滤) ⭐            │     │
│    │ - time_window_hours: 72h (时间窗口)                          │     │
│    │ - match_count: 5 (最多返回条数)                              │     │
│    └─────────────────────────────────────────────────────────────┘     │
│                                                                         │
│    🎯 min_confidence 的作用：                                           │
│    ┌─────────────────────────────────────────────────────────────┐     │
│    │ 只返回历史 AI 信号中 confidence >= min_confidence 的记录     │     │
│    │                                                             │     │
│    │ 为什么是 0.5/0.6 而不是 0.8？                               │     │
│    │                                                             │     │
│    │ ✅ 记忆搜索的目的是提供上下文，不是只找"完美案例"            │     │
│    │ ✅ 中等置信度的历史信号（0.5-0.7）也包含有价值信息：          │     │
│    │    - 失败案例：帮助识别风险模式                              │     │
│    │    - 部分成功：帮助理解事件演化                              │     │
│    │    - 模式识别：帮助发现相似事件特征                          │     │
│    │                                                             │     │
│    │ ❌ 如果用 0.8 作为阈值：                                     │     │
│    │    - 会过滤掉大量有用的历史信息                              │     │
│    │    - 只看到"高光时刻"，看不到失败的教训                      │     │
│    │    - AI 无法学习到完整的模式（成功+失败）                     │     │
│    └─────────────────────────────────────────────────────────────┘     │
│                                                                         │
│    📤 返回结果：                                                         │
│    - 最多 5 条相似的历史记忆                                            │
│    - 包含：summary, assets, action, confidence, similarity              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. 🤖 AI 信号分析                                                        │
│    - 输入：消息文本 + 历史记忆上下文                                      │
│    - 输出：SignalResult                                                │
│      ┌─────────────────────────────────────────────────────────────┐   │
│      │ confidence 分级：                                           │   │
│      │                                                             │   │
│      │ 0.0 - 0.4: 低可信 / 噪音                                    │   │
│      │   - 通常被跳过转发                                           │   │
│      │   - 即使命中关键词也可能被过滤                               │   │
│      │                                                             │   │
│      │ 0.4 - 0.6: 中等可信 / 部分可执行                            │   │
│      │   - 有交易方向但缺少时间节点                                 │   │
│      │   - 有数据但缺少明确标的                                     │   │
│      │   - 可能需要观望（observe）                                  │   │
│      │                                                             │   │
│      │ 0.6 - 0.8: 较高可信 / 可执行                                │   │
│      │   - 明确的买入/卖出标的 + 时间窗口                           │   │
│      │   - 有具体价格/数据支撑                                       │   │
│      │   - 通常是转发的信号                                         │   │
│      │                                                             │   │
│      │ 0.8 - 1.0: 高可信 / 高价值                                   │   │
│      │   - 多源确认 + 官方确认                                       │   │
│      │   - 价格验证通过                                             │   │
│      │   - 历史模式匹配                                             │   │
│      │   - 可能触发深度分析                                         │   │
│      └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 6. 转发决策                                                              │
│    ┌─────────────────────────────────────────────────────────────┐     │
│    │ 普通消息：                                                    │     │
│    │ - confidence >= 0.4 方可转发                                 │     │
│    │ - observe 信号需 confidence >= 0.85                          │     │
│    │                                                             │     │
│    │ 优先级 KOL：                                                 │     │
│    │ - confidence >= 0.3 即可转发（降低阈值）                     │     │
│    │ - observe 信号需 confidence >= 0.5                           │     │
│    └─────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 7. 持久化存储                                                            │
│    - news_events: 存储原始消息 + embedding                              │
│    - ai_signals: 存储 AI 分析结果（包括 confidence）                    │
│    - 这些数据会成为未来的历史记忆                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

## 🎯 为什么 min_confidence = 0.5/0.6 而不是 0.8？

### 核心原因：记忆搜索的目的不同

| 维度 | 记忆搜索（min_confidence） | 转发决策（confidence_threshold） |
|------|--------------------------|--------------------------------|
| **目的** | 提供历史上下文，帮助 AI 理解模式 | 决定是否转发给用户 |
| **使用时机** | AI 分析**之前** | AI 分析**之后** |
| **依赖数据** | 历史记录的置信度 | 当前消息的置信度 |
| **阈值设置** | 0.5/0.6（中等及以上） | 保险阈值 0.8（仅高价值） |

### 详细分析

#### 1. **记忆搜索的本质：提供上下文，不是筛选完美案例**

```python
# 记忆搜索的用途：
# ✅ 帮助 AI 理解"这类事件通常会怎样发展"
# ✅ 提供"类似事件的历史模式"
# ✅ 包括成功案例和失败案例，帮助 AI 做出更全面的判断

# 如果只用 0.8 的阈值：
# ❌ 只看到"成功的高光时刻"
# ❌ 看不到失败的教训和风险模式
# ❌ AI 可能过度乐观，无法识别潜在风险
```

#### 2. **置信度分级的实际含义**

```
confidence 0.5-0.6 的历史信号可能包含：
- ✅ 风险警告：虽然置信度不高，但确实发生了风险事件
- ✅ 模式识别：相似事件的共性特征
- ✅ 演化过程：事件从低置信度到高置信度的变化过程
- ✅ 失败教训：为什么某些信号被标记为低置信度

例如：
- 历史记录：某个代币上线消息，confidence=0.55
  → 原因是"缺少成交量数据"
  → 当前消息有类似的"上线"关键词但"补充了成交量数据"
  → AI 可以对比差异，提升当前信号的置信度
```

#### 3. **数据分布的现实考虑**

```
假设历史记录中 confidence 分布：
- 0.0 - 0.4: 40% (噪音，应该过滤)
- 0.4 - 0.6: 30% (中等可信，包含有价值信息)
- 0.6 - 0.8: 20% (较高可信)
- 0.8 - 1.0: 10% (高价值)

如果 min_confidence = 0.8：
→ 只看到 10% 的历史记录
→ 丢失了 30% 的中等可信记录中的有价值信息
→ AI 缺乏完整的模式学习

如果 min_confidence = 0.5：
→ 看到 60% 的历史记录（过滤掉 40% 的噪音）
→ 包含成功和失败案例
→ AI 有更全面的上下文
```

#### 4. **实际代码证据**

```python
# src/listener.py:1080
# 转发决策的阈值
if signal_result.confidence < 0.4:
    return True  # 跳过转发

# src/listener.py:746
# 普通消息转发阈值
confidence_threshold = self.config.AI_MIN_CONFIDENCE  # 默认 0.4
observe_threshold = self.config.AI_OBSERVE_THRESHOLD  # 默认 0.85

# src/config.py:235
# 记忆搜索的阈值
MEMORY_MIN_CONFIDENCE: float = float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.6"))

# 注意：
# - 转发阈值：0.4（过滤噪音）
# - 记忆阈值：0.5/0.6（保留中等可信的历史）
# - 这两个阈值的目的不同！
```

## 📈 完整的置信度使用场景

### 场景 1：历史记录置信度（用于记忆搜索）

```python
# 历史记录的 confidence 含义：
# 0.5-0.6: 中等可信，但可能包含有价值信息
# 0.6-0.8: 较高可信，通常是可靠的信号
# 0.8-1.0: 高价值，完美的案例

# min_confidence = 0.5/0.6 的逻辑：
# ✅ 过滤掉明显噪音（< 0.4）
# ✅ 保留中等及以上可信度的历史（>= 0.5）
# ✅ 让 AI 看到更全面的模式（成功+失败）
```

### 场景 2：finishReason.置信度（用于转发决策）

```python
# 当前消息的 confidence 含义：
# 0.0-0.4: 低可信，通常跳过转发
# 0.4-0.6: 中等可信，可能需要观望
# 0.6-0.8: 较高可信，可以转发
# 0.8-1.0: 高价值，必须转发

# confidence_threshold = 0.4 的逻辑：
# ✅ 过滤掉明显噪音（< 0.4）
# ✅ 转发中等及以上可信度的信号（>= 0.4）
# ✅ 对于 observe 信号，需要更高的阈值（0.85）
```

## 🔍 记忆搜索的完整逻辑链条

```
当前消息（无置信度，还未分析）
    ↓
计算 embedding + 提取 keywords
    ↓
查询历史记忆（search_memory RPC）
    ↓
┌─────────────────────────────────────────┐
│ 过滤条件：                               │
│ - 相似度 >= match_threshold (0.85)      │
│ - 历史 confidence >= min_confidence     │
│   (0.5/0.6) ← 这里是关键                │
│ - 时间窗口内（72h）                      │
│ - 最多返回 5 条                          │
└─────────────────────────────────────────┘
    ↓
返回历史记忆上下文
    ↓
注入到 AI Prompt
    ↓
AI 分析当前消息（参考历史模式）
    ↓
生成 SignalResult（包含 confidence）
    ↓
根据 confidence 决定是否转发
```

## 💡 总结

### 为什么 min_confidence = 0.5/0.6？

1. **目的不同**：记忆搜索是为了提供上下文，不是筛选完美案例
2. **数据价值**：中等置信度的历史记录（0.5-0.7）包含有价值的模式信息
3. **完整学习**：需要看到成功和失败案例，才能做出全面判断
4. **过滤噪音**：0.5/0.6 的阈值已经过滤掉明显噪音（< 0.4）
5. **实践平衡**：既保证质量（过滤噪音），又保证覆盖（包含中等可信记录）

### 为什么不用 0.8？

1. **过度过滤**：会丢失大量有价值的中等可信历史记录
2. **模式缺失**：只看到"高光时刻"，看不到失败教训
3. **上下文不足**：AI 缺乏完整的模式学习，可能过度乐观
4. **数据稀缺**：高置信度（>= 0.8）的历史记录较少，可能导致检索结果不足

### 关键区别

| 阈值类型 | 数值 | 用途 | 时机 |
|---------|------|------|------|
| **MEMORY_MIN_CONFIDENCE** | 0.5/0.6 | 过滤历史记录，提供上下文 | AI 分析**之前** |
| **AI_MIN_CONFIDENCE** | 0.4 | 决定是否转发当前消息 | AI 分析**之后** |
| **AI_OBSERVE_THRESHOLD** | 0.85 | observe 信号的转发阈值 | AI 分析**之后** |

**记忆搜索的 min_confidence 和转发决策的 confidence_threshold 服务于不同的目的，因此阈值不同是合理的！**

## 📝 代码中的实际使用

### 1. 去重时的 min_confidence = 0.0

```python
# src/db/repositories.py:43-58
# 去重检查时，使用 min_confidence=0.0
# 因为去重的目的是检测"是否已存在"，不需要过滤置信度
# 即使是低置信度的历史记录，也是重复，应该被过滤

# 使用统一的 search_memory RPC，match_count=1 用于去重
# min_confidence=0 不过滤 AI 信号置信度
params = {
    "min_confidence": 0.0,  # 不过滤 AI 信号置信度
    ...
}
```

### 2. 记忆检索时的 min_confidence = 0.5/0.6

```python
# src/memory/repository.py:85
# 记忆检索时，使用配置的 min_confidence（默认 0.6）
# 目的是过滤噪音，保留中等及以上可信度的历史记录

search_result = await self._unified_repo.search_memory(
    embedding_1536=list(embedding) if embedding else None,
    keywords=keyword_list if keyword_list else None,
    asset_codes=asset_list if asset_list else None,
    match_threshold=float(self._config.similarity_threshold),
    min_confidence=float(self._config.min_confidence),  # 默认 0.6
    time_window_hours=int(self._config.lookback_hours),
    match_count=int(self._config.max_notes),
)
```

### 3. 转发决策时的 confidence_threshold = 0.4

```python
# src/listener.py:737-746
# 转发决策时，使用 AI_MIN_CONFIDENCE（默认 0.4）
# 目的是过滤明显噪音，转发中等及以上可信度的信号

confidence_threshold = (
    self.config.AI_MIN_CONFIDENCE_KOL if is_priority_kol
    else self.config.AI_MIN_CONFIDENCE  # 默认 0.4
)

low_confidence_skip = signal_result.confidence < confidence_threshold
```

## 🎬 实际案例

### 案例 1：历史记录 confidence=0.55 的价值

```
场景：
- 历史记录：某交易所上线代币 ABC，confidence=0.55
  → 原因：缺少成交量数据，部分信息不完整
- 当前消息：交易所上线代币 XYZ
  → 关键词匹配："上线"、"交易所"
  → 相似度：0.87

如果 min_confidence = 0.8：
→ 历史记录被过滤，AI 看不到类似的案例
→ AI 可能无法识别"缺少成交量数据"这个风险

如果 min_confidence = 0.5：
→ 历史记录被返回，AI 看到类似的案例
→ AI 可以对比："历史上类似事件因缺少成交量数据而被降级"
→ AI 可以检查当前消息是否有成交量数据
→ 如果有，可以提高当前信号的置信度
→ 如果没有，应该降低当前信号的置信度
```

### 案例 2：失败案例的价值

```
场景：
- 历史记录：某稳定币脱锚传闻，confidence=0.6
  → 原因：多源确认但价格未验证，最终被证实为谣言
  → risk_flags: ["unverifiable"]
- 当前消息：另一个稳定币脱锚传闻
  → 相似度：0.82

如果 min_confidence = 0.8：
→ 历史记录被过滤，AI 看不到类似的失败案例
→ AI 可能无法识别"需要价格验证"这个重要条件

如果 min_confidence = 0.5：
→ 历史记录被返回，AI 看到类似的失败案例
→ AI 可以学习："类似事件需要价格验证才能确认"
→ AI 可以主动调用价格工具验证
→ 如果价格验证通过，可以提高置信度
→ 如果价格验证失败，应该降低置信度或标记为谣言
```

## 🔄 配置建议

### 推荐的配置值

```bash
# 记忆搜索配置（提供上下文）
MEMORY_MIN_CONFIDENCE=0.5  # 或 0.6
# 理由：过滤噪音（< 0.4），保留中等及以上可信度的历史（>= 0.5）

# 转发决策配置（决定是否转发）
AI_MIN_CONFIDENCE=0.4  # 过滤明显噪音
AI_OBSERVE_THRESHOLD=0.85  # observe 信号需要更高阈值

# 优先级 KOL 配置（降低阈值）
AI_MIN_CONFIDENCE_KOL=0.3  # 更低的转发阈值
AI_OBSERVE_THRESHOLD_KOL=0.5  # 更低的 observe 阈值
```

### 不同场景的阈值选择

| 场景 | min_confidence | 理由 |
|------|--------------|------|
| **记忆搜索（默认）** | 0.5/0.6 | 过滤噪音，保留中等可信历史 |
| **记忆搜索（严格）** | 0.7 | 只关注高可信历史，适合高质量数据源 |
| **记忆搜索（宽松）** | 0.4 | 保留更多历史，适合数据稀缺场景 |
| **去重检查** | 0.0 | 不过滤置信度，检测所有重复 |
| **转发决策（普通）** | 0.4 | 过滤明显噪音 |
| **转发决策（KOL）** | 0.3 | 降低阈值，保留重要信号 |
| **observe 信号** | 0.85 | 观望信号需要更高置信度 |
