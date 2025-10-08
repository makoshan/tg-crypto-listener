# LangGraph 改造方案

## 目标与收益

- 以 LangGraph 对监听 → 过滤 → AI 评估 → 深度分析 → 记忆回写 → 转发 → 持久化 的流程做显式状态建模，降低隐式协程逻辑带来的复杂度。
- 提升可观察性：借助 LangGraph 可视化/检查点，快速定位重复写入、回退或错误重试的路径。
- 改善容错与节流：在图节点层面统一限流、幂等标记、重试策略，避免重复消息三次落库等问题。
- 为后续接入多模型、多翻译供应商和新的工具链（例如 LangChain Runnable）提供标准化接口。

## 现有架构概览

- **数据入口**：`listener.TelegramListener` 使用 Telethon 监听多个频道，触发 `_handle_new_message` 协程。
- **预处理**：关键词匹配 (`contains_keywords`)、`MessageDeduplicator` 内存窗口去重、数据库哈希/向量去重。
- **AI 管道**：`AiSignalEngine` 调用 Gemini（及备选 Claude 深度分析）、翻译器、记忆检索 (`HybridMemoryRepository` 等)。
- **动作执行**：`MessageForwarder` 负责推送目标频道，Supabase 仓储 (`NewsEventRepository`, `AiSignalRepository`) 负责持久化。
- **辅助特性**：限流 (`FORWARD_COOLDOWN_SECONDS`)、Stats Reporter、资源清理等。

痛点：流程隐含在单个 `_handle_new_message` 协程内，逻辑分支、重试和去重散落在工具函数中，难以定位重复入库原因，也不易扩展其他处理步骤。

## LangGraph 设计蓝图

### 状态模型

- `RunState`（单条消息执行上下文）：
  - `raw_event`: Telegram 原始消息、来源、时间戳、链接。
  - `content`: 文本、翻译、关键词命中。
  - `hashes`: 原文哈希、规范化哈希。
  - `embedding`: 可选向量。
  - `dedup_flags`: 内存去重命中、哈希命中、向量命中等布尔值。
  - `ai_signal`: `SignalResult`。
  - `memory_context`: 检索结果。
  - `routing`: 是否触发深度分析、是否需要转发、是否需备份通道。
  - `errors`: 节点异常与重试计数。

状态保存在 LangGraph `StateGraph` 的 `TypedState` 或 `pydantic` 模型内，支持在节点间增量更新。

### 节点划分

1. **Ingest**：接收 Telethon 事件，组装 `raw_event`，进入图执行。失败时记录并终止。
2. **KeywordFilter**：调用现有 `contains_keywords`。未命中直接结束并写入统计节点。
3. **DedupFanout**：
   - 内存去重节点：`MessageDeduplicator.is_duplicate`。
   - 哈希去重节点：访问 `NewsEventRepository.check_duplicate`（带超时/重试）。
   - 向量去重节点：条件地调用 `compute_embedding` 与 `check_duplicate_by_embedding`。
   - 将结果写入 `dedup_flags`，若任何一项命中则走终止分支。
4. **Translation**（条件节点）：若开启翻译，使用 `Translator`，写入 `content.translation`。失败写入 `errors`。
5. **KeywordCollector**：在翻译完成后根据原文+译文生成关键词命中列表。
6. **MemoryFetch**：依据配置选择本地、Supabase 或 Hybrid 的 repo，存入 `memory_context`。
7. **AiSignal**：调用 `AiSignalEngine.generate_signal`，填入 `ai_signal`。
8. **DeepAnalysisRouter**（LangGraph 条件分支）：
   - 判断 `ai_signal.is_high_value_signal` 与冷却间隔。
   - 若需要，进入 `DeepAnalysis` 节点（调用 Gemini/Claude）并将补充内容合并。
9. **ForwardDecision**：综合 `ai_signal`、翻译配置、冷却状态，决定是否调用 `MessageForwarder`。记录发送结果和错误。
10. **Persistence**：写 Supabase（`NewsEventRepository.create_event`、`AiSignalRepository.store_signal` 等），并将记忆回写步骤抽成独立节点，便于失败时局部重试。
11. **Stats & Cleanup**：统一更新运行统计，将最终状态写入日志，释放资源。

以上节点通过 LangGraph 的有向边串联，并使用 `ConditionalEdge` 实现针对错误、重复、低置信度的不同路径。

### 服务与工具集成

- **模型/工具适配**：将现有 Gemini、Claude、翻译器封装为 LangChain Runnable 或 Tool，再在 LangGraph 节点内调用，保持重用。
- **记忆系统**：为 Supabase/Local/Hybrid 提供 `MemoryTool`，节点内部基于配置自动选择。
- **去重**：哈希与向量去重节点要使用统一的 `DedupResult` 数据结构，LangGraph 中可通过 `map_state` 传递。
- **资源管理**：LangGraph 支持在图运行器中注入共享依赖（例如数据库客户端、Forwarder 实例），避免重复初始化。

### 错误处理与重试策略

- 节点级装饰器：使用 LangGraph 的 `Retry` 控制或自定义中间件，实现指数退避和最大重试次数。
- 幂等性保证：在 Persistence 和 Forwarder 节点写入幂等 Key（原始哈希 + 来源 ID），防止重复执行。
- 失败分支：当关键节点失败（翻译、AI、持久化）时，记录错误并走“降级”路径，例如跳过翻译重新尝试 AI、或只存入错误状态。

### 并发与节流

- 在运行器层面利用 LangGraph 的并发控制，对每条消息独立执行；ForwardDecision 节点前新增 `CooldownGuard` 子节点，统一处理全局 `FORWARD_COOLDOWN_SECONDS`。
- 利用 `Semaphore` 或 Graph-level guard（可与 LangGraph 的 `add_conditional_edges` 配合）限制深度分析并发次数，替换现有 `AI_MAX_CONCURRENCY` 手写队列。

### 监控与可视化

- 启用 LangGraph 的图运行追踪，持久化每次执行的节点日志与状态 diff。
- 在 `Stats & Cleanup` 节点汇总关键信息（去重命中率、转发成功率、AI 失败数），输出到现有 logger。
- 结合 Supabase 新表或现有统计表记录图运行 ID、执行耗时和错误栈，方便排查重复入库。

## 迁移路线图

### 阶段 0：可行性验证

1. 抽取 `_handle_new_message` 逻辑为独立的 `MessagePipeline` 类，明确输入输出。
2. 使用 LangChain Runnable 封装 Gemini、翻译、记忆调用，保证调用方式统一。
3. 在开发环境跑端到端流程，验证现有工具封装是否稳定。

### 阶段 1：POC 图（影子模式）

1. 基于上文节点定义构建最小图：Ingest → Keyword → Dedup → AI → Persistence（跳过转发）。
2. Telethon 事件同时走旧路径和 LangGraph 影子路径，仅记录结果不执行转发。
3. 对比去重命中、持久化记录，确认重复写入问题是否消失。

### 阶段 2：扩展节点与替换

1. 补齐翻译、记忆、深度分析、转发节点，并实现冷却/并发控制。
2. 引入 LangGraph 中央运行器，统一管理 TelegramListener 初始化资源。
3. 在配置中增加 `USE_LANGGRAPH_PIPELINE` 开关，用于切换新旧路径。

### 阶段 3：正式切换与优化

1. 在生产环境开启 LangGraph 管道，旧逻辑保留作为回退选项。
2. 调整节点重试、错误告警阈值，利用图追踪面板监控运行。
3. 针对 Supabase 去重、向量写入等关键节点编写单元/集成测试。
4. 迭代图结构：例如将多个翻译供应商做成并行节点，根据配额动态选择。

## 风险与对策

- **外部服务依赖**：当 Supabase 或翻译 API 不稳定时，LangGraph 的重试可能放大压力。需在节点中设置熔断/降级策略。
- **状态一致性**：Graph 节点持有的状态若过大，可能导致内存占用上升。建议对嵌入向量等大对象做延迟加载或按需缓存。
- **团队学习成本**：LangGraph 使用事件图思维，需要补充团队内部的最佳实践文档。
- **兼容性**：确保 Telethon 的生命周期与 LangGraph runner 协调，避免重复注册事件或阻塞主线程。

## 验证清单

- 单元测试：针对 dedup、AI、转发节点分别编写测试，验证状态输入输出。
- 集成测试：构造模拟 Telegram 消息流，确保图执行路径符合预期。
- 回归指标：重复入库率、AI 成功率、转发成功率需与旧版本对比至少持平。
- 灰度监控：上线初期在日志中打印 Graph run_id、节点耗时、重试次数，确保可快速定位问题。

## 后续扩展

- 借助 LangGraph 的 Tool Router 实现按模型能力自动路由（如加上 OpenAI、Local LLM）。
- 将记忆检索与更新纳入 Graph 的循环节点，实现“事件 → 记忆追加 → 下次检索”的闭环监控。
- 结合 LangSmith 或自建追踪面板，做端到端性能分析和质量评估。

> 本方案聚焦于渐进迁移。推荐先完成阶段 1 影子模式，在验证重复写入问题缓解后，再逐步替换全部实时路径。
