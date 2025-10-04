"""Configuration loader for Telegram listener."""

from __future__ import annotations

import os
from typing import List, Set

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


class Config:
    """Application configuration loaded from .env."""

    TG_API_ID: int = int(os.getenv("TG_API_ID", "0"))
    TG_API_HASH: str = os.getenv("TG_API_HASH", "")
    TG_PHONE: str = os.getenv("TG_PHONE", "")

    TARGET_CHAT_ID: str = os.getenv("TARGET_CHAT_ID", "")
    TARGET_CHAT_ID_BACKUP: str = os.getenv("TARGET_CHAT_ID_BACKUP", "")

    SOURCE_CHANNELS: List[str] = [
        channel.strip()
        for channel in os.getenv("SOURCE_CHANNELS", "").split(",")
        if channel.strip()
    ]

    FILTER_KEYWORDS: Set[str] = {
        keyword.strip().lower()
        for keyword in os.getenv("FILTER_KEYWORDS", "").split(",")
        if keyword.strip()
    }

    DEDUP_WINDOW_HOURS: int = int(os.getenv("DEDUP_WINDOW_HOURS", "24"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    SESSION_PATH: str = "./session/tg_session"

    # AI configuration
    AI_ENABLED: bool = _as_bool(os.getenv("AI_ENABLED", "false"))
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    AI_API_KEY: str = os.getenv("AI_API_KEY", "") or GEMINI_API_KEY
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "gemini")
    AI_BASE_URL: str = os.getenv("AI_BASE_URL", "")
    AI_EXTRA_HEADERS: str = os.getenv("AI_EXTRA_HEADERS", "")
    AI_MODEL_NAME: str = os.getenv("AI_MODEL_NAME", "gemini-2.5-flash-lite")
    AI_SIGNAL_THRESHOLD: float = float(os.getenv("AI_SIGNAL_THRESHOLD", "0.6"))
    AI_TIMEOUT_SECONDS: float = float(os.getenv("AI_TIMEOUT_SECONDS", "8"))
    AI_MAX_CONCURRENCY: int = int(os.getenv("AI_MAX_CONCURRENCY", "2"))
    AI_RETRY_ATTEMPTS: int = int(os.getenv("AI_RETRY_ATTEMPTS", "1"))
    AI_RETRY_BACKOFF_SECONDS: float = float(os.getenv("AI_RETRY_BACKOFF_SECONDS", "1.5"))
    AI_SKIP_NEUTRAL_FORWARD: bool = _as_bool(os.getenv("AI_SKIP_NEUTRAL_FORWARD", "false"))

    TRANSLATION_ENABLED: bool = _as_bool(os.getenv("TRANSLATION_ENABLED", "true"))
    TRANSLATION_TIMEOUT_SECONDS: float = float(os.getenv("TRANSLATION_TIMEOUT_SECONDS", "6"))
    TRANSLATION_TARGET_LANGUAGE: str = os.getenv("TRANSLATION_TARGET_LANGUAGE", "zh").lower()
    TRANSLATION_PROVIDERS: List[str] = [
        provider.strip().lower()
        for provider in os.getenv(
            "TRANSLATION_PROVIDERS",
            "deepl,azure,google,amazon,baidu,alibaba,tencent,huawei,volcano,niutrans",
        ).split(",")
        if provider.strip()
    ]
    TRANSLATION_PROVIDER_QUOTAS: str = os.getenv("TRANSLATION_PROVIDER_QUOTAS", "")

    DEEPL_API_KEY: str = os.getenv("DEEPL_API_KEY", "")
    DEEPL_API_URL: str = os.getenv("DEEPL_API_URL", "https://api.deepl.com/v2/translate")

    AZURE_TRANSLATOR_KEY: str = os.getenv("AZURE_TRANSLATOR_KEY", "")
    AZURE_TRANSLATOR_REGION: str = os.getenv("AZURE_TRANSLATOR_REGION", "")
    AZURE_TRANSLATOR_ENDPOINT: str = os.getenv("AZURE_TRANSLATOR_ENDPOINT", "")

    AMAZON_TRANSLATE_ACCESS_KEY: str = os.getenv("AMAZON_TRANSLATE_ACCESS_KEY", "")
    AMAZON_TRANSLATE_SECRET_KEY: str = os.getenv("AMAZON_TRANSLATE_SECRET_KEY", "")
    AMAZON_TRANSLATE_REGION: str = os.getenv("AMAZON_TRANSLATE_REGION", "")
    AMAZON_TRANSLATE_SESSION_TOKEN: str = os.getenv("AMAZON_TRANSLATE_SESSION_TOKEN", "")

    GOOGLE_TRANSLATE_API_KEY: str = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
    GOOGLE_TRANSLATE_ENDPOINT: str = os.getenv("GOOGLE_TRANSLATE_ENDPOINT", "")

    BAIDU_TRANSLATE_APP_ID: str = os.getenv("BAIDU_TRANSLATE_APP_ID", "")
    BAIDU_TRANSLATE_SECRET_KEY: str = os.getenv("BAIDU_TRANSLATE_SECRET_KEY", "")

    ALIBABA_TRANSLATE_ACCESS_KEY_ID: str = os.getenv("ALIBABA_TRANSLATE_ACCESS_KEY_ID", "")
    ALIBABA_TRANSLATE_ACCESS_KEY_SECRET: str = os.getenv("ALIBABA_TRANSLATE_ACCESS_KEY_SECRET", "")
    ALIBABA_TRANSLATE_APP_KEY: str = os.getenv("ALIBABA_TRANSLATE_APP_KEY", "")
    ALIBABA_TRANSLATE_REGION_ID: str = os.getenv("ALIBABA_TRANSLATE_REGION_ID", "cn-hangzhou")

    TENCENT_TRANSLATE_SECRET_ID: str = os.getenv("TENCENT_TRANSLATE_SECRET_ID", "")
    TENCENT_TRANSLATE_SECRET_KEY: str = os.getenv("TENCENT_TRANSLATE_SECRET_KEY", "")
    TENCENT_TRANSLATE_REGION: str = os.getenv("TENCENT_TRANSLATE_REGION", "ap-beijing")
    TENCENT_TRANSLATE_PROJECT_ID: str = os.getenv("TENCENT_TRANSLATE_PROJECT_ID", "")

    HUAWEI_TRANSLATE_ACCESS_KEY: str = os.getenv("HUAWEI_TRANSLATE_ACCESS_KEY", "")
    HUAWEI_TRANSLATE_SECRET_KEY: str = os.getenv("HUAWEI_TRANSLATE_SECRET_KEY", "")
    HUAWEI_TRANSLATE_PROJECT_ID: str = os.getenv("HUAWEI_TRANSLATE_PROJECT_ID", "")
    HUAWEI_TRANSLATE_REGION: str = os.getenv("HUAWEI_TRANSLATE_REGION", "cn-north-4")
    HUAWEI_TRANSLATE_ENDPOINT: str = os.getenv("HUAWEI_TRANSLATE_ENDPOINT", "")

    VOLCANO_TRANSLATE_ACCESS_KEY: str = os.getenv("VOLCANO_TRANSLATE_ACCESS_KEY", "")
    VOLCANO_TRANSLATE_SECRET_KEY: str = os.getenv("VOLCANO_TRANSLATE_SECRET_KEY", "")
    VOLCANO_TRANSLATE_REGION: str = os.getenv("VOLCANO_TRANSLATE_REGION", "cn-north-1")
    VOLCANO_TRANSLATE_ENDPOINT: str = os.getenv("VOLCANO_TRANSLATE_ENDPOINT", "")

    NIUTRANS_API_KEY: str = os.getenv("NIUTRANS_API_KEY", "")
    NIUTRANS_API_ENDPOINT: str = os.getenv("NIUTRANS_API_ENDPOINT", "")

    FORWARD_COOLDOWN_SECONDS: float = float(os.getenv("FORWARD_COOLDOWN_SECONDS", "1.0"))

    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    SUPABASE_TIMEOUT_SECONDS: float = float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "8.0"))
    ENABLE_DB_PERSISTENCE: bool = _as_bool(os.getenv("ENABLE_DB_PERSISTENCE", "false"))

    # OpenAI Embedding configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_SIMILARITY_THRESHOLD: float = float(os.getenv("EMBEDDING_SIMILARITY_THRESHOLD", "0.92"))
    EMBEDDING_TIME_WINDOW_HOURS: int = int(os.getenv("EMBEDDING_TIME_WINDOW_HOURS", "72"))

    @classmethod
    def validate(cls) -> bool:
        """Ensure required config values exist."""
        required_values = [
            ("TG_API_ID", cls.TG_API_ID),
            ("TG_API_HASH", cls.TG_API_HASH),
            ("TG_PHONE", cls.TG_PHONE),
            ("TARGET_CHAT_ID", cls.TARGET_CHAT_ID),
        ]

        missing = [name for name, value in required_values if not value]
        if missing:
            print(f"❌ 缺少必需配置: {', '.join(missing)}")
            return False

        if cls.AI_ENABLED and not cls.AI_API_KEY:
            print("⚠️ 已启用 AI，但 AI_API_KEY/GEMINI_API_KEY 未配置，将自动降级为传统模式")
        return True
