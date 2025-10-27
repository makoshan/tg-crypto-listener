# Codex CLI 深度分析引擎

## 概述
- Codex CLI 作为完整的深度分析引擎，通过一次 Agent 调用完成规划、工具执行与结果综合，规避 Gemini Function Calling 的模型与费用限制。
- 引擎内置新闻搜索与历史记忆检索工具，Agent 可在 CLI 中自主执行 Bash 命令并解析 JSON 结果，保留全链路证据。
- 与现有 Gemini 引擎并列可选，可在配置层完成切换，并复用当前深度分析输出结构。

## 目标
- 完整替换：Codex CLI 独立承担深度分析全流程，而不仅是工具规划。
- Agent 能力：支持 CLI 内置工具调用、规划执行、综合分析。
- 灵活切换：通过配置在 Codex CLI 与 Gemini 深度分析引擎间自由切换。
- 成本优化：使用现有 Codex 订阅，避免额外 API 费用。
- 功能对等：覆盖搜索、价格、链上等核心能力，产出与 Gemini 一致的 JSON 信号。

## 架构与流程
- 现状：`GeminiDeepAnalysisEngine` 通过 LangGraph 拆分为上下文收集、工具规划、工具执行和结果汇总四个节点，依赖 Function Calling。
- 目标：抽象 `BaseDeepAnalysisEngine` 接口，新增 `CodexCliEngine`，单次 CLI 调用内部完成所有步骤；原有 Gemini 实现保持可用。
- 对比：
  | 特性 | Codex CLI | Gemini |
  | --- | --- | --- |
  | 执行方式 | 单次 Agent 调用 | 多节点 LangGraph |
  | 工具调用 | CLI 自主决策并执行 Bash 命令 | Function Calling + 手动执行 |
  | 状态管理 | CLI 内部黑盒 | 显式 State 管理 |
  | 费用 | 复用 Codex 订阅 | 额外 Gemini API |
  | 可观察性 | 依赖 CLI 输出日志 | 节点级观测 |
- 关键实现要点：
  - `CodexCliEngine.analyze()` 构建完整 Prompt → 临时文件输出 → `codex exec` 执行 → 读取 JSON。
  - `_build_analysis_prompt()` 注入工具守则、证据引用示例、失败处理策略，指导 Agent 使用搜索与记忆工具。
  - CLI 失败、超时或 JSON 解析异常时回退 Gemini 引擎，保证兼容性。

## 实施步骤
- **引擎接入**
  - 抽象 `BaseDeepAnalysisEngine`，在配置中新增 `DEEP_ANALYSIS_PROVIDER=codex_cli`，支持运行时切换。
  - 在 `CodexCliEngine` 中设置 `cli_path`、`model`、`timeout`，使用 `asyncio.create_subprocess_exec` 调用 `codex exec --full-auto`。
  - 管理临时输出文件并解析 CLI 输出，确保输出可提取 JSON。
  - 失败处理：捕获超时、非零退出码、JSONDecodeError，记录日志并降级。
- **CLI 工具实现**
  - `scripts/codex_tools/search_news.py`
    - 依赖 `SearchTool.fetch()`，支持 Tavily API、关键词/数量/域名参数。
    - 返回 `{success, data, confidence, triggered, error}`；失败时输出 `success=false`。
    - 运行示例：
      ```bash
      uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
        --query "Binance ABC token listing official announcement" \
        --max-results 6
      ```
  - `scripts/codex_tools/fetch_memory.py`
    - 通过 `HybridMemoryRepository.fetch_memories()` 检索历史案例，支持关键词与资产过滤。
    - 返回 `{success, entries, similarity_floor, message}`，entries 含 summary/action/confidence/similarity 等字段。
    - 运行示例：
      ```bash
      uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
        --query "USDC depeg risk" \
        --asset USDC \
        --limit 3
      ```
  - `_build_cli_prompt()` 中补充工具介绍、调用规则、失败处理与证据引用示例，确保 Agent 将数据写入 `notes` 并在失败时降置信度。
- **CLI 使用与示例**
  - 测试：
    ```bash
    pytest tests/ai/deep_analysis/test_codex_cli_planner.py -v
    pytest tests/ai/deep_analysis/test_codex_cli_planner.py::TestCodexCliInvocation::test_codex_exec_basic -v
    pytest tests/ai/deep_analysis/test_codex_cli_planner.py -m integration -v
    ```
  - 示例脚本：
    ```bash
    python examples/codex_cli_usage.py
    uvx --with-requirements requirements.txt python examples/codex_cli_usage.py
    ```
  - CLI 基本命令：
    ```bash
    codex exec "分析这个加密货币事件：BTC ETF 获批。返回 JSON 格式的工具列表。"
    codex exec "根据方案文档决定需要调用的工具。\n事件：BTC ETF 获批\n类型：listing\n资产：BTC\n\n@docs/codex_cli_deep_analysis.md"
    ```
  - Python 集成示例保留 `asyncio.create_subprocess_exec` 调用与 JSON 提取逻辑，用于测试或脚本化执行。

## 配置
- 深度分析切换：
  - `DEEP_ANALYSIS_PROVIDER=codex_cli`
  - `CODEX_CLI_PATH`、`CODEX_CLI_MODEL`、`CODEX_CLI_TIMEOUT`、`CODEX_CLI_MAX_TOKENS`
- 搜索工具：
  - `TAVILY_API_KEY`
  - `DEEP_ANALYSIS_SEARCH_PROVIDER=tavily`
  - `SEARCH_MAX_RESULTS=5`
- 记忆检索：
  - `MEMORY_ENABLED=true`
  - `MEMORY_BACKEND=hybrid`
  - `SUPABASE_URL`、`SUPABASE_SERVICE_KEY`（若使用 Supabase）
  - `MEMORY_MAX_NOTES=3`
  - `MEMORY_SIMILARITY_THRESHOLD=0.55`
- 建议使用 `uvx --with-requirements requirements.txt` 保证脚本依赖在临时环境中可用。

## 验证与测试
- CLI 工具自检：`bash scripts/test_codex_tools.sh`
- 语法检查：`python3 -m py_compile scripts/codex_tools/search_news.py scripts/codex_tools/fetch_memory.py`
- 深度分析单测与集成测试见 `tests/ai/deep_analysis/test_codex_cli_planner.py`。
- 观察 CLI 输出：启用 DEBUG 日志以审查 Agent 调用过程与证据注入。

## 里程碑与状态
- 2025-10-22：完成 CLI 搜索与记忆工具、提示词增强以及测试脚本。
- 当前状态：Codex CLI 与 Gemini 并行可选，CLI 路径已在 `.env` 配置中接入，等待进一步生产验证。

## 风险与后续
- CLI 黑盒带来可观察性下降；需通过日志和输出文件追踪分析流程。
- Tavily 或记忆检索失败时需降低置信度并记录 `data_incomplete`，避免误报。
- 建议后续补充更多工具（链上、行情）命令、细化异常重试策略，并将 CLI 结果与原始事件一并存档，方便回溯。
