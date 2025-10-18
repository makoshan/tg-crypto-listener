"""LangGraph Studio entrypoint with stubbed dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .langgraph_pipeline import LangGraphMessagePipeline, PipelineDependencies
from ..utils import MessageDeduplicator

logger = logging.getLogger("langgraph.studio")


@dataclass
class StudioConfig:
    """Lightweight config object used for LangGraph Studio."""

    FILTER_KEYWORDS: set[str]
    TRANSLATION_ENABLED: bool = False
    TRANSLATION_TARGET_LANGUAGE: str = "zh"
    AI_SKIP_NEUTRAL_FORWARD: bool = False
    FORWARD_INCLUDE_TRANSLATION: bool = False
    MEMORY_ENABLED: bool = False
    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_SIMILARITY_THRESHOLD: float = 0.85
    EMBEDDING_TIME_WINDOW_HOURS: int = 24
    MEMORY_MAX_NOTES: int = 3
    MEMORY_MIN_CONFIDENCE: float = 0.6


def _collect_keywords_stub(*texts: str) -> List[str]:
    hits: List[str] = []
    for text in texts:
        if not text:
            continue
        lower_text = text.lower()
        for kw in _STUDIO_CONFIG.FILTER_KEYWORDS:
            if kw in lower_text:
                hits.append(kw)
    return hits


async def _extract_media_stub(_message: Any) -> List[Dict[str, Any]]:
    return []


def _build_ai_kwargs_stub(signal_result: Any, _source: str, _is_priority_kol: bool = False) -> Dict[str, Any]:
    if not signal_result or getattr(signal_result, "status", None) != "success":
        return {}
    return {
        "ai_summary": getattr(signal_result, "summary", ""),
        "ai_action": getattr(signal_result, "action", "observe"),
        "ai_direction": getattr(signal_result, "direction", "neutral"),
        "ai_event_type": getattr(signal_result, "event_type", "other"),
        "ai_asset": getattr(signal_result, "asset", ""),
        "ai_asset_names": getattr(signal_result, "asset_names", ""),
        "ai_confidence": getattr(signal_result, "confidence", 0.0),
        "ai_strength": getattr(signal_result, "strength", "low"),
        "ai_timeframe": getattr(signal_result, "timeframe", "short"),
        "ai_risk_flags": getattr(signal_result, "risk_flags", []),
        "ai_notes": getattr(signal_result, "notes", ""),
        "ai_alert": getattr(signal_result, "alert", None),
        "ai_severity": getattr(signal_result, "severity", None),
    }


def _should_include_original_stub(
    *,
    original_text: Optional[str],
    translated_text: Optional[str],
    signal_result: Any = None,
) -> bool:
    return bool(original_text and original_text.strip()) and not translated_text


def _append_links_stub(message: str, links: List[str]) -> str:
    if not links:
        return message
    rendered = "\n".join(f"source: {link}" for link in links)
    return f"{message}\n{rendered}"


def _collect_links_stub(
    _signal_result: Any,
    _formatted_message: str,
    _translated_text: Optional[str],
    _original_text: Optional[str],
) -> List[str]:
    return []


async def _persist_event_stub(*_args: Any, **_kwargs: Any) -> None:
    return None


def _update_ai_stats_stub(_signal_result: Any) -> None:
    return None


_STUDIO_CONFIG = StudioConfig(FILTER_KEYWORDS=set())


def build_pipeline() -> LangGraphMessagePipeline:
    """Create LangGraph pipeline with stubbed dependencies."""

    stats: Dict[str, Any] = {
        "total_received": 0,
        "filtered_out": 0,
        "duplicates": 0,
        "forwarded": 0,
        "errors": 0,
        "ai_processed": 0,
        "ai_actions": 0,
        "ai_errors": 0,
        "ai_skipped": 0,
        "translations": 0,
        "translation_errors": 0,
    }

    dependencies = PipelineDependencies(
        config=_STUDIO_CONFIG,
        deduplicator=MessageDeduplicator(window_hours=24),
        translator=None,
        ai_engine=None,
        forwarder=None,
        news_repository=None,
        signal_repository=None,
        memory_repository=None,
        price_enabled=False,
        price_tool=None,
        db_enabled=False,
        stats=stats,
        logger=logger,
        collect_keywords=_collect_keywords_stub,
        extract_media=_extract_media_stub,
        build_ai_kwargs=_build_ai_kwargs_stub,
        should_include_original=_should_include_original_stub,
        append_links=_append_links_stub,
        collect_links=_collect_links_stub,
        persist_event=_persist_event_stub,
        update_ai_stats=_update_ai_stats_stub,
    )

    return LangGraphMessagePipeline(dependencies)


def build_graph():
    """Return compiled LangGraph for LangGraph Studio CLI."""

    pipeline = build_pipeline()
    return pipeline._graph  # pylint: disable=protected-access
