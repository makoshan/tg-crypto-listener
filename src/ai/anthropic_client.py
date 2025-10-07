"""
Anthropic Claude API client with Memory Tool support

Features:
- Memory Tool 循环（Tool Use → Execute → Feed back）
- Context Editing（自动清理旧 Tool Use 结果）
- 兼容现有 SignalResult 结构
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from anthropic import Anthropic
    from anthropic.types import Message, TextBlock, ToolUseBlock
except ImportError:
    Anthropic = None  # type: ignore
    Message = None  # type: ignore
    TextBlock = None  # type: ignore
    ToolUseBlock = None  # type: ignore

from src.memory.memory_tool_handler import MemoryToolHandler

logger = logging.getLogger(__name__)


class AiServiceError(RuntimeError):
    """Raised when the AI service call fails."""

    def __init__(self, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.temporary = temporary


@dataclass
class AnthropicResponse:
    """Structured response returned by the Anthropic client."""

    text: str
    usage: Optional[Dict[str, int]] = None  # token 使用统计
    stop_reason: Optional[str] = None


class AnthropicClient:
    """
    Claude API 客户端（支持 Memory Tool）

    核心特性:
    - Memory Tool 循环：自动执行工具调用并回填结果
    - Context Editing：自动清理旧 Tool Use 结果（节省 token）
    - 重试机制：网络错误和暂时性故障自动重试
    """

    def __init__(
        self,
        api_key: str,
        model_name: str,
        timeout: float,
        memory_handler: MemoryToolHandler,
        context_management: Optional[Dict[str, Any]] = None,
        max_retries: int = 1,
        retry_backoff_seconds: float = 1.5,
        max_tool_turns: int = 5,  # 防止 Tool Use 死循环
        *,
        context_trigger_tokens: Optional[int] = None,
        context_keep_tools: Optional[int] = None,
        context_clear_at_least: Optional[int] = None
    ) -> None:
        """
        初始化 Anthropic 客户端

        Args:
            api_key: Anthropic API key
            model_name: 模型名称（如 claude-sonnet-4-5-20250929）
            timeout: 请求超时时间（秒）
            memory_handler: Memory Tool 处理器
            context_management: Context Editing 配置
            max_retries: 最大重试次数
            retry_backoff_seconds: 重试退避时间（秒）
            max_tool_turns: 最大工具调用轮数
        """
        if not api_key:
            raise AiServiceError("Anthropic API key is required")

        if Anthropic is None:
            raise AiServiceError(
                "anthropic 未安装，请先在环境中安装该依赖: pip install anthropic"
            )

        self._client = Anthropic(api_key=api_key)
        self._model_name = model_name
        self._timeout = timeout
        self._memory_handler = memory_handler
        if context_management is None:
            self._context_management = self._default_context_config(
                trigger_tokens=context_trigger_tokens,
                keep_tools=context_keep_tools,
                clear_at_least=context_clear_at_least,
            )
        else:
            self._context_management = context_management
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff_seconds))
        self._max_tool_turns = max_tool_turns

        logger.info(
            f"AnthropicClient 初始化: {model_name} "
            f"(timeout={timeout}s, max_tool_turns={max_tool_turns})"
        )

    def _default_context_config(
        self,
        *,
        trigger_tokens: Optional[int] = None,
        keep_tools: Optional[int] = None,
        clear_at_least: Optional[int] = None,
    ) -> Dict[str, Any]:
        """默认 Context Editing 配置"""
        return {
            "edits": [
                {
                    "type": "clear_tool_uses_20250919",
                    "trigger": {
                        "type": "input_tokens",
                        "value": int(trigger_tokens) if trigger_tokens is not None else 10000,
                    },
                    "keep": {
                        "type": "tool_uses",
                        "value": int(keep_tools) if keep_tools is not None else 2,
                    },
                    "clear_at_least": {
                        "type": "input_tokens",
                        "value": int(clear_at_least) if clear_at_least is not None else 500,
                    },
                }
            ]
        }

    async def generate_signal(
        self,
        prompt: str | List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096
    ) -> AnthropicResponse:
        """
        执行信号分析（支持 Memory Tool 循环）

        Args:
            prompt: 提示词（字符串或 OpenAI 风格的 messages）
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数

        Returns:
            AnthropicResponse
        """
        # 转换为 Anthropic messages 格式
        messages = self._convert_to_anthropic_messages(prompt)

        # 执行 Memory Tool 循环
        last_exc: Exception | None = None
        last_error_message = "Claude 调用失败"
        last_error_temporary = False

        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._run_tool_loop,
                        messages,
                        system_prompt,
                        max_tokens
                    ),
                    timeout=self._timeout
                )
                return response

            except asyncio.TimeoutError as exc:
                last_exc = exc
                last_error_message = "Claude 请求超时"
                last_error_temporary = True
                logger.warning(
                    f"Claude 请求超时 (attempt {attempt + 1}/{self._max_retries + 1})"
                )

            except Exception as exc:
                last_exc = exc
                last_error_message, last_error_temporary = self._normalize_exception(exc)
                logger.warning(
                    f"Claude 调用异常 (attempt {attempt + 1}/{self._max_retries + 1}): {last_error_message}"
                )

            # 重试退避
            if attempt < self._max_retries and self._retry_backoff > 0:
                backoff = self._retry_backoff * (2 ** attempt)
                logger.debug(f"Claude 将在 {backoff:.2f} 秒后重试")
                await asyncio.sleep(backoff)

        raise AiServiceError(last_error_message, temporary=last_error_temporary) from last_exc

    def _run_tool_loop(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str],
        max_tokens: int
    ) -> AnthropicResponse:
        """
        执行 Memory Tool 循环

        流程:
        1. 调用 Claude API（带 Memory Tool）
        2. 检查 response.content 中是否有 tool_use
        3. 如果有，执行 MemoryToolHandler
        4. 将 tool_result 回填到 messages
        5. 继续调用 API（重复 1-4）
        6. 直到没有 tool_use 或达到最大轮数

        Returns:
            AnthropicResponse
        """
        tool_turn_count = 0

        # 定义 Memory Tool 的完整 schema
        memory_tool = {
            "type": "memory_20250818",
            "name": "memory",
            "description": "Memory management tool for storing, retrieving, and modifying information. Supports viewing, creating, editing, and deleting files in the memory storage.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["view", "create", "str_replace", "insert", "delete", "rename"],
                        "description": "The command to execute: view (read file/dir), create (write file), str_replace (replace text), insert (insert at line), delete (remove file/dir), rename (move/rename)"
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path (relative to memory root)"
                    },
                    "file_text": {
                        "type": "string",
                        "description": "Content to write when using 'create' command"
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Text to replace when using 'str_replace' command"
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement text when using 'str_replace' command"
                    },
                    "insert_line": {
                        "type": "integer",
                        "description": "Line number to insert at when using 'insert' command (1-indexed)"
                    },
                    "insert_text": {
                        "type": "string",
                        "description": "Text to insert when using 'insert' command"
                    },
                    "old_path": {
                        "type": "string",
                        "description": "Source path when using 'rename' command"
                    },
                    "new_path": {
                        "type": "string",
                        "description": "Destination path when using 'rename' command"
                    }
                },
                "required": ["command"]
            }
        }

        while tool_turn_count < self._max_tool_turns:
            # 调用 Claude API
            logger.info(f"🤖 Claude API 调用开始 (轮次: {tool_turn_count + 1}, model: {self._model_name})")
            response: Message = self._client.messages.create(
                model=self._model_name,
                max_tokens=max_tokens,
                system=system_prompt or "You are a helpful AI assistant.",
                messages=messages,
                tools=[memory_tool],
                context_management=self._context_management
            )
            logger.info(
                f"✅ Claude API 响应完成 (input_tokens: {response.usage.input_tokens}, "
                f"output_tokens: {response.usage.output_tokens}, stop_reason: {response.stop_reason})"
            )

            # 提取 text blocks 和 tool use blocks
            text_blocks = []
            tool_uses = []

            for block in response.content:
                if isinstance(block, TextBlock):
                    text_blocks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_uses.append(block)

            # 如果没有 tool use，说明对话完成
            if not tool_uses:
                final_text = "\n".join(text_blocks).strip()

                return AnthropicResponse(
                    text=final_text,
                    usage={
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens
                    },
                    stop_reason=response.stop_reason
                )

            # 执行 tool uses
            tool_turn_count += 1
            logger.debug(
                f"Tool Use 轮次 {tool_turn_count}/{self._max_tool_turns}: "
                f"{len(tool_uses)} 个工具调用"
            )

            # 将 assistant 的响应（含 tool_use）添加到 messages
            messages.append({
                "role": "assistant",
                "content": response.content  # 保持原始格式（包含 TextBlock 和 ToolUseBlock）
            })

            # 执行工具并构造 tool_result
            tool_results = []
            for tool_use in tool_uses:
                try:
                    result = self._memory_handler.execute_tool_use(tool_use.input)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": json.dumps(result, ensure_ascii=False)
                    })

                    logger.debug(
                        f"Memory Tool 执行: {tool_use.input.get('command')} "
                        f"{tool_use.input.get('path', '')[:50]}"
                    )

                except Exception as e:
                    logger.error(f"Memory Tool 执行失败: {e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False),
                        "is_error": True
                    })

            # 将 tool_result 添加到 messages
            messages.append({
                "role": "user",
                "content": tool_results
            })

        # 达到最大轮数，返回警告
        logger.warning(
            f"Tool Use 循环达到最大轮数 {self._max_tool_turns}，强制终止"
        )

        return AnthropicResponse(
            text="[Error: Tool Use 循环超过最大轮数]",
            stop_reason="max_tool_turns_exceeded"
        )

    def _convert_to_anthropic_messages(
        self,
        prompt: str | List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        """
        转换为 Anthropic messages 格式

        Args:
            prompt: 字符串或 OpenAI 风格的 messages

        Returns:
            Anthropic messages
        """
        if isinstance(prompt, str):
            return [{"role": "user", "content": prompt}]

        if isinstance(prompt, list) and prompt and isinstance(prompt[0], dict):
            # OpenAI 风格: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
            # Anthropic 风格: system 提取为独立参数，messages 只保留 user/assistant

            messages = []
            for msg in prompt:
                role = msg.get("role")
                content = msg.get("content")

                # 跳过 system（在 generate_signal 中单独处理）
                if role == "system":
                    continue

                # 转换 user/assistant
                if role in ("user", "assistant"):
                    messages.append({
                        "role": role,
                        "content": content
                    })

            return messages

        # 默认：包装为 user message
        return [{"role": "user", "content": str(prompt)}]

    def _normalize_exception(self, exc: Exception) -> tuple[str, bool]:
        """返回人类可读的错误消息和是否为暂时性错误"""

        message = str(exc).strip() or "Claude 调用失败"
        temporary = False

        # Anthropic SDK 异常处理
        # 参考: https://github.com/anthropics/anthropic-sdk-python
        exc_type = type(exc).__name__

        if "RateLimitError" in exc_type or "429" in message:
            return ("Claude 请求过于频繁，请稍后重试", True)

        if "APIConnectionError" in exc_type or "InternalServerError" in exc_type:
            return ("Claude 服务暂时不可用，请稍后重试", True)

        if "APITimeoutError" in exc_type:
            return ("Claude 请求超时", True)

        if "503" in message or "UNAVAILABLE" in message.upper():
            return ("Claude 服务暂时不可用，请稍后重试", True)

        if isinstance(exc, (ConnectionError, OSError)):
            return ("Claude 网络连接异常，请检查网络后重试", True)

        return (message, temporary)
