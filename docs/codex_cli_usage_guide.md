# Codex CLI 使用指南

## 快速开始

### 1. 运行测试

```bash
# 运行所有测试
pytest tests/ai/deep_analysis/test_codex_cli_planner.py -v

# 运行特定测试
pytest tests/ai/deep_analysis/test_codex_cli_planner.py::TestCodexCliInvocation::test_codex_exec_basic -v

# 运行集成测试（需要安装 codex CLI）
pytest tests/ai/deep_analysis/test_codex_cli_planner.py -m integration -v
```

### 2. 运行示例

```bash
# 运行完整示例
python examples/codex_cli_usage.py

# 使用 uvx（推荐）
uvx --with-requirements requirements.txt python examples/codex_cli_usage.py
```

---

## Codex CLI 基本用法

### 命令格式

```bash
codex exec "<prompt>" [@context-file]
```

### 示例 1：基础调用

```bash
codex exec "分析这个加密货币事件：BTC ETF 获批。返回 JSON 格式的工具列表。"
```

输出：
```json
{
  "tools": ["search", "price"],
  "search_keywords": "BTC ETF SEC approval",
  "reason": "需要搜索验证消息并获取价格数据"
}
```

### 示例 2：引用上下文文件

```bash
codex exec "根据方案文档决定需要调用的工具。
事件：BTC ETF 获批
类型：listing
资产：BTC

@docs/codex_cli_integration_plan.md"
```

**关键点**：
- `@docs/xxx.md` 引用文档作为上下文
- CLI 会自动读取文档内容并理解其中的规范

### 示例 3：在 Python 中调用

```python
import asyncio
import json

async def call_codex():
    prompt = """
    分析事件并返回 JSON：

    事件：BTC ETF 获批

    @docs/codex_cli_integration_plan.md
    """

    proc = await asyncio.create_subprocess_exec(
        "codex",
        "exec",
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()
    result = json.loads(stdout.decode())
    return result

# 运行
result = asyncio.run(call_codex())
print(result)
```

---

## 集成到深度分析引擎

### 配置

在 `.env` 中添加：

```bash
# 切换到 Codex CLI Planner
DEEP_ANALYSIS_PLANNER=codex_cli

# Codex CLI 配置
CODEX_CLI_TIMEOUT=60
CODEX_CLI_MAX_TOKENS=4000

# 如果需要 API key（某些 CLI 实现）
CLAUDE_API_KEY=sk-ant-...
```

### 使用

```python
from examples.codex_cli_usage import CodexCliPlanner

# 创建 planner
planner = CodexCliPlanner(timeout=60)

# 工具规划
state = {
    'payload': {'text': 'BTC ETF 获批', ...},
    'preliminary': {'event_type': 'listing', ...},
}
plan = await planner.plan(state, available_tools=['search', 'price'])

# 输出：
# {
#   'tools': ['search', 'price'],
#   'search_keywords': 'BTC ETF SEC approval',
#   'reason': '...'
# }

# 证据综合
final_json = await planner.synthesize(state)
```

---

## 关键特性

### 1. Markdown 包裹的 JSON

CLI 可能返回 markdown 格式：

```
```json
{
  "tools": ["search"]
}
```
```

Planner 会自动提取 JSON：

```python
def _extract_json(text):
    if "```json" in text:
        match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
        if match:
            return match.group(1)
    return text.strip()
```

### 2. 超时处理

```python
# 设置 60 秒超时
proc = await asyncio.create_subprocess_exec(...)
stdout, stderr = await asyncio.wait_for(
    proc.communicate(),
    timeout=60
)
```

### 3. 错误处理

```python
try:
    result = await planner.plan(state, tools)
except TimeoutError:
    # CLI 超时，降级到 Gemini
    result = await gemini_planner.plan(state, tools)
except RuntimeError as exc:
    # CLI 执行失败
    logger.error(f"CLI failed: {exc}")
except json.JSONDecodeError:
    # JSON 解析失败，重试或降级
    pass
```

---

## 测试用例说明

### TestCodexCliInvocation

测试基础的 CLI 调用：

- ✅ `test_codex_exec_basic` - 基础调用和参数验证
- ✅ `test_codex_exec_with_context_file` - 引用上下文文件

### TestCodexCliPlanner

测试 Planner 实现：

- ✅ `test_plan_with_codex_exec` - 完整规划流程
- ✅ `test_handle_markdown_wrapped_json` - JSON 提取逻辑

### TestCodexCliErrorHandling

测试错误场景：

- ✅ `test_timeout_handling` - 超时处理
- ✅ `test_non_zero_exit_code` - 非零退出码
- ✅ `test_invalid_json_output` - 无效 JSON

### TestCodexCliIntegration

集成测试（需要安装 CLI）：

- 🔧 `test_real_codex_exec_call` - 真实 CLI 调用

---

## 性能优化建议

### 1. 并发限制

CLI 调用是进程级操作，建议限制并发：

```python
# 最多同时 2 个 CLI 调用
semaphore = asyncio.Semaphore(2)

async def plan_with_semaphore(state, tools):
    async with semaphore:
        return await planner.plan(state, tools)
```

### 2. 结果缓存

对于相同的事件类型，缓存规划结果：

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_plan_key(event_type, asset, evidence_types):
    return (event_type, asset, frozenset(evidence_types))

# 使用缓存
cache_key = get_plan_key(
    preliminary.event_type,
    preliminary.asset,
    tuple(sorted(state.keys()))
)
if cache_key in plan_cache:
    return plan_cache[cache_key]
```

### 3. 降级策略

CLI 失败时自动降级到 Gemini：

```python
async def plan_with_fallback(state, tools):
    try:
        return await codex_planner.plan(state, tools)
    except Exception as exc:
        logger.warning(f"Codex CLI failed, fallback to Gemini: {exc}")
        return await gemini_planner.plan(state, tools)
```

---

## 常见问题

### Q1: CLI 找不到怎么办？

```bash
# 检查 CLI 是否安装
which codex

# 如果未安装，从环境变量获取路径
export CODEX_CLI_PATH=/path/to/codex
```

### Q2: CLI 返回的 JSON 格式不稳定？

使用鲁棒的解析逻辑：

```python
def _extract_json(text):
    # 尝试多种格式
    patterns = [
        r'```json\s*\n(.*?)\n```',  # markdown json
        r'```\s*\n(.*?)\n```',      # generic code block
        r'\{.*\}',                   # raw json
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                json.loads(match.group(1))
                return match.group(1)
            except:
                continue

    return text.strip()
```

### Q3: 如何调试 CLI 调用？

```python
# 记录详细日志
logger.debug(f"Codex CLI input: {prompt[:200]}...")
logger.debug(f"Codex CLI output: {cli_output[:500]}...")

# 保存 prompt 到文件
with open('/tmp/codex_prompt.txt', 'w') as f:
    f.write(prompt)
```

---

## 下一步

1. ✅ 运行测试确保 CLI 可用
2. ✅ 修改配置切换到 `codex_cli` planner
3. ✅ 监控性能和错误率
4. ✅ 根据实际情况优化 prompt

---

**文档版本**: v1.0
**最后更新**: 2025-10-16
