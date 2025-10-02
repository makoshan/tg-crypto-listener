"""Repository layer encapsulating Supabase persistence logic."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .models import AiSignalPayload, NewsEventPayload, StrategyInsightPayload
from .supabase_client import SupabaseClient, SupabaseError


def _strip_none(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


class NewsEventRepository:
    """Persistence helpers for the news_events table."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    async def check_duplicate(self, hash_raw: str) -> Optional[int]:
        if not hash_raw:
            return None
        record = await self._client.select_one(
            "news_events",
            filters={"hash_raw": hash_raw},
            columns="id",
        )
        if record and "id" in record:
            return int(record["id"])
        return None

    async def insert_event(self, payload: NewsEventPayload) -> Optional[int]:
        data = {
            "source": payload.source,
            "source_message_id": payload.source_message_id,
            "source_url": payload.source_url,
            "published_at": payload.published_at.isoformat(),
            "content_text": payload.content_text,
            "summary": payload.summary,
            "translated_text": payload.translated_text,
            "language": payload.language,
            "media_refs": payload.media_refs or [],
            "hash_raw": payload.hash_raw,
            "hash_canonical": payload.hash_canonical,
            "keywords_hit": payload.keywords_hit or [],
            "ingest_status": payload.ingest_status,
            "metadata": payload.metadata or {},
        }
        record = await self._client.insert("news_events", _strip_none(data))
        if record and "id" in record:
            return int(record["id"])
        return None


class AiSignalRepository:
    """Persistence helpers for the ai_signals table."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    async def insert_signal(self, payload: AiSignalPayload) -> Optional[int]:
        data = {
            "news_event_id": payload.news_event_id,
            "model_name": payload.model_name,
            "summary_cn": payload.summary_cn,
            "event_type": payload.event_type,
            "assets": payload.assets,
            "asset_names": payload.asset_names,
            "action": payload.action,
            "direction": payload.direction,
            "confidence": payload.confidence,
            "strength": payload.strength,
            "risk_flags": payload.risk_flags,
            "notes": payload.notes,
            "links": payload.links,
            "execution_path": payload.execution_path,
            "should_alert": payload.should_alert,
            "latency_ms": payload.latency_ms,
            "raw_response": payload.raw_response,
        }
        record = await self._client.insert("ai_signals", _strip_none(data))
        if record and "id" in record:
            return int(record["id"])
        return None


class StrategyInsightRepository:
    """Persistence helpers for strategy_insights table."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client

    async def insert_insight(self, payload: StrategyInsightPayload) -> Optional[int]:
        data = {
            "title": payload.title,
            "summary": payload.summary,
            "narrative": payload.narrative,
            "relation": payload.relation,
            "action": payload.action,
            "confidence": payload.confidence,
            "source_urls": payload.source_urls,
            "news_event_ids": payload.news_event_ids,
            "ai_signal_ids": payload.ai_signal_ids,
            "tags": payload.tags,
        }
        record = await self._client.insert("strategy_insights", _strip_none(data))
        if record and "id" in record:
            return int(record["id"])
        return None
