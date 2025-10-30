## 检索增强方案（RA）：Supabase 向量 → Supabase 关键词 → 本地关键词

参考：`docs/memory_system_overview.md`（记忆系统总览与多后端策略）。本方案为其执行细节与调用约定。

### 目标（第一性原则）
- 不改现有输出 schema；仅通过 `additional_context/state` 注入证据给模型参考。
- 统一“外部记忆/检索”协作层，避免分散调用与重复代码。
- 按成本/效果优先级执行：向量检索优先，其次 Supabase 关键词，最后本地关键词兜底。

### 总体流程（两层 AI 协同）
- 第一层（Signal/快速分析）：
  - 产出初判（event_type、asset、summary、confidence）。
  - 派生少量高置信关键词 `keywords` 与标准化资产码 `asset_codes`（不改 schema，作为内部变量）。
  - 满足阈值/白名单类型后触发二层。
- 第二层（Deep/深度分析）：
  - 调用“检索协调器”按优先级获取证据：Vector → Supabase Keyword → Local Keyword。
  - 仅在 `additional_context.memory_evidence` 注入检索结果：
    - `supabase_vector?: list[dict]`
    - `supabase_keyword?: list[dict]`
    - `local_keyword?: list[dict]`
  - 系统提示仅加一行：如提供 `memory_evidence`，仅在其能增强结论时引用；为空则忽略。

### 检索协调器（实现约定）
- 输入：
  - `embedding_1536: list[float] | None`
  - `keywords: list[str]`（来自快速分析与/或模型推断）
  - `asset_codes: list[str] | None`
  - `time_window_hours: int = 72`
  - `match_threshold: float = 0.85`，`min_confidence: float = 0.6`
- 环境开关：仅当同时存在 `SUPABASE_URL` 与 `SUPABASE_SERVICE_KEY` 时启用 Supabase 步骤。
- 执行顺序：
  1) 向量检索（Supabase）：调用 `search_memory_events(query_embedding, match_threshold, match_count, asset_filter, min_confidence, time_window_hours)`；命中写入 `supabase_vector`。
  2) 关键词检索（Supabase）：当向量无结果且 `keywords` 非空；简版可用 ILIKE + 时间窗口，再在 Python 侧过滤；命中写入 `supabase_keyword`。
  3) 本地关键词检索：始终可用，写入 `local_keyword` 作为最终兜底。
- 输出：`memory_evidence: dict`，按上述三个槽位返回，不做强制合并或排序，交由模型自行取舍。

### 模型提示最小增强（只加一行）
- 在 `src/ai/deep_analysis/base.py` 系统提示协同段追加：
  - “如提供 memory_evidence（优先 Supabase，无则本地），请仅在其能增强结论时引用；若为空则忽略。”

### 与现有模块的衔接
- Supabase 客户端：`src/db/supabase_client.py`（已提供 `rpc/select` 能力）。
- 检索函数：在 `src/db/repositories.py` 中新增：
  - `search_memory_events_by_embedding(...)` → 调用 SQL 函数 `search_memory_events`。
  - `search_memory_events_by_keywords(...)` → 首版 ILIKE 简化实现（后续可升级 TSV/视图）。
- 调用时机：
  - Gemini 深度分析传统分支 `_analyse_with_function_calling` 或 LangGraph 节点（ContextGather/Planner 前）注入 `memory_evidence`。

### 与记忆系统对齐（来自 memory_system_overview）
- 后端模式：`MEMORY_BACKEND=hybrid|supabase|local`（RA 在 hybrid/supabase 下启用 Supabase 步骤；local 下仅执行本地关键词）。
- 阈值/窗口沿用记忆系统默认：
  - `MEMORY_SIMILARITY_THRESHOLD≈0.40`（用于记忆聚合）与 RA 的 `match_threshold=0.85`（用于可引用证据）可分离配置。
  - `MEMORY_LOOKBACK_HOURS=168`；RA 默认为 `time_window_hours=72`，建议保持更短的“可引用”窗口。
- Claude Memory Tool：属于本地记忆写入/整理能力，与 RA 并列存在；RA 不改变其启停，只负责“读侧检索优先级”。

### 接口契约（最小化，供实现调用）
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
) -> dict:  # {"supabase_vector"?: [...], "supabase_keyword"?: [...], "local_keyword"?: [...]} 
    ...
```
- 仅当 `SUPABASE_URL/SUPABASE_SERVICE_KEY` 存在时尝试 Supabase；否则直接本地。
- 不抛出异常；网络/服务错误内部吞并并降级。

### 实现落点（建议）
- `src/db/repositories.py`：新增上述检索函数。
- `src/ai/deep_analysis/gemini.py` 或 LangGraph：在 ContextGather 前调用 `fetch_memory_evidence`，将返回值注入 `additional_context.memory_evidence` 或 `state.memory_evidence`。
- `src/ai/deep_analysis/base.py`：系统提示追加一行关于 `memory_evidence` 的使用准则（已在本方案描述）。

### 日志与降级
- 命中/空结果均打印简明日志：
  - “Supabase 向量检索命中 k 条（阈值=0.85, assets=[...], keywords=[...]）”
  - “Supabase 返回空结果，降级到本地检索 (dims=1536, assets=[], keywords=[...])”
- 网络/超时不阻断主流程；仅影响该层证据，保持分析可用性。

### 配置与默认值
- `.env`：`SUPABASE_URL`、`SUPABASE_SERVICE_KEY`；可选：
  - `MEMORY_MATCH_THRESHOLD=0.85`
  - `MEMORY_TIME_WINDOW_HOURS=72`
  - `MEMORY_MATCH_COUNT=5`
- 未配置时使用文档默认值；Supabase 变量缺失即跳过 Supabase 步骤。

### 简版实施要点（AI 自主搜索协调）
- 固定 RA 链路：向量 → Supabase 关键词 → 本地关键词，统一透出到 `memory_evidence`。
- 仅当 `memory_evidence` 为空或信心不足且事件需“最新外部资讯”时，才开放网络搜索工具给模型自选。
- 可选引入 `choose_search_mode` 动作：读取 `memory_evidence` 后再决定触发 `search_supabase_keywords`（若已跑过则跳过）或 `search_web`。
- Supabase 关键词步骤由环境控制：缺 `SUPABASE_URL/SUPABASE_SERVICE_KEY` 则跳过；外部搜索强调成本与必要性。
- 先接入命中率与降级日志，评估阈值后再扩展外部搜索触发条件。

### Supabase 关键词检索（极简方案）
- 目标：补齐向量对长尾/新资产的盲区；仅在向量无命中后尝试；不改输出 schema。
- 接口（供工具调用，签名即可）：
  ```python
  async def supabase_keyword_search(*, url: str, service_key: str,
                                    keywords: list[str],
                                    time_window_hours: int = 72,
                                    match_count: int = 10,
                                    asset_filter: list[str] | None = None) -> list[dict]
  ```
- 行为：
  - 关键词预处理（去重、小写、取前≤5）；拉取近窗口的最近 N×3 条，Python 侧 any(kw in text) 过滤，取前 match_count。
  - 命中写入 `memory_evidence.supabase_keyword`；为空则降级本地关键词检索。
- 开关：仅当存在 `SUPABASE_URL` 与 `SUPABASE_SERVICE_KEY` 时启用；失败/空返回 `[]`，不抛异常。

### Supabase 关键词检索 RPC（SQL，参考现有 embedding RPC）
```sql
-- 关键词检索（极简 ILIKE 版）：向量无命中后作为第二层保障
create or replace function search_memory_events_keywords(
  query_keywords text[],
  time_window_hours int default 72,
  match_count int default 10,
  min_confidence float default 0.0,
  asset_filter text[] default null
)
returns table (
  news_event_id bigint,
  created_at timestamptz,
  content_text text,
  translated_text text,
  match_score double precision
)
language sql
as $$
  with base as (
    select
      ne.id as news_event_id,
      ne.created_at,
      ne.content_text,
      ne.translated_text,
      -- 简单打分：命中关键词数量 / 关键词总数（可后续升级为 TSV）
      (
        select count(*)::double precision
        from unnest(query_keywords) kw
        where lower(coalesce(ne.translated_text, ne.content_text, '')) like '%' || lower(kw) || '%'
      ) / greatest(cardinality(query_keywords), 1) as kw_score
    from news_events ne
    left join ai_signals ais on ais.news_event_id = ne.id
    where ne.created_at >= now() - (time_window_hours || ' hours')::interval
      and (ais.confidence is null or ais.confidence >= min_confidence)
      and (
        asset_filter is null
        or (
          ais.assets is not null
          and regexp_split_to_array(
                regexp_replace(coalesce(nullif(ais.assets, ''), 'NONE'), '\\s+', '', 'g'),
                ','
              ) && asset_filter
        )
      )
  )
  select
    b.news_event_id,
    b.created_at,
    b.content_text,
    b.translated_text,
    b.kw_score as match_score
  from base b
  where b.kw_score > 0
  order by b.match_score desc, b.created_at desc
  limit match_count;
$$;
```

### 复杂场景的扩展位
- 本地 Claude 记忆（Memory Tool）：与 RA 并列存在，作为模型的工具链之一；不改变 RA 优先级链路。
- 搜索工具 API：属于“信息扩展”，可在 Planner 判定缺口后再调用；与 RA 互不阻塞。

### 验收标准（首版）
- 当 Supabase 可用时：遵循 Vector → Supabase Keyword → Local Keyword 的优先级；
- 当 Supabase 不可用或空结果时：自动降级本地；
- 输出 schema 不变，证据仅存在于 `additional_context.memory_evidence`；
- 日志可读，易于定位链路与降级路径。


