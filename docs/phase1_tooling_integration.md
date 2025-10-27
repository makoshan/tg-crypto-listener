# Phase 1 深度分析工具集成

## 概述
- Phase 1 聚焦在 LangGraph 深度分析子图中接入 Tavily 搜索工具，完成从记忆收集、工具规划、执行到证据综合的闭环。
- 方案在不影响现有 Function Calling 流程的前提下引入多工具框架，并预留后续扩展价格、宏观、链上等能力的接口。
- 当前阶段的主要成果包括节点实现指引、配置样板、成本控制策略以及测试与监控基线。

## 目标
- 使用 LangGraph 将深度分析拆分为 Context Gather、Tool Planner、Tool Executor、Synthesis 四个节点，提升可维护性。
- 实现搜索工具初版，为高价值事件提供多源验证与关键词生成。
- 建立可观测性、配额控制与白名单/黑名单策略，确保上线安全。
- 制定模块化拆分计划，将 800+ 行的 `gemini.py` 拆解为可复用组件。

## 架构与流程
- **LangGraph 节点**：
  - `ContextGather`：并行拉取本地与 Supabase 记忆，合并后填充 `memory_evidence`。
  - `ToolPlanner`：采用 Function Calling（或 JSON 文本）返回 `{tools, search_keywords, reason}`，并应用事件白/黑名单。
  - `ToolExecutor`：根据规划调用搜索工具，配置域名白名单，并支持硬编码降级方案。
  - `Synthesis`：综合证据，根据多源、官方确认等信号调整置信度（如 multi_source +0.15，official +0.10）。
  - `Router`：控制最大迭代次数（默认 3 轮），若工具返回空或达到上限则进入综合阶段。
- **代码结构**（目标）：
  ```
  src/ai/deep_analysis/
    gemini.py                  # 入口 & analyse()
    gemini_langgraph_nodes.py  # 节点实现与路由
    helpers/memory.py          # 记忆检索封装
    helpers/prompt_builder.py  # Prompt 构建
    helpers/tool_executor.py   # 工具执行 & 配额
  ```
- **搜索工具**（Phase 1 范围）：
  - `src/ai/tools/search/fetcher.py` → `SearchTool.fetch()`
  - 支持 Tavily、Google，自带缓存、关键词与域名过滤。
  - 返回 `multi_source`、`official_confirmed`、`sentiment`、`triggered` 等字段，为 Synthesis 提供量化依据。

## 实施步骤
- **节点实现优先级**：
  1. `_fetch_memory_entries`：封装记忆检索，供 Context Gather 与 Function Calling 共用。
  2. `_node_tool_planner`：构造决策 Prompt，解析 Function Calling 响应，生成搜索关键词。
  3. `_node_tool_executor`：执行搜索工具，按事件类型设置域名白名单，记录触发情况。
  4. `_node_synthesis`：根据证据调整置信度，输出结构化结果与 `data_incomplete` 标记。
  5. `_build_deep_graph` & 路由器：构建 LangGraph，控制循环次数、降级逻辑。
  6. `analyse()`：增加 `DEEP_ANALYSIS_TOOLS_ENABLED` 判断，按需回退旧流程。
- **成本控制与策略**：
  - `FORCE_SEARCH_EVENT_TYPES`、`NEVER_SEARCH_EVENT_TYPES` 控制必搜/禁搜场景。
  - 每日配额检查 `_check_tool_quota()`，结合 `DEEP_ANALYSIS_TOOL_DAILY_LIMIT`。
  - `PHASE1_ROLLOUT_PERCENTAGE` 控制灰度流量，默认 5%。
- **模块化拆分建议**：
  - 将 1,200+ 行的 `gemini.py` 分离为节点文件与 Helper，提升可测试性。
  - 为每个节点编写独立单测，Mock 外部服务。

## 配置
- `.env` 模板：
  ```bash
  DEEP_ANALYSIS_TOOLS_ENABLED=false
  DEEP_ANALYSIS_MAX_TOOL_CALLS=3
  DEEP_ANALYSIS_TOOL_TIMEOUT=10
  DEEP_ANALYSIS_TOOL_DAILY_LIMIT=50

  TOOL_SEARCH_ENABLED=true
  DEEP_ANALYSIS_SEARCH_PROVIDER=tavily
  TAVILY_API_KEY=tvly-xxxxx
  SEARCH_MAX_RESULTS=5
  SEARCH_MULTI_SOURCE_THRESHOLD=3
  SEARCH_CACHE_TTL_SECONDS=600

  DEEP_ANALYSIS_MONTHLY_BUDGET=30.0
  PHASE1_ROLLOUT_PERCENTAGE=0.05

  TOOL_PRICE_ENABLED=false
  TOOL_MACRO_ENABLED=false
  TOOL_ONCHAIN_ENABLED=false
  ```
- 运行时根据事件类型加载 `HIGH_PRIORITY_EVENT_DOMAINS`，确保搜索结果可信。

## 验证与测试
- `pytest tests/ai/deep_analysis/test_codex_cli_planner.py -v`（复用 CLI 测试验证工具规划逻辑）。
- 针对搜索工具编写单元测试：`tests/ai/tools/test_search_fetcher.py`，Mock API 响应与缓存。
- 集成测试覆盖 LangGraph 节点流程，验证 `tools`、`search_keywords`、`notes` 输出。
- 监控：
  - 日志中记录各节点耗时与工具触发情况。
  - 统计成功率、平均延迟、每日工具调用量。

## 里程碑与状态
- 搜索工具基础设施（registry、fetcher、异常处理）已完成。
- LangGraph 节点大部分逻辑落地，总代码量约 1,215 行，等待拆分与测试完善。
- 配置样板、配额与成本控制策略成文，进入灰度验证阶段。

## 风险与后续
- 单文件体量过大影响可维护性：需尽快拆分并补齐单元测试。
- 外部搜索配额有限：确保配额检测与灰度策略生效。
- 工具规划依赖 Prompt：需持续调参与回归，防止错误触发。
- 下一步：接入价格/宏观/链上工具、完善可观测性、将 LangGraph 节点迁移至独立模块并引入自动化负载测试。
