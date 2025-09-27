"""Gemini API client wrapper using google-genai."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
try:  # pragma: no cover - optional dependency
    from google import genai
except ImportError:  # pragma: no cover - runtime fallback
    genai = None  # type: ignore


class AiServiceError(RuntimeError):
    """Raised when the AI service call fails."""


@dataclass
class GeminiResponse:
    """Structured response returned by the Gemini client."""

    text: str


class GeminiClient:
    """Thin wrapper around google-genai models."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        timeout: float,
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
        try:
            self._client = genai.Client(api_key=api_key)
        except Exception as exc:  # pragma: no cover - network/proxy issues
            raise AiServiceError(str(exc)) from exc

    async def generate_signal(self, prompt: str) -> GeminiResponse:
        """Execute prompt against Gemini and return plain text."""
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(self._call_model, prompt),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise AiServiceError("Gemini 请求超时") from exc
        except Exception as exc:  # pragma: no cover - broader network errors
            raise AiServiceError(str(exc)) from exc

        if not text:
            raise AiServiceError("Gemini 返回空响应")
        return GeminiResponse(text=text)

    def _call_model(self, prompt: str) -> str:
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=prompt,
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
