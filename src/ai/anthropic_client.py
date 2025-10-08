"""Anthropic Claude client with Memory Tool support."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

try:  # pragma: no cover - optional dependency
    from anthropic import AsyncAnthropic
    from anthropic import APIStatusError
except ImportError:  # pragma: no cover - runtime fallback
    AsyncAnthropic = None  # type: ignore
    APIStatusError = Exception  # type: ignore

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
    usage: Optional[Dict[str, Any]] = None


class AnthropicClient:
    """Thin async wrapper around Anthropic Claude messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout: float = 30.0,
        max_tool_turns: int = 3,
        memory_handler: Optional[MemoryToolHandler] = None,
        context_trigger_tokens: int = 10_000,
        context_keep_tools: int = 2,
        context_clear_at_least: int = 500,
        max_output_tokens: int = 1024,
    ) -> None:
        if not api_key:
            raise AiServiceError("Anthropic API key is required")
        if AsyncAnthropic is None:
            raise AiServiceError("anthropic SDK 未安装，请先在环境中安装该依赖")

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model_name or "claude-3-5-sonnet-20240620"
        self._timeout = float(timeout)
        self._memory_handler = memory_handler
        self._max_tool_turns = max(0, int(max_tool_turns))
        self._context_trigger_tokens = int(context_trigger_tokens)
        self._context_keep_tools = int(context_keep_tools)
        self._context_clear_at_least = int(context_clear_at_least)
        self._max_output_tokens = max(256, int(max_output_tokens))

        logger.info("AnthropicClient 初始化完成 (model=%s)", self._model)

    async def generate_signal(self, messages: Sequence[Dict[str, Any]]) -> AnthropicResponse:
        """Execute prompt against Claude with optional Memory Tool loop."""

        system_prompt, convo = self._prepare_messages(messages)
        tools = self._build_tool_definitions()

        async def _call_claude(payload_messages: List[Dict[str, Any]]):
            kwargs = {
                "model": self._model,
                "system": system_prompt,
                "messages": payload_messages,
                "max_output_tokens": self._max_output_tokens,
            }
            if tools:
                kwargs["tools"] = tools
            return await self._client.messages.create(**kwargs)

        conversation = list(convo)
        usage: Dict[str, Any] = {}
        tool_round = 0

        while True:
            try:
                response = await asyncio.wait_for(_call_claude(conversation), timeout=self._timeout)
            except asyncio.TimeoutError as exc:
                raise AiServiceError("Claude 请求超时", temporary=True) from exc
            except APIStatusError as exc:  # pragma: no cover - network layer
                message, temporary = self._normalise_api_error(exc)
                raise AiServiceError(message, temporary=temporary) from exc
            except Exception as exc:  # pragma: no cover - defensive
                raise AiServiceError(str(exc)) from exc

            usage = self._merge_usage(usage, getattr(response, "usage", None))
            conversation.append(
                {
                    "role": "assistant",
                    "content": response.content,
                }
            )

            tool_use_blocks = [
                block for block in response.content if block.get("type") == "tool_use"
            ]

            if not tool_use_blocks or not tools or self._memory_handler is None:
                text = self._extract_text(response.content)
                return AnthropicResponse(text=text, usage=usage or None)

            if tool_round >= self._max_tool_turns:
                logger.warning("Claude Memory Tool 达到最大轮数，提前结束")
                text = self._extract_text(response.content)
                return AnthropicResponse(text=text, usage=usage or None)

            tool_round += 1
            for tool_block in tool_use_blocks:
                tool_result = self._execute_tool(tool_block)
                conversation.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_block.get("id"),
                                "content": json.dumps(tool_result, ensure_ascii=False),
                            }
                        ],
                    }
                )
            continue

    def _prepare_messages(
        self,
        messages: Sequence[Dict[str, Any]],
    ) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        conversation: List[Dict[str, Any]] = []

        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            text = self._normalize_content(content)

            if role == "system":
                system_parts.append(text)
                continue

            anthropic_role = "user" if role == "user" else "assistant"
            conversation.append(
                {
                    "role": anthropic_role,
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                        }
                    ],
                }
            )

        system_prompt = "\n\n".join(system_parts) if system_parts else ""
        return system_prompt, conversation

    def _build_tool_definitions(self) -> Optional[List[Dict[str, Any]]]:
        if self._memory_handler is None:
            return None
        return [
            {
                "name": "memory_tool",
                "description": (
                    "Read and write persistent knowledge via structured commands. "
                    "Supported commands: view, create, str_replace, insert, delete, rename."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": ["view", "create", "str_replace", "insert", "delete", "rename"],
                        },
                        "path": {"type": "string"},
                        "file_text": {"type": "string"},
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                        "insert_line": {"type": "integer"},
                        "insert_text": {"type": "string"},
                        "old_path": {"type": "string"},
                        "new_path": {"type": "string"},
                    },
                    "required": ["command"],
                    "additionalProperties": True,
                },
            }
        ]

    def _execute_tool(self, tool_block: Dict[str, Any]) -> Dict[str, Any]:
        if self._memory_handler is None:
            return {"success": False, "error": "memory handler not configured"}

        tool_name = tool_block.get("name")
        tool_input = tool_block.get("input", {})
        logger.debug("Claude Tool 调用: %s input=%s", tool_name, tool_input)

        if tool_name != "memory_tool":
            return {
                "success": False,
                "error": f"Unsupported tool: {tool_name}",
            }

        try:
            result = self._memory_handler.execute_tool_use(tool_input)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Memory Tool 执行失败: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

        return result

    def _extract_text(self, content: Sequence[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for block in content:
            if block.get("type") == "text":
                text = block.get("text")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()

    def _normalize_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            fragments = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    fragments.append(str(item["text"]))
                else:
                    fragments.append(str(item))
            return "\n".join(fragments)
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)
        return str(content)

    def _merge_usage(self, base: Dict[str, Any], new_usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not new_usage:
            return base
        merged = dict(base)
        for key, value in new_usage.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            else:
                merged[key] = value
        return merged

    def _normalise_api_error(self, exc: APIStatusError) -> tuple[str, bool]:
        temporary = False
        status = getattr(exc, "status_code", None)
        message = getattr(exc, "message", None) or str(exc) or "Claude 调用失败"

        if status in {408, 429, 500, 502, 503, 504}:
            temporary = True

        return message, temporary
