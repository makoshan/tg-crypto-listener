# Claude CLI 深度分析引擎

## 概述
- Claude CLI 通过命令行工具执行深度分析，结合内置 WebSearch 与 Bash 等 13 种工具，零额外费用即可完成验证与证据收集。
- 针对高价值信号提供独立的记忆子系统，基于 Anthropic Memory Tool 在本地 `./memories/claude_cli_deep_analysis/` 下沉淀资产档案、案例与分析模式。
- 引擎以配置驱动与 Codex/Gemini 并列可选，保持统一的 JSON 输出格式与降级策略。

## 目标
- 利用 Claude Code 订阅在本地执行深度分析，降低成本。
- 复用 WebSearch、Bash、Read 等安全工具完成搜索、脚本执行与日志检索。
- 通过 Memory Tool 建立专用记忆仓，面向高置信度信号持续学习。
- 与现有流程无缝切换，支持备用引擎降级。

## 架构与流程
- **执行路径**：
  1. 主分析（Gemini）触发深度分析 → `ClaudeCliEngine` 通过 stdin 投喂 Prompt。
  2. CLI 根据工具守则决定是否调用 `search_news.py`、`fetch_memory.py` 或内置 WebSearch。
  3. Agent 输出 Markdown 或纯文本 JSON，解析后回填结构化信号。
- **记忆系统**：
  - 使用 `ClaudeDeepAnalysisMemoryHandler` 代理 Memory Tool，支持 `view/create/str_replace/insert/delete/rename` 六种命令。
  - 目录结构按资产、事件类型、市场模式与学习洞察分层，示例：
    ```
    memories/claude_cli_deep_analysis/
      ├─ assets/BTC/profile.md
      ├─ patterns/hack_analysis.md
      ├─ case_studies/by_asset/BTC/2025-01-btc-etf.md
      ├─ learning_insights/confidence_calibration.md
      └─ context/session_state.md
    ```
  - Claude 在分析前先 `view` 相关目录，完成后将洞察增量写回，定期清理 30 天前文件。
- **安全控制**：仅开放必要工具，默认通过 `--dangerously-skip-permissions` 运行，依赖日志审计输出。

## 实施步骤
- **引擎接入**
  - 在配置层设置 `DEEP_ANALYSIS_PROVIDER=claude_cli`，必要时指定备用 `DEEP_ANALYSIS_FALLBACK_PROVIDER=codex_cli`。
  - `ClaudeCliEngine.analyze()` 通过 `asyncio.create_subprocess_exec` 调用 `claude --print --dangerously-skip-permissions`，将 Prompt 写入 stdin。
  - 解析 CLI 输出的 JSON（支持 Markdown 代码块、纯 JSON、混合文本），失败则降级到备用引擎。
- **工具调用守则**
  - Bash 示例：
    ```bash
    uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
      --query "Binance ABC listing official" --max-results 6
    uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
      --query "USDC depeg risk" --asset USDC --limit 3
    ```
  - WebSearch：直接使用 Claude 内置能力验证新闻来源，无需额外 API Key。
  - Read/Grep：扫描历史分析记录或配置文件，为深度分析提供上下文。
- **记忆系统集成**
  - 初始化 Anthropic 客户端时挂载 `ClaudeDeepAnalysisMemoryHandler` 并传入 `betas=["context-management-2025-06-27"]`。
  - Memory Tool Schema：
    ```json
    {"name": "memory_tool", "description": "...", "input_schema": {"type": "object", "properties": {"command": {"type": "string","enum": ["view","create","str_replace","insert","delete","rename"]}, "path": {"type": "string"}, "file_text": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}, "insert_line": {"type": "integer"}, "insert_text": {"type": "string"}, "old_path": {"type": "string"}, "new_path": {"type": "string"}}, "required": ["command"]}}
    ```
  - Claude 会自动读取资产档案、案例与模式文件，并在分析结束后更新规律、校准置信度或写入失败案例反思。
  - 触发条件：仅当主分析置信度 ≥ 0.75 时进入记忆流程，确保记忆仅服务高价值信号。

## 配置
- 基础：
  - `DEEP_ANALYSIS_ENABLED=true`
  - `DEEP_ANALYSIS_PROVIDER=claude_cli`
  - `DEEP_ANALYSIS_FALLBACK_PROVIDER=codex_cli`（可选）
- CLI：
  - `CLAUDE_CLI_PATH=claude`
  - `CLAUDE_CLI_TIMEOUT=60`（可调，如网络波动可提升至 90/120 秒）
  - `CLAUDE_CLI_RETRY_ATTEMPTS=1`
  - `CLAUDE_CLI_ALLOWED_TOOLS=Bash,Read,WebSearch,Grep`（最小集合，可按需追加 Glob/WebFetch）
  - `CLAUDE_CLI_EXTRA_ARGS=`、`CLAUDE_CLI_WORKDIR=`（按需配置）
- 记忆目录：确保应用对 `./memories/claude_cli_deep_analysis/` 具有读写权限，并定期备份。

## 验证与测试
- 执行 `pytest tests/ai/deep_analysis -v` 回归基础用例。
- 人工验证 CLI：
  ```bash
  echo "分析这个消息..." | claude --print --dangerously-skip-permissions
  ```
- 检查日志中 `Claude CLI stdout` 片段，确认 JSON 被正确解析，工具调用成对出现。
- Memory Tool 调试：在 DEBUG 模式观察 `memory_tool` 命令与目录变更。

## 里程碑与状态
- 记忆仓目录与协议已定稿，CLI 集成完成基础打通，当前阶段重点验证搜索与记忆交互的稳定性。
- 后续需根据生产运行情况调整工具白名单与记忆清理策略。

## 风险与后续
- CLI 以 `--dangerously-skip-permissions` 运行，需确保执行环境可信并限制敏感命令。
- WebSearch 受 Claude 配额限制，需监控调用频率并提前准备回退方案。
- 记忆文件为长期资产，若缺乏审计可能引入 Prompt 注入风险；建议增加内容校验与定期审查。
- 下一步：扩展 Bash 工具集（价格、链上数据）、完善记忆模板与回收机制、与 Codex CLI 共享统一日志格式。
