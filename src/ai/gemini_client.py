"""Gemini API client wrapper using google-genai."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any
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
    ) -> None:
        if not api_key:
            raise AiServiceError("Gemini API key is required")
        if genai is None:
            raise AiServiceError(
                "google-genai 未安装，请先在环境中安装该依赖"
            )

        self._client = None
        self._model_name = model_name
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff_seconds))
        try:
            self._client = genai.Client(api_key=api_key)
        except Exception as exc:  # pragma: no cover - network/proxy issues
            raise AiServiceError(str(exc)) from exc

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
            else:
                if not response.text and not response.parts:
                    raise AiServiceError("Gemini 返回空响应")
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
        import base64

        # Convert OpenAI-style messages to Gemini format
        if isinstance(prompt, list) and prompt and isinstance(prompt[0], dict) and "role" in prompt[0]:
            # Extract content from OpenAI-style messages [{'role': 'system', 'content': '...'}, ...]
            text_parts = []
            for msg in prompt:
                if isinstance(msg, dict) and "content" in msg:
                    text_parts.append(msg["content"])
            prompt_text = "\n\n".join(text_parts)
        elif isinstance(prompt, str):
            prompt_text = prompt
        else:
            # Assume it's already in the correct format
            prompt_text = prompt

        # Build multimodal content if images provided
        if images:
            contents = [prompt_text]

            # Add image parts
            for img in images:
                if img.get("base64") and img.get("mime_type"):
                    # Gemini expects inline_data format
                    contents.append({
                        "inline_data": {
                            "mime_type": img["mime_type"],
                            "data": img["base64"]  # Already base64 encoded
                        }
                    })
        else:
            contents = prompt_text

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
        )

        parts = self._extract_parts(response)
        text = self._combine_text_from_parts(parts)

        if not text:
            direct_text = getattr(response, "text", None)
            if direct_text:
                text = str(direct_text)

        return GeminiResponse(text=text or "", parts=parts)

    def _extract_parts(self, response: Any) -> list[GeminiContentPart]:
        candidates = getattr(response, "candidates", None) or []
        normalized_parts: list[GeminiContentPart] = []

        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
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

    def _normalize_exception(self, exc: Exception) -> tuple[str, bool]:
        """Return a human-readable message and whether the error is temporary."""

        message = str(exc).strip() or "Gemini 调用失败"
        temporary = False

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
