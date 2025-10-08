# 深度分析引擎切换方案

## 1. 目标

实现深度分析引擎在 **Claude** 和 **Gemini 2.5 Pro** 之间可配置切换，两者均支持记忆管理。

## 2. 当前架构

```
┌─────────────────────────────────────────────────────────┐
│                    消息接收层                            │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│          主 AI 分析（Gemini Flash）                      │
│          - 快速分析 90% 消息                             │
│          - 初步置信度评估                                │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ├─ confidence < 0.75 ─→ 跳过深度分析
                      │
                      ▼ confidence ≥ 0.75
┌─────────────────────────────────────────────────────────┐
│          深度分析（Claude + Memory Tool）                │
│          - 深度验证 10% 高价值信号                       │
│          - 记忆管理（查询/存储历史案例）                 │
│          - 置信度修正                                    │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   输出最终信号                           │
└─────────────────────────────────────────────────────────┘
```

## 3. 目标架构

```
┌─────────────────────────────────────────────────────────┐
│                    消息接收层                            │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│          主 AI 分析（Gemini Flash）                      │
│          - 快速分析 90% 消息                             │
│          - 初步置信度评估                                │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ├─ confidence < 0.75 ─→ 跳过深度分析
                      │
                      ▼ confidence ≥ 0.75
┌─────────────────────────────────────────────────────────┐
│          深度分析引擎抽象层                              │
│          ┌────────────────┬─────────────────┐          │
│          │                │                 │          │
│          ▼                ▼                 ▼          │
│   Claude Engine   Gemini 2.5 Pro    (未来可扩展)       │
│   + Memory Tool   + Function Call                      │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   输出最终信号                           │
└─────────────────────────────────────────────────────────┘
```

## 4. 核心设计

### 4.1 深度分析引擎抽象接口

```python
# src/ai/deep_analysis_engine.py

from abc import ABC, abstractmethod
from typing import Dict, Any
from .signal_engine import EventPayload, SignalResult

class DeepAnalysisEngine(ABC):
    """深度分析引擎抽象基类"""

    @abstractmethod
    async def analyze(
        self,
        payload: EventPayload,
        initial_result: SignalResult
    ) -> SignalResult:
        """
        执行深度分析

        Args:
            payload: 原始事件数据
            initial_result: 主 AI 的初步分析结果

        Returns:
            优化后的信号结果
        """
        pass

    @abstractmethod
    def supports_memory(self) -> bool:
        """是否支持记忆管理"""
        pass
```

### 4.2 Claude 深度分析引擎实现

```python
# src/ai/claude_deep_engine.py

from .deep_analysis_engine import DeepAnalysisEngine
from .anthropic_client import AnthropicClient
from ..memory import MemoryToolHandler

class ClaudeDeepAnalysisEngine(DeepAnalysisEngine):
    """Claude 深度分析引擎（支持 Memory Tool）"""

    def __init__(
        self,
        client: AnthropicClient,
        memory_handler: MemoryToolHandler
    ):
        self._client = client
        self._memory_handler = memory_handler

    async def analyze(self, payload, initial_result):
        # 构建 Claude prompt（包含 Gemini 初步结果）
        prompt = self._build_prompt(payload, initial_result)

        # 调用 Claude（自动执行 Memory Tool 循环）
        response = await self._client.generate_signal(prompt)

        # 解析并返回结果
        return self._parse_response(response)

    def supports_memory(self) -> bool:
        return True
```

### 4.3 Gemini 深度分析引擎实现

```python
# src/ai/gemini_deep_engine.py

from .deep_analysis_engine import DeepAnalysisEngine
from .gemini_client import GeminiClient
from ..memory import MemoryToolHandler

class GeminiDeepAnalysisEngine(DeepAnalysisEngine):
    """Gemini 2.5 Pro 深度分析引擎（支持 Function Calling）"""

    def __init__(
        self,
        client: GeminiClient,
        memory_handler: MemoryToolHandler,
        max_function_turns: int = 5
    ):
        self._client = client
        self._memory_handler = memory_handler
        self._max_turns = max_function_turns

    async def analyze(self, payload, initial_result):
        # 构建 Gemini prompt
        prompt = self._build_prompt(payload, initial_result)

        # 执行 Function Calling 循环
        response = await self._run_function_calling_loop(prompt)

        # 解析并返回结果
        return self._parse_response(response)

    async def _run_function_calling_loop(self, messages):
        """
        Gemini Function Calling 循环

        类似 Claude 的 Tool Use 循环：
        1. 调用 Gemini API（带 tools 定义）
        2. 检查是否有 function_call
        3. 执行 MemoryToolHandler
        4. 将结果回填并继续调用
        5. 直到没有 function_call 或达到最大轮数
        """
        conversation_history = []
        turn_count = 0

        # 定义 Memory Tool 的 Gemini Function schema
        memory_tool_schema = self._build_memory_function_schema()

        while turn_count < self._max_turns:
            # 调用 Gemini API
            response = await self._client.generate_content(
                contents=messages + conversation_history,
                tools=[memory_tool_schema]
            )

            # 检查是否有 function_call
            function_calls = self._extract_function_calls(response)

            if not function_calls:
                # 没有 function call，返回最终结果
                return response.text

            # 执行所有 function calls
            for fc in function_calls:
                result = self._memory_handler.execute_tool_use(fc.args)

                # 回填 function response
                conversation_history.append({
                    "role": "function",
                    "name": fc.name,
                    "response": result
                })

            turn_count += 1

        raise RuntimeError("Function calling 循环超过最大轮数")

    def _build_memory_function_schema(self) -> Dict:
        """
        构建 Gemini Function Calling schema

        将 Claude Memory Tool schema 转换为 Gemini 格式
        """
        return {
            "function_declarations": [
                {
                    "name": "memory",
                    "description": "Memory management tool for storing, retrieving, and modifying information",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "enum": ["view", "create", "str_replace", "insert", "delete", "rename"],
                                "description": "The command to execute"
                            },
                            "path": {
                                "type": "string",
                                "description": "File or directory path"
                            },
                            "file_text": {"type": "string"},
                            "old_str": {"type": "string"},
                            "new_str": {"type": "string"},
                            "insert_line": {"type": "integer"},
                            "insert_text": {"type": "string"},
                            "old_path": {"type": "string"},
                            "new_path": {"type": "string"}
                        },
                        "required": ["command"]
                    }
                }
            ]
        }

    def supports_memory(self) -> bool:
        return True
```

### 4.4 工厂模式构建引擎

```python
# src/ai/deep_analysis_factory.py

from typing import Optional
from .deep_analysis_engine import DeepAnalysisEngine
from .claude_deep_engine import ClaudeDeepAnalysisEngine
from .gemini_deep_engine import GeminiDeepAnalysisEngine
from .anthropic_client import AnthropicClient
from .gemini_client import GeminiClient
from ..memory import MemoryToolHandler

class DeepAnalysisEngineFactory:
    """深度分析引擎工厂"""

    @staticmethod
    def create(
        provider: str,
        config: Any,
        memory_handler: MemoryToolHandler
    ) -> Optional[DeepAnalysisEngine]:
        """
        创建深度分析引擎

        Args:
            provider: 'claude' 或 'gemini'
            config: 配置对象
            memory_handler: 记忆管理处理器

        Returns:
            深度分析引擎实例，失败返回 None
        """
        provider = provider.lower().strip()

        if provider == "claude":
            return DeepAnalysisEngineFactory._create_claude_engine(
                config, memory_handler
            )
        elif provider == "gemini":
            return DeepAnalysisEngineFactory._create_gemini_engine(
                config, memory_handler
            )
        else:
            logger.warning(f"未知的深度分析引擎: {provider}，将禁用深度分析")
            return None

    @staticmethod
    def _create_claude_engine(config, memory_handler):
        """创建 Claude 引擎"""
        api_key = getattr(config, "CLAUDE_API_KEY", "").strip()
        if not api_key:
            logger.warning("Claude API key 未配置，无法启用 Claude 深度分析")
            return None

        try:
            client = AnthropicClient(
                api_key=api_key,
                model_name=getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
                timeout=getattr(config, "CLAUDE_TIMEOUT_SECONDS", 30.0),
                max_tool_turns=getattr(config, "CLAUDE_MAX_TOOL_TURNS", 10),
                memory_handler=memory_handler,
            )
            logger.info("🧠 Claude 深度分析引擎已初始化")
            return ClaudeDeepAnalysisEngine(client, memory_handler)
        except Exception as exc:
            logger.warning(f"Claude 引擎初始化失败: {exc}")
            return None

    @staticmethod
    def _create_gemini_engine(config, memory_handler):
        """创建 Gemini 引擎"""
        api_key = getattr(config, "GEMINI_API_KEY", "").strip()
        if not api_key:
            logger.warning("Gemini API key 未配置，无法启用 Gemini 深度分析")
            return None

        try:
            client = GeminiClient(
                api_key=api_key,
                model_name=getattr(config, "GEMINI_DEEP_MODEL", "gemini-2.5-pro"),
                timeout=getattr(config, "GEMINI_DEEP_TIMEOUT_SECONDS", 30.0),
                max_retries=getattr(config, "GEMINI_DEEP_RETRY_ATTEMPTS", 1),
            )
            logger.info("🧠 Gemini 2.5 Pro 深度分析引擎已初始化")
            return GeminiDeepAnalysisEngine(
                client,
                memory_handler,
                max_function_turns=getattr(config, "GEMINI_MAX_FUNCTION_TURNS", 5)
            )
        except Exception as exc:
            logger.warning(f"Gemini 引擎初始化失败: {exc}")
            return None
```

### 4.5 集成到 AiSignalEngine

```python
# src/ai/signal_engine.py (修改部分)

class AiSignalEngine:
    def __init__(
        self,
        enabled: bool,
        client: Optional[OpenAIChatClient],
        threshold: float,
        semaphore: asyncio.Semaphore,
        *,
        provider_label: str = "AI",
        deep_analysis_engine: Optional[DeepAnalysisEngine] = None,  # 改为抽象引擎
        high_value_threshold: float = 0.75,
    ):
        self.enabled = enabled and client is not None
        self._client = client
        self._threshold = threshold
        self._semaphore = semaphore
        self._provider_label = provider_label or "AI"
        self._deep_engine = deep_analysis_engine  # 统一接口
        self._high_value_threshold = high_value_threshold

        if self._deep_engine:
            engine_name = type(self._deep_engine).__name__
            logger.info(f"🤖 深度分析引擎已启用: {engine_name}")

    @classmethod
    def from_config(cls, config: Any) -> "AiSignalEngine":
        # ... 原有主 AI 初始化逻辑 ...

        # 初始化深度分析引擎（可切换）
        deep_engine = None
        deep_enabled = getattr(config, "DEEP_ANALYSIS_ENABLED", False)
        if deep_enabled:
            provider = getattr(config, "DEEP_ANALYSIS_PROVIDER", "claude")
            memory_handler = MemoryToolHandler(
                base_path=getattr(config, "MEMORY_DIR", "./memories")
            )
            deep_engine = DeepAnalysisEngineFactory.create(
                provider, config, memory_handler
            )

        return cls(
            True,
            client,
            getattr(config, "AI_SIGNAL_THRESHOLD", 0.0),
            asyncio.Semaphore(concurrency),
            provider_label=provider_label,
            deep_analysis_engine=deep_engine,
            high_value_threshold=getattr(config, "HIGH_VALUE_CONFIDENCE_THRESHOLD", 0.75),
        )

    async def analyse(self, payload: EventPayload) -> SignalResult:
        # ... 主 AI 分析逻辑 ...

        # 深度分析（统一接口）
        if self._deep_engine and is_high_value:
            logger.info(f"🧠 触发深度分析: {type(self._deep_engine).__name__}")
            try:
                deep_result = await self._deep_engine.analyze(payload, gemini_result)
                logger.info(f"✅ 深度分析完成: confidence={deep_result.confidence}")
                return deep_result
            except Exception as exc:
                logger.warning(f"⚠️ 深度分析失败，回退到主 AI 结果: {exc}")
                return gemini_result

        return gemini_result
```

## 5. 配置参数设计

### 5.1 新增环境变量

```bash
# .env

# ========== 深度分析引擎配置 ==========
DEEP_ANALYSIS_ENABLED=true                    # 是否启用深度分析
DEEP_ANALYSIS_PROVIDER=claude                 # 深度分析引擎: claude | gemini
HIGH_VALUE_CONFIDENCE_THRESHOLD=0.75          # 触发深度分析的置信度阈值

# ========== Claude 深度分析配置 ==========
CLAUDE_ENABLED=true                           # 向后兼容（已废弃，使用 DEEP_ANALYSIS_PROVIDER）
CLAUDE_API_KEY=sk-ant-xxx
CLAUDE_MODEL=claude-sonnet-4-5-20250929
CLAUDE_TIMEOUT_SECONDS=30
CLAUDE_MAX_TOOL_TURNS=10

# ========== Gemini 深度分析配置 ==========
GEMINI_DEEP_MODEL=gemini-2.5-pro              # Gemini 深度分析模型
GEMINI_DEEP_TIMEOUT_SECONDS=30                # Gemini 深度分析超时时间
GEMINI_DEEP_RETRY_ATTEMPTS=1                  # Gemini 深度分析重试次数
GEMINI_MAX_FUNCTION_TURNS=5                   # Gemini Function Calling 最大轮数

# ========== 记忆管理配置（通用）==========
MEMORY_ENABLED=true
MEMORY_BACKEND=local                          # local | supabase | hybrid
MEMORY_DIR=./memories
```

### 5.2 配置类更新

```python
# src/config.py (新增部分)

class Config:
    # ... 原有配置 ...

    # Deep Analysis Engine
    DEEP_ANALYSIS_ENABLED: bool = _as_bool(os.getenv("DEEP_ANALYSIS_ENABLED", "false"))
    DEEP_ANALYSIS_PROVIDER: str = os.getenv("DEEP_ANALYSIS_PROVIDER", "claude").lower()

    # Gemini Deep Analysis
    GEMINI_DEEP_MODEL: str = os.getenv("GEMINI_DEEP_MODEL", "gemini-2.5-pro")
    GEMINI_DEEP_TIMEOUT_SECONDS: float = float(os.getenv("GEMINI_DEEP_TIMEOUT_SECONDS", "30"))
    GEMINI_DEEP_RETRY_ATTEMPTS: int = int(os.getenv("GEMINI_DEEP_RETRY_ATTEMPTS", "1"))
    GEMINI_MAX_FUNCTION_TURNS: int = int(os.getenv("GEMINI_MAX_FUNCTION_TURNS", "5"))
```

## 6. Gemini Function Calling 技术细节

### 6.1 Gemini SDK 示例

```python
import google.generativeai as genai

# 配置 API Key
genai.configure(api_key="YOUR_API_KEY")

# 定义 Function Declaration
memory_tool = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="memory",
            description="Memory management tool",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "command": genai.protos.Schema(type=genai.protos.Type.STRING),
                    "path": genai.protos.Schema(type=genai.protos.Type.STRING),
                    # ... 其他参数
                },
                required=["command"]
            )
        )
    ]
)

# 调用模型
model = genai.GenerativeModel("gemini-2.5-pro", tools=[memory_tool])
chat = model.start_chat()

response = chat.send_message("请查看记忆中的 BTC 历史案例")

# 检查是否有 function call
if response.candidates[0].content.parts[0].function_call:
    fc = response.candidates[0].content.parts[0].function_call

    # 执行 function
    result = execute_memory_tool(fc.args)

    # 回填结果
    response = chat.send_message(
        genai.protos.Content(
            parts=[genai.protos.Part(
                function_response=genai.protos.FunctionResponse(
                    name=fc.name,
                    response={"result": result}
                )
            )]
        )
    )
```

### 6.2 关键差异对比

| 特性 | Claude Memory Tool | Gemini Function Calling |
|------|-------------------|------------------------|
| API 参数 | `tools=[{type: "custom", ...}]` | `tools=[genai.protos.Tool(...)]` |
| 请求格式 | `messages` 列表 | `contents` 列表 |
| 响应格式 | `ToolUseBlock` | `FunctionCall` |
| 回填格式 | `tool_result` | `function_response` |
| 循环控制 | `stop_reason` | 检查 `function_call` 是否存在 |

## 7. 实施步骤

### Phase 1: 基础架构（1-2 天）
1. ✅ 创建抽象基类 `DeepAnalysisEngine`
2. ✅ 重构现有 Claude 实现为 `ClaudeDeepAnalysisEngine`
3. ✅ 创建工厂类 `DeepAnalysisEngineFactory`
4. ✅ 更新 `AiSignalEngine` 集成抽象引擎

### Phase 2: Gemini 实现（2-3 天）
1. ✅ 实现 `GeminiDeepAnalysisEngine`
2. ✅ 实现 Gemini Function Calling 循环
3. ✅ 转换 Memory Tool schema 到 Gemini 格式
4. ✅ 测试 Function Calling 基本流程

### Phase 3: 配置和测试（1-2 天）
1. ✅ 添加配置参数
2. ✅ 编写单元测试
3. ✅ 端到端测试（Claude 模式）
4. ✅ 端到端测试（Gemini 模式）

### Phase 4: 文档和优化（1 天）
1. ✅ 更新 README
2. ✅ 编写切换指南
3. ✅ 性能对比分析
4. ✅ 成本对比分析

## 8. 优缺点对比

### Claude 深度分析引擎

**优点**：
- ✅ Memory Tool 原生支持（官方 SDK）
- ✅ 上下文理解能力强
- ✅ 成熟稳定（已验证）
- ✅ 支持 Context Editing（自动清理旧 Tool Use）

**缺点**：
- ❌ 成本较高（$3/MTok input, $15/MTok output）
- ❌ 请求频率限制
- ❌ 调用速度较慢（20-30s）

### Gemini 2.5 Pro 深度分析引擎

**优点**：
- ✅ 成本更低（$1.25/MTok input, $5/MTok output，仅 Claude 1/3）
- ✅ 调用速度快（10-15s）
- ✅ 与主 AI 同一生态（更好的格式一致性）
- ✅ 支持多模态（可直接分析图片）

**缺点**：
- ❌ Function Calling 需要手动实现循环
- ❌ 深度推理能力略弱于 Claude
- ❌ 缺少 Context Editing（需手动管理上下文）

## 9. 推荐配置

### 9.1 成本优先（推荐）

```bash
DEEP_ANALYSIS_ENABLED=true
DEEP_ANALYSIS_PROVIDER=gemini
GEMINI_DEEP_MODEL=gemini-2.5-pro
HIGH_VALUE_CONFIDENCE_THRESHOLD=0.75
```

**适用场景**：
- 预算有限
- 消息量大（每天 > 100 条高价值信号）
- 对延迟敏感

**预估成本**：约 Claude 的 30%

### 9.2 质量优先

```bash
DEEP_ANALYSIS_ENABLED=true
DEEP_ANALYSIS_PROVIDER=claude
CLAUDE_MODEL=claude-sonnet-4-5-20250929
HIGH_VALUE_CONFIDENCE_THRESHOLD=0.80
```

**适用场景**：
- 预算充足
- 对分析质量要求极高
- 消息量适中（每天 < 50 条高价值信号）

**预估成本**：基准

### 9.3 混合模式（未来扩展）

可在工厂类中实现智能路由：
- 简单事件（listing、delisting） → Gemini
- 复杂事件（regulation、hack） → Claude

## 10. 监控指标

建议添加以下监控：

```python
# 深度分析引擎性能指标
metrics = {
    "deep_analysis_engine": "claude | gemini",
    "deep_analysis_calls_total": 0,
    "deep_analysis_success_rate": 0.0,
    "deep_analysis_avg_latency": 0.0,
    "memory_tool_calls_total": 0,
    "function_calling_loops_avg": 0.0,
    "cost_per_analysis": 0.0
}
```

## 11. 风险和缓解

### 风险 1: Gemini Function Calling 不稳定

**缓解**：
- 设置 `max_function_turns` 防止死循环
- 添加详细日志追踪每轮调用
- 自动回退到 Claude（配置 fallback）

### 风险 2: 两种引擎输出格式不一致

**缓解**：
- 使用统一的 Prompt 模板
- 统一的 JSON Schema 验证
- 在 `_parse_response` 中标准化输出

### 风险 3: 记忆管理行为差异

**缓解**：
- `MemoryToolHandler` 是通用实现，行为一致
- 只有调用方式不同（Tool Use vs Function Call）
- 编写集成测试确保一致性

## 12. 后续优化方向

1. **自动化 A/B 测试**：同时运行两个引擎，比较结果
2. **智能路由**：根据事件类型自动选择引擎
3. **混合验证**：关键信号同时调用两个引擎交叉验证
4. **成本优化**：动态调整触发阈值（Gemini 可降低到 0.65）
5. **Gemini Context Caching**：利用 Gemini 的 context caching 降低成本

---

**总结**：该方案通过抽象层实现深度分析引擎的灵活切换，既保持了 Claude 的高质量分析能力，又提供了 Gemini 的低成本选项，用户可根据实际需求自由配置。
