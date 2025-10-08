"""Gemini Function Calling client."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

try:  # pragma: no cover - optional dependency
    from google import genai
    from google.genai import errors as genai_errors
except ImportError:  # pragma: no cover - runtime fallback
    genai = None  # type: ignore
    genai_errors = None  # type: ignore


class AiServiceError(RuntimeError):
    """Raised when the AI service call fails."""

    def __init__(self, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.temporary = temporary


@dataclass
class FunctionCall:
    """Gemini function call representation."""

    name: str
    args: dict[str, Any]
    id: Optional[str] = None


@dataclass
class GeminiFunctionResponse:
    """Structured response for Gemini function calling."""

    text: Optional[str] = None
    function_calls: List[FunctionCall] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.function_calls is None:
            self.function_calls = []


logger = logging.getLogger(__name__)


class GeminiFunctionCallingClient:
    """Wrapper around google-genai client supporting function calling."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        timeout: float,
        max_retries: int = 1,
        retry_backoff_seconds: float = 1.5,
    ) -> None:
        if not api_key:
            raise AiServiceError("Gemini API key is required")
        if genai is None:
            raise AiServiceError("google-genai 未安装，请先在环境中安装该依赖")

        try:
            self._client = genai.Client(api_key=api_key)
        except Exception as exc:  # pragma: no cover - network/proxy issues
            raise AiServiceError(str(exc)) from exc

        self._model_name = model_name
        self._timeout = float(timeout)
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff_seconds))

    async def generate_content_with_tools(
        self,
        messages: Sequence[dict[str, str]] | str,
        *,
        tools: Optional[Iterable[Any]] = None,
    ) -> GeminiFunctionResponse:
        """Call Gemini model with function calling support."""

        last_exc: Exception | None = None
        last_error_message = "Gemini 调用失败"
        last_error_temporary = False

        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._call_model_with_tools,
                        messages,
                        tools,
                    ),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError as exc:
                last_exc = exc
                last_error_message = "Gemini 请求超时"
                last_error_temporary = True
                logger.warning(
                    "Gemini Function Calling 超时 (attempt %s/%s)",
                    attempt + 1,
                    self._max_retries + 1,
                )
            except Exception as exc:  # pragma: no cover - network errors
                last_exc = exc
                last_error_message, last_error_temporary = self._normalize_exception(exc)
                logger.warning(
                    "Gemini Function Calling 异常 (attempt %s/%s): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    last_error_message,
                )
                logger.debug("Gemini 异常详情", exc_info=True)
            else:
                return response

            if attempt < self._max_retries and self._retry_backoff > 0:
                backoff = self._retry_backoff * (2 ** attempt)
                await asyncio.sleep(backoff)

        raise AiServiceError(last_error_message, temporary=last_error_temporary) from last_exc

    def _call_model_with_tools(
        self,
        messages: Sequence[dict[str, str]] | str,
        tools: Optional[Iterable[Any]],
    ) -> GeminiFunctionResponse:
        contents = self._convert_messages(messages)

        request_kwargs: dict[str, Any] = {
            "model": self._model_name,
            "contents": contents,
        }
        if tools:
            request_kwargs["tools"] = list(tools)

        response = self._generate_with_fallback(request_kwargs)

        text = self._extract_text(response)
        function_calls = self._extract_function_calls(response)
        return GeminiFunctionResponse(text=text, function_calls=function_calls)

    def _generate_with_fallback(self, kwargs: dict[str, Any]) -> Any:
        """Call available google-genai API entrypoint."""
        if hasattr(self._client, "responses"):
            generator = getattr(self._client, "responses")
            if hasattr(generator, "generate"):
                return generator.generate(**kwargs)
        if hasattr(self._client, "models"):
            models = getattr(self._client, "models")
            if hasattr(models, "generate_content"):
                return models.generate_content(**kwargs)
        raise AiServiceError("当前 google-genai 客户端不支持所需的接口")

    def _convert_messages(self, messages: Sequence[dict[str, str]] | str) -> Any:
        if isinstance(messages, str):
            return messages
        parts: list[Any] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if content is None:
                continue
            if role == "user":
                parts.append({"role": "user", "parts": [content]})
            elif role == "system":
                parts.append({"role": "system", "parts": [content]})
            elif role == "tool":
                parts.append({"role": "tool", "parts": [content]})
            else:
                parts.append({"role": role or "user", "parts": [content]})
        return parts

    def _extract_text(self, response: Any) -> Optional[str]:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text

        candidates = getattr(response, "candidates", None)
        if not candidates:
            return None

        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None)
            if not parts:
                continue
            text_chunks = []
            for part in parts:
                value = getattr(part, "text", None)
                if isinstance(value, str):
                    text_chunks.append(value)
            if text_chunks:
                return "".join(text_chunks)
        return None

    def _extract_function_calls(self, response: Any) -> List[FunctionCall]:
        calls: List[FunctionCall] = []

        direct_calls = getattr(response, "function_calls", None)
        if isinstance(direct_calls, Iterable):
            for call in direct_calls:
                name = getattr(call, "name", "") or ""
                args = self._normalize_args(getattr(call, "args", {}))
                calls.append(FunctionCall(name=name, args=args, id=getattr(call, "id", None)))

        candidates = getattr(response, "candidates", None)
        if not candidates:
            return calls

        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None)
            if not parts:
                continue
            for part in parts:
                function_call = getattr(part, "function_call", None) or getattr(part, "functionCall", None)
                if not function_call:
                    continue
                name = getattr(function_call, "name", "") or ""
                args = self._normalize_args(getattr(function_call, "args", {}))
                calls.append(
                    FunctionCall(
                        name=name,
                        args=args,
                        id=getattr(function_call, "id", None),
                    )
                )
        return calls

    def _normalize_args(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return {str(key): val for key, val in value.items()}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {"value": value}
            if isinstance(parsed, dict):
                return {str(key): val for key, val in parsed.items()}
        return {}

    def _normalize_exception(self, exc: Exception) -> tuple[str, bool]:
        message = str(exc).strip() or "Gemini 调用失败"
        temporary = False

        if genai_errors is not None and isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            status = (getattr(exc, "status", "") or "").upper()
            numeric_code: int | None
            try:
                numeric_code = int(code) if code is not None else None
            except (TypeError, ValueError):
                numeric_code = None

            if isinstance(exc, genai_errors.ServerError):
                if status == "UNAVAILABLE" or numeric_code == 503:
                    return ("Gemini 服务暂时不可用，请稍后重试", True)
                if numeric_code is not None and 500 <= numeric_code < 600:
                    return (f"Gemini 服务端错误 (HTTP {code})", True)

            if numeric_code == 429 or status == "RESOURCE_EXHAUSTED":
                return ("Gemini 请求过于频繁，请稍后重试", True)

        return (message, temporary)
