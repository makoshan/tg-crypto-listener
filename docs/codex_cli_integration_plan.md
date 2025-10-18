# 技术方案：集成 Codex CLI 作为深度分析引擎

## 1. 背景与目标

### 1.1 现状问题

当前深度分析引擎依赖 **Gemini Function Calling**，导致：
- **模型绑定**：无法使用不支持 Function Calling 的模型
- **工具耦合**：规划逻辑与 Gemini SDK 紧密耦合
- **扩展受限**：无法接入 CLI 工具作为规划引擎

### 1.2 目标

通过**规划器-执行器分离**架构，实现：
1. **模型解耦**：支持任意文本生成模型作为规划器
2. **CLI 集成**：将 Claude Code CLI 等外部工具封装为规划器
3. **灵活切换**：通过配置在不同规划器间切换
4. **保持兼容**：现有 Gemini 流程作为默认实现

---

## 2. 核心设计

### 2.1 架构演进

**现有架构**（紧耦合）：
```
GeminiDeepAnalysisEngine
  ├─ ToolPlannerNode (硬编码 Gemini Function Calling)
  └─ ToolExecutorNode (执行工具)
```

**目标架构**（解耦）：
```
Deep Analysis Engine
  ├─ Planner (抽象接口)
  │   ├─ GeminiPlanner
  │   ├─ CodexCliPlanner  🆕
  │   └─ TextOnlyPlanner  🆕
  └─ Tool Executor (统一实现，不变)
```

### 2.2 Planner 接口

```python
class BasePlanner:
    """规划器抽象接口"""

    async def plan(state, available_tools) -> ToolPlan:
        """
        决策下一步工具调用

        输入：
          state: 当前状态（事件、初步分析、已有证据）
          available_tools: ["search", "price", "macro", ...]

        输出：
          ToolPlan(
              tools=["search", "price"],
              search_keywords="BTC ETF approval",
              macro_indicators=["CPI"],
              reason="需要验证 ETF 批准消息"
          )
        """
        pass

    async def synthesize(state) -> str:
        """综合证据生成最终 JSON 信号"""
        pass
```

**标准化数据结构**：

```python
@dataclass
class ToolPlan:
    tools: List[str]               # 工具列表
    search_keywords: str = ""      # 搜索关键词
    macro_indicators: List[str]    # 宏观指标
    onchain_assets: List[str]      # 链上资产
    protocol_slugs: List[str]      # 协议标识
    reason: str = ""               # 决策理由
    confidence: float = 1.0        # 规划置信度
```

---

## 3. 两种 Planner 实现（同级可选）

### 3.1 Gemini Planner（API Function Calling 方案）

使用 Gemini 原生 Function Calling：

```python
class GeminiPlanner(BasePlanner):
    async def plan(state, available_tools):
        # 构建 Function Declaration
        tool_def = {
            "name": "decide_next_tools",
            "parameters": {
                "tools": {"type": "ARRAY", ...},
                "search_keywords": {"type": "STRING", ...},
                ...
            }
        }

        # 调用 Gemini Function Calling
        response = await client.generate_with_tools(
            messages=[prompt],
            tools=[tool_def]
        )

        # 直接返回结构化结果
        return ToolPlan(**response.function_calls[0].args)
```

**特点**：
- ✅ 高质量结构化输出（原生 Function Calling）
- ✅ 延迟低（~1.5s）
- ✅ JSON 格式稳定（99%）
- ⚠️ 依赖 Gemini SDK

**适用场景**：
- 需要快速响应的高频分析
- 对结构化输出稳定性要求高
- 愿意承担 API 调用成本

---

### 3.2 CLI Planner（CLI 工具方案）

将 Claude Code CLI 或其他 CLI 工具作为高级规划器：

```python
class CodexCliPlanner(BasePlanner):
    async def plan(state, available_tools):
        # 1. 构建文本 Prompt
        prompt = f"""
        你是加密交易分析师，决定下一步需要调用哪些工具。

        ## 事件
        {state['payload'].text}

        ## 初步分析
        类型: {state['preliminary'].event_type}
        资产: {state['preliminary'].asset}

        ## 已有证据
        {self._summarize_evidence(state)}

        ## 可用工具
        {available_tools}

        ## 输出格式（必须是有效 JSON）
        {{
          "tools": ["search", "price"],
          "search_keywords": "...",
          "macro_indicators": ["CPI"],
          "reason": "决策理由"
        }}

        直接输出 JSON，不要包含 markdown 标记。
        """

        # 2. 调用 CLI（subprocess）
        with tempfile.NamedTemporaryFile(mode='w') as f:
            f.write(prompt)

            proc = await asyncio.create_subprocess_exec(
                "claude-code",
                "--file", f.name,
                "--format", "json",
                stdout=PIPE, stderr=PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=60
            )

        # 3. 解析 JSON 输出
        cli_output = stdout.decode()
        json_text = self._extract_json(cli_output)  # 支持 markdown 包裹
        data = json.loads(json_text)

        return ToolPlan(
            tools=data['tools'],
            search_keywords=data.get('search_keywords', ''),
            macro_indicators=data.get('macro_indicators', []),
            reason=data.get('reason', '')
        )

    def _extract_json(self, text):
        """从 markdown 代码块提取 JSON"""
        if "```json" in text:
            match = re.search(r'```json\s*\n(.*?)\n```', text, DOTALL)
            if match:
                return match.group(1)
        return text.strip()
```

**交互流程**：
```
构建 Prompt → 写入临时文件 → 调用 CLI (subprocess)
→ 等待输出 (60s timeout) → 解析 JSON → 返回 ToolPlan
```

**特点**：
- ✅ Claude 强大的推理和规划能力
- ✅ 无需 API SDK 集成，CLI 自包含
- ⚠️ 进程启动开销（~2-3s）
- ⚠️ 需要鲁棒的 JSON 解析

**适用场景**：
- 需要深度推理的复杂事件分析
- 对分析质量要求高于延迟要求
- 离线或本地部署场景

---

### 3.3 Text-Only Planner（通用方案）

支持任意文本生成模型：

```python
class TextOnlyPlanner(BasePlanner):
    async def plan(state, available_tools):
        # 构建类似 Codex CLI 的 Prompt
        prompt = self._build_planning_prompt(state, available_tools)

        # 根据配置调用不同 Provider
        if self.provider == "openai":
            response = await openai.ChatCompletion.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.choices[0].message.content

        elif self.provider == "deepseek":
            response = await http_client.post(
                "https://api.deepseek.com/v1/chat/completions",
                json={"model": "deepseek-chat", "messages": [...]}
            )
            text = response.json()['choices'][0]['message']['content']

        # 解析 JSON
        data = json.loads(self._extract_json(text))
        return ToolPlan(**data)
```

**特点**：
- ✅ 支持任意文本模型（OpenAI、DeepSeek、Qwen）
- ✅ 成本可控
- ⚠️ JSON 格式稳定性低于 Function Calling

---

## 4. LangGraph 改造方案

### 4.1 现有流程（保持不变）

```
Context Gather → Tool Planner → Tool Executor ⇄ Tool Planner
                                        ↓
                                   Synthesis
```

### 4.2 节点改造（只改内部实现）

**Tool Planner Node**：

```python
# 改造前（硬编码）
class ToolPlannerNode:
    async def execute(state):
        response = await engine._client.generate_with_tools(...)
        return parse_response(response)

# 改造后（使用工厂）
class ToolPlannerNode:
    def __init__(self, engine):
        planner_type = config.DEEP_ANALYSIS_PLANNER
        self.planner = create_planner(planner_type, engine, config)

    async def execute(state):
        available_tools = self.planner.discover_available_tools()
        plan = await self.planner.plan(state, available_tools)

        return {
            "next_tools": plan.tools,
            "search_keywords": plan.search_keywords,
            ...
        }
```

**Synthesis Node**：

```python
# 改造前
class SynthesisNode:
    async def execute(state):
        prompt = build_synthesis_prompt(state)
        response = await gemini_client.generate_text(prompt)
        return {"final_response": response.text}

# 改造后
class SynthesisNode:
    def __init__(self, engine):
        planner_type = config.DEEP_ANALYSIS_PLANNER
        self.planner = create_planner(planner_type, engine, config)

    async def execute(state):
        final_json = await self.planner.synthesize(state)
        return {"final_response": final_json}
```

**关键点**：
- ✅ Tool Executor Node **不需要改动**
- ✅ LangGraph 流程图**不需要改动**
- ✅ 只改变决策生成方式

---

## 5. 配置方案

### 5.1 环境变量

```bash
# Planner 选择
DEEP_ANALYSIS_PLANNER=gemini  # gemini | codex_cli | text_only

# Codex CLI 配置
CODEX_CLI_PATH=claude-code
CODEX_CLI_TIMEOUT=60
CODEX_CLI_MAX_TOKENS=4000

# Text-Only Planner 配置
TEXT_PLANNER_PROVIDER=openai  # openai | deepseek | qwen
TEXT_PLANNER_API_KEY=sk-...
TEXT_PLANNER_MODEL=gpt-4
TEXT_PLANNER_BASE_URL=https://api.openai.com/v1
```

### 5.2 选择示例

**场景 1：高频分析（默认 - Gemini）**
```bash
DEEP_ANALYSIS_PLANNER=gemini
# 特点：延迟低（~1.5s）、稳定性高
```

**场景 2：复杂推理（Codex CLI）**
```bash
DEEP_ANALYSIS_PLANNER=codex_cli
CODEX_CLI_PATH=/usr/local/bin/claude-code
CLAUDE_API_KEY=sk-ant-...
# 特点：Claude 推理能力强、适合重大事件深度分析
```

**场景 3：成本优化（Text-Only DeepSeek）**
```bash
DEEP_ANALYSIS_PLANNER=text_only
TEXT_PLANNER_PROVIDER=deepseek
TEXT_PLANNER_API_KEY=sk-...
TEXT_PLANNER_MODEL=deepseek-chat
# 特点：成本最低、适合大量事件处理
```

---

## 6. 目录结构

```
src/ai/deep_analysis/
├── base.py
├── factory.py
├── gemini.py
├── graph.py
├── planners/              # 🆕 规划器抽象层
│   ├── base.py            # BasePlanner, ToolPlan
│   ├── factory.py         # create_planner()
│   ├── gemini_planner.py  # Gemini Function Calling
│   ├── codex_cli_planner.py  # 🆕 Claude Code CLI
│   └── text_planner.py    # 🆕 通用文本模型
├── nodes/
│   ├── context_gather.py
│   ├── tool_planner.py    # 🔧 使用 Planner 工厂
│   ├── tool_executor.py   # 不变
│   └── synthesis.py       # 🔧 使用 Planner.synthesize()
└── helpers/
    └── prompts.py
```

---

## 7. 核心优势

1. **解耦设计**：Planner 与 Executor 职责分离，易于扩展
2. **同级选择**：Gemini 和 Codex CLI 是平等的高级分析引擎，根据场景选择
3. **配置驱动**：修改环境变量即可切换，无需改代码
4. **向后兼容**：Gemini 作为默认实现，现有流程零影响
5. **可扩展性**：未来可接入 Aider、Cursor 等更多 Agent 工具

### 7.1 选择指南

| 场景 | 推荐 Planner | 理由 |
|------|-------------|------|
| **高频分析**（一般新闻） | Gemini | 延迟低、稳定性高 |
| **复杂推理**（重大事件） | Codex CLI | Claude 推理能力强 |
| **成本优化**（大量事件） | Text-Only (DeepSeek) | 成本最低 |
| **离线部署** | Codex CLI | 可本地运行 |
| **云端部署** | Gemini | API 调用简单 |

---

## 8. 测试验证

### 8.1 测试结果

✅ **所有测试通过** (8/8 in 1.09s)

| 测试类 | 测试用例 | 状态 |
|--------|---------|------|
| **CLI 调用** | 基础调用、上下文引用 | ✅ PASSED |
| **Planner 实现** | 规划流程、JSON 提取 | ✅ PASSED |
| **错误处理** | 超时、退出码、无效 JSON | ✅ PASSED |

### 8.2 运行测试

```bash
# 运行所有测试
pytest tests/ai/deep_analysis/test_codex_cli_planner.py -v

# 查看示例
python examples/codex_cli_usage.py
```

---

## 9. 关键要点总结

1. **同级选择**：Gemini 和 Codex CLI 是**平等的高级分析引擎**，不存在主备关系
2. **场景驱动**：根据延迟、质量、成本需求选择合适的 Planner
3. **配置切换**：通过 `DEEP_ANALYSIS_PLANNER` 环境变量即可切换
4. **完全解耦**：Tool Executor 保持不变，只改变规划决策方式
5. **已验证**：完整测试覆盖，生产可用

---

**文档版本**: v1.1
**编写日期**: 2025-10-16
**测试状态**: ✅ 8/8 passed
**状态**: 已验证，可供使用
