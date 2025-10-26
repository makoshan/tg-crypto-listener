# 多数据源记忆集成方案（最小化 Supabase 变更）

## 目标

- 在不修改现有 Supabase 数据库的情况下，把主库 `news_events` 和副库（例如 `docs`）同时纳入 LangGraph 记忆检索。
- 只改本地配置与内存仓代码，可按需开关第二数据源。
- 为 AI 信号分析提供更丰富的历史上下文（新闻事件 + 文档知识库）。

## 背景

当前项目使用单一 Supabase 数据库的 `news_events` 表作为记忆源。为了增强 AI 分析能力，需要引入第二个数据源（如文档知识库 `docs` 表），但不希望：
- 修改现有数据库结构
- 影响现有功能稳定性
- 增加运维复杂度

该方案通过**客户端多连接 + 本地合并**的方式实现多数据源集成。

## 1. 环境变量配置

在 `.env` 增加副库配置（如不需要可留空）：

```bash
# ========== 主库（已存在）==========
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# ========== 副库（可选）==========
# 是否启用副库（默认 false）
SUPABASE_SECONDARY_ENABLED=false

# 副库连接信息（仅在 ENABLED=true 时需要）
SUPABASE_SECONDARY_URL=https://your-docs-project.supabase.co
SUPABASE_SECONDARY_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# 副库表名（默认 docs）
SUPABASE_SECONDARY_TABLE=docs

# 副库相似度阈值（默认与主库相同）
SUPABASE_SECONDARY_SIMILARITY_THRESHOLD=0.75

# 副库最大返回数量（默认与主库相同）
SUPABASE_SECONDARY_MAX_RESULTS=10
```

**配置说明**：
- `SUPABASE_SECONDARY_ENABLED=true` 时才会初始化副库 client，避免无配置导致报错。
- 副库与主库可以是同一个 Supabase 项目（不同表），也可以是不同项目。
- 副库独立配置相似度阈值，允许对文档知识库使用不同的检索策略。

## 2. 数据表结构

### 2.1 主库：`news_events` 表

```sql
CREATE TABLE news_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    text TEXT NOT NULL,                    -- 原始消息文本
    summary TEXT,                          -- AI 生成摘要
    hash_raw CHAR(64),                     -- 消息哈希
    embedding VECTOR(1536),                -- 文本向量
    metadata JSONB,                        -- 其他元数据
    -- ...其他字段
);
```

### 2.2 副库：`docs` 表

```sql
CREATE TABLE docs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    source TEXT,                           -- 来源标识
    canonical_url TEXT,                    -- 原始 URL
    content_text TEXT,                     -- 文档正文
    content_hash CHAR(64),                 -- 内容哈希
    url_hash CHAR(64),                     -- URL 哈希
    embedding VECTOR(1536),                -- 文本向量
    ai_summary_cn TEXT,                    -- 中文摘要
    tags JSONB,                            -- 标签（格式：{"entities":[], "narratives":[], "functions":[]}）
    source_author TEXT,                    -- 作者
    lang VARCHAR,                          -- 语言
    published_at TIMESTAMPTZ,              -- 发布时间
    similar_count INT DEFAULT 0            -- 相似文档数
);
```

**字段映射策略**：
| docs 表字段 | 处理方式 | 说明 |
|------------|----------|------|
| `ai_summary_cn` | 作为 `MemoryEntry.summary` 的主要内容 | 若为空则回退到 `content_text` 截断 |
| `content_text` | 摘要兜底 | 保留前若干字符，防止过长 |
| `created_at` | 转换为 `MemoryEntry.created_at` | 以 UTC 时间解析 |
| `tags` | 提取 `entities`/`tickers` 作为 `MemoryEntry.assets` | 统一转大写、去重 |
| `source` / `canonical_url` / `source_author` | 拼接到摘要尾部 | 文本方式保留关键信息 |
| `embedding` | 用于 RPC 相似度计算 | 不直接存入 `MemoryEntry` |

## 3. 代码改动思路

### 3.1 配置层：加载副库参数

- 在 `src/config.py` 中新增以下字段，并保持与现有模式一致（全部从环境变量读取，必要时使用 `_as_bool`）：

  ```python
  class Config:
      SUPABASE_SECONDARY_ENABLED: bool = _as_bool(
          os.getenv("SUPABASE_SECONDARY_ENABLED", "false")
      )
      SUPABASE_SECONDARY_URL: str = os.getenv("SUPABASE_SECONDARY_URL", "").strip()
      SUPABASE_SECONDARY_SERVICE_KEY: str = os.getenv(
          "SUPABASE_SECONDARY_SERVICE_KEY",
          "",
      ).strip()
      SUPABASE_SECONDARY_TABLE: str = os.getenv(
          "SUPABASE_SECONDARY_TABLE",
          "docs",
      ).strip()
      SUPABASE_SECONDARY_SIMILARITY_THRESHOLD: float = float(
          os.getenv("SUPABASE_SECONDARY_SIMILARITY_THRESHOLD", "0.75")
      )
      SUPABASE_SECONDARY_MAX_RESULTS: int = int(
          os.getenv("SUPABASE_SECONDARY_MAX_RESULTS", "6")
      )
  ```

- 额外提供一个简单的校验方法（例如 `validate_secondary_config`）以便在诊断脚本或启动日志中主动检查缺失项。

### 3.2 Supabase 客户端：支持多实例缓存

当前的 `get_supabase_client` 使用全局单例 `_CLIENT_INSTANCE`（`src/db/supabase_client.py:118-134`），无法同时维护两套凭据。需要改成基于 `(url, service_key)` 的缓存表，例如：

```python
_CLIENT_CACHE: dict[tuple[str, str], SupabaseClient] = {}

def get_supabase_client(url: str, service_key: str, *, timeout: float = 8.0) -> SupabaseClient:
    identifier = (url.strip(), service_key.strip())
    if identifier not in _CLIENT_CACHE:
        if not identifier[0] or not identifier[1]:
            raise SupabaseError("Supabase URL and service key are required")
        _CLIENT_CACHE[identifier] = SupabaseClient(rest_url=identifier[0], service_key=identifier[1], timeout=timeout)
    return _CLIENT_CACHE[identifier]
```

这样可以在不破坏现有调用的前提下，引入副库 client。

### 3.3 新建 `MultiSourceMemoryRepository`

保持现有 `SupabaseMemoryRepository.fetch_memories` 行为不变，避免影响现有调用链（管线依然期望返回 `MemoryContext`）。在 `src/memory` 下新增一个包装器，负责：

1. 调用主库 `SupabaseMemoryRepository.fetch_memories` 拿到 `MemoryContext`。
2. 按需调用副库 RPC，将返回结果转换成 `MemoryEntry` 列表。
3. 合并、去重、排序后重新打包成 `MemoryContext` 返回。

示例结构：

```python
class MultiSourceMemoryRepository:
    def __init__(
        self,
        primary: SupabaseMemoryRepository,
        *,
        secondary_client: SupabaseClient | None,
        secondary_table: str,
        config: MemoryRepositoryConfig,
    ) -> None:
        self._primary = primary
        self._secondary = (secondary_client, secondary_table) if secondary_client else None
        self._config = config
        self._logger = setup_logger(__name__)

    async def fetch_memories(
        self,
        *,
        embedding: Sequence[float] | None,
        asset_codes: Iterable[str] | None = None,
    ) -> MemoryContext:
        primary_context = await self._primary.fetch_memories(
            embedding=embedding,
            asset_codes=asset_codes,
        )

        entries: list[MemoryEntry] = list(primary_context.entries)

        if self._secondary and embedding:
            entries.extend(
                await self._fetch_from_secondary(
                    embedding=embedding,
                    asset_codes=asset_codes,
                )
            )

        merged = self._merge_and_rank(entries)
        context = MemoryContext()
        context.extend(merged[: self._config.max_notes])
        return context
```

### 3.4 副库检索逻辑

- `_fetch_from_secondary` 直接调用 `SupabaseClient.rpc("match_documents", {...})`，获取列表结果。
- 由于 `MemoryEntry` 仅包含 `id / created_at / assets / action / confidence / summary / similarity`，需要在转换时进行映射：

  ```python
  MemoryEntry(
      id=f"docs:{row.get('id', '')}",
      created_at=_parse_timestamp(row.get("created_at")),
      assets=_extract_assets(row),
      action="inform",
      confidence=float(row.get("similarity", 0.0)),
      summary=_format_summary(row),
      similarity=float(row.get("similarity", 0.0)),
  )
  ```

  - `_extract_assets` 可读取 `tags["entities"]` 或 `tags["tickers"]`，若不存在则返回 `[]`。
  - `_format_summary` 建议拼接摘要与核心元数据，例如：`"{summary}（来源: {source}，链接: {canonical_url}）"`，用文本方式保留必要信息。

- 如果副库无结果或调用失败，记录 warning 日志并继续使用主库结果，不抛异常。

### 3.5 合并与去重

在 `MultiSourceMemoryRepository` 内实现 `_merge_and_rank`：

1. 去重：根据 `(summary, created_at.date())` 或 `id` 去重，副库可使用带前缀的 id，主库保持原样。
2. 排序：按照相似度降序排列，若相似度相同再按时间降序。
3. 截断：遵守 `MemoryRepositoryConfig.max_notes`，确保返回给下游的上下文长度保持不变。

```python
def _merge_and_rank(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
    unique: dict[str, MemoryEntry] = {}
    for entry in entries:
        unique.setdefault(entry.id, entry)

    return sorted(
        unique.values(),
        key=lambda item: (item.similarity, item.created_at),
        reverse=True,
    )
```

> 如需更细粒度的去重（例如通过摘要哈希），可以在后续迭代中再增强，初版先保证兼容性。

### 3.6 与现有工厂/Hybrid 集成

- `src/memory/factory.py` 中新增一个分支：当 `MEMORY_BACKEND=supabase` 且启用了副库配置时，使用 `MultiSourceMemoryRepository` 包裹原先的 `SupabaseMemoryRepository`。
- `HybridMemoryRepository` 继续以 `SupabaseMemoryRepository` 为核心；若启用副库，可以在构造时传入 `MultiSourceMemoryRepository`，或在 `Hybrid` 内部识别 `repository.fetch_memories` 的行为保持一致即可。

整体目标是让 `LangGraph` 管线仍然拿到 `MemoryContext` 对象，而不需要改动 `src/pipeline/langgraph_pipeline.py`。

## 4. 副库 RPC 函数配置

副库需要创建类似主库的向量检索 RPC 函数。在副库 Supabase Dashboard 中执行：

```sql
-- 创建文档匹配函数
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding vector(1536),
    match_threshold float,
    match_count int
)
RETURNS TABLE (
    id bigint,
    content_text text,
    ai_summary_cn text,
    created_at timestamptz,
    tags jsonb,
    canonical_url text,
    source_author text,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        docs.id,
        docs.content_text,
        docs.ai_summary_cn,
        docs.created_at,
        docs.tags,
        docs.canonical_url,
        docs.source_author,
        1 - (docs.embedding <=> query_embedding) AS similarity
    FROM docs
    WHERE 1 - (docs.embedding <=> query_embedding) > match_threshold
    ORDER BY docs.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
```

**注意**：
- `<=>` 是 pgvector 的余弦距离运算符
- `1 - distance` 转换为相似度（0-1，越大越相似）
- 需要在 `docs.embedding` 列上创建 HNSW 索引以提升性能：

```sql
CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);
```

## 5. 排序与去重策略

### 5.1 去重规则

- **ID 维度**：主库沿用原始 `id`，副库创建带前缀的 ID（如 `docs:{id}`），自然避免冲突。
- **摘要哈希**：作为兜底，可对 `MemoryEntry.summary` 取哈希，防止同一条内容在不同来源重复出现。

示例：

```python
def _deduplicate(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    unique: dict[str, MemoryEntry] = {}
    seen_summaries: set[str] = set()

    for entry in entries:
        summary_hash = hashlib.sha256(entry.summary.encode("utf-8")).hexdigest()
        if entry.id in unique or summary_hash in seen_summaries:
            continue
        unique[entry.id] = entry
        seen_summaries.add(summary_hash)

    return list(unique.values())
```

### 5.2 排序规则

使用现有字段即可实现多维排序：

1. **相似度降序**：`entry.similarity`
2. **时间降序**：`entry.created_at`
3. **来源优先级**（可选）：通过 ID 前缀判定

```python
def _merge_and_rank(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
    deduped = self._deduplicate(entries)
    return sorted(
        deduped,
        key=lambda item: (
            round(item.similarity, 6),
            item.created_at.timestamp(),
            0 if item.id.startswith("news_events") else 1,
        ),
        reverse=True,
    )
```

## 6. 开关控制与日志

### 6.1 配置验证

可在 `Config` 中提供轻量校验方法：

```python
@classmethod
def validate_secondary_config(cls) -> None:
    if not cls.SUPABASE_SECONDARY_ENABLED:
        return
    if not cls.SUPABASE_SECONDARY_URL:
        raise ValueError("SUPABASE_SECONDARY_URL is required when secondary source is enabled")
    if not cls.SUPABASE_SECONDARY_SERVICE_KEY:
        raise ValueError("SUPABASE_SECONDARY_SERVICE_KEY is required when secondary source is enabled")
```

启动时调用一次，能够在日志里提前暴露配置问题。

### 6.2 运行时日志

- 初始化：记录主库、副库的 URL/表名以及阈值。
- 检索：输出各数据源返回数量、耗时、合并后数量。
- 降级：当副库失败时，仅记录 warning，不影响主流程。

保持现有日志格式即可与其它模块一致。

## 7. 测试与验证

### 7.1 配置检查

```bash
rg --no-heading SUPABASE_SECONDARY .env
```

确保 URL、Service Key 和表名均已设置，并启用 `SUPABASE_SECONDARY_ENABLED=true`。

### 7.2 本地验证流程

1. 运行 `pytest tests/db/test_supabase.py -v`，确认主库配置正常。
2. 运行 `pytest tests/memory/test_multi_source_repository.py -v`，验证副库合并逻辑。

### 7.3 集成测试示例

可以在 `tests/` 下新增 `test_memory_multi_source.py`：

```python
import asyncio
from src.config import Config
from src.memory.factory import create_memory_backend

async def _fetch():
    config = Config()
    bundle = create_memory_backend(config)
    repo = bundle.repository

    # 这里使用一个简单的假向量，确保长度与生产 embedding 相同
    dummy_embedding = [0.01] * 1536
    context = await repo.fetch_memories(embedding=dummy_embedding, asset_codes=None)
    return context

def test_multi_source_fetch(event_loop):
    context = event_loop.run_until_complete(_fetch())
    assert context is not None
    assert isinstance(context.entries, list)
    assert len(context.entries) <= Config.MEMORY_MAX_NOTES
```

> 若需要真实向量，可在测试前注入一条固定数据并读取其 embedding。

## 8. 最佳实践

- **异步并发**：副库请求可以与主库共享事件循环，不需要额外线程。
- **超时隔离**：副库查询设置独立超时，避免拖慢主库返回。
- **提示词格式**：在写入 `summary` 时控制长度（例如 300 字以内），保证 prompt 一致性。
- **监控**：在日志或指标里分别统计主库、副库命中率，有助于观察副库贡献度。

## 9. 后续扩展

- **多副库**：先引入单副库稳定运行，再考虑扩展到配置化的多副库，届时需要为每个源维护独立的阈值、权重和排序策略。
- **字段扩展**：如果未来要在 prompt 中展示更多结构化信息，可以考虑在 `MemoryEntry` 中新增 `metadata` 字段，但这会影响现有序列化逻辑，需要配套调整。
- **权重调节**：可以结合事件类型或关键词，为不同来源赋予动态权重（例如法规新闻更依赖文档）。实现方式是在 `_merge_and_rank` 之前调整 `entry.similarity`。

## 10. 故障排查

| 场景 | 可能原因 | 建议操作 |
|------|----------|----------|
| 副库始终返回 0 条 | RPC 没有部署 / 阈值过高 | 登录副库执行 `select count(*) from docs`，并尝试降低 `SUPABASE_SECONDARY_SIMILARITY_THRESHOLD` |
| 初始化报错 `Supabase URL and service key are required` | 未正确加载环境变量 | 确认 `.env` 和部署环境同时配置了 URL/Key |
| 检索明显变慢 | 副库延迟高 | 打开 DEBUG 日志观察副库耗时，必要时降低 `SUPABASE_SECONDARY_MAX_RESULTS` |

## 11. 总结

- **核心收益**：在保持现有 Supabase 主库逻辑不变的前提下，引入额外知识源，丰富 LangGraph 的上下文。
- **必要改动**：配置新增副库参数、允许 Supabase 客户端多实例、实现多源仓储包装器。
- **实施顺序**：
  1. 合并配置与客户端层改动；
  2. 引入 `MultiSourceMemoryRepository` 并在工厂中串联；
  3. 联调副库 RPC，并补充基础测试。
- **何时启用**：当副库准备好向量索引且数据质量稳定时再开启，否则保持开关关闭，避免影响现网流程。
