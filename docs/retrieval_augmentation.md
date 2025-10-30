## 检索增强（RA）单一方案：Supabase 统一检索 → 本地关键词兜底

参考：`docs/memory_system_overview.md`。本文件仅保留一个可执行方案，删去冗余背景与多方案讨论。

### 结论（唯一方案）
- 采用“单函数 RPC + 视图标准化 + 轻量路由”。
- 主链路：一次调用 `search_memory`（向量优先，命中不足自动降级 TSV 关键词）→ 若结果为空或异常，再执行本地关键词兜底。

### 目标
- 不改现有输出 schema，仅通过 `additional_context.memory_evidence` 注入证据。
- 统一外部记忆/检索调用，减少重复代码与复杂路由。
- 成本优先：优先 Supabase 统一检索，失败/空再本地关键词。

### 流程（两层协同）
- 第一层 Signal 产出少量高置信 `keywords` 与 `asset_codes`。
- 第二层 Deep 调用“检索协调器”执行：Supabase `search_memory` → 空/异常则本地关键词；仅将结果注入 `additional_context.memory_evidence`。

### 检索协调器约定
- 输入：`embedding_1536 | None`，`keywords: list[str]`，`asset_codes | None`，`time_window_hours=72`，`match_threshold=0.85`，`min_confidence=0.6`。
- 开关：仅当 `SUPABASE_URL` 和 `SUPABASE_SERVICE_KEY` 同时存在时启用 Supabase 步骤。
- 输出：`{"supabase_hits"?: [...], "local_keyword"?: [...], "notes"?: str}`；不抛异常，内部降级。

### 接口契约
```python
async def fetch_memory_evidence(
    *,
    config,
    embedding_1536: list[float] | None,
    keywords: list[str] | None,
    asset_codes: list[str] | None,
    match_threshold: float = 0.85,
    min_confidence: float = 0.6,
    time_window_hours: int = 72,
    match_count: int = 5,
) -> dict:  # {"supabase_hits"?: [...], "local_keyword"?: [...], "notes"?: str}
    ...
```
- system prompt（`src/ai/deep_analysis/base.py`）仅加一行：如提供 `memory_evidence`，仅在其能增强结论时引用；为空则忽略。

### 实施落点
- `src/db/repositories.py`：增加 `search_memory(...)` 封装，仅调用单一 RPC 并解析命中类型。
- 深度分析入口（`src/ai/deep_analysis/gemini.py` 或 LangGraph ContextGather）：调用 `fetch_memory_evidence(...)` 并注入到 `additional_context`。
- 日志：输出“Supabase 命中类型（vector/keyword）与数量 → 是否降级本地”。

### 配置与默认值
- `.env`：`SUPABASE_URL`、`SUPABASE_SERVICE_KEY`
- 可选：`MEMORY_MATCH_THRESHOLD=0.85`、`MEMORY_TIME_WINDOW_HOURS=72`、`MEMORY_MATCH_COUNT=5`

### Supabase 统一检索 RPC（向量 + TSV 一体化）
- 函数语义：`search_memory(query_embedding?, query_keywords?, match_threshold, match_count, min_confidence, time_window_hours, asset_filter?)`
- 建议结合视图 `v_news_events` / `v_ai_signals` 以标准化列名。

```sql
-- 需要先启用 pgvector 扩展：
-- create extension if not exists vector;
-- 余弦相似度写法：similarity = 1 - (embedding <=> query_embedding)
-- 单函数：向量优先，自动降级 TSV 关键词
create or replace function search_memory(
  query_embedding vector(1536) default null,
  query_keywords text[] default null,
  match_threshold double precision default 0.85,
  match_count integer default 10,
  min_confidence double precision default 0.6,
  time_window_hours integer default 72,
  asset_filter text[] default null
)
returns table (
  match_type text,               -- 'vector' 或 'keyword'
  news_event_id bigint,
  created_at timestamptz,
  content_text text,
  translated_text text,
  similarity double precision,
  keyword_score double precision,
  combined_score double precision
)
language sql
as $$
  with
  norm_kw as (
    select array_agg(distinct lower(trim(k))) as kws
    from unnest(coalesce(query_keywords, array[]::text[])) as k
    where coalesce(trim(k), '') <> ''
  ),
  kw_available as (
    select coalesce(array_length(kws, 1), 0) > 0 as has_kw from norm_kw
  ),
  kw_ts as (
    select
      string_agg(plainto_tsquery('simple', k)::text, ' | ')::tsquery as q
    from unnest((select kws from norm_kw)) as k
  ),
  ts_doc as (
    select
      *,
      setweight(to_tsvector('simple', coalesce(translated_text, '')), 'A') ||
      setweight(to_tsvector('simple', coalesce(content_text, '')), 'B') as docvec
    from v_news_events
  ),
  base as (
    select
      ne.id as news_event_id,
      ne.created_at,
      ne.content_text,
      ne.translated_text,
      ne.embedding,
      ais.confidence,
      ne.docvec,
      regexp_split_to_array(
        regexp_replace(coalesce(nullif(ais.assets, ''), 'NONE'), '\\s+', '', 'g'),
        ','
      )::text[] as asset_arr
    from ts_doc ne
    left join v_ai_signals ais on ais.news_event_id = ne.id
    where ne.created_at >= now() - (time_window_hours || ' hours')::interval
      and (ais.confidence is null or ais.confidence >= min_confidence)
  ),
  vector_hits as (
    select
      'vector'::text as match_type,
      b.news_event_id,
      b.created_at,
      b.content_text,
      b.translated_text,
      (1 - (b.embedding <=> query_embedding))::double precision as similarity,
      null::double precision as keyword_score,
      (1 - (b.embedding <=> query_embedding))::double precision as combined_score
    from base b
    where query_embedding is not null
      and (1 - (b.embedding <=> query_embedding)) >= match_threshold
      and (asset_filter is null or (b.asset_arr is not null and b.asset_arr && asset_filter))
    order by similarity desc, created_at desc
    limit match_count
  ),
  keyword_hits as (
    select
      'keyword'::text as match_type,
      b.news_event_id,
      b.created_at,
      b.content_text,
      b.translated_text,
      null::double precision as similarity,
      ts_rank(b.docvec, (select q from kw_ts), 1) as keyword_score,
      (
        0.6 * ts_rank(b.docvec, (select q from kw_ts), 1)
        + 0.3 * exp(- greatest(extract(epoch from (now() - b.created_at)) / 3600.0, 0) / 48.0)
        + 0.1 * coalesce(b.confidence, 0.0)
      )::double precision as combined_score
    from base b
    where (select has_kw from kw_available)
      and (select q from kw_ts) @@ b.docvec
      and (asset_filter is null or (b.asset_arr is not null and b.asset_arr && asset_filter))
    order by combined_score desc, created_at desc
    limit match_count
  )
  select * from vector_hits
  union all
  select * from keyword_hits
  order by combined_score desc nulls last, created_at desc
  limit match_count;
$$;
```

提示：如主要语种为英文，可将 `simple` 替换为 `english`；需要前缀匹配时将 `plainto_tsquery` 改为 `to_tsquery` 并拼 `':*'`。

### 验收标准
- Supabase 可用：`search_memory` 完成向量优先+关键词降级；空结果才触发本地关键词。
- Supabase 不可用/异常：自动降级本地；不阻断主流程。
- 仅在 `additional_context.memory_evidence` 注入结果（`supabase_hits`/`local_keyword`），日志清晰可读。
