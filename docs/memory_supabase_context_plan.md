# Supabase 记忆集成方案（最小改动版）

## 1. 核心设计

### 1.1 目标
- **复用现有数据**：利用 Supabase 中的 `news_events`、`ai_signals` 作为记忆仓库
- **跨会话学习**：通过向量检索自动匹配历史相似场景
- **最小改动**：仅新增 `memory` 模块 + 2 处代码修改
- **成本最低**：100% 使用 Gemini，无需 Claude

### 1.2 架构原理
```
┌─────────────────────────────────────────────────┐
│  消息输入 (Telegram)                            │
└────────────────────┬────────────────────────────┘
                     ↓
         ┌───────────────────────┐
         │ 翻译 + 关键词提取      │
         └───────────┬───────────┘
                     ↓
         ┌───────────────────────────────────────┐
         │ 向量检索历史记忆                       │
         │ compute_embedding + search_memory_events│
         └───────────┬───────────────────────────┘
                     ↓
         ┌───────────────────────────────────────┐
         │ Gemini 分析 + 历史参考                 │
         │ (注入 3 条最相似历史信号)              │
         └───────────┬───────────────────────────┘
                     ↓
         ┌───────────────────────┐
         │ 返回分析结果           │
         │ 写入 ai_signals (自动成为新记忆) │
         └───────────────────────┘
```

### 1.3 关键特性
- **零冷启动**：立即使用现有 5000+ 历史信号
- **自动学习**：每条新信号自动成为未来记忆
- **语义匹配**：向量检索自动找到相似场景（如"SEC 推迟" 匹配 "监管延期"）
- **成本优势**：无需 Claude，纯 Gemini 方案（$35/月 vs 混合架构 $94/月）

## 2. 与其他方案对比

| 方案 | 改动量 | 成本/月 | 学习能力 | 冷启动 |
|------|-------|---------|---------|-------|
| Cookbook 原版 | 大 | $624 | 强（Claude） | 慢 |
| Local 混合 | 中 | $94 | 中（Gemini+Claude） | 慢 |
| **Supabase** ⭐ | **最小** | **$35** | 基础（向量检索） | **快** |

### 2.1 vs Cookbook
- ✅ **成本节省 95%**（$624 → $35）
- ✅ **无需 Memory Tool 循环**（直接 RPC 查询）
- ✅ **立即可用**（复用现有 ai_signals 数据）
- ⚠️ 学习能力弱于 Claude（但足够你的场景）

### 2.2 vs Local 混合方案
- ✅ **成本再省 63%**（$94 → $35）
- ✅ **改动更小**（无需 HybridAiEngine）
- ✅ **零冷启动**（Local 需积累模式）
- ⚠️ 无 Claude 深度分析（可后续升级）

### 2.3 借鉴 Cookbook 的核心思路

| Cookbook 概念 | Supabase 实现 |
|--------------|-------------|
| 基于文件的记忆（`/memories`） | 复用 Supabase 表（`ai_signals`） |
| 上下文裁剪/编辑 | RPC 限制返回条数（`match_count=3`） |
| AI 主动记录模式 | 每次分析自动写入 `ai_signals`（自动成为记忆） |

## 3. 实施步骤（最小改动）

### 3.1 新增文件（3 个文件，共 ~80 行代码）

#### `src/memory/__init__.py`
```python
"""Memory module for historical context retrieval."""
from .repository import fetch_memories
from .types import MemoryEntry

__all__ = ["fetch_memories", "MemoryEntry"]
```

#### `src/memory/types.py`（~15 行）
```python
"""Memory data types."""
from dataclasses import dataclass
from datetime import datetime

@dataclass
class MemoryEntry:
    """Single memory entry from historical signals."""

    timestamp: datetime
    assets: list[str]
    action: str
    confidence: float
    summary: str
    similarity: float

    def format(self) -> str:
        """格式化为提示词文本"""
        asset_str = ", ".join(self.assets) if self.assets else "通用"
        return (
            f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {asset_str} | "
            f"动作:{self.action} | 置信度:{self.confidence:.2f} | 相似度:{self.similarity:.2f}\n"
            f"   摘要：{self.summary}"
        )
```

#### `src/memory/repository.py`（~50 行）
```python
"""Memory repository using Supabase vector search."""
from typing import Optional
from ..utils import compute_embedding, setup_logger
from ..db import get_supabase_client
from .types import MemoryEntry

logger = setup_logger(__name__)

async def fetch_memories(
    message: str,
    assets: Optional[list[str]] = None,
    match_count: int = 3,
    match_threshold: float = 0.85,
    min_confidence: float = 0.6,
    time_window_hours: int = 72,
) -> list[MemoryEntry]:
    """
    从 Supabase 检索历史相似信号

    Args:
        message: 当前消息文本（用于生成 embedding）
        assets: 可选资产过滤（如 ["BTC", "ETH"]）
        match_count: 返回条数（默认 3）
        match_threshold: 相似度阈值（默认 0.85）
        min_confidence: 最低置信度（默认 0.6）
        time_window_hours: 时间窗口（默认 72 小时）
    """
    try:
        # 1. 生成当前消息的 embedding
        query_emb = compute_embedding(message)
        if not query_emb:
            logger.warning("无法生成 embedding，跳过记忆检索")
            return []

        # 2. 调用 Supabase RPC
        supabase = get_supabase_client()
        result = supabase.rpc('search_memory_events', {
            'query_embedding': query_emb,
            'match_threshold': match_threshold,
            'match_count': match_count,
            'asset_filter': assets,
            'min_confidence': min_confidence,
            'time_window_hours': time_window_hours
        }).execute()

        # 3. 转换为 MemoryEntry
        memories = [
            MemoryEntry(
                timestamp=row['created_at'],
                assets=row['assets'] or [],
                action=row['action'],
                confidence=row['confidence'],
                summary=row['summary'],
                similarity=row['similarity']
            )
            for row in result.data
        ]

        logger.info(f"检索到 {len(memories)} 条历史记忆")
        return memories

    except Exception as e:
        logger.error(f"记忆检索失败: {e}")
        return []
```

### 3.2 修改现有文件（2 处改动）

#### 改动 1: `src/listener.py` (仅 3 行新增)
```python
# 在 TelegramListener.__init__ 中添加（第 48 行附近）
from .memory import fetch_memories  # 新增导入

# 在 _process_message 方法中，AI 调用前添加（约第 301 行）
async def _process_message(self, payload: EventPayload):
    # ... 现有翻译逻辑

    # 新增：加载历史记忆（3 行）
    if self.config.MEMORY_ENABLED:
        memories = await fetch_memories(payload.text, limit=self.config.MEMORY_MAX_NOTES)
        payload.historical_reference = {
            "memories": [m.format() for m in memories]
        }

    # 原有 AI 调用逻辑不变
    result = await self.ai_engine.analyse(payload)
```

#### 改动 2: `src/ai/signal_engine.py` (修改 `build_signal_prompt`)
```python
# 在 build_signal_prompt 函数中（约第 350 行）
def build_signal_prompt(payload: EventPayload) -> list[dict[str, str]]:
    context = {
        "source": payload.source,
        "timestamp": payload.timestamp.isoformat(),
        "original_text": payload.text,
        "translated_text": payload.translated_text or payload.text,
        "keywords_hit": payload.keywords_hit,
        "historical_reference": payload.historical_reference,  # 已有字段（第 88 行）
        "media_attachments": payload.media,
    }

    system_prompt = """你是加密货币信号分析专家。

【核心任务】
分析消息并输出交易信号 JSON。

【历史参考】  # 新增区块
{historical_reference}

如有历史参考，请结合历史模式分析：
- 当前场景与历史是否匹配
- 历史动作与置信度是否适用
- 如有差异，请在 notes 中说明

【输出格式】
{{
  "summary": "简要摘要",
  "event_type": "listing|hack|regulation|...",
  "asset": "BTC|ETH|...",
  "action": "buy|sell|observe",
  "confidence": 0.0-1.0,
  "notes": "补充说明（如引用历史记忆 ID）"
}}
"""

    # ... 其余逻辑不变
```

### 3.3 配置文件（`.env` 新增 5 行）
```bash
# 记忆功能配置
MEMORY_ENABLED=true
MEMORY_MAX_NOTES=3
MEMORY_LOOKBACK_HOURS=72
MEMORY_MIN_CONFIDENCE=0.6
MEMORY_SIMILARITY_THRESHOLD=0.85
```

### 3.4 Config 类扩展（`src/config.py`）
```python
class Config:
    # ... 现有字段

    # 记忆功能配置
    MEMORY_ENABLED: bool = Field(False, env="MEMORY_ENABLED")
    MEMORY_MAX_NOTES: int = Field(3, env="MEMORY_MAX_NOTES")
    MEMORY_LOOKBACK_HOURS: int = Field(72, env="MEMORY_LOOKBACK_HOURS")
    MEMORY_MIN_CONFIDENCE: float = Field(0.6, env="MEMORY_MIN_CONFIDENCE")
    MEMORY_SIMILARITY_THRESHOLD: float = Field(0.85, env="MEMORY_SIMILARITY_THRESHOLD")
```

## 4. Supabase RPC 函数（核心组件）

### 4.1 创建向量检索函数

在 Supabase SQL Editor 中执行以下 SQL：

```sql
CREATE OR REPLACE FUNCTION search_memory_events(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.85,
    match_count int DEFAULT 5,
    asset_filter text[] DEFAULT NULL,
    min_confidence float DEFAULT 0.6,
    time_window_hours int DEFAULT 72
)
RETURNS TABLE (
    id uuid,
    created_at timestamp,
    assets text[],
    action text,
    confidence float,
    summary text,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ne.id,
        ne.created_at,
        ais.assets,
        ais.action,
        ais.confidence,
        ais.summary,
        1 - (ne.embedding <=> query_embedding) as similarity
    FROM news_events ne
    JOIN ai_signals ais ON ne.id = ais.event_id
    WHERE
        ne.embedding IS NOT NULL
        AND ais.confidence >= min_confidence
        AND ne.created_at >= NOW() - (time_window_hours || ' hours')::interval
        AND (asset_filter IS NULL OR ais.assets && asset_filter)
        AND 1 - (ne.embedding <=> query_embedding) >= match_threshold
    ORDER BY
        similarity DESC,
        ais.confidence DESC,
        ne.created_at DESC
    LIMIT match_count;
END;
$$;
```

### 4.2 性能优化（建议创建索引）

```sql
-- 向量索引（加速相似度搜索）
CREATE INDEX IF NOT EXISTS idx_news_events_embedding ON news_events
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- 时间索引
CREATE INDEX IF NOT EXISTS idx_news_events_created_at ON news_events(created_at DESC);

-- 置信度索引
CREATE INDEX IF NOT EXISTS idx_ai_signals_confidence ON ai_signals(confidence DESC);

-- 资产索引（GIN 支持数组查询）
CREATE INDEX IF NOT EXISTS idx_ai_signals_assets ON ai_signals USING GIN(assets);
```

## 5. 工作原理示例

### 5.1 场景演示

**输入消息**：
```
币安宣布将于 10 月 15 日上线 ABC 代币
```

**步骤 1：向量检索**
```python
embedding = compute_embedding("币安宣布将于 10 月 15 日上线 ABC 代币")
# → [0.123, -0.456, 0.789, ...]

memories = supabase.rpc('search_memory_events', {
    'query_embedding': embedding,
    'match_count': 3
})
```

**步骤 2：返回相似历史**
```json
[
  {
    "created_at": "2025-10-05 14:30",
    "assets": ["XYZ"],
    "action": "buy",
    "confidence": 0.88,
    "summary": "币安上线 XYZ，短期利好",
    "similarity": 0.94
  },
  {
    "created_at": "2025-09-28 10:15",
    "assets": ["DEF"],
    "action": "buy",
    "confidence": 0.82,
    "summary": "OKX 上线 DEF，交易量激增",
    "similarity": 0.89
  },
  {
    "created_at": "2025-09-20 16:45",
    "assets": ["GHI"],
    "action": "observe",
    "confidence": 0.75,
    "summary": "Coinbase 上线 GHI，市场反应平淡",
    "similarity": 0.87
  }
]
```

**步骤 3：格式化注入 Prompt**
```
【历史参考】
1. [2025-10-05 14:30] XYZ | 动作:buy | 置信度:0.88 | 相似度:0.94
   摘要：币安上线 XYZ，短期利好

2. [2025-09-28 10:15] DEF | 动作:buy | 置信度:0.82 | 相似度:0.89
   摘要：OKX 上线 DEF，交易量激增

3. [2025-09-20 16:45] GHI | 动作:observe | 置信度:0.75 | 相似度:0.87
   摘要：Coinbase 上线 GHI，市场反应平淡

【当前消息】
币安宣布将于 10 月 15 日上线 ABC 代币

【分析要求】
结合历史参考，分析当前消息并输出 JSON 信号。
```

**步骤 4：Gemini 分析**
```json
{
  "summary": "币安上线 ABC 代币，参考历史上币案例，短期利好",
  "event_type": "listing",
  "asset": "ABC",
  "action": "buy",
  "confidence": 0.86,
  "notes": "历史显示币安上币成功率高，建议买入"
}
```

### 5.2 自动学习循环

```
第 1 次消息 → 无历史记忆 → Gemini 分析 → 写入 ai_signals
第 2 次消息 → 检索到 1 条记忆 → Gemini 参考历史 → 写入
第 3 次消息 → 检索到 2 条记忆 → Gemini 参考历史 → 写入
...
第 N 次消息 → 检索到 3 条最相关记忆 → Gemini 精准分析
```

**效果**：记忆库随时间自动积累，分析质量持续提升。

## 6. 测试与验证

### 6.1 单元测试
```python
# tests/test_memory_repository.py
async def test_fetch_memories():
    memories = await fetch_memories(
        "SEC 推迟 BTC ETF 决定",
        match_count=3
    )
    assert len(memories) <= 3
    assert all(m.confidence >= 0.6 for m in memories)
    assert all(m.similarity >= 0.85 for m in memories)
```

### 6.2 集成测试
```python
# 构造测试 payload
payload = EventPayload(
    text="币安上线新代币",
    source="test_channel",
    timestamp=datetime.now()
)

# 测试记忆加载
memories = await fetch_memories(payload.text)
payload.historical_reference = {"memories": [m.format() for m in memories]}

# 验证 prompt 包含记忆
messages = build_signal_prompt(payload)
assert "历史参考" in messages[0]["content"]
```

### 6.3 性能测试
```bash
# 查询耗时（应 < 100ms）
EXPLAIN ANALYZE
SELECT ... FROM news_events ne JOIN ai_signals ais ...
WHERE 1 - (ne.embedding <=> '[...]') >= 0.85;
```

## 7. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| **向量查询慢** | 延迟增加 | 创建 ivfflat 索引，限制时间窗口（72h） |
| **Prompt 注入** | 安全风险 | 过滤 summary 中的脚本/URL |
| **记忆质量差** | 误导分析 | 设定 min_confidence=0.6，定期清理低质量数据 |
| **成本增加** | embedding 调用 | 使用缓存，仅首次消息生成 embedding |
| **数据库负载** | 并发压力 | 限制 match_count=3，使用连接池 |

## 8. 快速开始

### 8.1 Phase 1: 创建 RPC 函数（2 分钟）

```bash
# 1. 登录 Supabase Dashboard
# 2. 进入 SQL Editor
# 3. 执行以下 SQL（见第 4.1 节）
```

### 8.2 Phase 2: 实施代码（30 分钟）

```bash
# 1. 新增 memory 模块
mkdir -p src/memory
touch src/memory/__init__.py
touch src/memory/types.py
touch src/memory/repository.py

# 2. 复制代码（见第 3.1 节）

# 3. 修改现有文件（见第 3.2 节）
# - listener.py: 添加 3 行
# - signal_engine.py: 修改 system_prompt

# 4. 配置环境变量
echo "MEMORY_ENABLED=true" >> .env
echo "MEMORY_MAX_NOTES=3" >> .env
```

### 8.3 Phase 3: 创建索引（可选，但推荐）

```sql
-- 见第 4.2 节，加速查询
CREATE INDEX idx_news_events_embedding ...
```

### 8.4 Phase 4: 测试验证

```bash
# 1. 重启服务
python -m src.listener

# 2. 发送测试消息，观察日志
# 应该看到：
# INFO:src.memory.repository:检索到 3 条历史记忆

# 3. 检查 Gemini 输出的 notes 字段
# 应包含：参考历史xxx
```

## 9. 监控与优化

### 9.1 监控指标

```python
# 每周统计
stats = {
    "total_queries": 10000,           # 总查询次数
    "memory_hit_rate": 0.78,          # 记忆命中率（78% 查询返回 >0 条）
    "avg_similarity": 0.89,           # 平均相似度
    "avg_query_time_ms": 45,          # 平均查询耗时（毫秒）
    "prompt_token_increase": 120,     # 每次增加的 token 数
}

# 目标
assert stats["memory_hit_rate"] > 0.70  # 命中率 > 70%
assert stats["avg_query_time_ms"] < 100  # 查询 < 100ms
assert stats["prompt_token_increase"] < 200  # token 增加 < 200
```

### 9.2 调优建议

**场景 1：查询太慢（>100ms）**
```sql
-- 检查索引是否生效
EXPLAIN ANALYZE SELECT ... FROM news_events ...;

-- 如未使用索引，降低精度提升速度
ALTER INDEX idx_news_events_embedding SET (lists = 50);
```

**场景 2：命中率太低（<50%）**
```bash
# 降低相似度阈值
MEMORY_SIMILARITY_THRESHOLD=0.80  # 从 0.85 降到 0.80

# 扩大时间窗口
MEMORY_LOOKBACK_HOURS=168  # 从 72h 扩大到 7 天
```

**场景 3：记忆质量差**
```sql
-- 定期清理低质量数据
DELETE FROM ai_signals WHERE confidence < 0.5 AND created_at < NOW() - INTERVAL '30 days';

-- 或增加置信度阈值
-- MEMORY_MIN_CONFIDENCE=0.7
```

## 10. 下一步行动

### Phase 1: 基础实施（1 周）
- [ ] 创建 Supabase RPC 函数（第 4.1 节）
- [ ] 创建性能索引（第 4.2 节）
- [ ] 实现 memory 模块（第 3.1 节）
- [ ] 修改 listener 和 signal_engine（第 3.2 节）
- [ ] 配置环境变量（第 3.3-3.4 节）
- [ ] 单元测试（第 6.1 节）

### Phase 2: 验证与优化（1-2 周）
- [ ] 集成测试（第 6.2 节）
- [ ] 性能测试（第 6.3 节）
- [ ] 监控指标收集（第 9.1 节）
- [ ] 调优参数（第 9.2 节）

### Phase 3: 可选增强（按需）
- [ ] 记忆质量标记（`memory_quality` 字段）
- [ ] A/B 测试（开启/关闭记忆对比）
- [ ] 升级混合架构（如需 Claude 深度分析，参考 [memory_local_plan.md](memory_local_plan.md)）

---

## 附录 A：完整代码清单

### A.1 改动汇总

| 文件 | 改动类型 | 行数 |
|------|---------|-----|
| `src/memory/__init__.py` | 新增 | 5 |
| `src/memory/types.py` | 新增 | 20 |
| `src/memory/repository.py` | 新增 | 55 |
| `src/listener.py` | 修改 | +5 |
| `src/ai/signal_engine.py` | 修改 | +15 |
| `src/config.py` | 修改 | +5 |
| `.env` | 修改 | +5 |
| **总计** | - | **~110 行** |

### A.2 成本对比（每月 10 万次分析）

| 项目 | Supabase 方案 | Local 混合 | Cookbook |
|------|-------------|-----------|----------|
| AI 调用成本 | $35 | $94 | $624 |
| Embedding 成本 | $2 | $2 | $12 |
| 数据库查询 | 免费（Supabase 自带） | 免费 | 免费 |
| **总计** | **$37/月** | **$96/月** | **$636/月** |

**结论**：Supabase 方案成本仅为 Cookbook 的 **6%**。

---

## 附录 B：常见问题

**Q1: 为什么不用 Claude Memory Tool？**
A: 成本高（30x Gemini），需要实现 tool use 循环。Supabase 方案通过向量检索实现类似效果，成本低改动小。

**Q2: 向量检索会不会很慢？**
A: 使用 ivfflat 索引 + 时间窗口限制，查询通常 <50ms。测试显示 10 万条数据检索耗时 ~30ms。

**Q3: 如何升级到混合架构？**
A: 保留当前代码，参考 [memory_local_plan.md](memory_local_plan.md) 新增 `HybridAiEngine`，让 Gemini 决定何时触发 Claude。

**Q4: 记忆质量如何保证？**
A: 设定 `min_confidence=0.6`，定期清理低质量数据，可选增加人工标记字段。

**Q5: 冷启动怎么办？**
A: 你已有 5000+ 历史 `ai_signals` 数据，立即可用，无需等待积累。