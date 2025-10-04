# OpenAI Embedding 去重设置指南

## 📋 概述

本项目已集成 **OpenAI Embedding 语义去重**功能，可以识别语义相似的重复消息（即使文字不完全一致）。

## 🚀 设置步骤

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

在 `.env` 文件中添加以下配置：

```env
# Supabase 配置
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
ENABLE_DB_PERSISTENCE=true

# OpenAI Embedding 配置
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_SIMILARITY_THRESHOLD=0.92
EMBEDDING_TIME_WINDOW_HOURS=72
```

### 3. 执行数据库迁移

#### 步骤 A: 创建表结构

在 Supabase Dashboard → SQL Editor 执行：

```bash
docs/supabase_migration.sql
```

#### 步骤 B: 创建向量搜索函数

在 Supabase Dashboard → SQL Editor 执行：

```bash
docs/supabase_embedding_function.sql
```

### 4. 启用 pgvector 扩展

在 Supabase Dashboard:
1. 进入 **Database → Extensions**
2. 搜索并启用 `pgvector`

### 5. 创建向量索引（可选，数据达到 1000 条后）

```sql
create index concurrently idx_news_events_embedding
  on news_events using ivfflat(embedding vector_cosine_ops)
  with (lists = 100);
```

## 🔧 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `OPENAI_API_KEY` | - | OpenAI API 密钥（必需） |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding 模型 |
| `EMBEDDING_SIMILARITY_THRESHOLD` | `0.85` | 相似度阈值（0-1），>= 此值视为重复 |
| `EMBEDDING_TIME_WINDOW_HOURS` | `72` | 去重时间窗口（小时） |

### 阈值建议

- **新闻快讯**: 0.85（默认，用于高频快讯降低重复处理）
- **社交媒体**: 0.88（宽松，捕获改写）
- **官方公告**: 0.95（严格，精确去重）

## 📋 实施清单（与监听管线对应）

- ✅ L1 关键词白名单：`src/listener.py:203` 已使用 `FILTER_KEYWORDS` 过滤；持续在 `.env` 中维护精简白名单。
- ✅ L2 指纹前移：`src/listener.py:220-235` 现在在翻译/AI 前生成 `hash_raw/hash_canonical` 并向 Supabase 查询重复，命中后直接退出管线。
- ✅ L2 本地+远端去重：命中哈希时通过 `NewsEventRepository.check_duplicate` 返回历史 `news_event_id`，避免再消耗翻译与推理资源。
- ✅ L3 语义去重前移：`src/listener.py:237-264` 会预先生成 embedding 并调用 `check_duplicate_by_embedding`，72 小时窗口内相似度 ≥ 阈值即跳过后续处理。
- ✅ Supabase RPC 函数：`docs/supabase_embedding_function.sql` 已包含 `find_similar_events`；确保执行迁移并授予权限。
- ✅ 配置基线：默认阈值改为 0.85；在 `.env` / 环境变量中填好 Supabase 与 OpenAI 凭据即可启用上述流程。

## 📊 工作流程

以下流程图反映目标状态——哈希与语义去重提前到翻译和 AI 调用之前：

```
新消息接收
    ↓
L1: 关键词白名单命中
    ↓
计算原文哈希/指纹 → 命中则跳过
    ↓
生成 Embedding (OpenAI API)
    ↓
调用 Supabase find_similar_events 查询 72 小时窗口
    ↓
相似度 ≥ 0.85 → 当作重复并结束
    ↓
相似度 < 0.85 → 继续翻译与 AI 分析
    ↓
写入数据库（含哈希、Embedding、AI 结果）
```

## 🔍 监控和调试

### 查看去重日志

```bash
tail -f logs/app.log | grep "去重"
```

### 查询相似事件

```sql
select
  id,
  content_text,
  1 - (embedding <=> '[your_embedding_vector]'::vector) as similarity
from news_events
where embedding is not null
order by embedding <=> '[your_embedding_vector]'::vector
limit 5;
```

### 统计 Embedding 覆盖率

```sql
select
  count(*) as total,
  count(embedding) as with_embedding,
  round(100.0 * count(embedding) / count(*), 2) as coverage_pct
from news_events;
```

## ⚠️ 注意事项

1. **API 成本**: 每条消息调用一次 OpenAI API，建议监控用量
2. **延迟**: 生成 embedding 约增加 100-300ms 延迟
3. **向量索引**: 数据量达到 1000+ 后创建索引可大幅提升查询速度
4. **时间窗口**: 默认只查 72 小时内的记录，减少计算量

## 🧪 测试

测试 embedding 生成：

```python
from src.utils import compute_embedding

embedding = await compute_embedding(
    "测试文本",
    api_key="your-openai-key"
)
print(f"向量维度: {len(embedding)}")  # 应该是 1536
```

测试语义去重：

```python
from src.db import get_supabase_client, NewsEventRepository

client = get_supabase_client(url=..., service_key=...)
repo = NewsEventRepository(client)

similar = await repo.check_duplicate_by_embedding(
    embedding=embedding,
    threshold=0.92
)
if similar:
    print(f"发现相似消息: {similar}")
```

## 📈 性能优化（Phase 2）

如需进一步优化性能，可以：

1. **异步 Embedding 生成** - 先写入，后台生成
2. **内存缓存** - LRU 缓存最近 1000 条哈希
3. **批量生成** - 累积 10 条后批量调用 API
4. **HNSW 索引** - 替代 IVFFlat，查询更快

详见 [data_storage_schema.md](./data_storage_schema.md) Phase 2 部分。
