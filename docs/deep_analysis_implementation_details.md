# 深度分析引擎实施细节

本文档针对原方案的关键实施风险，给出具体解决方案。

## 问题 1: GeminiClient 扩展 - 支持 Function Calling

### 现状
`src/ai/gemini_client.py` 只支持纯文本输出，没有 `tools` 参数和 `function_call` 处理逻辑。

### 解决方案：创建 GeminiFunctionCallingClient

```python
# src/ai/gemini_function_client.py

"""Gemini Function Calling client (google-genai SDK)"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from google import genai
    from google.genai.types import Tool, FunctionDeclaration, Content, Part
except ImportError:
    genai = None

from .gemini_client import AiServiceError

logger = logging.getLogger(__name__)


@dataclass
class FunctionCall:
    """Gemini function call representation"""
    name: str
    args: Dict[str, Any]
    id: Optional[str] = None


@dataclass
class GeminiFunctionResponse:
    """Gemini function calling response"""
    text: Optional[str] = None
    function_calls: List[FunctionCall] = None

    def __post_init__(self):
        if self.function_calls is None:
            self.function_calls = []


class GeminiFunctionCallingClient:
    """
    Gemini client with Function Calling support

    扩展自 GeminiClient，增加 tools 和 function response 处理
    """

    def __init__(
        self,
        api_key: str,
        model_name: str,
        timeout: float,
        max_retries: int = 1,
        retry_backoff_seconds: float = 1.5,
    ):
        if not api_key:
            raise AiServiceError("Gemini API key is required")
        if genai is None:
            raise AiServiceError("google-genai 未安装")

        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff_seconds))

        logger.info(f"GeminiFunctionCallingClient 初始化: model={model_name}")

    async def generate_content_with_tools(
        self,
        contents: str | List[Dict],
        tools: Optional[List[Tool]] = None
    ) -> GeminiFunctionResponse:
        """
        调用 Gemini API with tools support

        Args:
            contents: 对话内容（字符串或消息列表）
            tools: Function declarations

        Returns:
            GeminiFunctionResponse (包含文本或 function calls)
        """
        last_exc: Exception | None = None
        last_error_message = "Gemini 调用失败"
        last_error_temporary = False

        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._call_model_with_tools,
                        contents,
                        tools
                    ),
                    timeout=self._timeout,
                )
                return response

            except asyncio.TimeoutError as exc:
                last_exc = exc
                last_error_message = "Gemini 请求超时"
                last_error_temporary = True
                logger.warning(f"Gemini 超时 (尝试 {attempt + 1}/{self._max_retries + 1})")

            except Exception as exc:
                last_exc = exc
                last_error_message = str(exc)
                last_error_temporary = self._is_temporary_error(exc)
                logger.warning(
                    f"Gemini 异常 (尝试 {attempt + 1}/{self._max_retries + 1}): {exc}"
                )

            if attempt < self._max_retries:
                backoff = self._retry_backoff * (2 ** attempt)
                await asyncio.sleep(backoff)

        raise AiServiceError(last_error_message, temporary=last_error_temporary) from last_exc

    def _call_model_with_tools(
        self,
        contents: str | List[Dict],
        tools: Optional[List[Tool]]
    ) -> GeminiFunctionResponse:
        """
        同步调用 Gemini API

        Returns:
            GeminiFunctionResponse
        """
        # 构建请求参数
        config = {}
        if tools:
            config["tools"] = tools

        # 调用 API
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config
        )

        # 解析响应
        return self._parse_response(response)

    def _parse_response(self, response) -> GeminiFunctionResponse:
        """
        解析 Gemini 响应，提取文本或 function calls

        Response structure:
        - response.candidates[0].content.parts[0].text (文本响应)
        - response.candidates[0].content.parts[0].function_call (函数调用)
        """
        if not hasattr(response, "candidates") or not response.candidates:
            raise AiServiceError("Gemini 返回无候选结果")

        candidate = response.candidates[0]
        if not hasattr(candidate, "content") or not candidate.content:
            raise AiServiceError("Gemini 候选结果无内容")

        content = candidate.content
        parts = getattr(content, "parts", [])

        # 提取文本和 function calls
        text_chunks = []
        function_calls = []

        for part in parts:
            # 文本部分
            if hasattr(part, "text") and part.text:
                text_chunks.append(part.text)

            # Function call 部分
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                function_calls.append(FunctionCall(
                    name=fc.name,
                    args=dict(fc.args),  # 转换 protobuf Struct 为 dict
                    id=getattr(fc, "id", None)
                ))

        # 优先返回 function calls
        if function_calls:
            logger.debug(f"Gemini 返回 {len(function_calls)} 个 function calls")
            return GeminiFunctionResponse(
                text=None,
                function_calls=function_calls
            )

        # 否则返回文本
        final_text = "".join(text_chunks).strip()
        if not final_text:
            raise AiServiceError("Gemini 返回空内容")

        return GeminiFunctionResponse(text=final_text)

    def build_function_response_content(
        self,
        function_name: str,
        response_data: Dict[str, Any]
    ) -> Content:
        """
        构建 function response 内容（用于回填）

        Args:
            function_name: 函数名
            response_data: 函数执行结果

        Returns:
            Gemini Content 对象
        """
        from google.genai.types import FunctionResponse

        return Content(
            parts=[
                Part(
                    function_response=FunctionResponse(
                        name=function_name,
                        response=response_data
                    )
                )
            ]
        )

    def _is_temporary_error(self, exc: Exception) -> bool:
        """判断是否为暂时性错误"""
        if isinstance(exc, (ConnectionError, OSError, asyncio.TimeoutError)):
            return True

        error_msg = str(exc).upper()
        temporary_keywords = ["TIMEOUT", "UNAVAILABLE", "503", "429", "RESOURCE_EXHAUSTED"]
        return any(kw in error_msg for kw in temporary_keywords)
```

### 使用示例

```python
# 初始化客户端
client = GeminiFunctionCallingClient(
    api_key="xxx",
    model_name="gemini-2.5-pro",
    timeout=30.0
)

# 定义工具
from google.genai.types import Tool, FunctionDeclaration, Schema

memory_tool = Tool(
    function_declarations=[
        FunctionDeclaration(
            name="memory",
            description="Memory management",
            parameters=Schema(
                type="object",
                properties={
                    "command": Schema(type="string"),
                    "path": Schema(type="string"),
                },
                required=["command"]
            )
        )
    ]
)

# 调用
response = await client.generate_content_with_tools(
    contents="请查看 memories/btc_analysis.json",
    tools=[memory_tool]
)

# 检查响应
if response.function_calls:
    for fc in response.function_calls:
        print(f"Function: {fc.name}, Args: {fc.args}")
else:
    print(f"Text: {response.text}")
```

---

## 问题 2: Gemini 上下文管理 - 防止 Token 爆炸

### 问题
Gemini 没有 Claude 的 `context_management` 自动清理机制，多轮 function calling 会导致：
- `contents` 列表无限增长
- Token 数超过限制（Gemini 2.5 Pro 上限 2M tokens，但实际 32K 后性能下降）

### 解决方案：手动上下文窗口管理

```python
# src/ai/gemini_deep_engine.py (优化版)

class GeminiDeepAnalysisEngine(DeepAnalysisEngine):
    def __init__(
        self,
        client: GeminiFunctionCallingClient,
        memory_handler: MemoryToolHandler,
        max_function_turns: int = 5,
        context_window_tokens: int = 16000,  # 保留 16K token 窗口
        context_keep_turns: int = 2,         # 保留最近 2 轮对话
    ):
        self._client = client
        self._memory_handler = memory_handler
        self._max_turns = max_function_turns
        self._context_window = context_window_tokens
        self._keep_turns = context_keep_turns

    async def _run_function_calling_loop(
        self,
        initial_messages: List[Dict],
        tools: List[Tool]
    ) -> str:
        """
        Function calling 循环 with context pruning
        """
        # 初始消息
        conversation = initial_messages.copy()
        turn_count = 0

        while turn_count < self._max_turns:
            # 步骤 1: 上下文裁剪（在每次调用前）
            pruned_conversation = self._prune_context(conversation)

            logger.debug(
                f"🔧 Function calling 轮次 {turn_count + 1}: "
                f"原始消息数={len(conversation)}, 裁剪后={len(pruned_conversation)}"
            )

            # 步骤 2: 调用 Gemini
            response = await self._client.generate_content_with_tools(
                contents=pruned_conversation,
                tools=tools
            )

            # 步骤 3: 检查是否有 function calls
            if not response.function_calls:
                # 没有 function call，返回最终文本
                return response.text or ""

            # 步骤 4: 执行所有 function calls
            for fc in response.function_calls:
                logger.info(f"🔧 执行函数: {fc.name}({fc.args})")

                # 执行函数
                try:
                    result = self._memory_handler.execute_tool_use(fc.args)
                except Exception as e:
                    result = {"success": False, "error": str(e)}
                    logger.error(f"❌ 函数执行失败: {e}")

                # 将 function call 添加到对话历史
                conversation.append({
                    "role": "model",
                    "parts": [{"function_call": {"name": fc.name, "args": fc.args}}]
                })

                # 将 function response 添加到对话历史
                conversation.append({
                    "role": "function",
                    "parts": [{
                        "function_response": {
                            "name": fc.name,
                            "response": result
                        }
                    }]
                })

            turn_count += 1

        raise RuntimeError(f"Function calling 超过最大轮数 {self._max_turns}")

    def _prune_context(self, conversation: List[Dict]) -> List[Dict]:
        """
        裁剪上下文，防止 token 爆炸

        策略：
        1. 始终保留初始系统提示 + 用户消息
        2. 保留最近 N 轮函数调用（每轮包含 function_call + function_response）
        3. 删除中间的历史消息

        Example:
        [
            {role: "user", parts: [...]},           # 保留：初始消息
            {role: "model", parts: [fc1]},          # 删除：旧的 function call
            {role: "function", parts: [resp1]},     # 删除：旧的 function response
            {role: "model", parts: [fc2]},          # 保留：最近的 function call
            {role: "function", parts: [resp2]},     # 保留：最近的 function response
        ]
        """
        if len(conversation) <= 3:
            # 消息太少，不需要裁剪
            return conversation

        # 步骤 1: 提取初始消息（第一个 user 消息）
        initial_messages = []
        for msg in conversation:
            if msg.get("role") == "user":
                initial_messages.append(msg)
                break

        # 步骤 2: 提取最近的 N 轮函数调用
        # 从后往前查找 function_call + function_response 对
        recent_turns = []
        turn_pairs = []
        current_pair = []

        for msg in reversed(conversation[len(initial_messages):]):
            role = msg.get("role")

            # function response
            if role == "function":
                current_pair.insert(0, msg)

            # model function call
            elif role == "model":
                current_pair.insert(0, msg)

                # 如果 pair 完整（model + function）
                if len(current_pair) == 2:
                    turn_pairs.append(current_pair)
                    current_pair = []

                    if len(turn_pairs) >= self._keep_turns:
                        break

        # 反转回正序
        for pair in reversed(turn_pairs):
            recent_turns.extend(pair)

        # 步骤 3: 合并
        pruned = initial_messages + recent_turns

        # 步骤 4: 估算 token 数（简单估算：1 token ≈ 4 chars）
        estimated_tokens = sum(
            len(json.dumps(msg)) // 4 for msg in pruned
        )

        if estimated_tokens > self._context_window:
            logger.warning(
                f"⚠️ 上下文仍然过长（{estimated_tokens} tokens），"
                f"将减少保留轮数"
            )
            # 进一步裁剪：只保留最近 1 轮
            if len(turn_pairs) > 1:
                recent_turns = []
                for pair in turn_pairs[-1:]:
                    recent_turns.extend(pair)
                pruned = initial_messages + recent_turns

        return pruned
```

### 上下文管理对比

| 策略 | Claude | Gemini 方案 |
|------|--------|------------|
| 机制 | Context Editing API（自动） | 手动裁剪 `_prune_context` |
| 触发条件 | `input_tokens > trigger` | 每次调用前 |
| 保留内容 | 最近 N 个 tool uses | 初始消息 + 最近 N 轮 |
| Token 估算 | 精确（API 返回） | 简单估算（字符数 / 4） |

---

## 问题 3: Memory Backend 统一初始化

### 问题
原方案直接创建 `MemoryToolHandler(base_path="./memories")`，只支持 Local 模式，忽略了现有的 Supabase/Hybrid backend 配置。

### 解决方案：统一 Backend 工厂

```python
# src/memory/__init__.py (新增)

def create_memory_backend(config):
    """
    根据配置创建记忆后端

    Returns:
        LocalMemoryStore | SupabaseMemoryRepository | HybridMemoryRepository
    """
    backend_type = getattr(config, "MEMORY_BACKEND", "local").lower()

    if backend_type == "supabase":
        from .repository import SupabaseMemoryRepository

        supabase_url = getattr(config, "SUPABASE_URL", "")
        supabase_key = getattr(config, "SUPABASE_SERVICE_KEY", "")

        if not supabase_url or not supabase_key:
            logger.warning("Supabase 配置不完整，回退到 Local 模式")
            return create_local_backend(config)

        return SupabaseMemoryRepository(
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            timeout=getattr(config, "SUPABASE_TIMEOUT_SECONDS", 8.0)
        )

    elif backend_type == "hybrid":
        from .hybrid_repository import HybridMemoryRepository

        # Hybrid = Supabase (remote) + Local (fallback)
        supabase_repo = create_memory_backend(
            type("Config", (), {"MEMORY_BACKEND": "supabase", **vars(config)})()
        )
        local_store = create_local_backend(config)

        return HybridMemoryRepository(
            remote=supabase_repo,
            local=local_store
        )

    else:  # local
        return create_local_backend(config)


def create_local_backend(config):
    """创建 Local 内存存储"""
    from .local_memory_store import LocalMemoryStore

    memory_dir = getattr(config, "MEMORY_DIR", "./memories")
    return LocalMemoryStore(base_path=memory_dir)
```

### 工厂类更新

```python
# src/ai/deep_analysis_factory.py (修正版)

from ..memory import create_memory_backend, MemoryToolHandler

class DeepAnalysisEngineFactory:
    @staticmethod
    def create(provider: str, config: Any) -> Optional[DeepAnalysisEngine]:
        """
        创建深度分析引擎

        注意：不再直接传入 memory_handler，而是从 config 创建
        """
        # 统一创建 Memory Backend（支持 Local/Supabase/Hybrid）
        memory_backend = create_memory_backend(config)
        memory_handler = MemoryToolHandler(
            base_path=getattr(config, "MEMORY_DIR", "./memories"),
            backend=memory_backend
        )

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
            logger.warning(f"未知引擎: {provider}")
            return None
```

### Backend 初始化流程

```
Config
  ├─ MEMORY_BACKEND=local    → LocalMemoryStore
  ├─ MEMORY_BACKEND=supabase → SupabaseMemoryRepository
  └─ MEMORY_BACKEND=hybrid   → HybridMemoryRepository
         └─ remote: Supabase
         └─ local: LocalMemoryStore (fallback)

MemoryToolHandler(backend=...)
  └─ 统一接口，调用 backend.store() / backend.query()
```

---

## 问题 4: 统一 Prompt 和解析逻辑

### 问题
两种引擎必须使用完全相同的 prompt 模板和 JSON schema，否则输出格式会分叉。

### 解决方案：抽象基类提供统一方法

```python
# src/ai/deep_analysis_engine.py (完整版)

import json
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
        """执行深度分析"""
        pass

    @abstractmethod
    def supports_memory(self) -> bool:
        """是否支持记忆管理"""
        pass

    # ========== 统一的 Prompt 构建方法 ==========

    def build_deep_analysis_prompt(
        self,
        payload: EventPayload,
        initial_result: SignalResult
    ) -> Dict[str, str]:
        """
        构建深度分析 Prompt（Claude 和 Gemini 通用）

        Returns:
            {"system": "...", "user": "..."}
        """
        # 只保留最相关的历史记录（前2条）
        historical_ref = payload.historical_reference or {}
        historical_entries = historical_ref.get("entries", [])
        if len(historical_entries) > 2:
            historical_ref = {"entries": historical_entries[:2]}

        context = {
            "text": payload.translated_text or payload.text,
            "source": payload.source,
            "timestamp": payload.timestamp.isoformat(),
            "historical_reference": historical_ref,
            "initial_analysis": {
                "summary": initial_result.summary,
                "event_type": initial_result.event_type,
                "asset": initial_result.asset,
                "action": initial_result.action,
                "confidence": initial_result.confidence,
                "risk_flags": initial_result.risk_flags,
                "notes": initial_result.notes,
            },
        }

        context_json = json.dumps(context, ensure_ascii=False, indent=2)

        system_prompt = (
            "你是加密交易台的资深分析师，负责验证和优化 AI 初步分析结果。\n\n"
            "**任务**：\n"
            "1. 验证事件类型、资产识别、置信度是否合理\n"
            "2. 结合历史案例判断当前事件的独特性\n"
            "3. 评估风险点（流动性、监管、市场情绪）\n"
            "4. 调整置信度并给出操作建议\n\n"
            "**输出格式**：严格的 JSON，包含以下字段：\n"
            "```json\n"
            "{\n"
            '  "summary": "string (简体中文，1-2句话)",\n'
            '  "event_type": "listing|delisting|hack|regulation|funding|whale|liquidation|partnership|product_launch|governance|macro|celebrity|airdrop|other",\n'
            '  "asset": "string (大写币种代码，多个用逗号分隔，如 BTC,ETH；若无明确加密资产则为 NONE)",\n'
            '  "asset_name": "string (资产名称，简体中文)",\n'
            '  "action": "buy|sell|observe",\n'
            '  "direction": "long|short|neutral",\n'
            '  "confidence": 0.0-1.0 (保留两位小数),\n'
            '  "strength": "low|medium|high",\n'
            '  "risk_flags": ["price_volatility", "liquidity_risk", "regulation_risk", "confidence_low", "data_incomplete"],\n'
            '  "notes": "string (修正理由或疑点，简体中文)",\n'
            '  "links": ["string (相关链接)"]\n'
            "}\n"
            "```\n\n"
            "**重要**：\n"
            "- 若初步分析有误（如误判资产、置信度过高），在 notes 中说明修正理由\n"
            "- 禁止返回 Markdown、额外文本或解释，仅返回纯 JSON\n"
            "- 所有字符串字段使用简体中文"
        )

        user_prompt = (
            "请分析以下事件并返回优化后的 JSON：\n"
            f"```json\n{context_json}\n```"
        )

        return {
            "system": system_prompt,
            "user": user_prompt
        }

    # ========== 统一的响应解析方法 ==========

    def parse_response(self, raw_text: str) -> SignalResult:
        """
        解析 AI 响应（Claude 和 Gemini 通用）

        Args:
            raw_text: AI 返回的原始文本

        Returns:
            SignalResult
        """
        # 复用 AiSignalEngine 的解析逻辑
        from .signal_engine import AiSignalEngine

        # 创建临时响应对象
        class TempResponse:
            def __init__(self, text):
                self.text = text

        # 使用现有的 _parse_response 方法
        # 注意：需要将 AiSignalEngine._parse_response 改为 @staticmethod
        # 或者直接在这里实现完整的解析逻辑

        normalized_text = self._prepare_json_text(raw_text)

        try:
            data = json.loads(normalized_text)
        except json.JSONDecodeError:
            logger.warning(f"深度分析返回非 JSON: {normalized_text[:100]}")
            return SignalResult(
                status="error",
                summary="深度分析返回格式异常",
                confidence=0.0,
                error="JSON 解析失败"
            )

        # 提取字段（与 AiSignalEngine._parse_response 保持一致）
        return SignalResult(
            status="success",
            summary=str(data.get("summary", "")).strip(),
            event_type=str(data.get("event_type", "other")).lower(),
            asset=str(data.get("asset", "NONE")).upper().strip(),
            asset_names=str(data.get("asset_name", "")).strip(),
            action=str(data.get("action", "observe")).lower(),
            direction=str(data.get("direction", "neutral")).lower(),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
            strength=str(data.get("strength", "low")).lower(),
            risk_flags=data.get("risk_flags", []),
            notes=str(data.get("notes", "")).strip(),
            links=data.get("links", []),
            raw_response=raw_text
        )

    @staticmethod
    def _prepare_json_text(text: str) -> str:
        """去除 Markdown 代码块标记"""
        candidate = text.strip()
        if candidate.startswith("```") and candidate.endswith("```"):
            candidate = candidate[3:-3].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
        return candidate
```

### 子类实现（简化版）

```python
# src/ai/claude_deep_engine.py

class ClaudeDeepAnalysisEngine(DeepAnalysisEngine):
    async def analyze(self, payload, initial_result):
        # 使用基类的统一 prompt
        prompt_dict = self.build_deep_analysis_prompt(payload, initial_result)

        # 转换为 Claude messages 格式
        messages = [
            {"role": "system", "content": prompt_dict["system"]},
            {"role": "user", "content": prompt_dict["user"]}
        ]

        # 调用 Claude（自动执行 Memory Tool）
        response = await self._client.generate_signal(
            json.dumps(messages),  # Claude 期望 JSON 字符串
            max_tokens=4096
        )

        # 使用基类的统一解析
        return self.parse_response(response.text)
```

```python
# src/ai/gemini_deep_engine.py

class GeminiDeepAnalysisEngine(DeepAnalysisEngine):
    async def analyze(self, payload, initial_result):
        # 使用基类的统一 prompt
        prompt_dict = self.build_deep_analysis_prompt(payload, initial_result)

        # 转换为 Gemini contents 格式
        contents = [
            {"role": "user", "parts": [{"text": f"{prompt_dict['system']}\n\n{prompt_dict['user']}"}]}
        ]

        # 执行 Function Calling 循环
        tools = [self._build_memory_tool_schema()]
        final_text = await self._run_function_calling_loop(contents, tools)

        # 使用基类的统一解析
        return self.parse_response(final_text)
```

---

## 问题 5: 配置向后兼容和迁移策略

### 问题
新增 `DEEP_ANALYSIS_ENABLED` 和 `DEEP_ANALYSIS_PROVIDER`，如何与旧的 `CLAUDE_ENABLED` 并存？

### 解决方案：分阶段迁移策略

#### Phase 1: 兼容模式（向后兼容）

```python
# src/config.py

class Config:
    # ========== 旧配置（向后兼容）==========
    CLAUDE_ENABLED: bool = _as_bool(os.getenv("CLAUDE_ENABLED", "false"))

    # ========== 新配置（推荐）==========
    DEEP_ANALYSIS_ENABLED: bool = _as_bool(
        os.getenv("DEEP_ANALYSIS_ENABLED", os.getenv("CLAUDE_ENABLED", "false"))
        # 如果未设置 DEEP_ANALYSIS_ENABLED，回退到 CLAUDE_ENABLED
    )

    DEEP_ANALYSIS_PROVIDER: str = os.getenv(
        "DEEP_ANALYSIS_PROVIDER",
        "claude" if _as_bool(os.getenv("CLAUDE_ENABLED", "false")) else "gemini"
        # 如果 CLAUDE_ENABLED=true，默认用 claude；否则用 gemini
    ).lower()

    @classmethod
    def get_deep_analysis_config(cls) -> Dict[str, Any]:
        """
        获取深度分析配置（统一入口，处理兼容性）

        返回:
            {
                "enabled": bool,
                "provider": "claude" | "gemini"
            }
        """
        # 优先级：DEEP_ANALYSIS_ENABLED > CLAUDE_ENABLED
        enabled = cls.DEEP_ANALYSIS_ENABLED
        provider = cls.DEEP_ANALYSIS_PROVIDER

        # 兼容性警告
        if cls.CLAUDE_ENABLED and not os.getenv("DEEP_ANALYSIS_ENABLED"):
            logger.warning(
                "⚠️ CLAUDE_ENABLED 已废弃，请迁移到 DEEP_ANALYSIS_ENABLED 和 DEEP_ANALYSIS_PROVIDER"
            )

        return {
            "enabled": enabled,
            "provider": provider
        }
```

#### Phase 2: 工厂类使用统一配置

```python
# src/ai/signal_engine.py (from_config 方法)

@classmethod
def from_config(cls, config: Any) -> "AiSignalEngine":
    # ... 主 AI 初始化 ...

    # 获取深度分析配置（自动处理兼容性）
    deep_config = config.get_deep_analysis_config()

    deep_engine = None
    if deep_config["enabled"]:
        deep_engine = DeepAnalysisEngineFactory.create(
            provider=deep_config["provider"],
            config=config
        )

    return cls(
        enabled=True,
        client=client,
        threshold=getattr(config, "AI_SIGNAL_THRESHOLD", 0.0),
        semaphore=asyncio.Semaphore(concurrency),
        provider_label=provider_label,
        deep_analysis_engine=deep_engine,
        high_value_threshold=getattr(config, "HIGH_VALUE_CONFIDENCE_THRESHOLD", 0.75),
    )
```

#### Phase 3: 配置迁移指南

**旧配置（仍然有效）**：
```bash
# .env (旧版)
CLAUDE_ENABLED=true
CLAUDE_API_KEY=sk-ant-xxx
```

**新配置（推荐）**：
```bash
# .env (新版)
DEEP_ANALYSIS_ENABLED=true
DEEP_ANALYSIS_PROVIDER=gemini  # 或 claude
GEMINI_API_KEY=xxx  # 如果用 gemini
CLAUDE_API_KEY=xxx  # 如果用 claude
```

**迁移步骤**：
1. 现有用户无需修改，`CLAUDE_ENABLED=true` 会自动映射为 `DEEP_ANALYSIS_ENABLED=true` + `DEEP_ANALYSIS_PROVIDER=claude`
2. 新用户直接使用新配置
3. 在下个大版本（2.0）废弃 `CLAUDE_ENABLED`

#### Phase 4: 配置优先级表

| 配置组合 | 行为 | 警告 |
|---------|------|-----|
| `DEEP_ANALYSIS_ENABLED=true` + `DEEP_ANALYSIS_PROVIDER=gemini` | 使用 Gemini | 无 |
| `DEEP_ANALYSIS_ENABLED=true` + `DEEP_ANALYSIS_PROVIDER=claude` | 使用 Claude | 无 |
| `CLAUDE_ENABLED=true` (未设置 `DEEP_ANALYSIS_*`) | 使用 Claude | ⚠️ 旧配置警告 |
| 均未设置 | 禁用深度分析 | 无 |
| `DEEP_ANALYSIS_ENABLED=true` + `CLAUDE_ENABLED=true` (冲突) | 使用 `DEEP_ANALYSIS_*` | ⚠️ 忽略旧配置 |

---

## 总结：关键修改清单

| 问题 | 解决方案 | 修改文件 |
|------|---------|---------|
| 1. GeminiClient 扩展 | 创建 `GeminiFunctionCallingClient` | `src/ai/gemini_function_client.py` (新建) |
| 2. Token 爆炸 | 实现 `_prune_context` 方法 | `src/ai/gemini_deep_engine.py` |
| 3. Backend 兼容 | 创建 `create_memory_backend` 工厂 | `src/memory/__init__.py` |
| 4. Prompt 统一 | 在基类实现 `build_deep_analysis_prompt` 和 `parse_response` | `src/ai/deep_analysis_engine.py` |
| 5. 配置兼容 | 添加 `get_deep_analysis_config` 方法 | `src/config.py` |

---

## 下一步

建议按以下顺序实施：
1. ✅ 创建 `GeminiFunctionCallingClient`（最关键）
2. ✅ 实现统一 Prompt 和解析逻辑（避免分叉）
3. ✅ 实现 Memory Backend 统一初始化（保证现有功能不断）
4. ✅ 实现 Gemini 上下文管理（防止 token 爆炸）
5. ✅ 配置兼容性处理（平滑迁移）
