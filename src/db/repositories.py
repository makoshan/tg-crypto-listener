"""Repository layer encapsulating Supabase persistence logic."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.utils import setup_logger

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

    async def check_duplicate_by_embedding(
        self,
        embedding: List[float],
        threshold: float = 0.92,
        time_window_hours: int = 72,
    ) -> Optional[Dict[str, Any]]:
        """Check for semantically similar events using vector similarity."""
        if not embedding:
            return None

        try:
            response = await self._client.rpc(
                "find_similar_events",
                {
                    "query_embedding": embedding,
                    "similarity_threshold": threshold,
                    "time_window_hours": time_window_hours,
                    "max_results": 1,
                },
            )
        except SupabaseError:
            return None
        except Exception:
            return None

        if isinstance(response, list) and response:
            result = response[0]
            return {
                "id": int(result["id"]),
                "content_text": result.get("content_text", ""),
                "similarity": float(result.get("similarity", 0.0)),
            }
        return None

    async def insert_event(self, payload: NewsEventPayload) -> Optional[int]:
        embedding_str = None
        if payload.embedding:
            embedding_str = "[" + ",".join(str(v) for v in payload.embedding) + "]"

        data = {
            "source": payload.source,
            "source_message_id": payload.source_message_id,
            "source_url": payload.source_url,
            "published_at": payload.published_at.isoformat(),
            "content_text": payload.content_text,
            "summary": payload.summary,
            "translated_text": payload.translated_text,
            "language": payload.language,
            "hash_raw": payload.hash_raw,
            "hash_canonical": payload.hash_canonical,
            "embedding": embedding_str,
            "keywords_hit": payload.keywords_hit or [],
            "ingest_status": payload.ingest_status,
            "metadata": payload.metadata or {},
            "price_snapshot": payload.price_snapshot,
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
            "price_snapshot": payload.price_snapshot,
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


class MemoryRepository:
    """Unified memory search wrapper over Supabase RPC search_memory."""

    def __init__(self, client: SupabaseClient) -> None:
        self._client = client
        self._logger = setup_logger(__name__)

    async def search_memory(
        self,
        *,
        embedding_1536: Optional[List[float]] = None,
        keywords: Optional[List[str]] = None,
        asset_codes: Optional[List[str]] = None,
        match_threshold: float = 0.85,
        min_confidence: float = 0.6,
        time_window_hours: int = 72,
        match_count: int = 5,
    ) -> Dict[str, Any]:
        """Call RPC search_memory and normalize results.

        Returns a dict with keys:
          - hits: list[dict]
          - stats: { total, vector, keyword }
          - notes: str (optional)
        """

        params: Dict[str, Any] = {
            "query_embedding": embedding_1536,
            "query_keywords": keywords or [],
            "match_threshold": float(match_threshold),
            "match_count": int(max(match_count, 1)),
            "min_confidence": float(min_confidence),
            "time_window_hours": int(max(time_window_hours, 1)),
            "asset_filter": asset_codes or None,
        }

        try:
            response = await self._client.rpc("search_memory", params)
        except SupabaseError as exc:
            self._logger.info("memory.search: RPC failed, will degrade to local. error=%s", exc)
            return {"hits": [], "stats": {"total": 0, "vector": 0, "keyword": 0}, "notes": str(exc)}
        except Exception as exc:  # pragma: no cover - safety net
            self._logger.info("memory.search: unexpected error, will degrade to local. error=%s", exc)
            return {"hits": [], "stats": {"total": 0, "vector": 0, "keyword": 0}, "notes": str(exc)}

        if not isinstance(response, list):
            self._logger.info("memory.search: empty/non-list response, degrade to local. type=%s", type(response))
            return {"hits": [], "stats": {"total": 0, "vector": 0, "keyword": 0}, "notes": "empty response"}

        hits: List[Dict[str, Any]] = []
        num_vector = 0
        num_keyword = 0
        for row in response:
            match_type = (row.get("match_type") or "").lower()
            if match_type == "vector":
                num_vector += 1
            elif match_type == "keyword":
                num_keyword += 1

            hits.append(
                {
                    "match_type": match_type,
                    "news_event_id": int(row.get("news_event_id")) if row.get("news_event_id") is not None else None,
                    "created_at": row.get("created_at"),
                    "content_text": row.get("content_text"),
                    "translated_text": row.get("translated_text"),
                    "similarity": (float(row.get("similarity")) if row.get("similarity") is not None else None),
                    "keyword_score": (float(row.get("keyword_score")) if row.get("keyword_score") is not None else None),
                    "combined_score": (float(row.get("combined_score")) if row.get("combined_score") is not None else None),
                }
            )

        total = len(hits)
        self._logger.info(
            "memory.search: supabase hits → total=%s, vector=%s, keyword=%s",
            total,
            num_vector,
            num_keyword,
        )

        return {
            "hits": hits,
            "stats": {"total": total, "vector": num_vector, "keyword": num_keyword},
        }
