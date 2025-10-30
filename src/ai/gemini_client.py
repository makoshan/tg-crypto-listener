"""Gemini API client wrapper using google-genai."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional

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
class GeminiContentPart:
    """Normalized Gemini content part."""

    type: str
    text: str | None = None
    data: dict[str, Any] | None = None


@dataclass
class GeminiResponse:
    """Structured response returned by the Gemini client."""

    text: str
    parts: list[GeminiContentPart] = field(default_factory=list)


logger = logging.getLogger(__name__)


class GeminiClient:
    """Thin wrapper around google-genai models."""

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
        logger.info(
            "GeminiClient.__init__ called with api_keys=%s (type=%s)",
            "provided" if api_keys else "None",
            type(api_keys).__name__ if api_keys else "NoneType",
        )
        self._model_name = model_name
        self._timeout = float(timeout)
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff_seconds))
        self._client = None
        self._client_api_key: Optional[str] = None

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

        # Initialize key rotation if multiple keys provided
        self._key_rotator: Optional[GeminiKeyRotator] = None
        logger.info(
            "🔍 Key rotation check: api_keys_provided=%s (count=%d), GeminiKeyRotator=%s",
            bool(api_keys),
            len(api_keys) if api_keys else 0,
            "available" if GeminiKeyRotator is not None else "NOT_AVAILABLE",
        )
        if api_keys and len(api_keys) > 1 and GeminiKeyRotator is not None:
            self._key_rotator = GeminiKeyRotator(api_keys)
            logger.info(f"🔑 启用 Gemini API key 轮换机制，共 {len(api_keys)} 个 keys")
            self._current_api_key = self._key_rotator.get_next_key()
        else:
            self._current_api_key = (api_key or "").strip()
            if api_keys and len(api_keys) > 1:
                logger.warning(
                    "⚠️ 配置了 %d 个 API keys 但轮换器未初始化 (GeminiKeyRotator=%s)",
                    len(api_keys),
                    "None" if GeminiKeyRotator is None else "available",
                )

        if not self._current_api_key:
            raise AiServiceError("Gemini API key is required")

        if not self._use_http_fallback:
            if genai is None:
                raise AiServiceError("google-genai 未安装，请先在环境中安装该依赖")
            try:
                self._client = genai.Client(api_key=self._current_api_key)
            except Exception as exc:  # pragma: no cover - network/proxy issues
                if self._should_switch_to_http_fallback(exc):
                    logger.warning(
                        "Gemini 客户端初始化失败，切换至 HTTP/1.1 fallback 通道: %s",
                        exc,
                    )
                    self._use_http_fallback = True
                    self._client = None
                else:
                    raise AiServiceError(str(exc)) from exc
            else:
                self._client_api_key = self._current_api_key

        if self._use_http_fallback and httpx is None:
            raise AiServiceError("httpx 未安装，无法启用 Gemini HTTP fallback 通道")

    async def generate_signal(self, prompt: str | list, images: list[dict] = None) -> GeminiResponse:
        """Execute prompt against Gemini and return plain text.

        Args:
            prompt: Text prompt or list of content parts
            images: Optional list of image dicts with base64 data
        """
        last_exc: Exception | None = None
        last_error_message = "Gemini 调用失败"
        last_error_temporary = False

        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(self._call_model, prompt, images),
                    timeout=self._timeout,
                )
            except asyncio.CancelledError:  # propagate cooperative cancellation
                raise
            except asyncio.TimeoutError as exc:
                last_exc = exc
                last_error_message = "Gemini 请求超时"
                last_error_temporary = True
                logger.warning(
                    "Gemini 请求超时 (attempt %s/%s)",
                    attempt + 1,
                    self._max_retries + 1,
                )
                # 超时也是暂时性错误,轮换 API key
                if self._key_rotator is not None:
                    old_key_preview = self._current_api_key[:8] if self._current_api_key else "unknown"
                    old_key_index = self._key_rotator.get_key_index(self._current_api_key) if self._current_api_key else None
                    
                    self._current_api_key = self._key_rotator.get_next_key()
                    new_key_preview = self._current_api_key[:8]
                    new_key_index = self._key_rotator.get_key_index(self._current_api_key)
                    
                    logger.info(
                        "🔄 检测到超时错误，轮换 API key: %s[key[%s]] → %s[key[%d/%d]]",
                        old_key_preview,
                        old_key_index if old_key_index else "?",
                        new_key_preview,
                        new_key_index,
                        self._key_rotator.key_count,
                    )
                    # 标记失败的 key
                    if self._client_api_key:
                        self._key_rotator.mark_key_failed(self._client_api_key)
                    # 强制下次调用重建客户端
                    self._client = None
                    self._client_api_key = None
            except Exception as exc:  # pragma: no cover - broader network errors
                last_exc = exc
                last_error_message, last_error_temporary = self._normalize_exception(exc)
                logger.warning(
                    "Gemini 调用异常 (attempt %s/%s): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    last_error_message,
                )
                debug_hint = "Gemini 暂时性异常详情" if last_error_temporary else "Gemini 非暂时性异常详情"
                logger.debug(debug_hint, exc_info=True)

                # 如果是暂时性错误且配置了多个 key,立即轮换到下一个 key
                if last_error_temporary and self._key_rotator is not None:
                    old_key_preview = self._current_api_key[:8] if self._current_api_key else "unknown"
                    old_key_index = self._key_rotator.get_key_index(self._current_api_key) if self._current_api_key else None
                    
                    self._current_api_key = self._key_rotator.get_next_key()
                    new_key_preview = self._current_api_key[:8]
                    new_key_index = self._key_rotator.get_key_index(self._current_api_key)
                    
                    logger.info(
                        "🔄 检测到暂时性错误，轮换 API key: %s[key[%s]] → %s[key[%d/%d]]",
                        old_key_preview,
                        old_key_index if old_key_index else "?",
                        new_key_preview,
                        new_key_index,
                        self._key_rotator.key_count,
                    )
                    # 标记失败的 key
                    if self._client_api_key:
                        self._key_rotator.mark_key_failed(self._client_api_key)
                    # 强制下次调用重建客户端
                    self._client = None
                    self._client_api_key = None
            else:
                if not response.text and not response.parts:
                    raise AiServiceError("Gemini 返回空响应")
                # 成功调用后记录使用的 key 信息
                if self._key_rotator is not None and self._current_api_key:
                    key_index = self._key_rotator.get_key_index(self._current_api_key)
                    usage_stats = self._key_rotator.get_usage_stats()
                    if key_index:
                        logger.debug(
                            "✅ Gemini 调用成功，使用 key[%d/%d] %s... (总计使用: %s)",
                            key_index,
                            self._key_rotator.key_count,
                            self._current_api_key[:8],
                            usage_stats.get(key_index - 1, 0),
                        )
                return response

            if attempt < self._max_retries and self._retry_backoff > 0:
                backoff = self._retry_backoff * (2 ** attempt)
                logger.debug(
                    "Gemini 将在 %.2f 秒后重试 (attempt %s/%s)",
                    backoff,
                    attempt + 1,
                    self._max_retries + 1,
                )
                await asyncio.sleep(backoff)

        raise AiServiceError(last_error_message, temporary=last_error_temporary) from last_exc

    def _call_model(self, prompt: str | list, images: list[dict] = None) -> GeminiResponse:
        if self._use_http_fallback:
            return self._call_model_http(prompt, images, rotate=True)

        try:
            return self._call_model_native(prompt, images)
        except Exception as exc:
            if self._should_switch_to_http_fallback(exc):
                logger.warning(
                    "Gemini 遇到网络/SSL 异常，切换至 HTTP/1.1 fallback 通道: %s",
                    exc,
                )
                self._use_http_fallback = True
                return self._call_model_http(prompt, images, rotate=False)
            raise

    def _call_model_native(self, prompt: str | list, images: list[dict] | None) -> GeminiResponse:
        if genai is None:
            raise AiServiceError("google-genai 未安装，请先在环境中安装该依赖")

        api_key = self._select_api_key(rotate=True)

        if self._client is None or self._client_api_key != api_key:
            old_key_preview = self._client_api_key[:8] if self._client_api_key else None
            old_key_index = None
            if old_key_preview and self._key_rotator is not None:
                old_key_index = self._key_rotator.get_key_index(self._client_api_key)
            
            try:
                self._client = genai.Client(api_key=api_key)
            except Exception as exc:
                key_index = self._key_rotator.get_key_index(api_key) if self._key_rotator is not None else None
                logger.warning(
                    "切换 Gemini API key 失败: %s[key[%s]] → %s[key[%s]]: %s",
                    old_key_preview or "none",
                    old_key_index if old_key_index else "?",
                    api_key[:8],
                    key_index if key_index else "?",
                    exc,
                )
                if self._key_rotator is not None:
                    self._key_rotator.mark_key_failed(api_key)
                raise
            else:
                self._client_api_key = api_key
                key_index = self._key_rotator.get_key_index(api_key) if self._key_rotator is not None else None
                if key_index:
                    logger.info(
                        "✅ Gemini API key 切换成功: %s[key[%s]] → %s[key[%d/%d]]",
                        old_key_preview or "none",
                        old_key_index if old_key_index else "?",
                        api_key[:8],
                        key_index,
                        self._key_rotator.key_count if self._key_rotator else 1,
                    )

        contents = self._prepare_contents_native(prompt, images)

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
        )
        
        # 成功调用后记录使用的 key 信息
        if self._key_rotator is not None and self._client_api_key:
            key_index = self._key_rotator.get_key_index(self._client_api_key)
            usage_stats = self._key_rotator.get_usage_stats()
            if key_index:
                logger.debug(
                    "✅ Gemini Native 调用成功，使用 key[%d/%d] %s... (总计使用: %s)",
                    key_index,
                    self._key_rotator.key_count,
                    self._client_api_key[:8],
                    usage_stats.get(key_index - 1, 0),
                )
        
        return self._build_response(response)

    def _call_model_http(
        self,
        prompt: str | list,
        images: list[dict] | None,
        *,
        rotate: bool,
    ) -> GeminiResponse:
        if httpx is None:
            raise AiServiceError("httpx 未安装，无法使用 Gemini HTTP fallback 通道")

        api_key = self._select_api_key(rotate=rotate)
        contents = self._prepare_contents_http(prompt, images)
        payload: dict[str, Any] = {
            "model": self._model_name,
            "contents": contents,
        }

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

        # 成功调用后记录使用的 key 信息
        if self._key_rotator is not None and api_key:
            key_index = self._key_rotator.get_key_index(api_key)
            usage_stats = self._key_rotator.get_usage_stats()
            if key_index:
                logger.debug(
                    "✅ Gemini HTTP Fallback 调用成功，使用 key[%d/%d] %s... (总计使用: %s)",
                    key_index,
                    self._key_rotator.key_count,
                    api_key[:8],
                    usage_stats.get(key_index - 1, 0),
                )

        return self._build_response(data)

    def _prepare_contents_native(self, prompt: str | list, images: list[dict] | None) -> Any:
        if isinstance(prompt, list) and prompt and isinstance(prompt[0], dict) and "role" in prompt[0]:
            text_parts = []
            for msg in prompt:
                if isinstance(msg, dict) and "content" in msg:
                    text_parts.append(msg["content"])
            prompt_text = "\n\n".join(text_parts)
        elif isinstance(prompt, str):
            prompt_text = prompt
        else:
            prompt_text = prompt

        if images:
            contents: list[Any] = [prompt_text]
            for img in images:
                base64_data = img.get("base64")
                mime_type = img.get("mime_type")
                if base64_data and mime_type:
                    contents.append(
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64_data,
                            }
                        }
                    )
            return contents
        return prompt_text

    def _prepare_contents_http(self, prompt: str | list, images: list[dict] | None) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []

        if isinstance(prompt, list):
            for message in prompt:
                if not isinstance(message, dict):
                    continue
                role = message.get("role") or "user"
                content = message.get("content")
                parts = self._normalise_plain_parts(content)
                if not parts:
                    continue
                contents.append(
                    {
                        "role": "model" if role == "assistant" else "user",
                        "parts": parts,
                    }
                )
        elif isinstance(prompt, str):
            contents.append(
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            )
        elif isinstance(prompt, dict):
            role = prompt.get("role", "user")
            parts = prompt.get("parts")
            if isinstance(parts, list):
                contents.append({"role": role, "parts": parts})
        else:
            parts = self._normalise_plain_parts(prompt)
            if parts:
                contents.append({"role": "user", "parts": parts})

        if not contents:
            contents.append(
                {
                    "role": "user",
                    "parts": [{"text": ""}],
                }
            )

        if images:
            inline_parts: list[dict[str, Any]] = []
            for img in images:
                base64_data = img.get("base64")
                mime_type = img.get("mime_type")
                if base64_data and mime_type:
                    inline_parts.append(
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64_data,
                            }
                        }
                    )
            if inline_parts:
                target = contents[-1]
                if target.get("role") != "user":
                    target = {"role": "user", "parts": []}
                    contents.append(target)
                target_parts = target.setdefault("parts", [])
                target_parts.extend(inline_parts)

        return contents

    def _normalise_plain_parts(self, content: Any) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []

        if content is None:
            return parts

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
            try:
                parts.append({"text": json.dumps(content, ensure_ascii=False)})
            except TypeError:
                parts.append({"text": str(content)})
        return parts

    def _build_response(self, payload: Any) -> GeminiResponse:
        parts = self._extract_parts(payload)
        text = self._combine_text_from_parts(parts)

        if not text:
            if isinstance(payload, dict) and payload.get("text"):
                text = str(payload["text"])
            else:
                direct_text = getattr(payload, "text", None)
                if direct_text:
                    text = str(direct_text)

        return GeminiResponse(text=text or "", parts=parts)

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

    @staticmethod
    def _env_flag(name: str) -> bool:
        value = os.getenv(name, "")
        return value.lower() in {"1", "true", "yes", "on"}

    def _extract_parts(self, response: Any) -> list[GeminiContentPart]:
        if isinstance(response, dict):
            candidates = response.get("candidates") or []
        else:
            candidates = getattr(response, "candidates", None) or []
        normalized_parts: list[GeminiContentPart] = []

        for candidate in candidates:
            if isinstance(candidate, dict):
                content = candidate.get("content")
                parts = (content or {}).get("parts") if isinstance(content, dict) else []
            else:
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None)
            if not content:
                continue
            parts_iterable = parts or []
            for part in parts_iterable:
                normalized_parts.append(self._normalize_part(part))
            if normalized_parts:
                break  # 优先取第一候选项，其余通常重复
        return normalized_parts

    def _normalize_part(self, part: Any) -> GeminiContentPart:
        part_type = getattr(part, "type_", None) or getattr(part, "type", None)
        text_value = getattr(part, "text", None)

        raw_payload: dict[str, Any] | None = None

        if isinstance(part, dict):
            raw_payload = {k: v for k, v in part.items() if k != "text"}
            if text_value is None and "text" in part:
                text_value = part.get("text")
            part_type = part_type or part.get("type") or part.get("kind")
        else:
            for attr in (
                "function_call",
                "inline_data",
                "file_data",
                "executable_code",
                "code_execution_result",
                "thought",
                "thought_signature",
                "json",
                "parsed_json",
                "metadata",
            ):
                if hasattr(part, attr):
                    value = getattr(part, attr)
                    if value is not None:
                        raw_payload = raw_payload or {}
                        raw_payload[attr] = self._safe_to_dict(value)

            if raw_payload is None and hasattr(part, "to_dict"):
                try:
                    raw_payload = self._safe_to_dict(part.to_dict())
                except Exception:
                    raw_payload = None

            if raw_payload is None:
                extracted: dict[str, Any] = {}
                for attr in dir(part):
                    if attr.startswith("_") or attr in {"text", "type", "type_"}:
                        continue
                    value = getattr(part, attr)
                    if callable(value):
                        continue
                    extracted[attr] = self._safe_to_dict(value)
                raw_payload = extracted or None

        sanitized_payload = self._sanitize_part_data(raw_payload)

        if not part_type and sanitized_payload:
            if isinstance(sanitized_payload, dict):
                for candidate_key in ("type", "kind", "role", "mime_type"):
                    candidate_value = sanitized_payload.get(candidate_key)
                    if candidate_value:
                        part_type = candidate_value
                        break
                if not part_type and len(sanitized_payload) == 1:
                    part_type = next(iter(sanitized_payload.keys()))

        if not part_type:
            part_type = "text" if text_value is not None else (
                part.__class__.__name__.lower() if not isinstance(part, dict) else "dict"
            )

        return GeminiContentPart(
            type=str(part_type),
            text=str(text_value) if text_value is not None else None,
            data=sanitized_payload,
        )

    def _combine_text_from_parts(self, parts: list[GeminiContentPart]) -> str:
        text_chunks = [
            chunk.strip()
            for chunk in (part.text or "" for part in parts)
            if chunk and chunk.strip()
        ]
        if text_chunks:
            return "\n".join(text_chunks)

        for part in parts:
            payload = part.data
            if not isinstance(payload, dict):
                continue

            candidate: Any | None = None
            if part.type == "function_call":
                args = payload.get("args")
                if isinstance(args, (dict, list)):
                    candidate = args
            elif "json" in payload and isinstance(payload["json"], (dict, list)):
                candidate = payload["json"]
            elif "parsed_json" in payload and isinstance(payload["parsed_json"], (dict, list)):
                candidate = payload["parsed_json"]
            elif "thought_signature" in payload and isinstance(
                payload["thought_signature"], (dict, list, str)
            ):
                candidate = payload["thought_signature"]

            if candidate is not None:
                try:
                    if isinstance(candidate, str):
                        return candidate
                    return json.dumps(candidate, ensure_ascii=False)
                except (TypeError, ValueError):
                    continue

        return ""

    def _safe_to_dict(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {k: self._safe_to_dict(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._safe_to_dict(v) for v in value]
        to_dict_method = getattr(value, "to_dict", None)
        if callable(to_dict_method):
            try:
                return self._safe_to_dict(to_dict_method())
            except Exception:
                return str(value)
        if hasattr(value, "__dict__"):
            collected: dict[str, Any] = {}
            for attr, attr_value in value.__dict__.items():
                if attr.startswith("_"):
                    continue
                collected[attr] = self._safe_to_dict(attr_value)
            if collected:
                return collected
        return str(value)

    def _sanitize_part_data(self, data: dict[str, Any] | None) -> dict[str, Any] | None:
        if data is None:
            return None

        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            if key == "inline_data" and isinstance(value, dict):
                inline_sanitized: dict[str, Any] = {}
                for inline_key, inline_value in value.items():
                    if inline_key == "data" and isinstance(inline_value, (bytes, str)):
                        inline_sanitized["data_length"] = len(inline_value)
                    elif inline_key != "data":
                        inline_sanitized[inline_key] = inline_value
                sanitized[key] = inline_sanitized
            else:
                sanitized[key] = value
        return sanitized or None

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
        """Return a human-readable message and whether the error is temporary."""

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
            except (TypeError, ValueError):  # pragma: no cover - defensive
                numeric_code = None

            if isinstance(exc, genai_errors.ServerError):
                if status == "UNAVAILABLE" or numeric_code == 503:
                    return ("Gemini 服务暂时不可用，请稍后重试", True)
                if numeric_code is not None and 500 <= numeric_code < 600:
                    return (f"Gemini 服务端错误 (HTTP {code})", True)

            if numeric_code == 429 or status == "RESOURCE_EXHAUSTED":
                return ("Gemini 请求过于频繁，请稍后重试", True)

        upper_message = message.upper()
        if "UNAVAILABLE" in upper_message or "503" in upper_message:
            return ("Gemini 服务暂时不可用，请稍后重试", True)

        if isinstance(exc, (ConnectionError, OSError)):
            return ("Gemini 网络连接异常，请检查网络后重试", True)

        return (message, temporary)
