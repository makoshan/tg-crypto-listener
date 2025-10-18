"""
Planner factory for creating appropriate planner instances.

Provides centralized planner instantiation based on configuration,
enabling flexible switching between different planning engines.
"""

import logging
from typing import Any

from .base import BasePlanner
from .codex_cli_planner import CodexCliPlanner
from .gemini_planner import GeminiPlanner
from .text_planner import TextPlanner

logger = logging.getLogger(__name__)


def create_planner(
    planner_type: str,
    engine: Any,
    config: Any
) -> BasePlanner:
    """
    Create a planner instance based on configuration.

    Args:
        planner_type: Planner type identifier
            - "gemini": Gemini Function Calling (default, high quality)
            - "codex_cli": Claude Code CLI (advanced reasoning)
            - "text_only": Generic text models (cost-effective)
        engine: Deep analysis engine instance
        config: Configuration object

    Returns:
        BasePlanner instance

    Raises:
        ValueError: If planner_type is invalid or configuration is missing
        RuntimeError: If planner initialization fails

    Examples:
        >>> planner = create_planner("gemini", engine, config)
        >>> planner = create_planner("codex_cli", engine, config)
        >>> planner = create_planner("text_only", engine, config)
    """
    planner_type = planner_type.strip().lower()

    logger.info("创建 Planner: type=%s", planner_type)

    try:
        if planner_type == "gemini":
            planner = GeminiPlanner(engine, config)
            logger.info("✅ Gemini Planner 已创建 (Function Calling)")
            return planner

        elif planner_type == "codex_cli":
            planner = CodexCliPlanner(engine, config)
            logger.info(
                "✅ Codex CLI Planner 已创建 (CLI: %s, timeout: %.1fs)",
                getattr(config, "CODEX_CLI_PATH", "codex"),
                getattr(config, "CODEX_CLI_TIMEOUT", 60.0),
            )
            return planner

        elif planner_type == "text_only":
            planner = TextPlanner(engine, config)
            provider = getattr(config, "TEXT_PLANNER_PROVIDER", "unknown")
            model = getattr(config, "TEXT_PLANNER_MODEL", "unknown")
            logger.info(
                "✅ Text Planner 已创建 (provider: %s, model: %s)",
                provider,
                model,
            )
            return planner

        else:
            raise ValueError(
                f"Invalid DEEP_ANALYSIS_PLANNER: '{planner_type}'. "
                f"Supported values: gemini, codex_cli, text_only"
            )

    except ValueError:
        # Re-raise ValueError as-is
        raise
    except Exception as exc:
        # Wrap other exceptions
        logger.error("Planner 创建失败 (type=%s): %s", planner_type, exc)
        raise RuntimeError(
            f"Failed to create planner '{planner_type}': {exc}"
        ) from exc


def create_planner_with_fallback(
    primary_type: str,
    fallback_type: str,
    engine: Any,
    config: Any
) -> BasePlanner:
    """
    Create planner with fallback support.

    Attempts to create primary planner, falls back to secondary if it fails.
    Useful for scenarios where CLI or external dependencies might not be available.

    Args:
        primary_type: Primary planner type
        fallback_type: Fallback planner type
        engine: Deep analysis engine instance
        config: Configuration object

    Returns:
        BasePlanner instance (primary or fallback)

    Examples:
        >>> # Try CLI first, fallback to Gemini
        >>> planner = create_planner_with_fallback(
        ...     "codex_cli", "gemini", engine, config
        ... )
    """
    try:
        return create_planner(primary_type, engine, config)
    except Exception as exc:
        logger.warning(
            "Primary planner '%s' 创建失败: %s. 降级到 '%s'",
            primary_type,
            exc,
            fallback_type,
        )
        return create_planner(fallback_type, engine, config)
