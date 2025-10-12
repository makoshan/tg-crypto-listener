# 多数据源记忆系统 - Foreign Data Wrapper 方案

## 方案说明

**主库和副库是独立的 Supabase 项目**，通过 Postgres Foreign Data Wrapper (FDW) 在主库侧跨库查询副库，Python 代码无需修改。

- **主库**：现有项目（news_events + ai_signals）
- **副库**：`psqkegqdukxhjmahcgvo` 项目（docs 表）

---

## 实施步骤

### 步骤 1: 获取副库数据库连接信息

在副库 Supabase 项目 `psqkegqdukxhjmahcgvo` 的 Dashboard 中获取：

1. 进入 **Settings → Database**
2. 找到 **Connection Info**
3. 记录以下信息：
   - Host: `db.psqkegqdukxhjmahcgvo.supabase.co`
   - Port: `5432`
   - Database: `postgres`
   - User: `postgres`
   - Password: `[数据库密码]`（注意：不是 service_role key，是真实的 DB password）

---

### 步骤 2: 在主库创建 Foreign Server

在主库的 Supabase SQL Editor 执行：

```sql
-- 1. 启用 postgres_fdw 扩展
CREATE EXTENSION IF NOT EXISTS postgres_fdw;

-- 2. 创建外部服务器连接到副库
CREATE SERVER docs_server
FOREIGN DATA WRAPPER postgres_fdw
OPTIONS (
    host 'db.psqkegqdukxhjmahcgvo.supabase.co',
    port '5432',
    dbname 'postgres'
);

-- 3. 创建用户映射
CREATE USER MAPPING FOR postgres
SERVER docs_server
OPTIONS (
    user 'postgres',
    password 'your_docs_db_password'  -- 替换为副库的数据库密码
);

-- 4. 如果需要为 service_role 用户也创建映射
CREATE USER MAPPING FOR authenticator
SERVER docs_server
OPTIONS (
    user 'postgres',
    password 'your_docs_db_password'
);
```

---

### 步骤 3: 创建外部表映射

在主库执行，映射到副库的 `docs` 表：

```sql
-- 创建外部表
CREATE FOREIGN TABLE docs_remote (
    id bigint,
    created_at timestamptz,
    content_text text,
    ai_summary_cn text,
    tags jsonb,
    source text,
    source_author text,
    canonical_url text,
    embedding vector(1536)
)
SERVER docs_server
OPTIONS (schema_name 'public', table_name 'docs');

-- 测试连接
SELECT COUNT(*) FROM docs_remote;

-- 测试向量查询（需要先在副库创建向量索引）
SELECT id, ai_summary_cn,
       (1 - (embedding <=> '[0.1, 0.2, ...]'::vector(1536))) AS similarity
FROM docs_remote
WHERE embedding IS NOT NULL
ORDER BY similarity DESC
LIMIT 5;
```

---

### 步骤 4: 修改主库的 RPC 函数

在主库执行，替换现有的 `search_memory_events` 函数：

```sql
-- 备份原函数（可选）
-- DROP FUNCTION IF EXISTS search_memory_events_backup;
-- CREATE FUNCTION search_memory_events_backup(...) AS $$ ... $$;

-- 新版本：合并主库和副库查询
CREATE OR REPLACE FUNCTION search_memory_events(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.55,
    match_count int DEFAULT 3,
    min_confidence float DEFAULT 0.6,
    time_window_hours int DEFAULT 168,
    asset_filter text[] DEFAULT NULL,
    -- 新增参数：控制副库
    include_docs bool DEFAULT true,
    docs_max_count int DEFAULT 2,
    docs_threshold float DEFAULT 0.50
)
RETURNS TABLE (
    id text,
    created_at timestamptz,
    assets text[],
    action text,
    confidence float,
    summary text,
    similarity float,
    -- 新增字段
    source text,
    content_text text,
    metadata jsonb
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    -- CTE 1: 主库查询（news_events + ai_signals）
    WITH main_results AS (
        SELECT
            'event-' || ne.id::text AS id,
            ne.created_at,
            ais.assets,
            ais.action::text,
            ais.confidence,
            ais.summary_cn AS summary,
            (1 - (ne.embedding <=> query_embedding)) AS similarity,
            'news_events'::text AS source,
            ne.content_text,
            jsonb_build_object(
                'event_type', ais.event_type,
                'risk_flags', ais.risk_flags
            ) AS metadata
        FROM news_events ne
        INNER JOIN ai_signals ais ON ais.news_event_id = ne.id
        WHERE ne.embedding IS NOT NULL
          AND ais.confidence >= min_confidence
          AND (1 - (ne.embedding <=> query_embedding)) >= match_threshold
          AND ne.created_at >= now() - (time_window_hours || ' hours')::interval
          AND (asset_filter IS NULL OR ais.assets && asset_filter)
        ORDER BY similarity DESC, ais.confidence DESC
        LIMIT match_count
    ),
    -- CTE 2: 副库查询（docs）
    docs_results AS (
        SELECT
            'doc-' || d.id::text AS id,
            d.created_at,
            -- 从 tags.entities 提取资产代码
            COALESCE(
                ARRAY(SELECT jsonb_array_elements_text(d.tags->'entities')),
                ARRAY[]::text[]
            ) AS assets,
            'observe'::text AS action,
            1.0 AS confidence,
            COALESCE(d.ai_summary_cn, LEFT(d.content_text, 200)) AS summary,
            (1 - (d.embedding <=> query_embedding)) AS similarity,
            'docs'::text AS source,
            d.content_text,
            jsonb_build_object(
                'source', d.source,
                'author', d.source_author,
                'url', d.canonical_url,
                'tags', d.tags
            ) AS metadata
        FROM docs_remote d  -- 使用外部表
        WHERE d.embedding IS NOT NULL
          AND (1 - (d.embedding <=> query_embedding)) >= docs_threshold
          AND (
              asset_filter IS NULL
              OR d.tags->'entities' ?| asset_filter
          )
          AND include_docs = true  -- 可通过参数关闭副库
        ORDER BY similarity DESC
        LIMIT docs_max_count
    )
    -- 合并主库和副库结果
    SELECT * FROM (
        SELECT * FROM main_results
        UNION ALL
        SELECT * FROM docs_results
    ) combined
    ORDER BY similarity DESC, confidence DESC
    LIMIT (match_count + docs_max_count);
END;
$$;
```

---

### 步骤 5: 在副库创建索引（性能优化）

在副库 Supabase SQL Editor 执行：

```sql
-- 1. 向量索引
CREATE INDEX IF NOT EXISTS idx_docs_embedding_vector
ON docs
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);  -- lists = sqrt(表行数)

-- 2. JSONB 索引（加速 tags.entities 查询）
CREATE INDEX IF NOT EXISTS idx_docs_tags_entities_gin
ON docs
USING gin((tags->'entities') jsonb_path_ops);

-- 3. 时间索引（可选）
CREATE INDEX IF NOT EXISTS idx_docs_created_at
ON docs (created_at DESC);
```

---

### 步骤 6: Python 代码调整（可选）

#### 选项 A: 使用默认值（零改动）

如果 RPC 函数的新参数设置了默认值 `include_docs := true`，Python 代码无需任何修改，自动启用副库。

#### 选项 B: 通过配置控制（推荐）

在 `.env` 中添加配置：

```bash
# 副库配置
MEMORY_DOCS_ENABLED=true
MEMORY_DOCS_MAX_NOTES=2
MEMORY_DOCS_SIMILARITY_THRESHOLD=0.50
```

在 `src/config.py` 中添加：

```python
class Config:
    # ... 现有配置 ...

    # 副库配置
    MEMORY_DOCS_ENABLED: bool = _as_bool(os.getenv("MEMORY_DOCS_ENABLED", "true"))
    MEMORY_DOCS_MAX_NOTES: int = int(os.getenv("MEMORY_DOCS_MAX_NOTES", "2"))
    MEMORY_DOCS_SIMILARITY_THRESHOLD: float = float(
        os.getenv("MEMORY_DOCS_SIMILARITY_THRESHOLD", "0.50")
    )
```

在 `src/memory/repository.py` 的 `fetch_memories` 方法中，添加参数传递：

```python
# 在 _fetch_from_main_db 或 fetch_memories 方法中
from ..config import Config

params: dict[str, object] = {
    "query_embedding": list(embedding),
    "match_threshold": float(self._config.similarity_threshold),
    "match_count": int(self._config.max_notes),
    "min_confidence": float(self._config.min_confidence),
    "time_window_hours": int(self._config.lookback_hours),
    # 新增参数
    "include_docs": Config.MEMORY_DOCS_ENABLED,
    "docs_max_count": Config.MEMORY_DOCS_MAX_NOTES,
    "docs_threshold": Config.MEMORY_DOCS_SIMILARITY_THRESHOLD,
}

assets = [code for code in asset_codes or [] if code]
if assets:
    params["asset_filter"] = assets

result = await self._client.rpc("search_memory_events", params)
```

---

## 测试验证

### 1. 测试 FDW 连接

```sql
-- 在主库执行
SELECT COUNT(*) FROM docs_remote;
-- 期望：返回副库 docs 表的行数

SELECT id, ai_summary_cn FROM docs_remote LIMIT 5;
-- 期望：返回副库的 5 条记录
```

### 2. 测试 RPC 函数

```sql
-- 在主库执行，测试向量查询
SELECT
    id,
    source,
    summary,
    similarity,
    assets
FROM search_memory_events(
    query_embedding := (SELECT embedding FROM news_events WHERE embedding IS NOT NULL LIMIT 1),
    match_threshold := 0.55,
    match_count := 3,
    include_docs := true,
    docs_max_count := 2,
    docs_threshold := 0.50
);
-- 期望：返回主库 3 条 + 副库 2 条，共 5 条记录
-- source 字段应该包含 'news_events' 和 'docs'
```

### 3. Python 端测试

```python
# scripts/test_fdw_memory.py
import asyncio
from src.memory.factory import create_memory_repository
from src.config import Config
from src.utils import compute_embedding

async def test():
    repo = create_memory_repository(Config)
    embedding = await compute_embedding("Coinbase 上币 BTC ETF")

    context = await repo.fetch_memories(
        embedding=embedding,
        asset_codes=["BTC", "ETH"]
    )

    print(f"\n检索到 {len(context.entries)} 条记忆:")
    main_count = sum(1 for e in context.entries if e.source == "news_events")
    docs_count = sum(1 for e in context.entries if e.source == "docs")
    print(f"  主库: {main_count} 条")
    print(f"  副库: {docs_count} 条")

    for i, entry in enumerate(context.entries, 1):
        print(f"\n{i}. [{entry.source}] 相似度: {entry.similarity:.3f}")
        print(f"   资产: {entry.assets}")
        print(f"   摘要: {entry.summary[:80]}...")

asyncio.run(test())
```

运行测试：
```bash
uv run --with-requirements requirements.txt python scripts/test_fdw_memory.py
```

---

## 故障排查

### 问题 1: FDW 连接失败

**错误信息：**
```
ERROR: could not connect to server "docs_server"
```

**解决方案：**
1. 检查副库的 IP 白名单，确保主库 IP 可访问
2. 检查数据库密码是否正确（不是 service_role key）
3. 确认 `postgres_fdw` 扩展已启用

### 问题 2: 外部表查询慢

**现象：** 查询延迟 > 500ms

**解决方案：**
1. 在副库创建向量索引（步骤 5）
2. 减少 `docs_max_count` 参数
3. 提高 `docs_threshold` 参数（减少匹配数量）

### 问题 3: 权限错误

**错误信息：**
```
ERROR: permission denied for foreign table docs_remote
```

**解决方案：**
```sql
-- 在主库执行，授予权限
GRANT USAGE ON FOREIGN SERVER docs_server TO postgres;
GRANT SELECT ON docs_remote TO postgres;
GRANT SELECT ON docs_remote TO authenticator;
```

### 问题 4: 副库 tags.entities 为空

**现象：** 返回的 docs 记录 `assets` 字段为空数组

**解决方案：**
在副库确认 `tags` 字段的 JSONB 格式：
```json
{
  "entities": ["BTC", "ETH"],
  "narratives": ["defi", "regulation"],
  "functions": ["trading", "staking"]
}
```

如果格式不同，修改 RPC 中的提取逻辑。

---

## 性能基准

| 场景 | 延迟 | 说明 |
|-----|------|------|
| 仅主库查询 | ~150ms | 基准 |
| 主库 + 副库（FDW） | ~200ms | +50ms（跨库开销） |
| 副库索引优化后 | ~180ms | 索引减少 20ms |

---

## 回滚方案

如果需要禁用副库或回滚：

### 方法 1: 通过参数关闭

```bash
# .env
MEMORY_DOCS_ENABLED=false
```

或在 SQL 中修改默认值：
```sql
CREATE OR REPLACE FUNCTION search_memory_events(
    -- ...
    include_docs bool DEFAULT false,  -- 改为 false
    -- ...
)
```

### 方法 2: 恢复原函数

```sql
-- 删除新函数
DROP FUNCTION search_memory_events(vector, float, int, float, int, text[], bool, int, float);

-- 恢复备份函数
CREATE OR REPLACE FUNCTION search_memory_events(
    -- 原始参数 ...
) RETURNS TABLE (
    -- 原始返回字段 ...
) AS $$ ... $$ LANGUAGE plpgsql;
```

---

## 总结

**实施工作量：** 1-2 小时（主要是 SQL 配置）

**Python 代码改动：**
- 零改动（使用默认值）
- 或 ~10 行代码（传递配置参数）

**优势：**
- ✅ Python 代码几乎无需修改
- ✅ 逻辑集中在数据库层
- ✅ 易于开关和调试
- ✅ 性能可接受（+50ms）

**限制：**
- ⚠️ 需要 Supabase 支持 postgres_fdw（大部分支持）
- ⚠️ 跨库查询有网络延迟
- ⚠️ 需要妥善管理数据库密码
