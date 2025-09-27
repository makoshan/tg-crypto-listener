"""Utility translator to normalize multi-language content into Simplified Chinese via DeepL."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .gemini_client import AiServiceError

try:  # pragma: no cover - optional dependency
    import deepl
except ImportError:  # pragma: no cover - runtime fallback
    deepl = None  # type: ignore

HAN_REGEX = re.compile(r"[\u4e00-\u9fff]")
HANGUL_REGEX = re.compile(r"[\uac00-\ud7af]")


@dataclass
class TranslationResult:
    text: str
    language: str
    confidence: float
    translated: bool


class Translator:
    """Translate arbitrary text into Simplified Chinese using DeepL."""

    def __init__(
        self,
        enabled: bool,
        api_key: str,
        timeout: float,
        api_url: str = "https://api.deepl.com/v2/translate",
    ) -> None:
        self.enabled = enabled and bool(api_key)
        self._timeout = timeout
        self._api_key = api_key
        self._api_url = api_url
        self._client: "deepl.DeepLClient | None" = None

        if self.enabled:
            if deepl is None:
                raise AiServiceError("deepl SDK 未安装，请先在环境中安装该依赖")
            self._client = self._init_client(api_key, api_url)

    async def translate(self, text: str) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text=text, language="unknown", confidence=0.0, translated=False)

        language = detect_language(text)
        if not self.enabled or language == "zh":
            return TranslationResult(text=text, language=language, confidence=1.0 if language == "zh" else 0.2, translated=False)

        try:
            translated_text = await asyncio.wait_for(
                self._call_deepl(text),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            raise AiServiceError("翻译请求超时") from exc
        except Exception as exc:
            raise AiServiceError(str(exc)) from exc

        changed = translated_text.strip() != text.strip()
        return TranslationResult(
            text=translated_text,
            language="zh",
            confidence=0.8 if changed else 0.4,
            translated=changed,
        )

    async def _call_deepl(self, text: str) -> str:
        if not self._client:
            raise AiServiceError("DeepL 客户端未初始化")
        return await asyncio.to_thread(self._translate_sync, text)

    def _translate_sync(self, text: str) -> str:
        if not self._client:
            raise AiServiceError("DeepL 客户端未初始化")
        try:
            result = self._client.translate_text(text, target_lang="ZH")
        except Exception as exc:  # pragma: no cover - SDK raises runtime errors
            deepl_exc = getattr(deepl, "DeepLException", tuple())
            if deepl is not None and isinstance(exc, deepl_exc):
                raise AiServiceError(str(exc)) from exc
            raise AiServiceError(str(exc)) from exc

        translated_text = self._extract_text(result)
        if not translated_text.strip():
            raise AiServiceError("DeepL 未返回翻译结果")
        return translated_text

    def _init_client(self, api_key: str, api_url: str) -> "deepl.DeepLClient":
        client_kwargs: dict[str, str] = {}
        client_cls = getattr(deepl, "DeepLClient", None)
        if client_cls is None:  # pragma: no cover - legacy SDK fallback
            raise AiServiceError("当前 deepl SDK 版本缺少 DeepLClient 类，请升级到官方最新版 deepl")
        normalized_url = (api_url or "").strip()
        if normalized_url:
            normalized_url = normalized_url.rstrip("/")
            if normalized_url.endswith("/v2/translate"):
                normalized_url = normalized_url[: -len("/v2/translate")]
            if normalized_url:
                client_kwargs["server_url"] = normalized_url

        try:
            return client_cls(api_key, **client_kwargs)
        except TypeError:
            client_kwargs.pop("server_url", None)
            return client_cls(api_key)
        except Exception as exc:  # pragma: no cover - SDK initialisation issues
            raise AiServiceError(str(exc)) from exc

    @staticmethod
    def _extract_text(result: object) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, (list, tuple)) and result:
            return Translator._extract_text(result[0])
        return str(getattr(result, "text", ""))



def detect_language(text: str) -> str:
    if HAN_REGEX.search(text):
        return "zh"
    if HANGUL_REGEX.search(text):
        return "ko"
    ascii_ratio = sum(1 for ch in text if ch.isascii()) / max(len(text), 1)
    if ascii_ratio > 0.6:
        return "en"
    return "unknown"
