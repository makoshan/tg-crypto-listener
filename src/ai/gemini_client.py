"""Gemini API client wrapper using google-genai."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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
class GeminiResponse:
    """Structured response returned by the Gemini client."""

    text: str


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
                text = await asyncio.wait_for(
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
                if not text:
                    raise AiServiceError("Gemini 返回空响应")
                return GeminiResponse(text=text)

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

    def _call_model(self, prompt: str | list, images: list[dict] = None) -> str:
        import base64

        # Build multimodal content if images provided
        if images:
            contents = []

            # Add text part
            if isinstance(prompt, str):
                contents.append(prompt)
            else:
                contents.extend(prompt)

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
            contents = prompt

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
        )

        if hasattr(response, "text") and response.text:
            return str(response.text)

        candidates = getattr(response, "candidates", None)
        if not candidates:
            return ""

        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None)
            if not parts:
                continue
            text_chunks = [getattr(part, "text", "") for part in parts]
            concatenated = "".join(chunk for chunk in text_chunks if chunk)
            if concatenated:
                return concatenated
        return ""

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
