"""Configuration loader for Telegram listener."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
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
CHAT_ID_PATTERN = re.compile(r"-?\d+")


def _normalize_keyword(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value.strip())
    return normalized.lower()


def _parse_chat_identifier(value: str) -> int | str:
    candidate = value.strip()
    if not candidate:
        return candidate
    if CHAT_ID_PATTERN.fullmatch(candidate):
        try:
            return int(candidate)
        except ValueError:
            logger.warning("无法将聊天标识解析为整数: %s", value)
    return candidate


def _parse_source_channels(value: str) -> list[int | str]:
    """Parse comma/newline separated channel handles while tolerating line continuations."""
    if not value:
        return []

    cleaned = value.replace("\\\r\n", "\n").replace("\\\n", "\n").replace("\r\n", "\n")
    tokens: list[int | str] = []
    invalid: list[str] = []
    seen: set[int | str] = set()

    def _add_token(raw: str) -> None:
        candidate = raw.strip()
        if not candidate:
            return

        if candidate.startswith("#"):
            return

        candidate = candidate.strip("'\"").strip()
        candidate = candidate.rstrip("\\").strip()

        if not candidate:
            invalid.append(raw)
            return

        if candidate == "\\":
            invalid.append(raw)
            return

        parsed_candidate = _parse_chat_identifier(candidate)

        if parsed_candidate not in seen:
            tokens.append(parsed_candidate)
            seen.add(parsed_candidate)

    for segment in cleaned.split("\n"):
        if not segment:
            continue
        for piece in segment.split(","):
            _add_token(piece)

    if invalid:
        logger.warning("忽略无效 SOURCE_CHANNELS 条目: %s", ", ".join(sorted(set(invalid))))

    return tokens


def _parse_cli_args(value: str) -> List[str]:
    if not value:
        return []
    try:
        parsed = shlex.split(value)
    except ValueError as exc:
        logger.warning("无法解析 CLI 参数字符串 '%s': %s", value, exc)
        parsed = value.split()
    return [token.strip() for token in parsed if token.strip()]


def _parse_tool_list(value: str) -> List[str]:
    """Parse comma-separated tool list."""
    if not value:
        return []
    return [tool.strip() for tool in value.split(",") if tool.strip()]


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

    TARGET_CHAT_ID: int | str = _parse_chat_identifier(os.getenv("TARGET_CHAT_ID", ""))
    TARGET_CHAT_ID_BACKUP: int | str = _parse_chat_identifier(os.getenv("TARGET_CHAT_ID_BACKUP", ""))
    FORWARD_TO_CHANNEL_ENABLED: bool = _as_bool(os.getenv("FORWARD_TO_CHANNEL_ENABLED", "true"))

    SOURCE_CHANNELS: list[int | str] = _parse_source_channels(os.getenv("SOURCE_CHANNELS", ""))

    FILTER_KEYWORDS_FILE: str = os.getenv("FILTER_KEYWORDS_FILE", "").strip()
    _ENV_FILTER_KEYWORDS: Set[str] = _load_keywords_from_env()
    _FILE_FILTER_KEYWORDS: Set[str] = _load_keywords_from_file(FILTER_KEYWORDS_FILE)
    FILTER_KEYWORDS: Set[str] = (
        _FILE_FILTER_KEYWORDS.union(_ENV_FILTER_KEYWORDS)
        if (_FILE_FILTER_KEYWORDS or _ENV_FILTER_KEYWORDS)
        else set()
    )

    DEDUP_WINDOW_HOURS: int = int(os.getenv("DEDUP_WINDOW_HOURS", "24"))
    SIGNAL_DEDUP_ENABLED: bool = _as_bool(os.getenv("SIGNAL_DEDUP_ENABLED", "true"))
    SIGNAL_DEDUP_WINDOW_MINUTES: int = int(os.getenv("SIGNAL_DEDUP_WINDOW_MINUTES", "360"))
    SIGNAL_DEDUP_SIMILARITY: float = float(os.getenv("SIGNAL_DEDUP_SIMILARITY", "0.68"))
    SIGNAL_DEDUP_MIN_COMMON_CHARS: int = int(os.getenv("SIGNAL_DEDUP_MIN_COMMON_CHARS", "10"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    SESSION_PATH: str = "./session/tg_session"
    USE_LANGGRAPH_PIPELINE: bool = _as_bool(os.getenv("USE_LANGGRAPH_PIPELINE", "false"))

    # AI configuration
    AI_ENABLED: bool = _as_bool(os.getenv("AI_ENABLED", "false"))
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    # Support multiple Gemini API keys for rotation (comma-separated)
    GEMINI_API_KEYS: List[str] = [
        key.strip()
        for key in os.getenv("GEMINI_API_KEYS", "").split(",")
        if key.strip()
    ]
    # If GEMINI_API_KEYS is not set, fall back to single GEMINI_API_KEY
    if not GEMINI_API_KEYS and GEMINI_API_KEY:
        GEMINI_API_KEYS = [GEMINI_API_KEY]
    AI_API_KEY: str = os.getenv("AI_API_KEY", "") or GEMINI_API_KEY
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "gemini")
    AI_BASE_URL: str = os.getenv("AI_BASE_URL", "")
    AI_EXTRA_HEADERS: str = os.getenv("AI_EXTRA_HEADERS", "")
    AI_MODEL_NAME: str = os.getenv("AI_MODEL_NAME", "gemini-2.5-flash-lite")
    AI_SIGNAL_THRESHOLD: float = float(os.getenv("AI_SIGNAL_THRESHOLD", "0.65"))
    AI_TIMEOUT_SECONDS: float = float(os.getenv("AI_TIMEOUT_SECONDS", "8"))
    AI_MAX_CONCURRENCY: int = int(os.getenv("AI_MAX_CONCURRENCY", "2"))
    AI_RETRY_ATTEMPTS: int = int(os.getenv("AI_RETRY_ATTEMPTS", "1"))
    AI_RETRY_BACKOFF_SECONDS: float = float(os.getenv("AI_RETRY_BACKOFF_SECONDS", "1.5"))
    AI_SKIP_NEUTRAL_FORWARD: bool = _as_bool(os.getenv("AI_SKIP_NEUTRAL_FORWARD", "false"))

    # Forwarding thresholds (confidence-based filtering)
    AI_MIN_CONFIDENCE: float = float(os.getenv("AI_MIN_CONFIDENCE", "0.4"))
    AI_OBSERVE_THRESHOLD: float = float(os.getenv("AI_OBSERVE_THRESHOLD", "0.70"))
    AI_MIN_CONFIDENCE_KOL: float = float(os.getenv("AI_MIN_CONFIDENCE_KOL", "0.3"))
    AI_OBSERVE_THRESHOLD_KOL: float = float(os.getenv("AI_OBSERVE_THRESHOLD_KOL", "0.5"))

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
    MEMORY_DUPLICATE_ENABLED: bool = _as_bool(os.getenv("MEMORY_DUPLICATE_ENABLED", "true"))
    MEMORY_DUPLICATE_SIMILARITY: float = float(os.getenv("MEMORY_DUPLICATE_SIMILARITY", "0.6"))
    MEMORY_DUPLICATE_SUMMARY_RATIO: float = float(os.getenv("MEMORY_DUPLICATE_SUMMARY_RATIO", "0.82"))
    MEMORY_DUPLICATE_LOOKBACK_HOURS: int = int(os.getenv("MEMORY_DUPLICATE_LOOKBACK_HOURS", str(max(1, MEMORY_LOOKBACK_HOURS))))
    MEMORY_DUPLICATE_MIN_ASSET_OVERLAP: int = int(os.getenv("MEMORY_DUPLICATE_MIN_ASSET_OVERLAP", "1"))

    # Claude configuration (for deep analysis)
    CLAUDE_ENABLED: bool = _as_bool(os.getenv("CLAUDE_ENABLED", "false"))
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    CLAUDE_API_KEY: str = os.getenv("CLAUDE_API_KEY", "").strip() or ANTHROPIC_API_KEY
    CLAUDE_BASE_URL: str = os.getenv("CLAUDE_BASE_URL", "").strip() or ANTHROPIC_BASE_URL
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
    CLAUDE_TIMEOUT_SECONDS: float = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "30"))
    CLAUDE_MAX_TOOL_TURNS: int = int(os.getenv("CLAUDE_MAX_TOOL_TURNS", "3"))

    # MiniMax configuration (Claude-compatible)
    MINIMAX_API_KEY: str = os.getenv("MINIMAX_API_KEY", "").strip()
    MINIMAX_BASE_URL: str = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1").strip()
    MINIMAX_MODEL: str = os.getenv("MINIMAX_MODEL", CLAUDE_MODEL).strip()
    MINIMAX_TIMEOUT_SECONDS: float = float(
        os.getenv("MINIMAX_TIMEOUT_SECONDS", str(CLAUDE_TIMEOUT_SECONDS))
    )
    MINIMAX_MAX_TOOL_TURNS: int = int(
        os.getenv("MINIMAX_MAX_TOOL_TURNS", str(CLAUDE_MAX_TOOL_TURNS))
    )

    # Deep analysis unified configuration
    DEEP_ANALYSIS_ENABLED: bool = _as_bool(
        os.getenv("DEEP_ANALYSIS_ENABLED", os.getenv("CLAUDE_ENABLED", "false"))
    )
    DEEP_ANALYSIS_PROVIDER: str = os.getenv(
        "DEEP_ANALYSIS_PROVIDER",
        "claude" if CLAUDE_ENABLED else "gemini",
    ).strip().lower()
    DEEP_ANALYSIS_PLANNER: str = os.getenv("DEEP_ANALYSIS_PLANNER", "gemini").strip().lower()
    CODEX_CLI_PATH: str = os.getenv("CODEX_CLI_PATH", "codex").strip()
    CODEX_CLI_TIMEOUT: float = float(os.getenv("CODEX_CLI_TIMEOUT", "60"))
    CODEX_CLI_MAX_TOKENS: int = int(os.getenv("CODEX_CLI_MAX_TOKENS", "4000"))
    CODEX_CLI_CONTEXT: str = os.getenv(
        "CODEX_CLI_CONTEXT",
        "@docs/codex_cli_integration_plan.md",
    ).strip()
    CODEX_CLI_RETRY_ATTEMPTS: int = int(os.getenv("CODEX_CLI_RETRY_ATTEMPTS", "1"))
    CODEX_CLI_DISABLE_AFTER_FAILURES: int = int(os.getenv("CODEX_CLI_DISABLE_AFTER_FAILURES", "2"))
    CODEX_CLI_FAILURE_COOLDOWN_SECONDS: float = float(
        os.getenv("CODEX_CLI_FAILURE_COOLDOWN_SECONDS", "600")
    )
    CODEX_CLI_EXTRA_ARGS: List[str] = _parse_cli_args(os.getenv("CODEX_CLI_EXTRA_ARGS", ""))
    CODEX_CLI_WORKDIR: str = os.getenv("CODEX_CLI_WORKDIR", "").strip()

    # Claude CLI configuration
    CLAUDE_CLI_PATH: str = os.getenv("CLAUDE_CLI_PATH", "claude").strip()
    CLAUDE_CLI_TIMEOUT: float = float(os.getenv("CLAUDE_CLI_TIMEOUT", "60"))
    CLAUDE_CLI_RETRY_ATTEMPTS: int = int(os.getenv("CLAUDE_CLI_RETRY_ATTEMPTS", "1"))
    CLAUDE_CLI_EXTRA_ARGS: List[str] = _parse_cli_args(os.getenv("CLAUDE_CLI_EXTRA_ARGS", ""))
    CLAUDE_CLI_WORKDIR: str = os.getenv("CLAUDE_CLI_WORKDIR", "").strip()
    CLAUDE_CLI_ALLOWED_TOOLS: List[str] = _parse_tool_list(os.getenv("CLAUDE_CLI_ALLOWED_TOOLS", "Bash,Read"))

    # Claude CLI Deep Analysis Memory configuration
    CLAUDE_DEEP_MEMORY_ENABLED: bool = _as_bool(os.getenv("CLAUDE_DEEP_MEMORY_ENABLED", "false"))
    CLAUDE_DEEP_MEMORY_BASE_PATH: str = os.getenv("CLAUDE_DEEP_MEMORY_BASE_PATH", "./memories/claude_cli_deep_analysis").strip()
    CLAUDE_DEEP_MEMORY_MAX_FILE_SIZE: int = int(os.getenv("CLAUDE_DEEP_MEMORY_MAX_FILE_SIZE", "51200"))  # 50KB
    CLAUDE_DEEP_MEMORY_AUTO_CLEANUP: bool = _as_bool(os.getenv("CLAUDE_DEEP_MEMORY_AUTO_CLEANUP", "true"))
    CLAUDE_DEEP_MEMORY_CLEANUP_DAYS: int = int(os.getenv("CLAUDE_DEEP_MEMORY_CLEANUP_DAYS", "30"))
    TEXT_PLANNER_PROVIDER: str = os.getenv("TEXT_PLANNER_PROVIDER", "").strip().lower()
    TEXT_PLANNER_API_KEY: str = os.getenv("TEXT_PLANNER_API_KEY", "")
    TEXT_PLANNER_MODEL: str = os.getenv("TEXT_PLANNER_MODEL", "")
    TEXT_PLANNER_BASE_URL: str = os.getenv("TEXT_PLANNER_BASE_URL", "")
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
    # Gemini Deep Analysis API Keys (for rotation)
    GEMINI_DEEP_API_KEYS: List[str] = [
        key.strip()
        for key in os.getenv("GEMINI_DEEP_API_KEYS", "").split(",")
        if key.strip()
    ]

    # ==============================================
    # Qwen (千问) Deep Analysis Configuration
    # ==============================================
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    QWEN_BASE_URL: str = os.getenv(
        "QWEN_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    QWEN_DEEP_MODEL: str = os.getenv("QWEN_DEEP_MODEL", "qwen-plus")  # qwen-plus | qwen-max | qwen-turbo
    QWEN_DEEP_TIMEOUT_SECONDS: float = float(os.getenv("QWEN_DEEP_TIMEOUT_SECONDS", "30"))
    QWEN_DEEP_MAX_FUNCTION_TURNS: int = int(os.getenv("QWEN_DEEP_MAX_FUNCTION_TURNS", "6"))
    QWEN_ENABLE_SEARCH: bool = os.getenv("QWEN_ENABLE_SEARCH", "false").lower() == "true"  # 千问特色：内置联网搜索

    # ==============================================
    # OpenAI Deep Analysis Configuration (预留)
    # ==============================================
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_DEEP_MODEL: str = os.getenv("OPENAI_DEEP_MODEL", "gpt-4-turbo")
    OPENAI_DEEP_TIMEOUT_SECONDS: float = float(os.getenv("OPENAI_DEEP_TIMEOUT_SECONDS", "30"))
    OPENAI_DEEP_MAX_FUNCTION_TURNS: int = int(os.getenv("OPENAI_DEEP_MAX_FUNCTION_TURNS", "6"))

    # ==============================================
    # DeepSeek Deep Analysis Configuration (预留)
    # ==============================================
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_DEEP_MODEL: str = os.getenv("DEEPSEEK_DEEP_MODEL", "deepseek-chat")
    DEEPSEEK_DEEP_TIMEOUT_SECONDS: float = float(os.getenv("DEEPSEEK_DEEP_TIMEOUT_SECONDS", "30"))
    DEEPSEEK_DEEP_MAX_FUNCTION_TURNS: int = int(os.getenv("DEEPSEEK_DEEP_MAX_FUNCTION_TURNS", "6"))

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

    # Secondary Supabase datasource (optional, disabled by default)
    SUPABASE_SECONDARY_ENABLED: bool = _as_bool(
        os.getenv("SUPABASE_SECONDARY_ENABLED", "false")
    )
    SUPABASE_SECONDARY_URL: str = os.getenv("SUPABASE_SECONDARY_URL", "").strip()
    SUPABASE_SECONDARY_SERVICE_KEY: str = os.getenv(
        "SUPABASE_SECONDARY_SERVICE_KEY",
        "",
    ).strip()
    SUPABASE_SECONDARY_TABLE: str = os.getenv(
        "SUPABASE_SECONDARY_TABLE",
        "docs",
    ).strip()
    SUPABASE_SECONDARY_SIMILARITY_THRESHOLD: float = float(
        os.getenv("SUPABASE_SECONDARY_SIMILARITY_THRESHOLD", "0.75")
    )
    SUPABASE_SECONDARY_MAX_RESULTS: int = int(
        os.getenv("SUPABASE_SECONDARY_MAX_RESULTS", "6")
    )

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
    PRICE_ENABLED: bool = _as_bool(os.getenv("PRICE_ENABLED", "false"))
    PRICE_PROVIDER: str = os.getenv("PRICE_PROVIDER", "coinmarketcap")
    TOOL_PRICE_ENABLED: bool = _as_bool(os.getenv("TOOL_PRICE_ENABLED", "false"))
    TOOL_MACRO_ENABLED: bool = _as_bool(os.getenv("TOOL_MACRO_ENABLED", "false"))
    TOOL_ONCHAIN_ENABLED: bool = _as_bool(os.getenv("TOOL_ONCHAIN_ENABLED", "false"))
    TOOL_PROTOCOL_ENABLED: bool = _as_bool(os.getenv("TOOL_PROTOCOL_ENABLED", "false"))
    DEEP_ANALYSIS_PRICE_PROVIDER: str = os.getenv("DEEP_ANALYSIS_PRICE_PROVIDER", "coinmarketcap")
    DEEP_ANALYSIS_MACRO_PROVIDER: str = os.getenv("DEEP_ANALYSIS_MACRO_PROVIDER", "fred")
    DEEP_ANALYSIS_ONCHAIN_PROVIDER: str = os.getenv("DEEP_ANALYSIS_ONCHAIN_PROVIDER", "defillama")
    DEEP_ANALYSIS_PROTOCOL_PROVIDER: str = os.getenv("DEEP_ANALYSIS_PROTOCOL_PROVIDER", "defillama")

    # CoinMarketCap configuration
    COINMARKETCAP_API_KEY: str = os.getenv("COINMARKETCAP_API_KEY", "")
    COINMARKETCAP_API_BASE_URL: str = os.getenv("COINMARKETCAP_API_BASE_URL", "https://pro-api.coinmarketcap.com")
    PRICE_CRASH_ALERT_THRESHOLD: float = float(os.getenv("PRICE_CRASH_ALERT_THRESHOLD", "7.0"))
    PRICE_BTC_CORRELATION_THRESHOLD: float = float(os.getenv("PRICE_BTC_CORRELATION_THRESHOLD", "2.0"))

    # CoinGecko configuration (fallback)
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
    DEFI_LLAMA_STABLECOIN_URL: str = os.getenv(
        "DEFI_LLAMA_STABLECOIN_URL",
        "https://stablecoins.llama.fi/stablecoins",
    )
    ONCHAIN_CACHE_TTL_SECONDS: int = int(os.getenv("ONCHAIN_CACHE_TTL_SECONDS", "300"))
    ONCHAIN_REGISTRY_CACHE_SECONDS: int = int(
        os.getenv("ONCHAIN_REGISTRY_CACHE_SECONDS", "300")
    )
    ONCHAIN_TVL_DROP_THRESHOLD: float = float(
        os.getenv("ONCHAIN_TVL_DROP_THRESHOLD", "20.0")
    )
    ONCHAIN_REDEMPTION_USD_THRESHOLD: float = float(
        os.getenv("ONCHAIN_REDEMPTION_USD_THRESHOLD", "500000000.0")
    )
    DEFI_LLAMA_PROTOCOL_URL: str = os.getenv(
        "DEFI_LLAMA_PROTOCOL_URL",
        "https://api.llama.fi/protocol",
    )
    PROTOCOL_CACHE_TTL_SECONDS: int = int(os.getenv("PROTOCOL_CACHE_TTL_SECONDS", "600"))
    PROTOCOL_TOP_CHAIN_LIMIT: int = int(os.getenv("PROTOCOL_TOP_CHAIN_LIMIT", "5"))
    PROTOCOL_TVL_DROP_THRESHOLD_PCT: float = float(
        os.getenv("PROTOCOL_TVL_DROP_THRESHOLD_PCT", "15.0")
    )
    PROTOCOL_TVL_DROP_THRESHOLD_USD: float = float(
        os.getenv("PROTOCOL_TVL_DROP_THRESHOLD_USD", "300000000.0")
    )

    # Hyperliquid source prioritization configuration
    PRIORITY_KOL_HANDLES: Set[str] = {
        handle.strip().lower()
        for handle in os.getenv("PRIORITY_KOL_HANDLES", "sleepinrain,journey_of_someone,retardfrens").split(",")
        if handle.strip()
    }
    PRIORITY_KOL_FORCE_FORWARD: bool = _as_bool(os.getenv("PRIORITY_KOL_FORCE_FORWARD", "true"))
    PRIORITY_KOL_DEDUP_THRESHOLD: float = float(os.getenv("PRIORITY_KOL_DEDUP_THRESHOLD", "0.95"))

    # Email notification configuration
    EMAIL_ENABLED: bool = _as_bool(os.getenv("EMAIL_ENABLED", "false"))
    EMAIL_SMTP_HOST: str = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    EMAIL_SMTP_PORT: int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
    EMAIL_TO: str = os.getenv("EMAIL_TO", "")

    # Telegram Bot notification configuration
    BOT_ENABLED: bool = _as_bool(os.getenv("BOT_ENABLED", "false"))
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USER_CHAT_ID: str = os.getenv("BOT_USER_CHAT_ID", "")

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
        allowed_providers = {
            "claude",
            "gemini",
            "minimax",
            "codex_cli",
            "claude_cli",
            "qwen",
            "openai",
            "deepseek",
        }
        if provider not in allowed_providers:
            if provider:
                logger.warning("未知的 DEEP_ANALYSIS_PROVIDER=%s，自动回退为 claude", provider)
            provider = "claude" if cls.CLAUDE_ENABLED else "gemini"

        if fallback not in allowed_providers:
            if fallback:
                logger.warning("未知的深度分析备用引擎 %s，已忽略", fallback)
            fallback = ""
        elif fallback == provider:
            fallback = ""

        def _parse_context_refs(value: str) -> List[str]:
            if not value:
                return []
            refs: List[str] = []
            for raw in value.replace(",", "\n").splitlines():
                candidate = raw.strip()
                if candidate:
                    refs.append(candidate)
            return refs

        config: Dict[str, Any] = {
            "enabled": enabled,
            "provider": provider,
            "fallback_provider": fallback,
            "claude": {
                "api_key": cls.CLAUDE_API_KEY,
                "base_url": cls.CLAUDE_BASE_URL,
                "model": cls.CLAUDE_MODEL,
                "timeout": cls.CLAUDE_TIMEOUT_SECONDS,
                "max_tool_turns": cls.CLAUDE_MAX_TOOL_TURNS,
            },
            "minimax": {
                "api_key": cls.MINIMAX_API_KEY,
                "base_url": cls.MINIMAX_BASE_URL,
                "model": cls.MINIMAX_MODEL,
                "timeout": cls.MINIMAX_TIMEOUT_SECONDS,
                "max_tool_turns": cls.MINIMAX_MAX_TOOL_TURNS,
            },
            "gemini": {
                "model": cls.GEMINI_DEEP_MODEL,
                "timeout": cls.GEMINI_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": cls.GEMINI_DEEP_MAX_FUNCTION_TURNS,
                "max_retries": cls.GEMINI_DEEP_RETRY_ATTEMPTS,
                "retry_backoff": cls.GEMINI_DEEP_RETRY_BACKOFF_SECONDS,
                "api_key": cls.GEMINI_API_KEY,
                "api_keys": cls.GEMINI_DEEP_API_KEYS if cls.GEMINI_DEEP_API_KEYS else cls.GEMINI_API_KEYS,
            },
            "codex_cli": {
                "cli_path": cls.CODEX_CLI_PATH,
                "timeout": cls.CODEX_CLI_TIMEOUT,
                "max_retries": cls.CODEX_CLI_RETRY_ATTEMPTS,
                "disable_after_failures": cls.CODEX_CLI_DISABLE_AFTER_FAILURES,
                "failure_cooldown": cls.CODEX_CLI_FAILURE_COOLDOWN_SECONDS,
                "context_refs": _parse_context_refs(cls.CODEX_CLI_CONTEXT),
                "extra_args": list(cls.CODEX_CLI_EXTRA_ARGS),
                "working_directory": cls.CODEX_CLI_WORKDIR,
            },
            "claude_cli": {
                "cli_path": cls.CLAUDE_CLI_PATH,
                "timeout": cls.CLAUDE_CLI_TIMEOUT,
                "max_retries": cls.CLAUDE_CLI_RETRY_ATTEMPTS,
                "extra_args": list(cls.CLAUDE_CLI_EXTRA_ARGS),
                "working_directory": cls.CLAUDE_CLI_WORKDIR,
                "allowed_tools": list(cls.CLAUDE_CLI_ALLOWED_TOOLS),
            },
            "qwen": {
                "api_key": cls.DASHSCOPE_API_KEY,
                "base_url": cls.QWEN_BASE_URL,
                "model": cls.QWEN_DEEP_MODEL,
                "timeout": cls.QWEN_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": cls.QWEN_DEEP_MAX_FUNCTION_TURNS,
                "enable_search": cls.QWEN_ENABLE_SEARCH,
            },
            "openai": {
                "api_key": cls.OPENAI_API_KEY,
                "base_url": cls.OPENAI_BASE_URL,
                "model": cls.OPENAI_DEEP_MODEL,
                "timeout": cls.OPENAI_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": cls.OPENAI_DEEP_MAX_FUNCTION_TURNS,
            },
            "deepseek": {
                "api_key": cls.DEEPSEEK_API_KEY,
                "base_url": cls.DEEPSEEK_BASE_URL,
                "model": cls.DEEPSEEK_DEEP_MODEL,
                "timeout": cls.DEEPSEEK_DEEP_TIMEOUT_SECONDS,
                "max_function_turns": cls.DEEPSEEK_DEEP_MAX_FUNCTION_TURNS,
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

        try:
            cls.validate_secondary_config()
        except ValueError as exc:
            print(f"❌ {exc}")
            return False

        if cls.AI_ENABLED and not cls.AI_API_KEY:
            print("⚠️ 已启用 AI，但 AI_API_KEY/GEMINI_API_KEY 未配置，将自动降级为传统模式")
        return True

    @classmethod
    def validate_secondary_config(cls) -> None:
        """Validate optional secondary Supabase datasource configuration."""

        if not cls.SUPABASE_SECONDARY_ENABLED:
            return

        missing: list[str] = []
        if not cls.SUPABASE_SECONDARY_URL:
            missing.append("SUPABASE_SECONDARY_URL")
        if not cls.SUPABASE_SECONDARY_SERVICE_KEY:
            missing.append("SUPABASE_SECONDARY_SERVICE_KEY")

        if missing:
            raise ValueError(
                "已启用副记忆库，但缺少必需配置: " + ", ".join(missing)
            )
