# 深度分析引擎一体化策略

## 概述
- 构建统一的深度分析引擎抽象，支持 Gemini LangGraph 流程、Codex CLI、Claude CLI 等多种实现并行存在。
- 通过 LangGraph + 多工具编排提升证据覆盖度，引入价格、搜索、宏观、协议、链上等工具组合，实现自动化验证。
- 统一配置、监控与测试基线，确保不同引擎之间可平滑切换、可回退、可观测。

## 目标
- 抽象出 `BaseDeepAnalysisEngine` 接口，屏蔽不同模型与工具实现差异。
- 支持成本、质量、延迟的灵活权衡：根据配置切换 Gemini、Claude、Codex 等提供商。
- 在不破坏现有业务的前提下扩展工具链，并保持深度分析输出 Schema 稳定。
- 为记忆系统、多引擎策略与未来自动路由奠定基础。

## 架构与流程
- **现状**：
  ```
  GeminiDeepAnalysisEngine
    ├─ Context Gather
    ├─ Tool Planner (Function Calling)
    ├─ Tool Executor (手动调用工具)
    └─ Synthesis (结果汇总)
  ```
- **目标**：
  ```
  BaseDeepAnalysisEngine (抽象接口)
    ├─ GeminiEngine (LangGraph 多节点流程)
    ├─ CodexCliEngine (单次 Agent 调用)
    └─ ClaudeCliEngine (CLI + Memory Tool)
  ```
- **LangGraph 节点设计**：
  - `ContextGather`：并行查询本地与 Supabase 记忆，合并排序后填充 `memory_evidence`。
  - `ToolPlanner`：Gemini Flash 根据证据状态返回 JSON（不使用 Function Calling），最多循环 3 轮。
  - `ToolExecutor`：并行调用价格、协议、搜索、宏观、链上等工具，填充对应证据槽位。
  - `Synthesis`：汇总证据生成最终 JSON，记录置信度调整原因并输出降级标记。

## 实施步骤
- **Phase 0 预备**：确认 Gemini/Claude 配额、准备记忆后端凭据、完成日志/指标管道。
- **Phase 1 基础设施**：
  - 实现引擎基类、工厂、配置读取 (`get_deep_analysis_config`)。
  - 构建 Gemini Function 客户端与记忆后端工厂，统一日志与超时策略。
- **Phase 2 引擎适配**：
  - 接入 Claude、Gemini、Codex，实现统一 Prompt、输出 Schema、重试与 fallback。
  - 共用记忆 handler，保持结果格式一致。
- **Phase 3 集成回归**：
  - 在 `AiSignalEngine` 中启用新抽象，编写端到端测试，对比关键指标（成功率、延迟、token）。
  - 24 小时影子运行验证稳定性。
- **Phase 4 发布与文档**：
  - 更新操作手册、迁移指南、回滚策略，输出成本/性能对比。
- **工具编排实现要点**：
  - Planner Prompt 明确决策规则（价格偏离→price，叙事事件→search，宏观→macro 等）。
  - Executor 按工具类型划分模块，返回结构化 JSON 并设置 `triggered`、`confidence`、阈值字段。
  - 所有工具支持缓存与异常降级，失败时写入 `data_incomplete` 并反馈 Planner。

## 配置
- 主开关：`DEEP_ANALYSIS_ENABLED` 控制整体启用；`DEEP_ANALYSIS_PROVIDER` 指定当前引擎。
- 兼容旧变量：同时存在旧变量时以新变量优先，并在日志中提示迁移。
- 推荐组合：
  - 成本优先：`DEEP_ANALYSIS_PROVIDER=gemini`，`GEMINI_DEEP_MODEL=gemini-2.5-pro`。
  - 质量优先：`DEEP_ANALYSIS_PROVIDER=claude`，`CLAUDE_MODEL=claude-sonnet-4.5`。
  - 国内合规：`DEEP_ANALYSIS_PROVIDER=minimax`，`MINIMAX_BASE_URL=https://api.minimax.chat/v1`，并设置 `MINIMAX_API_KEY`/`MINIMAX_MODEL`。
  - 混合：配置备用引擎 `DEEP_ANALYSIS_FALLBACK_PROVIDER`。
- 工具阈值：
  - 价格：`PRICE_TRIGGER_PCT`、`PRICE_LIQUIDATION_MULTIPLIER`。
  - 协议：`PROTOCOL_TVL_DROP_THRESHOLD_PCT`、`PROTOCOL_TVL_DROP_THRESHOLD_USD`。
  - 链上：`ONCHAIN_TVL_DROP_THRESHOLD`、`ONCHAIN_REDEMPTION_USD_THRESHOLD`。
  - 宏观：`MACRO_CACHE_TTL_SECONDS`、`MACRO_EXPECTATIONS_JSON`。
  - 搜索：`SEARCH_PROVIDER`、`SEARCH_MAX_RESULTS`、`TAVILY_API_KEY`。

## 验证与测试
- 单元测试覆盖：配置解析、记忆合并、工具输出解析、上下文裁剪。
- 集成测试：分别对 Gemini、Claude、Codex 引擎执行真实案例，校验 JSON Schema。
- 性能测试：检测长上下文与高并发场景是否超时或循环过多。
- 监控指标：引擎类型、调用次数、成功率、平均延迟、token 消耗、记忆读写次数、Function 回合数。

## 里程碑与状态
- 深度分析抽象层完成需求评估与目标设计，Phase 1-2 方案与配置策略已定稿。
- 工具节点规划与 JSON 输出格式已标准化，等待在 LangGraph 子图中全面落地。

## 风险与后续
- Function Calling 不稳定：限制回合数、收集细粒度日志，必要时自动回退到 Claude。
- 输出不一致：统一解析器 + JSON Schema 校验，部署前做对比测试。
- 记忆差异：通过统一的记忆 handler 与合并策略缓解，后续监控记忆读写质量。
- 上下文激增：引入消息裁剪、摘要、超时控制；监控 token 消耗。
- 下一步：扩展自动路由策略、构建健康监控仪表盘、优化记忆后端的缓存与批处理。
