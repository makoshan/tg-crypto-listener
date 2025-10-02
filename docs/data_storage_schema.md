# 数据存储设计（新闻与信号）

> 目标：持久化 Telegram 等渠道抓取的原始新闻、AI 解析输出的结构化信号，以及用于回测与监控的辅助元数据。设计遵循 Supabase/PostgreSQL 生态，字段命名与数据类型可跨项目共用。

## 1. 总览

数据流分为三层：

1. **原始事件层（Raw Layer）**：按消息粒度保留来源文本、附件引用、哈希，用于追溯与去重。
2. **信号决策层（Signal Layer）**：存储 AI 解析结果、行动建议、置信度等结构化字段，关联到原始事件。
3. **聚合洞察层（Insight Layer）**：可选，用于人工整理、策略调参或多事件合成。

```
[source_feeds] -> [news_events] -> [ai_signals] -> [strategy_insights]
                                 ↘ [market_snapshots]*
```

`market_snapshots` 为可选行情快照，便于回测。星号表示按需启用。

## 2. `news_events` — 原始新闻表

| 列名 | 类型 | 说明 | 约束 / 默认 |
| --- | --- | --- | --- |
| `id` | `bigserial` | 主键 | `primary key` |
| `created_at` | `timestamptz` | 写入时间 | `default now()` |
| `updated_at` | `timestamptz` | 更新时间 | `default now()`，自动触发器更新 |
| `source` | `text` | 来源通道名称（如 `Onchain Lens Channel`） | `not null` |
| `source_message_id` | `text` | 来源平台消息 ID（Telegram message id） | `not null`，同源唯一 |
| `source_url` | `text` | 原始消息/帖子链接 | 可为空 |
| `language` | `varchar(12)` | 自动识别语言 | 默认 `unknown` |
| `published_at` | `timestamptz` | 消息发布时间（若缺失取抓取时间） | `not null` |
| `content_text` | `text` | 原文文本 | `not null` |
| `summary` | `text` | 原始新闻摘要（如果来源提供） | 可为空 |
| `translated_text` | `text` | 译文（若启用翻译） | 可为空 |
| `media_refs` | `jsonb` | 附件信息（图片、链接、文档） | 默认 `[]` |
| `hash_raw` | `char(64)` | 原文 SHA-256，用于去重 | `not null`，建唯一索引 |
| `hash_canonical` | `char(64)` | 归一化后哈希（去掉空格、URL 等） | 可空 |
| `embedding` | `vector(1536)` | 原文语义向量，用于语义去重/检索 | 可空，`pgvector`，基于 OpenAI `text-embedding-3-small` |
| `keywords_hit` | `jsonb` | 命中关键词列表 | 默认 `[]` |
| `ingest_status` | `varchar(32)` | 处理状态：`pending/processed/error` | 默认 `pending` |
| `metadata` | `jsonb` | 额外元数据（抓取延迟、机器人版本） | 默认 `{}` |

**索引建议**：
- `unique (source, source_message_id)` 防重复写入。
- `unique (hash_raw)` 支持跨源去重。
- `gin (keywords_hit)` 方便关键词查询。
- `index (published_at desc)` 支持时间序列查询。
- `index (ingest_status)` 加速状态筛选。
- `index (updated_at desc)` 追踪更新记录。
- `ivfflat (embedding)` 或 `hnsw (embedding)` 支持向量相似度筛查潜在重复。

## 3. `ai_signals` — AI 决策信号表

| 列名 | 类型 | 说明 | 约束 / 默认 |
| --- | --- | --- | --- |
| `id` | `bigserial` | 主键 | `primary key` |
| `news_event_id` | `bigint` | 对应原始事件 ID | 外键 `references news_events(id)`，`on delete cascade` |
| `created_at` | `timestamptz` | 写入时间 | `default now()` |
| `model_name` | `text` | 调用的模型（如 `gemini-2.0-flash`） | `not null` |
| `summary_cn` | `text` | AI 中文摘要 | `not null` |
| `event_type` | `varchar(32)` | 事件分类（枚举：listing/hack/whale/...） | `not null` |
| `assets` | `varchar(120)` | 影响资产代码，逗号分隔 | 默认 `NONE` |
| `asset_names` | `text` | 资产中文名或描述 | 可为空 |
| `action` | `varchar(24)` | 建议动作：`buy/sell/observe` | `not null` |
| `direction` | `varchar(24)` | `long/short/neutral` | 默认 `neutral` |
| `confidence` | `float4` | 0–1 置信度 | 默认 `0` |
| `strength` | `varchar(16)` | `low/medium/high` | 默认 `low` |
| `risk_flags` | `jsonb` | 风险标签数组 | 默认 `[]` |
| `notes` | `text` | 模型补充说明 | 可为空 |
| `links` | `jsonb` | 关联链接列表 | 默认 `[]` |
| `execution_path` | `varchar(16)` | `hot/warm/cold/skip` | 默认 `cold` |
| `should_alert` | `boolean` | 是否推送 | 默认 `false` |
| `latency_ms` | `integer` | 从抓取到出信号的延迟 | 可为空 |
| `raw_response` | `jsonb` | 模型原始 JSON（截断或压缩） | 可为空 |

**索引建议**：
- `index on news_event_id` 用于联查。
- `index on created_at desc` 支持时间序列。
- `partial index where should_alert = true` 加速待推送查询。
- `index on event_type` 支持事件类型筛选。
- `index on execution_path` 支持执行路径查询。

## 4. `strategy_insights` — 洞察/人工标注表（可选）

| 列名 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `bigserial` | 主键 |
| `created_at` / `updated_at` | `timestamptz` | 默认 `now()`，自动触发器更新 |
| `title` | `text` | 洞察标题（如"Solana CME 未平仓量暴增"） |
| `summary` | `text` | 洞察摘要 |
| `narrative` | `text` | 长文描述，支持总结多条事件 |
| `relation` | `text` | 事件间关系描述 |
| `action` | `text` | 建议动作或策略 |
| `confidence` | `float4` | 主观信心值 |
| `source_urls` | `jsonb` | 关联链接数组 |
| `news_event_ids` | `_int8` | 关联的新闻事件 ID 列表 |
| `ai_signal_ids` | `_int8` | 关联的信号 ID 列表 |
| `tags` | `jsonb` | 自定义标签（叙事、板块等） |
| `url_hash` | `text` | URL 哈希，用于去重 |
| `content_hash` | `text` | 内容哈希，用于去重 |
| `embedding` | `vector(1536)` | （可选）用于语义检索（OpenAI `text-embedding-3-small` 输出） |

**索引建议**：
- `index on created_at desc` 支持时间序列查询。
- `gin (tags)` 支持标签查询。
- `unique (url_hash) where url_hash is not null` URL 去重。
- `index on content_hash` 内容去重筛查。

## 5. `market_snapshots` — 行情快照（可选）

用于回测信号表现，按需保存主要交易对行情。

| 列名 | 类型 | 说明 | 约束 / 默认 |
| --- | --- | --- | --- |
| `id` | `bigserial` | 主键 | `primary key` |
| `captured_at` | `timestamptz` | 截取时间 | `not null` |
| `asset` | `varchar(32)` | 资产代码 | `not null` |
| `price` | `numeric(18,8)` | 即时价格 | `not null` |
| `volume_1h` | `numeric(20,2)` | 近 1 小时成交量 | 可为空 |
| `open_interest` | `numeric(20,2)` | 衍生品未平仓量 | 可为空 |
| `external_source` | `text` | 数据来源（Binance API 等） | 可为空 |
| `metadata` | `jsonb` | 额外指标（资金费率、深度等） | 默认 `{}` |

**索引建议**：
- `unique (asset, captured_at)` 防止同一时刻重复记录
- `index (captured_at desc)` 支持时间序列查询
- `index (asset)` 支持按资产筛选

可结合 `ai_signals.execution_path` 做 T+5m / T+1h 表现评估。

## 6. 事件生命周期

1. **抓取**：`listener.py` 捕获消息，写入 `news_events`，计算 `hash_raw` / `hash_canonical`，并标记 `ingest_status='pending'`。
2. **去重 + 翻译**：若检测重复则直接关联已有记录；成功翻译后更新 `translated_text`、`language`、`metadata.translation_confidence`。
   - 去重策略结合 `hash_raw` 与 `embedding` 近邻搜索（如 `cosine_distance < 0.1`），避免跨渠道改写的重复新闻。
3. **AI 信号**：调用 `AiSignalEngine`，结果写入 `ai_signals`（含 `news_event_id`），并决定是否推送与执行。
4. **追踪执行**（可选）：若触发热路径交易，在同事务或异步任务中更新 `ai_signals.execution_path='hot'`，并写入交易日志表（此处略）。
5. **回测/洞察**：研发或策略人员可在 `strategy_insights` 中记录复盘要点，并通过 `news_event_ids` / `ai_signal_ids` 进行关联。

## 7. 实施建议

- **数据库**：使用 Supabase（PostgreSQL 16 + pgvector），与现有项目对齐。
- **迁移管理**：采用 `sqlx migrate` 或 `dbmate`，保持 DDL 可追踪。
- **向量生成**：在 ingest 流程中调用 `text-embedding-3-small`（1536 维）等模型写入 `embedding`，并定期重建 `ivfflat`/`hnsw` 索引；若换模型需同步调整列维度。
- **数据保留**：建议对 `raw_response`、`media_refs` 设定 30 天 TTL，过期转存到 S3 以减少主库压力。
- **访问权限**：
  - 服务端角色：`ingest_writer`（只能写入 raw）、`signal_writer`（只能写入信号）、`analyst_reader`（只读视图）。
  - 前端 / BI：通过只读视图，例如 `v_signal_feed`（join 原始事件 + 信号）。
- **质量监控**：
  - 建立定时任务统计 `should_alert=true` 的信号数量、模型延迟、重复率。
  - 对 `confidence` < 阈值但仍推送的案例记录 `risk_flags`，便于调参。

## 8. Supabase 集成实现方案

### 8.1 模块结构

```
src/db/
├── __init__.py
├── supabase_client.py   # 负责初始化并缓存 Supabase 客户端
├── repositories.py      # Repository 层封装 CRUD
└── models.py            # 可选的数据模型/类型提示
```

- `supabase_client.py`：从环境变量读取 `SUPABASE_URL`、`SUPABASE_SERVICE_KEY`，构建单例客户端；若禁用持久化则返回空实现。
- `repositories.py`：拆分为 `NewsEventRepository`、`AiSignalRepository`、`StrategyInsightRepository`，对外提供异步方法（`insert_event`、`insert_signal`、`check_duplicate` 等）。
- `models.py`：定义数据类/TypedDict，约束入参结构，便于静态检查。

### 8.2 数据写入流程

**主流程（同步）：**

1. **收集原始数据**（`listener._handle_new_message`）
   - 从 Telegram event 提取：
     ```python
     source_message_id = str(event.message.id)
     source_url = f"https://t.me/c/{chat_id}/{message_id}"  # 私有频道
     published_at = event.message.date  # datetime 对象
     media_refs = await self._extract_media(event.message)
     ```
   - 已有数据：`message_text`、`translated_text`、`language`、`keywords_hit`

2. **计算哈希与去重**
   ```python
   import hashlib
   import re

   def compute_hash_raw(text: str) -> str:
       return hashlib.sha256(text.encode('utf-8')).hexdigest()

   def compute_hash_canonical(text: str) -> str:
       # 归一化：去除空白、URL、emoji、标点
       normalized = re.sub(r'\s+', '', text)
       normalized = re.sub(r'https?://\S+', '', normalized)
       normalized = re.sub(r'[^\w\u4e00-\u9fff]', '', normalized)  # 保留中英文
       return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

   hash_raw = compute_hash_raw(message_text)
   hash_canonical = compute_hash_canonical(message_text)
   ```

3. **生成 Embedding**（同步，Phase 1 即启用）
   ```python
   from openai import AsyncOpenAI

   openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

   # 生成向量
   try:
       response = await openai_client.embeddings.create(
           model="text-embedding-3-small",
           input=message_text[:8000]  # 限制长度
       )
       embedding = response.data[0].embedding  # 1536 维
   except Exception as e:
       logger.warning("Embedding 生成失败，跳过向量去重: %s", e)
       embedding = None
   ```

4. **去重检查**（若启用 `ENABLE_DB_PERSISTENCE=true`）
   ```python
   # Level 1: 精确哈希去重
   existing_id = await NewsEventRepository.check_duplicate_by_hash(hash_raw)
   if existing_id:
       logger.debug("精确去重命中: event_id=%s", existing_id)
       return existing_id

   # Level 2: 语义向量去重（仅当 embedding 生成成功）
   if embedding:
       similar = await NewsEventRepository.check_duplicate_by_embedding(
           embedding=embedding,
           threshold=0.92,  # 相似度阈值
           time_window_hours=72  # 只查 3 天内
       )
       if similar:
           logger.info(
               "语义去重命中: event_id=%s similarity=%.3f",
               similar.id,
               similar.similarity
           )
           # 标记为语义重复，但仍写入（可选）
           return similar.id
   ```

5. **写入原始事件**（含 embedding）
   ```python
   news_event_id = await NewsEventRepository.insert_event({
       "source": source_name,
       "source_message_id": source_message_id,
       "source_url": source_url,
       "published_at": published_at.isoformat(),
       "content_text": message_text,
       "translated_text": translated_text,
       "language": language,
       "hash_raw": hash_raw,
       "hash_canonical": hash_canonical,
       "embedding": embedding,  # 🆕 直接写入向量
       "keywords_hit": keywords_hit,
       "media_refs": media_refs,
       "ingest_status": "processed",  # 已有 embedding，直接标记为 processed
       "metadata": {
           "translation_confidence": translation_confidence,
           "embedding_model": "text-embedding-3-small",
           "embedding_generated_at": datetime.now().isoformat(),
           "bot_version": __version__,
           "ingestion_latency_ms": int((datetime.now() - event_time).total_seconds() * 1000)
       }
   })
   ```

6. **写入 AI 信号**（若有结果）
   ```python
   if signal_result and signal_result.status in ("success", "skip"):
       latency_ms = int((datetime.now() - event_time).total_seconds() * 1000)

       # 判定执行路径
       execution_path = "cold"
       if signal_result.should_execute_hot_path:
           execution_path = "hot"
       elif signal_result.confidence >= 0.7:
           execution_path = "warm"
       elif signal_result.status == "skip":
           execution_path = "skip"

       await AiSignalRepository.insert_signal({
           "news_event_id": news_event_id,
           "model_name": config.AI_MODEL_NAME,
           "summary_cn": signal_result.summary,
           "event_type": signal_result.event_type,
           "assets": signal_result.asset,
           "asset_names": signal_result.asset_names,
           "action": signal_result.action,
           "direction": signal_result.direction,
           "confidence": signal_result.confidence,
           "strength": signal_result.strength,
           "risk_flags": signal_result.risk_flags,
           "notes": signal_result.notes,
           "links": signal_result.links,
           "execution_path": execution_path,
           "should_alert": forwarded,  # 实际是否推送
           "latency_ms": latency_ms,
           "raw_response": signal_result.raw_response[:5000]  # 截断避免过大
       })
   ```

7. **向量索引创建**（Phase 1 完成后立即执行）
   ```sql
   -- 当表中有数据后，立即创建向量索引
   create index concurrently idx_news_events_embedding
     on news_events using ivfflat(embedding vector_cosine_ops)
     with (lists = 100);
   ```

**Phase 2 优化（异步 Embedding）：**

8. **改为异步生成**（Phase 2 性能优化）
   ```python
   # 写入时先不生成 embedding，标记为 pending
   news_event_id = await NewsEventRepository.insert_event({
       ...
       "embedding": None,  # 暂不生成
       "ingest_status": "pending",  # 等待 embedding
   })

   # 后台任务异步生成
   asyncio.create_task(generate_embedding_async(news_event_id, message_text))
   ```

9. **后台 Embedding Worker**
   ```python
   async def embedding_worker():
       while True:
           events = await NewsEventRepository.get_pending_embedding(limit=10)
           if events:
               # 批量生成
               texts = [e.content_text for e in events]
               response = await openai_client.embeddings.create(
                   model="text-embedding-3-small",
                   input=texts
               )
               for event, emb_data in zip(events, response.data):
                   await NewsEventRepository.update_embedding(
                       event.id,
                       emb_data.embedding
                   )
           await asyncio.sleep(5)  # 每 5 秒一批
   ```

### 8.3 事务与重试策略

**事务处理：**

Supabase REST API 不支持原生事务，采用以下补偿策略：

1. **两阶段提交模式**
   ```python
   news_event_id = None
   try:
       # Phase 1: 写入 news_events
       news_event_id = await NewsEventRepository.insert_event(...)

       # Phase 2: 写入 ai_signals
       if signal_result:
           await AiSignalRepository.insert_signal(news_event_id=news_event_id, ...)
   except Exception as e:
       # 补偿：删除孤立的 news_event
       if news_event_id and not signal_result:
           await NewsEventRepository.delete(news_event_id)
       raise
   ```

2. **外键级联**
   - 利用数据库 `on delete cascade` 保证数据一致性
   - 删除 news_event 时自动清理关联的 ai_signals

**重试机制：**

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((NetworkError, TimeoutError))
)
async def insert_with_retry(...):
    return await supabase.insert(...)
```

- **可重试错误**：网络超时、503 Service Unavailable、429 Rate Limit
- **不可重试错误**：唯一索引冲突（重复数据）、数据格式错误
- **重试策略**：指数退避，1s → 2s → 4s → 8s（最多 3 次）
- **超时阈值**：单次请求 5 秒超时，总重试时长不超过 30 秒

**失败降级：**

```python
try:
    await persist_to_db(...)
except SupabaseError as e:
    logger.error("数据库存储失败: %s", e)

    # 降级方案：写入本地文件队列
    await write_to_local_queue({
        "event": event_data,
        "signal": signal_data,
        "timestamp": datetime.now().isoformat(),
        "retry_count": 0
    })

    # 不阻断主流程，继续转发消息
```

**本地队列恢复**（后台任务）：
- 定期扫描本地队列文件
- 重试写入 Supabase
- 成功后删除队列文件

### 8.4 多层去重策略

**Level 1: 内存缓存去重**（最快，~0.1ms）
```python
# 维护最近 1000 条消息的哈希 LRU 缓存
from functools import lru_cache

class HashCache:
    def __init__(self, maxsize=1000):
        self._cache = {}
        self._order = []
        self._maxsize = maxsize

    def contains(self, hash_raw: str) -> bool:
        return hash_raw in self._cache

    def add(self, hash_raw: str, event_id: int):
        if len(self._cache) >= self._maxsize:
            oldest = self._order.pop(0)
            del self._cache[oldest]
        self._cache[hash_raw] = event_id
        self._order.append(hash_raw)
```

**Level 2: 精确哈希去重**（快，~5ms）
- 依赖 `hash_raw` 唯一索引
- 数据库查询：
  ```sql
  select id from news_events where hash_raw = $1 limit 1;
  ```
- 若命中：跳过写入，复用已有 `news_event_id`

**Level 3: 归一化哈希去重**（中等，~10ms）
- 使用 `hash_canonical`（去除空白、URL、emoji、标点后的哈希）
- 捕获轻微改写的重复内容
- 查询：
  ```sql
  select id from news_events
  where hash_canonical = $1
  limit 1;
  ```

**Level 3: 语义向量去重**（Phase 1 即启用，~100-300ms）
- 生成 embedding 后立即执行
- 使用余弦相似度搜索：
  ```sql
  select
    id,
    content_text,
    1 - (embedding <=> $1::vector) as similarity
  from news_events
  where embedding is not null
    and created_at > now() - interval '72 hours'  -- 只查近 3 天
  order by embedding <=> $1::vector
  limit 1;
  ```
- 判定规则（Phase 1 保守阈值）：
  - `similarity >= 0.92` → 确定重复，跳过写入
  - `0.85 <= similarity < 0.92` → 疑似重复，写入但标记 `metadata.similar_to`
  - `similarity < 0.85` → 不重复

**去重决策树（Phase 1 版本）：**
```
1. 生成 embedding（同步调用 OpenAI API）
   ↓
2. 检查 hash_raw → 命中？跳过
   ↓ 未命中
3. 检查 embedding 相似度（similarity >= 0.92）→ 命中？跳过
   ↓ 未命中
4. 写入数据库（含 embedding）
```

**去重决策树（Phase 2 优化版本）：**
```
1. 检查内存缓存 → 命中？跳过
   ↓ 未命中
2. 检查 hash_raw → 命中？跳过
   ↓ 未命中
3. 异步生成 embedding，先写入（ingest_status=pending）
   ↓
4. 后台任务：生成 embedding 完成后，检查相似度
   ↓
5. 若发现重复，标记 metadata.semantic_duplicate_of
```

**阈值调优建议：**
- **新闻快讯**：阈值 0.92（保守，减少误判）
- **社交媒体**：阈值 0.88（宽松，捕获改写）
- **官方公告**：阈值 0.95（严格，精确去重）

### 8.5 配置与依赖

- `config.py` 中新增：
  ```python
  SUPABASE_URL = os.getenv("SUPABASE_URL", "")
  SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
  ENABLE_DB_PERSISTENCE = _as_bool(os.getenv("ENABLE_DB_PERSISTENCE", "false"))
  ```
- `.env` 示例：
  ```
  SUPABASE_URL=https://crypto-signal-lab.supabase.co
  SUPABASE_SERVICE_KEY=******
  ENABLE_DB_PERSISTENCE=true
  ```
- 依赖：安装 `supabase` 或使用 `httpx` 调用 Supabase REST；嵌入生成使用 OpenAI SDK（`text-embedding-3-small`）。

### 8.6 分阶段实施路线图

**Phase 1: MVP + Embedding 去重** ✅ 优先级：P0
- [ ] 创建 `src/db/` 模块结构
- [ ] 实现 `SupabaseClient` 单例
- [ ] 实现哈希计算工具（`hash_raw`, `hash_canonical`）
- [ ] 实现 `NewsEventRepository.insert_event()`
- [ ] 实现 `AiSignalRepository.insert_signal()`
- [ ] 集成 OpenAI `text-embedding-3-small` API
- [ ] 实现同步 embedding 生成（写入时生成）
- [ ] 实现 `NewsEventRepository.check_duplicate()` - 支持哈希 + 向量去重
- [ ] 实现语义相似度搜索（余弦距离）
- [ ] 在 `listener._persist_event()` 集成调用
- [ ] 配置 `.env` 添加 Supabase 和 OpenAI 凭证
- [ ] 基础错误处理（捕获异常，不阻断主流程）

**验收标准：**
- 新消息成功写入 `news_events` 表，自动生成 embedding
- AI 信号成功写入 `ai_signals` 表
- 精确重复（hash_raw）被去重
- 语义重复（similarity >= 0.92）被去重
- 数据库故障时程序不崩溃

**性能目标：**
- 单条消息处理延迟 < 500ms（含 embedding 生成）
- Embedding API 调用成功率 > 95%

---

**Phase 2: 性能优化** 🔄 优先级：P1
- [ ] 添加内存哈希缓存（LRU 1000 条）
- [ ] Embedding 改为异步生成（后台队列）
- [ ] 完善 `source_message_id` 提取逻辑
- [ ] 添加 `source_url` 构建（Telegram 消息链接）
- [ ] 重试机制（指数退避）
- [ ] 本地队列降级方案
- [ ] 批量 embedding 生成（10 条/批次）

**验收标准：**
- 内存缓存命中率 > 80%
- 单条消息处理延迟 < 100ms（异步 embedding）
- 数据库暂时不可用时，消息写入本地队列

---

**Phase 3: 向量索引与高级去重** 🚀 优先级：P2
- [ ] 创建向量索引（ivfflat/hnsw）
- [ ] 语义去重阈值动态调优
- [ ] 相似消息聚类分析
- [ ] 跨时间窗口去重（7 天内）
- [ ] 定时任务：补齐历史数据 embedding

**验收标准：**
- 向量搜索响应时间 < 50ms
- 去重准确率 > 98%
- 误判率 < 1%

---

**Phase 4: 性能优化与监控** 📊 优先级：P3
- [ ] 批量写入优化（累积 10 条后批量提交）
- [ ] 数据库连接池优化
- [ ] 监控仪表板（Grafana/Supabase Dashboard）
- [ ] 慢查询分析与索引优化
- [ ] 数据保留策略（30 天后归档 `raw_response`）
- [ ] 备份与恢复流程

**验收标准：**
- 写入延迟 P95 < 50ms
- 数据库 CPU 使用率 < 60%
- 自动备份正常运行

---

**Phase 5: 高级功能** 🔬 优先级：P4
- [ ] 实现 `strategy_insights` 表人工标注
- [ ] 实现 `market_snapshots` 行情快照
- [ ] 信号回测分析（T+1h 收益率）
- [ ] 多租户支持（workspace_id）
- [ ] Row Level Security (RLS) 权限控制
- [ ] 数据导出到 S3/Data Lake

**验收标准：**
- 支持人工标注和复盘
- 信号准确率可追溯
- 数据可供 BI 分析

### 8.7 错误处理与降级策略

**分层错误处理：**

```python
async def _persist_event(self, ...):
    if not self.config.ENABLE_DB_PERSISTENCE:
        logger.debug("数据库持久化已禁用，跳过存储")
        return None

    try:
        # Level 1: 尝试直接写入
        news_event_id = await self._try_insert_event(...)

        if signal_result:
            await self._try_insert_signal(news_event_id, signal_result)

        logger.debug("数据持久化成功: news_event_id=%s", news_event_id)
        return news_event_id

    except DuplicateError as e:
        # 去重冲突：正常情况，不是错误
        logger.debug("消息已存在，跳过: hash=%s", e.hash_raw)
        return e.existing_id

    except NetworkError as e:
        # Level 2: 网络错误 → 重试
        logger.warning("网络错误，尝试重试: %s", e)
        return await self._retry_with_backoff(...)

    except SupabaseError as e:
        # Level 3: 数据库错误 → 降级到本地队列
        logger.error("Supabase 错误，降级到本地队列: %s", e)
        await self._write_to_local_queue(...)
        self.stats["db_fallback_count"] += 1
        return None

    except Exception as e:
        # Level 4: 未知错误 → 记录并继续
        logger.exception("数据持久化失败，但不影响主流程: %s", e)
        self.stats["db_error_count"] += 1
        return None
```

**降级策略矩阵：**

| 错误类型 | 处理策略 | 是否重试 | 是否降级 | 是否告警 |
|---------|---------|---------|---------|---------|
| 唯一索引冲突 | 跳过写入 | ❌ | ❌ | ❌ |
| 网络超时 | 指数退避重试 | ✅ (3次) | ✅ 本地队列 | ⚠️ |
| 503 Service Unavailable | 重试 | ✅ (3次) | ✅ 本地队列 | ⚠️ |
| 401/403 认证错误 | 立即失败 | ❌ | ✅ 禁用持久化 | 🚨 |
| 数据格式错误 | 记录日志 | ❌ | ❌ | 🚨 |
| 未知异常 | 捕获并继续 | ❌ | ✅ 记录到文件 | 🚨 |

**本地队列设计：**

```python
# 队列文件格式: data/db_queue/{timestamp}_{hash}.json
{
    "event_data": { ... },
    "signal_data": { ... },
    "retry_count": 0,
    "max_retries": 5,
    "created_at": "2025-10-02T10:30:00Z",
    "last_error": "Connection timeout"
}
```

**队列恢复任务：**
- 每 5 分钟扫描 `data/db_queue/` 目录
- 按时间顺序重试写入
- 成功后删除文件
- 超过 `max_retries` 的文件移至 `data/db_failed/`

**监控指标：**
- `db_success_rate`: 成功率（目标 > 99.9%）
- `db_avg_latency`: 平均延迟（目标 < 50ms）
- `db_fallback_count`: 降级次数（目标 < 10/天）
- `db_queue_size`: 队列积压（目标 < 100）

**健康检查：**
```python
async def health_check(self) -> bool:
    try:
        result = await supabase.from_("news_events").select("id").limit(1).execute()
        return result.data is not None
    except:
        return False
```

**熔断机制：**
```python
if self.stats["db_error_count"] > 50:  # 1 分钟内 50 次错误
    logger.critical("数据库连续失败，启动熔断，禁用持久化 5 分钟")
    self.config.ENABLE_DB_PERSISTENCE = False
    await asyncio.sleep(300)  # 5 分钟后恢复
    self.config.ENABLE_DB_PERSISTENCE = True
```

## 9. 参考视图

为常见查询准备视图示例：

```sql
create view v_signal_feed as
select
  s.id as signal_id,
  e.published_at,
  e.source,
  e.content_text,
  e.translated_text,
  s.summary_cn,
  s.event_type,
  s.assets,
  s.action,
  s.direction,
  s.confidence,
  s.risk_flags,
  s.links
from ai_signals s
join news_events e on e.id = s.news_event_id
where s.should_alert = true
order by e.published_at desc;
```

```sql
create materialized view mv_signal_perf_1h as
select
  s.id,
  s.assets,
  s.action,
  s.confidence,
  snap.price as price_t0,
  snap1h.price as price_t1h,
  snap1h.price - snap.price as delta_abs,
  (snap1h.price / nullif(snap.price, 0) - 1) as delta_pct
from ai_signals s
join market_snapshots snap on snap.asset = split_part(s.assets, ',', 1) and snap.captured_at = s.created_at
left join market_snapshots snap1h on snap1h.asset = snap.asset and snap1h.captured_at = s.created_at + interval '1 hour';
```

> materialized view 可异步刷新，供日报或 BI 使用。

## 10. 后续扩展

- 若需全文搜索，建议在 `news_events.content_text` 上启用 `pg_search` 或接入 OpenSearch。
- 如需多租户隔离，可在各表增加 `workspace_id`，并为视图加 `row level security`。
- 可以对 `ai_signals.raw_response` 及 `news_events.metadata` 定期落地到 S3 + Iceberg，为后续大数据分析做准备。
