"""Configuration loader for Telegram listener."""

from __future__ import annotations

import json
import logging
import os
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Set

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYWORDS_FILE = PROJECT_ROOT / "keywords.txt"


def _normalize_keyword(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value.strip())
    return normalized.lower()


def _load_keywords_from_env() -> Set[str]:
    return {
        _normalize_keyword(keyword)
        for keyword in os.getenv("FILTER_KEYWORDS", "").split(",")
        if keyword.strip()
    }


def _resolve_keywords_file(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _load_keywords_from_file(path: str | None) -> Set[str]:
    keywords: Set[str] = set()
    explicit_path = (path or "").strip()
    if explicit_path:
        keywords_file = _resolve_keywords_file(explicit_path)
        warn_when_missing = True
    else:
        keywords_file = DEFAULT_KEYWORDS_FILE
        warn_when_missing = False

    if not keywords_file.exists():
        if warn_when_missing:
            print(f"⚠️ 关键词文件不存在: {keywords_file}")
        return keywords

    try:
        with keywords_file.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.split("#", 1)[0].strip()
                if not line:
                    continue
                for token in line.split(","):
                    keyword = _normalize_keyword(token)
                    if keyword:
                        keywords.add(keyword)
    except OSError as exc:
        print(f"⚠️ 无法读取关键词文件 {keywords_file}: {exc}")

    return keywords


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

    FILTER_KEYWORDS_FILE: str = os.getenv("FILTER_KEYWORDS_FILE", "").strip()
    _ENV_FILTER_KEYWORDS: Set[str] = _load_keywords_from_env()
    _FILE_FILTER_KEYWORDS: Set[str] = _load_keywords_from_file(FILTER_KEYWORDS_FILE)
    FILTER_KEYWORDS: Set[str] = (
        _FILE_FILTER_KEYWORDS.union(_ENV_FILTER_KEYWORDS)
        if (_FILE_FILTER_KEYWORDS or _ENV_FILTER_KEYWORDS)
        else set()
    )

    DEDUP_WINDOW_HOURS: int = int(os.getenv("DEDUP_WINDOW_HOURS", "24"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    SESSION_PATH: str = "./session/tg_session"
    USE_LANGGRAPH_PIPELINE: bool = _as_bool(os.getenv("USE_LANGGRAPH_PIPELINE", "false"))

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

    FORWARD_INCLUDE_TRANSLATION: bool = _as_bool(
        os.getenv("FORWARD_INCLUDE_TRANSLATION", "true")
    )

    # Memory configuration
    MEMORY_ENABLED: bool = _as_bool(os.getenv("MEMORY_ENABLED", "false"))
    MEMORY_BACKEND: str = os.getenv("MEMORY_BACKEND", "supabase")  # local | supabase | hybrid
    MEMORY_DIR: str = os.getenv("MEMORY_DIR", "./memories")
    MEMORY_MAX_NOTES: int = int(os.getenv("MEMORY_MAX_NOTES", "3"))
    MEMORY_LOOKBACK_HOURS: int = int(os.getenv("MEMORY_LOOKBACK_HOURS", "72"))
    MEMORY_MIN_CONFIDENCE: float = float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.6"))
    MEMORY_SIMILARITY_THRESHOLD: float = float(
        os.getenv("MEMORY_SIMILARITY_THRESHOLD", "0.55")
    )

    # Claude configuration (for deep analysis)
    CLAUDE_ENABLED: bool = _as_bool(os.getenv("CLAUDE_ENABLED", "false"))
    CLAUDE_API_KEY: str = os.getenv("CLAUDE_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
    CLAUDE_TIMEOUT_SECONDS: float = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "30"))
    CLAUDE_MAX_TOOL_TURNS: int = int(os.getenv("CLAUDE_MAX_TOOL_TURNS", "3"))

    # Deep analysis unified configuration
    DEEP_ANALYSIS_ENABLED: bool = _as_bool(
        os.getenv("DEEP_ANALYSIS_ENABLED", os.getenv("CLAUDE_ENABLED", "false"))
    )
    DEEP_ANALYSIS_PROVIDER: str = os.getenv(
        "DEEP_ANALYSIS_PROVIDER",
        "claude" if CLAUDE_ENABLED else "gemini",
    ).strip().lower()
    DEEP_ANALYSIS_FALLBACK_PROVIDER: str = os.getenv(
        "DEEP_ANALYSIS_FALLBACK_PROVIDER",
        "",
    ).strip().lower()

    GEMINI_DEEP_MODEL: str = os.getenv("GEMINI_DEEP_MODEL", "gemini-2.5-pro")
    GEMINI_DEEP_TIMEOUT_SECONDS: float = float(os.getenv("GEMINI_DEEP_TIMEOUT_SECONDS", "25"))
    GEMINI_DEEP_MAX_FUNCTION_TURNS: int = int(os.getenv("GEMINI_DEEP_MAX_FUNCTION_TURNS", "6"))
    GEMINI_DEEP_RETRY_ATTEMPTS: int = int(os.getenv("GEMINI_DEEP_RETRY_ATTEMPTS", "1"))
    GEMINI_DEEP_RETRY_BACKOFF_SECONDS: float = float(
        os.getenv("GEMINI_DEEP_RETRY_BACKOFF_SECONDS", "1.5")
    )

    # Context Editing configuration (Claude Memory Tool)
    MEMORY_CONTEXT_TRIGGER_TOKENS: int = int(os.getenv("MEMORY_CONTEXT_TRIGGER_TOKENS", "6000"))
    MEMORY_CONTEXT_KEEP_TOOLS: int = int(os.getenv("MEMORY_CONTEXT_KEEP_TOOLS", "1"))
    MEMORY_CONTEXT_CLEAR_AT_LEAST: int = int(os.getenv("MEMORY_CONTEXT_CLEAR_AT_LEAST", "1000"))

    # Routing strategy (Gemini + Claude hybrid)
    HIGH_VALUE_CONFIDENCE_THRESHOLD: float = float(os.getenv("HIGH_VALUE_CONFIDENCE_THRESHOLD", "0.75"))
    CRITICAL_KEYWORDS: Set[str] = {
        keyword.strip().lower()
        for keyword in os.getenv("CRITICAL_KEYWORDS", "上币,listing,hack,黑客,监管,regulation").split(",")
        if keyword.strip()
    }

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
    EMBEDDING_SIMILARITY_THRESHOLD: float = float(os.getenv("EMBEDDING_SIMILARITY_THRESHOLD", "0.85"))
    EMBEDDING_TIME_WINDOW_HOURS: int = int(os.getenv("EMBEDDING_TIME_WINDOW_HOURS", "72"))

    # Deep analysis tools configuration (Phase 1: Search Tool)
    DEEP_ANALYSIS_TOOLS_ENABLED: bool = _as_bool(os.getenv("DEEP_ANALYSIS_TOOLS_ENABLED", "false"))
    DEEP_ANALYSIS_MAX_TOOL_CALLS: int = int(os.getenv("DEEP_ANALYSIS_MAX_TOOL_CALLS", "3"))
    DEEP_ANALYSIS_TOOL_TIMEOUT: int = int(os.getenv("DEEP_ANALYSIS_TOOL_TIMEOUT", "10"))
    DEEP_ANALYSIS_TOOL_DAILY_LIMIT: int = int(os.getenv("DEEP_ANALYSIS_TOOL_DAILY_LIMIT", "50"))

    # Search tool configuration
    TOOL_SEARCH_ENABLED: bool = _as_bool(os.getenv("TOOL_SEARCH_ENABLED", "true"))
    DEEP_ANALYSIS_SEARCH_PROVIDER: str = os.getenv("DEEP_ANALYSIS_SEARCH_PROVIDER", "tavily")
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
    SEARCH_MAX_RESULTS: int = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
    SEARCH_MULTI_SOURCE_THRESHOLD: int = int(os.getenv("SEARCH_MULTI_SOURCE_THRESHOLD", "3"))
    SEARCH_CACHE_TTL_SECONDS: int = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "600"))

    # High-priority event domain whitelisting
    HIGH_PRIORITY_EVENT_DOMAINS: Dict[str, list[str]] = {
        "hack": ["coindesk.com", "theblock.co", "cointelegraph.com", "decrypt.co"],
        "regulation": ["coindesk.com", "theblock.co", "theblockcrypto.com"],
        "listing": ["coindesk.com", "theblock.co", "cointelegraph.com"],
        "partnership": ["coindesk.com", "theblock.co"],
    }

    # Future tools (Phase 2+)
    TOOL_PRICE_ENABLED: bool = _as_bool(os.getenv("TOOL_PRICE_ENABLED", "false"))
    TOOL_MACRO_ENABLED: bool = _as_bool(os.getenv("TOOL_MACRO_ENABLED", "false"))
    TOOL_ONCHAIN_ENABLED: bool = _as_bool(os.getenv("TOOL_ONCHAIN_ENABLED", "false"))
    DEEP_ANALYSIS_PRICE_PROVIDER: str = os.getenv("DEEP_ANALYSIS_PRICE_PROVIDER", "coingecko")
    DEEP_ANALYSIS_MACRO_PROVIDER: str = os.getenv("DEEP_ANALYSIS_MACRO_PROVIDER", "fred")
    COINGECKO_API_KEY: str = os.getenv("COINGECKO_API_KEY", "")
    COINGECKO_API_BASE_URL: str = os.getenv("COINGECKO_API_BASE_URL", "https://api.coingecko.com/api/v3")
    PRICE_CACHE_TTL_SECONDS: int = int(os.getenv("PRICE_CACHE_TTL_SECONDS", "60"))
    PRICE_MARKET_CHART_CACHE_SECONDS: int = int(os.getenv("PRICE_MARKET_CHART_CACHE_SECONDS", "300"))
    PRICE_DEVIATION_THRESHOLD: float = float(os.getenv("PRICE_DEVIATION_THRESHOLD", "2.0"))
    PRICE_STABLECOIN_TOLERANCE: float = float(os.getenv("PRICE_STABLECOIN_TOLERANCE", "0.5"))
    PRICE_VOLATILITY_SPIKE_MULTIPLIER: float = float(os.getenv("PRICE_VOLATILITY_SPIKE_MULTIPLIER", "3.0"))
    PRICE_BINANCE_FALLBACK_ENABLED: bool = _as_bool(os.getenv("PRICE_BINANCE_FALLBACK_ENABLED", "true"))
    BINANCE_REST_BASE_URL: str = os.getenv("BINANCE_REST_BASE_URL", "https://api.binance.com")
    FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
    FRED_API_BASE_URL: str = os.getenv("FRED_API_BASE_URL", "https://api.stlouisfed.org/fred")
    MACRO_CACHE_TTL_SECONDS: int = int(os.getenv("MACRO_CACHE_TTL_SECONDS", "1800"))
    MACRO_EXPECTATIONS_JSON: str = os.getenv("MACRO_EXPECTATIONS_JSON", "").strip()
    try:
        MACRO_EXPECTATIONS: Dict[str, float] = (
            json.loads(MACRO_EXPECTATIONS_JSON) if MACRO_EXPECTATIONS_JSON else {}
        )
        if not isinstance(MACRO_EXPECTATIONS, dict):
            raise ValueError("MACRO_EXPECTATIONS_JSON 必须是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("解析 MACRO_EXPECTATIONS_JSON 失败: %s", exc)
        MACRO_EXPECTATIONS = {}

    @classmethod
    def get_deep_analysis_config(cls) -> Dict[str, Any]:
        """Return normalised deep analysis configuration."""

        provider = (cls.DEEP_ANALYSIS_PROVIDER or "").strip().lower()
        fallback = (cls.DEEP_ANALYSIS_FALLBACK_PROVIDER or "").strip().lower()

        if cls.CLAUDE_ENABLED and not os.getenv("DEEP_ANALYSIS_ENABLED"):
            logger.warning(
                "⚠️ CLAUDE_ENABLED 已废弃，请迁移到 DEEP_ANALYSIS_ENABLED 和 DEEP_ANALYSIS_PROVIDER"
            )

        enabled = cls.DEEP_ANALYSIS_ENABLED
        if provider not in {"claude", "gemini"}:
            if provider:
                logger.warning("未知的 DEEP_ANALYSIS_PROVIDER=%s，自动回退为 claude", provider)
            provider = "claude" if cls.CLAUDE_ENABLED else "gemini"

        config: Dict[str, Any] = {
            "enabled": enabled,
            "provider": provider,
            "fallback_provider": fallback if fallback in {"claude", "gemini"} else "",
            "claude": {
                "api_key": cls.CLAUDE_API_KEY,
                "model": cls.CLAUDE_MODEL,
                "timeout": cls.CLAUDE_TIMEOUT_SECONDS,
                "max_tool_turns": cls.CLAUDE_MAX_TOOL_TURNS,
            },
            "gemini": {
                "model": cls.GEMINI_DEEP_MODEL,
                "timeout": cls.GEMINI_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": cls.GEMINI_DEEP_MAX_FUNCTION_TURNS,
                "max_retries": cls.GEMINI_DEEP_RETRY_ATTEMPTS,
                "retry_backoff": cls.GEMINI_DEEP_RETRY_BACKOFF_SECONDS,
                "api_key": cls.GEMINI_API_KEY,
            },
        }
        return config

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
