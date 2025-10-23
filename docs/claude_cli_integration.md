# Claude CLI 深度分析引擎集成文档

## 概述

Claude CLI 引擎通过调用 `claude` 命令行工具来执行深度分析。与 Codex CLI 相比，Claude CLI 具有以下特点：

- **零额外费用**：利用现有的 Claude Code 订阅
- **原生 Web 搜索**：支持 WebSearch 工具直接验证新闻真实性
- **更强的工具集成**：支持 13 种内置工具

## 配置说明

### 环境变量配置

在 `.env` 文件中添加以下配置：

```bash
# 深度分析统一配置
DEEP_ANALYSIS_ENABLED=true
DEEP_ANALYSIS_PROVIDER=claude_cli  # 切换到 Claude CLI

# Claude CLI 配置
CLAUDE_CLI_PATH=claude  # Claude CLI 可执行文件路径
CLAUDE_CLI_TIMEOUT=60   # 超时时间（秒）
CLAUDE_CLI_RETRY_ATTEMPTS=1  # 重试次数
CLAUDE_CLI_ALLOWED_TOOLS=Bash,Read,WebSearch,Grep  # 允许的工具列表
CLAUDE_CLI_EXTRA_ARGS=  # 额外的 CLI 参数
CLAUDE_CLI_WORKDIR=     # 工作目录（留空使用当前目录）
```

### 可用工具列表

Claude Code 提供以下 13 种工具：

| 工具名称 | 描述 | 是否需要权限 | 推荐用于深度分析 |
|---------|------|-------------|----------------|
| **Bash** | 执行 shell 命令 | ✅ 需要 | ✅ 是（运行 codex_tools 脚本） |
| **Read** | 读取文件内容 | ❌ 不需要 | ✅ 是（读取配置、数据） |
| **WebSearch** | 执行网页搜索 | ✅ 需要 | ✅ 是（验证新闻真实性） |
| **Grep** | 搜索文件内容 | ❌ 不需要 | ✅ 是（搜索历史记录） |
| **Edit** | 编辑文件 | ✅ 需要 | ❌ 否 |
| **Write** | 创建/覆盖文件 | ✅ 需要 | ❌ 否 |
| **Glob** | 文件模式匹配 | ❌ 不需要 | ⚠️ 可选 |
| **WebFetch** | 获取 URL 内容 | ✅ 需要 | ⚠️ 可选（已有 WebSearch） |
| **NotebookEdit** | 编辑 Jupyter Notebook | ✅ 需要 | ❌ 否 |
| **NotebookRead** | 读取 Jupyter Notebook | ❌ 不需要 | ❌ 否 |
| **SlashCommand** | 执行自定义命令 | ✅ 需要 | ❌ 否 |
| **Task** | 运行子代理 | ❌ 不需要 | ❌ 否 |
| **TodoWrite** | 管理任务列表 | ❌ 不需要 | ❌ 否 |

### 推荐工具配置

对于深度分析场景，推荐以下工具组合：

```bash
# 最小配置（只运行脚本）
CLAUDE_CLI_ALLOWED_TOOLS=Bash,Read

# 标准配置（包含搜索和验证）
CLAUDE_CLI_ALLOWED_TOOLS=Bash,Read,WebSearch,Grep

# 完整配置（包含所有安全工具）
CLAUDE_CLI_ALLOWED_TOOLS=Bash,Read,WebSearch,Grep,Glob,WebFetch
```

**注意**：不要包含 `Edit`、`Write`、`NotebookEdit` 等修改文件的工具，避免意外修改代码。

## 工作原理

### 1. Prompt 构建

Claude CLI 引擎通过 stdin 接收 prompt（与 Codex CLI 的命令行参数不同）：

```python
# Codex CLI: 通过 --prompt 参数传递
codex --prompt "分析这个消息..." --full-auto

# Claude CLI: 通过 stdin 传递
echo "分析这个消息..." | claude --print --dangerously-skip-permissions
```

### 2. 工具调用

Claude CLI 可以直接调用以下工具：

#### Bash 工具（运行分析脚本）
```bash
# 新闻搜索
uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
    --query "Binance ABC listing official" --max-results 6

# 价格查询
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
    --assets BTC ETH SOL

# 历史记忆检索
uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
    --query "USDC depeg risk" --asset USDC --limit 3
```

#### WebSearch 工具（原生搜索）
```bash
# Claude CLI 内置的 WebSearch 工具
# 优势：不需要外部 API key，直接使用 Claude Code 的搜索能力
# 示例：搜索 "Binance ABC token listing announcement"
```

#### Read 工具（读取文件）
```bash
# 读取配置文件、数据文件
# 示例：读取历史分析结果
```

#### Grep 工具（搜索内容）
```bash
# 在文件中搜索关键词
# 示例：搜索历史记录中的相似事件
```

### 3. JSON 提取

Claude CLI 的输出可能包含解释性文本，引擎会自动提取 JSON 部分：

```python
# 支持的格式：
# 1. Markdown 代码块
"""
```json
{"summary": "...", "confidence": 0.8}
```
"""

# 2. 纯 JSON
"""
{"summary": "...", "confidence": 0.8}
"""

# 3. 混合文本
"""
分析完成，结果如下：
{"summary": "...", "confidence": 0.8}
"""
```

## 与 Codex CLI 的对比

| 特性 | Codex CLI | Claude CLI |
|-----|-----------|-----------|
| **成本** | 零（利用 Codex 订阅） | 零（利用 Claude Code 订阅） |
| **Prompt 传递** | 命令行参数 `--prompt` | stdin 输入 |
| **权限模式** | `--full-auto` | `--dangerously-skip-permissions` |
| **工具限制** | `--allowed-tools` | `--allowedTools` |
| **原生搜索** | ❌ 无 | ✅ WebSearch 工具 |
| **上下文引用** | ✅ `@docs/...` | ❌ 需通过 prompt 传递 |
| **工具数量** | 有限 | 13 种内置工具 |
| **适用场景** | 需要文档上下文 | 需要搜索验证 |

## 切换引擎

### 从 Codex CLI 切换到 Claude CLI

```bash
# 修改 .env 文件
DEEP_ANALYSIS_PROVIDER=claude_cli  # 从 codex_cli 改为 claude_cli
```

### 从 Claude CLI 切换到 Codex CLI

```bash
# 修改 .env 文件
DEEP_ANALYSIS_PROVIDER=codex_cli  # 从 claude_cli 改为 codex_cli
```

### 使用混合模式

```bash
# 主引擎使用 Claude CLI，备用引擎使用 Codex CLI
DEEP_ANALYSIS_PROVIDER=claude_cli
DEEP_ANALYSIS_FALLBACK_PROVIDER=codex_cli
```

## 测试验证

运行集成测试：

```bash
python3 test_claude_cli_integration.py
```

预期输出：
```
✅ PASS: Config validation
✅ PASS: Factory creation
✅ PASS: Prompt building
Total: 3/3 tests passed
🎉 All integration tests passed!
```

## 性能调优

### 超时配置

```bash
CLAUDE_CLI_TIMEOUT=60  # 默认 60 秒
# 如果深度分析经常超时，可以增加到 90 或 120 秒
```

### 重试策略

```bash
CLAUDE_CLI_RETRY_ATTEMPTS=1  # 默认 1 次重试
# 如果网络不稳定，可以增加到 2-3 次
```

### 工具权限

```bash
# 使用 --dangerously-skip-permissions 跳过所有权限检查
# 适用于沙箱环境或无外网访问的服务器
```

## 故障排除

### 问题：Claude CLI 未找到

```bash
# 检查 Claude CLI 是否安装
which claude

# 如果未安装，安装 Claude Code
# 参考：https://docs.claude.com/en/docs/claude-code/installation
```

### 问题：工具调用失败

```bash
# 检查工具权限配置
CLAUDE_CLI_ALLOWED_TOOLS=Bash,Read,WebSearch,Grep

# 确保工具名称正确（区分大小写）
# 正确：Bash, Read, WebSearch
# 错误：bash, read, websearch
```

### 问题：JSON 解析失败

```bash
# 查看原始输出日志
# 日志位置：listener 输出中的 DEBUG 级别日志
# 搜索关键词：Claude CLI stdout 预览
```

### 问题：超时错误

```bash
# 增加超时时间
CLAUDE_CLI_TIMEOUT=120  # 从 60 增加到 120 秒

# 或者减少工具调用次数（在 prompt 中优化）
```

## 最佳实践

1. **工具选择**：只启用必要的工具，避免安全风险
2. **超时设置**：根据网络状况调整，避免频繁超时
3. **重试策略**：合理设置重试次数，平衡成功率和响应时间
4. **日志监控**：开启 DEBUG 日志，监控工具调用情况
5. **成本控制**：Claude CLI 使用订阅配额，注意使用频率

## 相关文档

- [Claude Code 官方文档](https://docs.claude.com/en/docs/claude-code/introduction)
- [CLI 参考文档](https://docs.claude.com/en/docs/claude-code/cli-reference)
- [工具配置文档](https://docs.claude.com/en/docs/claude-code/settings)
- [深度分析引擎设计](./deep_analysis_engine_switch_plan.md)
- [Codex CLI 集成方案](./codex_cli_integration_plan.md)
