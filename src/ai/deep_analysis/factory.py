"""Deep analysis engine factory."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.ai.anthropic_client import AnthropicClient
from src.ai.deep_analysis.base import DeepAnalysisEngine, DeepAnalysisError
from src.ai.deep_analysis.claude import ClaudeDeepAnalysisEngine
from src.ai.deep_analysis.gemini import GeminiDeepAnalysisEngine
from src.ai.gemini_function_client import GeminiFunctionCallingClient
from src.memory.factory import MemoryBackendBundle

logger = logging.getLogger(__name__)


def create_deep_analysis_engine(
    *,
    provider: str,
    config: Any,
    parse_callback: Callable[[str], "SignalResult"],
    memory_bundle: Optional[MemoryBackendBundle],
) -> DeepAnalysisEngine:
    provider = provider.strip().lower()
    deep_config = getattr(config, "get_deep_analysis_config", lambda: {})()

    if provider == "claude":
        claude_cfg = deep_config.get("claude", {})
        api_key = claude_cfg.get("api_key") or getattr(config, "CLAUDE_API_KEY", "")
        if not api_key:
            raise DeepAnalysisError("Claude API key 未配置，无法启用深度分析")

        memory_handler = memory_bundle.handler if memory_bundle else None
        if memory_handler is None:
            from src.memory.memory_tool_handler import MemoryToolHandler

            memory_handler = MemoryToolHandler(
                base_path=getattr(config, "MEMORY_DIR", "./memories")
            )

        client = AnthropicClient(
            api_key=api_key,
            model_name=claude_cfg.get("model") or getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
            timeout=float(claude_cfg.get("timeout") or getattr(config, "CLAUDE_TIMEOUT_SECONDS", 30.0)),
            max_tool_turns=int(claude_cfg.get("max_tool_turns") or getattr(config, "CLAUDE_MAX_TOOL_TURNS", 5)),
            memory_handler=memory_handler,
            context_trigger_tokens=getattr(config, "MEMORY_CONTEXT_TRIGGER_TOKENS", 10000),
            context_keep_tools=getattr(config, "MEMORY_CONTEXT_KEEP_TOOLS", 2),
            context_clear_at_least=getattr(config, "MEMORY_CONTEXT_CLEAR_AT_LEAST", 500),
        )
        logger.info("🧠 Claude 深度分析引擎已初始化")
        return ClaudeDeepAnalysisEngine(client=client, parse_json_callback=parse_callback)

    if provider == "gemini":
        gemini_cfg = deep_config.get("gemini", {})
        api_key = gemini_cfg.get("api_key") or getattr(config, "GEMINI_API_KEY", "")
        if not api_key:
            raise DeepAnalysisError("Gemini API key 未配置，无法启用深度分析")

        client = GeminiFunctionCallingClient(
            api_key=api_key,
            model_name=gemini_cfg.get("model") or getattr(config, "GEMINI_DEEP_MODEL", "gemini-2.5-pro"),
            timeout=float(gemini_cfg.get("timeout") or getattr(config, "GEMINI_DEEP_TIMEOUT_SECONDS", 25.0)),
            max_retries=int(gemini_cfg.get("max_retries") or getattr(config, "GEMINI_DEEP_RETRY_ATTEMPTS", 1)),
            retry_backoff_seconds=float(
                gemini_cfg.get("retry_backoff") or getattr(config, "GEMINI_DEEP_RETRY_BACKOFF_SECONDS", 1.5)
            ),
        )

        memory_limit = getattr(config, "MEMORY_MAX_NOTES", 3)
        memory_min_conf = getattr(config, "MEMORY_MIN_CONFIDENCE", 0.6)
        max_turns = int(gemini_cfg.get("max_function_turns") or getattr(config, "GEMINI_DEEP_MAX_FUNCTION_TURNS", 6))

        logger.info("🧠 Gemini 深度分析引擎已初始化")
        return GeminiDeepAnalysisEngine(
            client=client,
            memory_bundle=memory_bundle,
            parse_json_callback=parse_callback,
            max_function_turns=max_turns,
            memory_limit=memory_limit,
            memory_min_confidence=memory_min_conf,
        )

    raise DeepAnalysisError(f"未知的深度分析提供商: {provider}")


from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import SignalResult
