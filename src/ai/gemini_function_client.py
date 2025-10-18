"""Gemini Function Calling client."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, List, Optional, Sequence

try:  # pragma: no cover - optional dependency
    from google import genai
    from google.genai import errors as genai_errors
except ImportError:  # pragma: no cover - runtime fallback
    genai = None  # type: ignore
    genai_errors = None  # type: ignore

try:  # pragma: no cover - optional dependency
    import httpx
except ImportError:  # pragma: no cover - runtime fallback
    httpx = None  # type: ignore

try:
    from src.ai.gemini_key_rotator import GeminiKeyRotator
except ImportError:
    GeminiKeyRotator = None  # type: ignore


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
        api_keys: Optional[List[str]] = None,
        force_http_fallback: Optional[bool] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._model_name = model_name
        self._timeout = float(timeout)
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff_seconds))

        if not api_key and not api_keys:
            raise AiServiceError("Gemini API key is required")

        self._http_base_url = (
            base_url
            or os.getenv("GEMINI_API_BASE_URL")
            or "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        self._force_http_fallback = (
            force_http_fallback
            if force_http_fallback is not None
            else self._env_flag("GEMINI_HTTP_FORCE_HTTP1")
        )
        self._use_http_fallback = bool(self._force_http_fallback)
        self._client = None
        self._current_api_key = (api_key or "").strip()

        if genai is None and not self._use_http_fallback:
            raise AiServiceError("google-genai 未安装，请先在环境中安装该依赖")

        # Initialize key rotation if multiple keys provided
        self._key_rotator: Optional[GeminiKeyRotator] = None
        if api_keys and len(api_keys) > 1 and GeminiKeyRotator is not None:
            self._key_rotator = GeminiKeyRotator(api_keys)
            logger.info(f"🔑 启用 Gemini Function Calling API key 轮换机制，共 {len(api_keys)} 个 keys")
            self._current_api_key = self._key_rotator.get_next_key()
        elif api_key:
            self._current_api_key = api_key

        if not self._use_http_fallback:
            try:
                self._client = genai.Client(api_key=self._current_api_key)
            except Exception as exc:  # pragma: no cover - network/proxy issues
                if self._should_switch_to_http_fallback(exc):
                    logger.warning(
                        "Gemini Function Calling 客户端初始化失败，自动切换 HTTP/1.1 fallback: %s",
                        exc,
                    )
                    self._use_http_fallback = True
                    self._client = None
                else:
                    raise AiServiceError(str(exc)) from exc

        if self._use_http_fallback and httpx is None:
            raise AiServiceError("httpx 未安装，无法启用 Gemini HTTP fallback 通道")

    @staticmethod
    def _env_flag(name: str) -> bool:
        value = os.getenv(name, "")
        return value.lower() in {"1", "true", "yes", "on"}

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
        if self._use_http_fallback:
            return self._call_model_with_tools_http(messages, tools, rotate=True)

        try:
            return self._call_model_with_tools_native(messages, tools)
        except Exception as exc:
            if self._should_switch_to_http_fallback(exc):
                logger.warning(
                    "Gemini Function Calling 遇到网络/SSL 异常，切换至 HTTP/1.1 fallback 通道: %s",
                    exc,
                )
                self._use_http_fallback = True
                return self._call_model_with_tools_http(messages, tools, rotate=False)
            raise

    def _call_model_with_tools_native(
        self,
        messages: Sequence[dict[str, str]] | str,
        tools: Optional[Iterable[Any]],
    ) -> GeminiFunctionResponse:
        api_key = self._select_api_key(rotate=True)

        if self._client is None or self._key_rotator is not None:
            try:
                self._client = genai.Client(api_key=api_key)
            except Exception as exc:
                logger.warning("切换 Gemini API key 失败: %s", exc)
                if self._key_rotator is not None:
                    self._key_rotator.mark_key_failed(api_key)
                raise

        contents, system_instruction = self._convert_messages(messages)

        request_kwargs: dict[str, Any] = {
            "model": self._model_name,
            "contents": contents,
        }
        if tools:
            request_kwargs["tools"] = list(tools)
        if system_instruction:
            request_kwargs["system_instruction"] = system_instruction

        response = self._generate_with_fallback(request_kwargs)

        text = self._extract_text(response)
        function_calls = self._extract_function_calls(response)
        return GeminiFunctionResponse(text=text, function_calls=function_calls)

    def _call_model_with_tools_http(
        self,
        messages: Sequence[dict[str, str]] | str,
        tools: Optional[Iterable[Any]],
        *,
        rotate: bool,
    ) -> GeminiFunctionResponse:
        if httpx is None:
            raise AiServiceError("httpx 未安装，无法使用 Gemini HTTP fallback 通道")

        api_key = self._select_api_key(rotate=rotate)
        contents, system_instruction = self._convert_messages(messages, force_plain=True)

        payload: dict[str, Any] = {
            "model": self._model_name,
            "contents": contents,
        }
        if tools:
            payload["tools"] = self._normalise_tools(tools)
        if system_instruction:
            payload["system_instruction"] = system_instruction

        url = f"{self._http_base_url}/v1beta/models/{self._model_name}:generateContent"

        try:
            with httpx.Client(timeout=httpx.Timeout(self._timeout), http2=False) as client:
                response = client.post(
                    url,
                    params={"key": api_key},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:  # pragma: no cover - runtime fallback
            raise AiServiceError("Gemini HTTP 请求超时", temporary=True) from exc
        except httpx.HTTPStatusError as exc:  # pragma: no cover - runtime fallback
            status_code = exc.response.status_code if exc.response is not None else None
            temporary = bool(status_code and (status_code == 429 or 500 <= status_code < 600))
            message = f"Gemini HTTP 错误 (HTTP {status_code})" if status_code else "Gemini HTTP 请求失败"
            raise AiServiceError(message, temporary=temporary) from exc
        except httpx.RequestError as exc:  # pragma: no cover - runtime fallback
            raise AiServiceError(f"Gemini HTTP 请求异常: {exc}") from exc

        try:
            data = response.json()
        except json.JSONDecodeError as exc:  # pragma: no cover - unexpected response format
            raise AiServiceError("Gemini 返回了无法解析的 JSON 响应") from exc

        text = self._extract_text(data)
        function_calls = self._extract_function_calls(data)
        return GeminiFunctionResponse(text=text, function_calls=function_calls)

    def _select_api_key(self, *, rotate: bool) -> str:
        if rotate and self._key_rotator is not None:
            key = self._key_rotator.get_next_key()
        else:
            key = self._current_api_key or (
                self._key_rotator.get_next_key() if self._key_rotator is not None else ""
            )

        if not key:
            raise AiServiceError("Gemini API key is required")

        self._current_api_key = key
        return key

    def _generate_with_fallback(self, kwargs: dict[str, Any]) -> Any:
        """Call available google-genai API entrypoint."""
        if hasattr(self._client, "responses"):
            generator = getattr(self._client, "responses")
            if hasattr(generator, "generate"):
                return generator.generate(**kwargs)
        if hasattr(self._client, "models"):
            models = getattr(self._client, "models")
            if hasattr(models, "generate_content"):
                # models.generate_content expects tools and system_instruction in config parameter
                request_kwargs = {
                    "model": kwargs["model"],
                    "contents": kwargs["contents"],
                }
                config = {}
                if "tools" in kwargs:
                    config["tools"] = kwargs["tools"]
                if "system_instruction" in kwargs:
                    config["system_instruction"] = kwargs["system_instruction"]
                if config:
                    request_kwargs["config"] = config
                return models.generate_content(**request_kwargs)
        raise AiServiceError("当前 google-genai 客户端不支持所需的接口")

    def _convert_messages(
        self,
        messages: Sequence[dict[str, str]] | str,
        *,
        force_plain: bool = False,
    ) -> tuple[Any, Any]:
        """Convert messages to Gemini format, extracting system instruction.

        Returns:
            Tuple of (contents, system_instruction)
        """
        if isinstance(messages, str):
            if force_plain:
                return [{"role": "user", "parts": [{"text": messages}]}], None
            return messages, None

        Content = None
        Part = None
        use_native_types = False
        if not force_plain:
            try:
                from google.genai.types import Content as GeminiContent, Part as GeminiPart
            except ImportError:
                use_native_types = False
            else:
                Content = GeminiContent
                Part = GeminiPart
                use_native_types = True

        system_instruction: Any = None
        contents: list[Any] = []

        for message in messages:
            role = message.get("role")
            content = message.get("content")
            name = message.get("name")

            if content is None:
                continue

            if role == "system":
                if use_native_types:
                    system_instruction = content
                else:
                    system_instruction = {
                        "role": "system",
                        "parts": self._normalise_plain_parts(content),
                    }
                continue

            if role == "tool":
                # Tool responses need to use FunctionResponse format
                if use_native_types:
                    try:
                        response_data = self._parse_tool_response_payload(content)
                        part = Part.from_function_response(
                            name=name or "unknown",
                            response=response_data,
                        )
                        contents.append(
                            Content(
                                role="user",
                                parts=[part],
                            )
                        )
                    except (ImportError, AttributeError, TypeError) as exc:
                        # Fallback to text format if function response creation fails
                        logger.debug("无法创建 FunctionResponse，降级为文本格式: %s", exc)
                        contents.append(
                            Content(
                                role="user",
                                parts=[Part.from_text(text=f"Function {name}: {content}")],
                            )
                        )
                else:
                    contents.append(
                        {
                            "role": "function",
                            "parts": [
                                self._prepare_tool_response(content, name),
                            ],
                        }
                    )
            else:
                # User or model messages
                gemini_role = "model" if role == "assistant" else "user"
                if use_native_types:
                    contents.append(
                        Content(
                            role=gemini_role,
                            parts=[Part.from_text(text=content)],
                        )
                    )
                else:
                    contents.append(
                        {
                            "role": gemini_role,
                            "parts": self._normalise_plain_parts(content),
                        }
                    )

        return contents, system_instruction

    def _normalise_plain_parts(self, content: Any) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []

        if isinstance(content, list):
            for item in content:
                parts.extend(self._normalise_plain_parts(item))
            return parts

        if isinstance(content, dict):
            if "text" in content:
                parts.append({"text": str(content["text"])})
            elif "inline_data" in content:
                parts.append({"inline_data": content["inline_data"]})
            elif "functionCall" in content or "function_call" in content:
                function_call = content.get("functionCall") or content.get("function_call")
                parts.append({"functionCall": function_call})
            else:
                parts.append({"text": json.dumps(content, ensure_ascii=False)})
            return parts

        if isinstance(content, str):
            parts.append({"text": content})
        else:
            parts.append({"text": json.dumps(content, ensure_ascii=False)})
        return parts

    def _prepare_tool_response(self, content: Any, name: Optional[str]) -> dict[str, Any]:
        return {
            "functionResponse": {
                "name": name or "unknown",
                "response": self._parse_tool_response_payload(content),
            }
        }

    def _parse_tool_response_payload(self, content: Any) -> dict[str, Any]:
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return {"result": content}
            if isinstance(parsed, dict):
                return parsed
            return {"result": parsed}
        return {"result": content}

    def _normalise_tools(self, tools: Iterable[Any]) -> list[Any]:
        normalised: list[Any] = []
        for tool in tools:
            if tool is None:
                continue
            normalised.append(self._tool_to_dict(tool))
        return normalised

    def _tool_to_dict(self, tool: Any) -> Any:
        if isinstance(tool, dict):
            return {key: self._tool_value_to_serializable(value) for key, value in tool.items()}
        if isinstance(tool, list):
            return [self._tool_value_to_serializable(item) for item in tool]

        for attr in ("to_dict", "model_dump", "as_dict"):
            method = getattr(tool, attr, None)
            if callable(method):
                try:
                    data = method()
                except TypeError:
                    data = method()  # type: ignore[misc]
                if isinstance(data, dict):
                    return {key: self._tool_value_to_serializable(value) for key, value in data.items()}
                if isinstance(data, list):
                    return [self._tool_value_to_serializable(item) for item in data]
                if isinstance(data, str):
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError:
                        return {"raw": data}
                    return self._tool_to_dict(parsed)

        if hasattr(tool, "model_dump_json"):
            try:
                parsed = json.loads(tool.model_dump_json())
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return {key: self._tool_value_to_serializable(value) for key, value in parsed.items()}

        if hasattr(tool, "__dict__"):
            serializable = {
                key: self._tool_value_to_serializable(value)
                for key, value in vars(tool).items()
                if not key.startswith("_")
            }
            if serializable:
                return serializable

        return {"raw": str(tool)}

    def _tool_value_to_serializable(self, value: Any) -> Any:
        if isinstance(value, dict):
            # Normalize type names for Gemini REST API (expects lowercase)
            normalized = {}
            for key, val in value.items():
                if key == "type" and isinstance(val, str):
                    # Convert TYPE -> type, OBJECT -> object, ARRAY -> array, etc.
                    normalized[key] = val.lower()
                else:
                    normalized[key] = self._tool_value_to_serializable(val)
            return normalized
        if isinstance(value, list):
            return [self._tool_value_to_serializable(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return self._tool_to_dict(value)

    def _extract_text(self, response: Any) -> Optional[str]:
        # Skip response.text accessor to avoid SDK warnings about non-text parts
        # Instead, directly extract text from candidates.content.parts
        if isinstance(response, dict):
            candidates = response.get("candidates") or []
        else:
            candidates = getattr(response, "candidates", None)

        if not candidates:
            return None

        for candidate in candidates:
            if isinstance(candidate, dict):
                content = candidate.get("content") or {}
                parts = content.get("parts") or []
            else:
                content = getattr(candidate, "content", None)
                if not content:
                    continue
                parts = getattr(content, "parts", None)

            if not content:
                continue
            if not parts:
                continue
            text_chunks = []
            for part in parts:
                # Only extract text parts, ignoring function_call and thought_signature
                if isinstance(part, dict):
                    value = part.get("text")
                else:
                    value = getattr(part, "text", None)
                if isinstance(value, str):
                    text_chunks.append(value)
            if text_chunks:
                return "".join(text_chunks)
        return None

    def _extract_function_calls(self, response: Any) -> List[FunctionCall]:
        calls: List[FunctionCall] = []

        if isinstance(response, dict):
            direct_calls = response.get("functionCalls") or response.get("function_calls")
        else:
            direct_calls = getattr(response, "function_calls", None)

        if isinstance(direct_calls, Iterable):
            for call in direct_calls:
                if isinstance(call, dict):
                    name = call.get("name", "") or ""
                    args = self._normalize_args(call.get("args", {}))
                    call_id = call.get("id")
                else:
                    name = getattr(call, "name", "") or ""
                    args = self._normalize_args(getattr(call, "args", {}))
                    call_id = getattr(call, "id", None)
                calls.append(FunctionCall(name=name, args=args, id=call_id))

        if isinstance(response, dict):
            candidates = response.get("candidates") or []
        else:
            candidates = getattr(response, "candidates", None)

        if not candidates:
            return calls

        for candidate in candidates:
            if isinstance(candidate, dict):
                content = candidate.get("content") or {}
                parts = content.get("parts") or []
            else:
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None)
            if not parts:
                continue
            for part in parts:
                if isinstance(part, dict):
                    function_call = (
                        part.get("function_call")
                        or part.get("functionCall")
                        or part.get("function-call")
                    )
                else:
                    function_call = getattr(part, "function_call", None) or getattr(part, "functionCall", None)
                if not function_call:
                    continue
                if isinstance(function_call, dict):
                    name = function_call.get("name", "") or ""
                    args = self._normalize_args(function_call.get("args", {}))
                    call_id = function_call.get("id")
                else:
                    name = getattr(function_call, "name", "") or ""
                    args = self._normalize_args(getattr(function_call, "args", {}))
                    call_id = getattr(function_call, "id", None)
                calls.append(
                    FunctionCall(
                        name=name,
                        args=args,
                        id=call_id,
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

    def _should_switch_to_http_fallback(self, exc: Exception) -> bool:
        for candidate in self._iter_exception_chain(exc):
            if isinstance(candidate, ssl.SSLError):
                return True
            message = str(candidate).lower()
            if any(
                token in message
                for token in (
                    "wrong version number",
                    "bad record mac",
                    "decryption_failed_or_bad_record_mac",
                    "server disconnected without sending a response",
                )
            ):
                return True
        return False

    def _iter_exception_chain(self, exc: Exception) -> Iterator[Exception]:
        seen: set[int] = set()
        current: Optional[Exception] = exc
        while current is not None and id(current) not in seen:
            yield current
            seen.add(id(current))
            current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    def _normalize_exception(self, exc: Exception) -> tuple[str, bool]:
        message = str(exc).strip() or "Gemini 调用失败"
        temporary = False

        if isinstance(exc, ssl.SSLError):
            return ("Gemini SSL 握手失败，请检查代理或禁用 HTTP/2", True)

        if httpx is not None:
            if isinstance(exc, httpx.TimeoutException):
                return ("Gemini 请求超时", True)
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 429:
                    return ("Gemini 请求过于频繁，请稍后重试", True)
                if status_code and 500 <= status_code < 600:
                    return (f"Gemini 服务端错误 (HTTP {status_code})", True)
                return (f"Gemini HTTP 错误 (HTTP {status_code})", False)
            if isinstance(exc, httpx.RequestError):
                return (f"Gemini 请求异常: {exc}", False)

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
