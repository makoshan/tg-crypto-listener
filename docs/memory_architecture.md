# 记忆系统架构说明

## 概述

记忆系统基于 **news_events + ai_signals** 表实现，无需单独的 `memory_entries` 表。

## 数据流程

```
Telegram 消息
    ↓
news_events (存储原始内容 + embedding)
    ↓
AI 分析 → ai_signals (存储决策信号)
    ↓
记忆检索 ← search_memory_events() RPC 函数
```

## 核心组件

### 1. 数据库表

- **news_events**: 存储原始新闻和 embedding 向量
  - `content_text`: 原始内容
  - `embedding`: OpenAI text-embedding-3-small (1536维)
  - `created_at`: 时间戳

- **ai_signals**: 存储 AI 分析结果
  - `summary_cn`: 中文摘要
  - `assets`: 相关资产 (BTC, ETH, etc.)
  - `action`: 操作建议 (buy/sell/observe)
  - `confidence`: 置信度 (0-1)
  - `news_event_id`: 关联 news_events

### 2. RPC 函数

**search_memory_events()**
- 输入：query_embedding, match_threshold, match_count, min_confidence, time_window_hours
- 逻辑：
  ```sql
  SELECT ai_signals.*
  FROM news_events ne
  JOIN ai_signals ais ON ais.news_event_id = ne.id
  WHERE ne.embedding IS NOT NULL
    AND ais.confidence >= min_confidence
    AND (1 - (ne.embedding <=> query_embedding)) >= match_threshold
    AND ne.created_at >= now() - interval 'time_window_hours hours'
  ORDER BY similarity DESC, confidence DESC
  LIMIT match_count
  ```

### 3. 混合检索策略

**HybridMemoryRepository**
- 主：Supabase 向量检索（实时，基于 embedding 相似度）
- 备：Local JSON 检索（降级，基于关键词匹配）
- 降级条件：
  - Supabase 返回空结果
  - Supabase 连接失败 (3次)

## 配置参数

```bash
# .env 配置
MEMORY_SIMILARITY_THRESHOLD=0.40  # 向量相似度阈值（降低以匹配更多相关案例）
MEMORY_MIN_CONFIDENCE=0.6         # 最小置信度
MEMORY_LOOKBACK_HOURS=168         # 时间窗口 7天
MEMORY_MAX_NOTES=3                # 最大返回数量
```

## 检索效果

### 阈值对比

| 阈值 | 检索范围 | 适用场景 |
|------|---------|---------|
| 0.85 | 极高相似 | 精确匹配（可能过滤太多） |
| 0.50 | 高相似 | 平衡准确性和召回率 |
| **0.40** | **中等相似** | **覆盖更多相关案例（推荐）** |

### 实测示例

查询："PEPE meme coin"
- 相似度 0.498: BTC Meme 币观察（DOGE, PEPE, TRUMP）
- 相似度 0.467: BNB 中秋 Meme 币
- 相似度 0.456: BNB Chain Four Meme 启动器

## 数据统计

- news_events (有 embedding): **1000+ 条**
- ai_signals: **965+ 条**
- 记忆可用性: ✅ 正常工作

## 降级机制

当 Supabase 无法返回结果时，自动降级到本地 JSON 模板：

```
memories/
├── core-001.json  # 交易所上币短期利好
├── core-002.json  # 安全事件恐慌抛售
└── core-003.json  # 监管不确定性观望
```

## 验证方法

```bash
# 测试 Supabase 连接和表结构
pytest tests/db/test_supabase.py -v

# 测试记忆检索效果
pytest tests/memory/test_multi_source_repository.py -v
```

## 优势

1. **无需维护单独表** - 直接复用 news_events + ai_signals
2. **自动积累历史** - 每次 AI 分析都自动成为未来的记忆
3. **向量检索准确** - 基于语义相似度，不依赖关键词
4. **灾备机制** - Local JSON 确保基础功能可用
5. **配置灵活** - 阈值/时间窗口/返回数量可调

## 后续优化

1. 定期清理旧数据（>30天）
2. 创建向量索引（HNSW/IVFFlat）加速检索
3. 按资产类型分组检索
4. 增加历史胜率统计
