"""Multi-provider translation aggregator for normalizing content into a target language."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .gemini_client import AiServiceError
from .translation_providers import BaseTranslationProvider, TranslationProviderError, build_provider

logger = logging.getLogger(__name__)


HAN_REGEX = re.compile(r"[\u4e00-\u9fff]")
HANGUL_REGEX = re.compile(r"[\uac00-\ud7af]")

DEFAULT_PROVIDER_QUOTAS: dict[str, int] = {
    "tencent": 5_000_000,
    "volcano": 2_000_000,
    "baidu": 1_000_000,
    "alibaba": 1_000_000,
    "huawei": 1_000_000,
    "azure": 2_000_000,
    "amazon": 2_000_000,
    "google": 500_000,
    "deepl": 500_000,
    "niutrans": 6_000_000,
}


@dataclass(slots=True)
class TranslationResult:
    text: str
    language: str
    confidence: float
    translated: bool
    provider: str | None = None


class Translator:
    """Translate arbitrary text into the configured target language using a provider chain."""

    def __init__(
        self,
        *,
        enabled: bool,
        providers: Sequence[BaseTranslationProvider],
        target_language: str = "zh",
        quotas: Mapping[str, int] | None = None,
    ) -> None:
        self.enabled = enabled and bool(providers)
        self._providers = list(providers)
        self._target_language = (target_language or "zh").lower()
        self._quotas = {key.lower(): value for key, value in (quotas or {}).items() if value}
        self._usage: dict[str, int] = {}
        self._usage_lock = asyncio.Lock()

    async def translate(self, text: str) -> TranslationResult:
        normalized = text.strip()
        if not normalized:
            return TranslationResult(text=text, language="unknown", confidence=0.0, translated=False)

        detected_language = detect_language(normalized)
        if not self.enabled or _language_matches(detected_language, self._target_language):
            return TranslationResult(
                text=text,
                language=detected_language,
                confidence=1.0 if _language_matches(detected_language, self._target_language) else 0.2,
                translated=False,
            )

        last_errors: list[str] = []
        text_length = len(normalized)
        for provider in self._providers:
            if await self._is_quota_exhausted(provider.name, text_length):
                last_errors.append(f"{provider.name}: quota exhausted")
                continue
            source_lang = detected_language if detected_language != "unknown" else None
            try:
                provider_result = await provider.translate(normalized, source_lang, self._target_language)
            except TranslationProviderError as exc:
                last_errors.append(f"{provider.name}: {exc}")
                continue
            except Exception as exc:  # pylint: disable=broad-except
                last_errors.append(f"{provider.name}: {exc}")
                continue

            translated_text = provider_result.text.strip()
            if not translated_text:
                last_errors.append(f"{provider.name}: translated text empty")
                continue

            changed = translated_text != normalized
            confidence = 0.85 if changed else 0.4
            language = getattr(provider_result, "detected_source", None) or detected_language
            await self._register_usage(provider.name, text_length)
            return TranslationResult(
                text=translated_text,
                language=language or detected_language or "unknown",
                confidence=confidence,
                translated=changed,
                provider=provider.name,
            )

        error_message = "; ".join(last_errors) if last_errors else "未配置任何可用翻译服务"
        raise AiServiceError(f"所有翻译服务均失败: {error_message}")

    async def _is_quota_exhausted(self, provider_name: str, amount: int) -> bool:
        if amount <= 0:
            return False
        quota = self._quotas.get(provider_name.lower())
        if quota is None:
            return False
        async with self._usage_lock:
            used = self._usage.get(provider_name.lower(), 0)
            exhausted = used + amount > quota
            if exhausted:
                logger.debug(
                    "翻译服务 %s 已达到配额上限 (%d/%d)",
                    provider_name,
                    used,
                    quota,
                )
            return exhausted

    async def _register_usage(self, provider_name: str, amount: int) -> None:
        if amount <= 0:
            return
        key = provider_name.lower()
        if key not in self._quotas:
            return
        async with self._usage_lock:
            self._usage[key] = self._usage.get(key, 0) + amount


def detect_language(text: str) -> str:
    if HAN_REGEX.search(text):
        return "zh"
    if HANGUL_REGEX.search(text):
        return "ko"
    ascii_ratio = sum(1 for ch in text if ch.isascii()) / max(len(text), 1)
    if ascii_ratio > 0.6:
        return "en"
    return "unknown"


def _language_matches(source: str | None, target: str) -> bool:
    if not source:
        return False
    source_lower = source.lower()
    target_lower = target.lower()
    if source_lower == target_lower:
        return True
    if target_lower.startswith("zh"):
        return source_lower.startswith("zh")
    return False


def build_translator_from_config(config: Any) -> Translator | None:
    """Factory helper to instantiate Translator from a Config-like object."""

    if not getattr(config, "TRANSLATION_ENABLED", False):
        return None

    provider_specs = _collect_provider_specs(config)
    providers: list[BaseTranslationProvider] = []
    for name, credentials in provider_specs:
        try:
            provider = build_provider(name, getattr(config, "TRANSLATION_TIMEOUT_SECONDS", 8.0), credentials)
        except TranslationProviderError as exc:
            logger.warning("翻译服务 %s 初始化失败: %s", name, exc)
            continue
        providers.append(provider)

    if not providers:
        logger.warning("未找到可用翻译服务，将继续使用原文")

    quotas = _load_quota_config(config)

    return Translator(
        enabled=bool(providers),
        providers=providers,
        target_language=getattr(config, "TRANSLATION_TARGET_LANGUAGE", "zh"),
        quotas=quotas,
    )


def _collect_provider_specs(config: Any) -> Sequence[tuple[str, dict[str, Any]]]:
    provider_specs: list[tuple[str, dict[str, Any]]] = []
    for name in getattr(config, "TRANSLATION_PROVIDERS", []):
        credentials = _provider_credentials(name, config)
        if not credentials:
            logger.debug("翻译服务 %s 缺少凭据，已跳过", name)
            continue
        provider_specs.append((name, credentials))
    return provider_specs


def _provider_credentials(name: str, config: Any) -> dict[str, Any]:
    normalized = name.strip().lower()
    if normalized == "deepl":
        api_key = getattr(config, "DEEPL_API_KEY", "")
        if not api_key:
            return {}
        return {"api_key": api_key, "api_url": getattr(config, "DEEPL_API_URL", "") or None}
    if normalized == "azure":
        key = getattr(config, "AZURE_TRANSLATOR_KEY", "")
        region = getattr(config, "AZURE_TRANSLATOR_REGION", "")
        if not key or not region:
            return {}
        return {
            "api_key": key,
            "region": region,
            "endpoint": getattr(config, "AZURE_TRANSLATOR_ENDPOINT", "") or None,
        }
    if normalized == "amazon":
        access = getattr(config, "AMAZON_TRANSLATE_ACCESS_KEY", "")
        secret = getattr(config, "AMAZON_TRANSLATE_SECRET_KEY", "")
        region = getattr(config, "AMAZON_TRANSLATE_REGION", "")
        if not access or not secret or not region:
            return {}
        token = getattr(config, "AMAZON_TRANSLATE_SESSION_TOKEN", "") or None
        return {
            "access_key": access,
            "secret_key": secret,
            "region": region,
            "session_token": token,
        }
    if normalized == "google":
        key = getattr(config, "GOOGLE_TRANSLATE_API_KEY", "")
        if not key:
            return {}
        return {"api_key": key, "endpoint": getattr(config, "GOOGLE_TRANSLATE_ENDPOINT", "") or None}
    if normalized == "baidu":
        app_id = getattr(config, "BAIDU_TRANSLATE_APP_ID", "")
        secret = getattr(config, "BAIDU_TRANSLATE_SECRET_KEY", "")
        if not app_id or not secret:
            return {}
        return {"app_id": app_id, "app_secret": secret}
    if normalized == "alibaba":
        access_id = getattr(config, "ALIBABA_TRANSLATE_ACCESS_KEY_ID", "")
        access_secret = getattr(config, "ALIBABA_TRANSLATE_ACCESS_KEY_SECRET", "")
        app_key = getattr(config, "ALIBABA_TRANSLATE_APP_KEY", "")
        if not access_id or not access_secret or not app_key:
            return {}
        return {
            "access_key_id": access_id,
            "access_key_secret": access_secret,
            "app_key": app_key,
            "region_id": getattr(config, "ALIBABA_TRANSLATE_REGION_ID", "cn-hangzhou") or "cn-hangzhou",
        }
    if normalized == "tencent":
        secret_id = getattr(config, "TENCENT_TRANSLATE_SECRET_ID", "")
        secret_key = getattr(config, "TENCENT_TRANSLATE_SECRET_KEY", "")
        if not secret_id or not secret_key:
            return {}
        project_raw = getattr(config, "TENCENT_TRANSLATE_PROJECT_ID", "") or None
        project_id = None
        if project_raw:
            try:
                project_id = int(project_raw)
            except ValueError:
                logger.warning("无效的 Tencent Translate ProjectId: %s", project_raw)
        return {
            "secret_id": secret_id,
            "secret_key": secret_key,
            "region": getattr(config, "TENCENT_TRANSLATE_REGION", "ap-beijing") or "ap-beijing",
            "project_id": project_id,
        }
    if normalized == "huawei":
        access = getattr(config, "HUAWEI_TRANSLATE_ACCESS_KEY", "")
        secret = getattr(config, "HUAWEI_TRANSLATE_SECRET_KEY", "")
        project = getattr(config, "HUAWEI_TRANSLATE_PROJECT_ID", "")
        if not access or not secret or not project:
            return {}
        return {
            "access_key": access,
            "secret_key": secret,
            "project_id": project,
            "region": getattr(config, "HUAWEI_TRANSLATE_REGION", "cn-north-4") or "cn-north-4",
            "endpoint": getattr(config, "HUAWEI_TRANSLATE_ENDPOINT", "") or None,
        }
    if normalized == "volcano":
        access = getattr(config, "VOLCANO_TRANSLATE_ACCESS_KEY", "")
        secret = getattr(config, "VOLCANO_TRANSLATE_SECRET_KEY", "")
        if not access or not secret:
            return {}
        return {
            "access_key": access,
            "secret_key": secret,
            "region": getattr(config, "VOLCANO_TRANSLATE_REGION", "cn-north-1") or "cn-north-1",
            "endpoint": getattr(config, "VOLCANO_TRANSLATE_ENDPOINT", "") or None,
        }
    if normalized == "niutrans":
        api_key = getattr(config, "NIUTRANS_API_KEY", "")
        if not api_key:
            return {}
        return {
            "api_key": api_key,
            "endpoint": getattr(config, "NIUTRANS_API_ENDPOINT", "") or None,
        }
    return {}


def _load_quota_config(config: Any) -> dict[str, int]:
    quotas = dict(DEFAULT_PROVIDER_QUOTAS)
    overrides_raw = getattr(config, "TRANSLATION_PROVIDER_QUOTAS", "")
    if not overrides_raw:
        return quotas

    for entry in overrides_raw.split(","):
        token = entry.strip()
        if not token:
            continue
        if ":" not in token:
            logger.warning("忽略无效的翻译配额配置: %s", token)
            continue
        key, value = token.split(":", 1)
        key = key.strip().lower()
        try:
            limit = int(value.strip())
        except ValueError:
            logger.warning("翻译配额值无效 (%s=%s)，已忽略", key, value.strip())
            continue
        if limit <= 0:
            quotas.pop(key, None)
        else:
            quotas[key] = limit
    return quotas
