"""Utility translator to normalize multi-language content into Simplified Chinese via DeepL."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import httpx

from .gemini_client import AiServiceError

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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._api_url,
                data={
                    "auth_key": self._api_key,
                    "text": text,
                    "target_lang": "ZH",
                },
            )
            response.raise_for_status()
            payload = response.json()
            translations = payload.get("translations")
            if not translations:
                raise AiServiceError("DeepL 未返回翻译结果")
            return translations[0].get("text", text)


def detect_language(text: str) -> str:
    if HAN_REGEX.search(text):
        return "zh"
    if HANGUL_REGEX.search(text):
        return "ko"
    ascii_ratio = sum(1 for ch in text if ch.isascii()) / max(len(text), 1)
    if ascii_ratio > 0.6:
        return "en"
    return "unknown"
