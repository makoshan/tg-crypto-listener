## 检索增强方案（RA）：Supabase 向量 → Supabase 关键词 → 本地关键词

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

### 复杂场景的扩展位
- 本地 Claude 记忆（Memory Tool）：与 RA 并列存在，作为模型的工具链之一；不改变 RA 优先级链路。
- 搜索工具 API：属于“信息扩展”，可在 Planner 判定缺口后再调用；与 RA 互不阻塞。

### 验收标准（首版）
- 当 Supabase 可用时：遵循 Vector → Supabase Keyword → Local Keyword 的优先级；
- 当 Supabase 不可用或空结果时：自动降级本地；
- 输出 schema 不变，证据仅存在于 `additional_context.memory_evidence`；
- 日志可读，易于定位链路与降级路径。


