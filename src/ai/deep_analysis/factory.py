"""Deep analysis engine factory."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.ai.anthropic_client import AnthropicClient
from src.ai.deep_analysis.base import DeepAnalysisEngine, DeepAnalysisError
from src.ai.deep_analysis.claude_cli import ClaudeCliDeepAnalysisEngine
from src.ai.deep_analysis.codex_cli import CodexCliDeepAnalysisEngine
from src.ai.deep_analysis.claude import ClaudeDeepAnalysisEngine
from src.ai.deep_analysis.gemini import GeminiDeepAnalysisEngine
from src.ai.deep_analysis.openai_compatible import OpenAICompatibleEngine
from src.ai.gemini_function_client import GeminiFunctionCallingClient
from src.memory.factory import MemoryBackendBundle

logger = logging.getLogger(__name__)


def _normalise_openai_compatible_model(provider: str, model: str) -> str:
    """Normalise provider-specific model identifiers."""

    if not model:
        return model

    if provider != "qwen":
        return model

    trimmed = model.strip()
    lowered = trimmed.lower()

    alias_map = {
        "qwen/qwen3-max-instruct": "qwen3-max",
        "qwen/qwen-max": "qwen-max",
        "qwen/qwen-plus": "qwen-plus",
        "qwen/qwen-turbo": "qwen-turbo",
    }

    normalised = alias_map.get(lowered, trimmed)
    if normalised != trimmed:
        logger.info(
            "ℹ️ 标准化 Qwen 模型名称: %s -> %s",
            trimmed,
            normalised,
        )
    return normalised


def create_deep_analysis_engine(
    *,
    provider: str,
    config: Any,
    parse_callback: Callable[[str], "SignalResult"],
    memory_bundle: Optional[MemoryBackendBundle],
) -> DeepAnalysisEngine:
    provider = provider.strip().lower()
    deep_config = getattr(config, "get_deep_analysis_config", lambda: {})()

    if provider == "claude_cli":
        logger.info("🔧 开始初始化 Claude CLI 深度分析引擎...")

        claude_cli_cfg = deep_config.get("claude_cli", {})
        logger.debug("Claude CLI 配置: %s", claude_cli_cfg)

        cli_path = claude_cli_cfg.get("cli_path") or getattr(config, "CLAUDE_CLI_PATH", "claude")
        if not cli_path:
            logger.error("❌ Claude CLI 路径未配置")
            raise DeepAnalysisError("Claude CLI 路径未配置，无法启用深度分析")

        logger.debug("Claude CLI 可执行文件路径: %s", cli_path)

        extra_args = claude_cli_cfg.get("extra_args") or []
        if isinstance(extra_args, str):
            extra_args = [extra_args]
        extra_args = [str(arg).strip() for arg in extra_args if str(arg).strip()]
        if extra_args:
            logger.debug("Claude CLI 额外参数: %s", extra_args)

        allowed_tools = claude_cli_cfg.get("allowed_tools") or []
        if isinstance(allowed_tools, str):
            allowed_tools = [allowed_tools]
        allowed_tools = [str(tool).strip() for tool in allowed_tools if str(tool).strip()]
        if allowed_tools:
            logger.debug("Claude CLI 允许的工具: %s", allowed_tools)

        timeout = float(claude_cli_cfg.get("timeout") or getattr(config, "CLAUDE_CLI_TIMEOUT", 60.0))
        max_retries = int(claude_cli_cfg.get("max_retries") or getattr(config, "CLAUDE_CLI_RETRY_ATTEMPTS", 1))
        working_dir = claude_cli_cfg.get("working_directory") or getattr(config, "CLAUDE_CLI_WORKDIR", "") or None

        # Initialize memory handler if enabled
        memory_handler = None
        memory_enabled = getattr(config, "CLAUDE_DEEP_MEMORY_ENABLED", False)
        if memory_enabled:
            from src.memory.claude_deep_memory_handler import ClaudeDeepAnalysisMemoryHandler

            memory_handler = ClaudeDeepAnalysisMemoryHandler(
                base_path=getattr(config, "CLAUDE_DEEP_MEMORY_BASE_PATH", "./memories/claude_cli_deep_analysis"),
                max_file_size=getattr(config, "CLAUDE_DEEP_MEMORY_MAX_FILE_SIZE", 51200),
                auto_cleanup=getattr(config, "CLAUDE_DEEP_MEMORY_AUTO_CLEANUP", True),
                cleanup_days=getattr(config, "CLAUDE_DEEP_MEMORY_CLEANUP_DAYS", 30),
            )
            logger.info("✅ Claude CLI 深度分析记忆系统已启用: base_path=%s", memory_handler.base_path)

        logger.info(
            "🧠 Claude CLI 深度分析引擎已初始化: path=%s timeout=%.1fs max_retries=%d working_dir=%s allowed_tools=%s memory_enabled=%s",
            cli_path,
            timeout,
            max_retries,
            working_dir or ".",
            allowed_tools,
            memory_enabled,
        )
        return ClaudeCliDeepAnalysisEngine(
            cli_path=cli_path,
            timeout=timeout,
            parse_json_callback=parse_callback,
            extra_cli_args=tuple(extra_args),
            max_retries=max_retries,
            working_directory=working_dir,
            allowed_tools=tuple(allowed_tools) if allowed_tools else None,
            memory_handler=memory_handler,
        )

    if provider == "codex_cli":
        logger.info("🔧 开始初始化 Codex CLI 深度分析引擎...")

        codex_cfg = deep_config.get("codex_cli", {})
        logger.debug("Codex CLI 配置: %s", codex_cfg)

        cli_path = codex_cfg.get("cli_path") or getattr(config, "CODEX_CLI_PATH", "codex")
        if not cli_path:
            logger.error("❌ Codex CLI 路径未配置")
            raise DeepAnalysisError("Codex CLI 路径未配置，无法启用深度分析")

        logger.debug("Codex CLI 可执行文件路径: %s", cli_path)

        context_refs = codex_cfg.get("context_refs") or []
        if isinstance(context_refs, str):
            context_refs = [context_refs]
        context_refs = [str(ref).strip() for ref in context_refs if str(ref).strip()]
        if context_refs:
            logger.debug("Codex CLI 上下文引用数量: %d", len(context_refs))

        extra_args = codex_cfg.get("extra_args") or []
        if isinstance(extra_args, str):
            extra_args = [extra_args]
        extra_args = [str(arg).strip() for arg in extra_args if str(arg).strip()]
        if extra_args:
            logger.debug("Codex CLI 额外参数: %s", extra_args)

        timeout = float(codex_cfg.get("timeout") or getattr(config, "CODEX_CLI_TIMEOUT", 60.0))
        max_retries = int(codex_cfg.get("max_retries") or getattr(config, "CODEX_CLI_RETRY_ATTEMPTS", 1))
        working_dir = codex_cfg.get("working_directory") or getattr(config, "CODEX_CLI_WORKDIR", "") or None
        disable_after_failures = int(
            codex_cfg.get("disable_after_failures")
            or getattr(config, "CODEX_CLI_DISABLE_AFTER_FAILURES", 2)
        )
        failure_cooldown = float(
            codex_cfg.get("failure_cooldown")
            or getattr(config, "CODEX_CLI_FAILURE_COOLDOWN_SECONDS", 600.0)
        )

        logger.info(
            "🧠 Codex CLI 深度分析引擎已初始化: path=%s timeout=%.1fs max_retries=%d working_dir=%s context_refs=%d extra_args=%d disable_after=%d cooldown=%.0fs",
            cli_path,
            timeout,
            max_retries,
            working_dir or ".",
            len(context_refs),
            len(extra_args),
            disable_after_failures,
            failure_cooldown,
        )
        return CodexCliDeepAnalysisEngine(
            cli_path=cli_path,
            timeout=timeout,
            parse_json_callback=parse_callback,
            context_refs=tuple(context_refs),
            extra_cli_args=tuple(extra_args),
            max_retries=max_retries,
            working_directory=working_dir,
            disable_after_failures=disable_after_failures,
            failure_cooldown=failure_cooldown,
        )

    if provider in ("claude", "minimax"):
        cfg_key = "claude" if provider == "claude" else "minimax"
        provider_cfg = deep_config.get(cfg_key, {})

        api_key_attr = "CLAUDE_API_KEY" if provider == "claude" else "MINIMAX_API_KEY"
        api_key = provider_cfg.get("api_key") or getattr(config, api_key_attr, "")
        if not api_key:
            raise DeepAnalysisError(f"{provider.capitalize()} API key 未配置，无法启用深度分析")

        base_url_attr = "CLAUDE_BASE_URL" if provider == "claude" else "MINIMAX_BASE_URL"
        base_url = (provider_cfg.get("base_url") or getattr(config, base_url_attr, "")).strip()

        memory_handler = memory_bundle.handler if memory_bundle else None
        if memory_handler is None:
            from src.memory.memory_tool_handler import MemoryToolHandler

            memory_handler = MemoryToolHandler(
                base_path=getattr(config, "MEMORY_DIR", "./memories")
            )

        client = AnthropicClient(
            api_key=api_key,
            base_url=base_url or None,
            model_name=provider_cfg.get("model")
            or getattr(
                config,
                "CLAUDE_MODEL" if provider == "claude" else "MINIMAX_MODEL",
                "claude-sonnet-4-5-20250929",
            ),
            timeout=float(
                provider_cfg.get("timeout")
                or getattr(config, "CLAUDE_TIMEOUT_SECONDS" if provider == "claude" else "MINIMAX_TIMEOUT_SECONDS", 30.0)
            ),
            max_tool_turns=int(
                provider_cfg.get("max_tool_turns")
                or getattr(config, "CLAUDE_MAX_TOOL_TURNS" if provider == "claude" else "MINIMAX_MAX_TOOL_TURNS", 5)
            ),
            memory_handler=memory_handler,
            context_trigger_tokens=getattr(config, "MEMORY_CONTEXT_TRIGGER_TOKENS", 10000),
            context_keep_tools=getattr(config, "MEMORY_CONTEXT_KEEP_TOOLS", 2),
            context_clear_at_least=getattr(config, "MEMORY_CONTEXT_CLEAR_AT_LEAST", 500),
        )
        logger.info(
            "🧠 %s 深度分析引擎已初始化 (base_url=%s)",
            "Claude" if provider == "claude" else "MiniMax",
            base_url or "default",
        )
        return ClaudeDeepAnalysisEngine(client=client, parse_json_callback=parse_callback)

    if provider == "gemini":
        gemini_cfg = deep_config.get("gemini", {})
        api_key = gemini_cfg.get("api_key") or getattr(config, "GEMINI_API_KEY", "")
        if not api_key:
            raise DeepAnalysisError("Gemini API key 未配置，无法启用深度分析")

        # Get all Gemini API keys for rotation
        api_keys = gemini_cfg.get("api_keys") or getattr(config, "GEMINI_API_KEYS", [])

        client = GeminiFunctionCallingClient(
            api_key=api_key,
            model_name=gemini_cfg.get("model") or getattr(config, "GEMINI_DEEP_MODEL", "gemini-2.5-pro"),
            timeout=float(gemini_cfg.get("timeout") or getattr(config, "GEMINI_DEEP_TIMEOUT_SECONDS", 25.0)),
            max_retries=int(gemini_cfg.get("max_retries") or getattr(config, "GEMINI_DEEP_RETRY_ATTEMPTS", 1)),
            retry_backoff_seconds=float(
                gemini_cfg.get("retry_backoff") or getattr(config, "GEMINI_DEEP_RETRY_BACKOFF_SECONDS", 1.5)
            ),
            api_keys=api_keys if api_keys else None,
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
            config=config,
        )

    # OpenAI Compatible API (Qwen, OpenAI, DeepSeek)
    if provider in ["qwen", "openai", "deepseek"]:
        logger.info(f"🔧 开始初始化 {provider.upper()} 深度分析引擎...")

        # Get provider-specific config
        provider_cfg = deep_config.get(provider, {})

        # API Key
        api_key_attr = f"{provider.upper()}_API_KEY" if provider != "qwen" else "DASHSCOPE_API_KEY"
        api_key = provider_cfg.get("api_key") or getattr(config, api_key_attr, "")
        if not api_key:
            raise DeepAnalysisError(f"{provider.upper()} API key 未配置，无法启用深度分析")

        # Base URL
        base_url_map = {
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
        }
        base_url_attr = f"{provider.upper()}_BASE_URL"
        base_url = provider_cfg.get("base_url") or getattr(config, base_url_attr, base_url_map[provider])

        # Model
        model_map = {
            "qwen": "qwen-plus",
            "openai": "gpt-4-turbo",
            "deepseek": "deepseek-chat",
        }
        model_attr = f"{provider.upper()}_DEEP_MODEL"
        model = provider_cfg.get("model") or getattr(config, model_attr, model_map[provider])

        # Enable search (Qwen specific)
        enable_search = False
        if provider == "qwen":
            enable_search = provider_cfg.get("enable_search") or getattr(config, "QWEN_ENABLE_SEARCH", False)
            model = _normalise_openai_compatible_model(provider, model)

        # Timeout
        timeout_attr = f"{provider.upper()}_DEEP_TIMEOUT_SECONDS"
        timeout = float(provider_cfg.get("timeout") or getattr(config, timeout_attr, 30.0))

        # Max function turns
        max_turns_attr = f"{provider.upper()}_DEEP_MAX_FUNCTION_TURNS"
        max_turns = int(provider_cfg.get("max_function_turns") or getattr(config, max_turns_attr, 6))

        logger.info(
            f"🧠 {provider.upper()} 深度分析引擎已初始化: "
            f"model={model}, enable_search={enable_search}, max_turns={max_turns}"
        )

        return OpenAICompatibleEngine(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
            enable_search=enable_search,
            timeout=timeout,
            max_function_turns=max_turns,
            parse_json_callback=parse_callback,
            memory_bundle=memory_bundle,
            config=config,
        )

    raise DeepAnalysisError(f"未知的深度分析提供商: {provider}")


from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import SignalResult
