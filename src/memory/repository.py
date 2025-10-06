"""Supabase-backed memory retrieval implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from ..db.supabase_client import SupabaseClient, SupabaseError
from .types import MemoryContext, MemoryEntry


@dataclass(slots=True)
class MemoryRepositoryConfig:
    """Configuration parameters controlling memory retrieval."""

    max_notes: int = 3
    similarity_threshold: float = 0.85
    lookback_hours: int = 72
    min_confidence: float = 0.6


class SupabaseMemoryRepository:
    """Fetch past AI signals for prompt enrichment."""

    def __init__(self, client: SupabaseClient, config: MemoryRepositoryConfig | None = None) -> None:
        self._client = client
        self._config = config or MemoryRepositoryConfig()

    async def fetch_memories(
        self,
        *,
        embedding: Sequence[float] | None,
        asset_codes: Iterable[str] | None = None,
    ) -> MemoryContext:
        """Retrieve similar events from Supabase via RPC.

        Args:
            embedding: Vector representing current message semantics.
            asset_codes: Optional list of asset codes to narrow the search.
        """
        if not embedding:
            return MemoryContext()

        params: dict[str, object] = {
            "query_embedding": list(embedding),
            "match_threshold": float(self._config.similarity_threshold),
            "match_count": int(self._config.max_notes),
            "min_confidence": float(self._config.min_confidence),
            "time_window_hours": int(self._config.lookback_hours),
        }

        assets = [code for code in asset_codes or [] if code]
        if assets:
            params["asset_filter"] = assets

        try:
            result = await self._client.rpc("search_memory_events", params)
        except SupabaseError:
            return MemoryContext()
        except Exception:
            return MemoryContext()

        entries: list[MemoryEntry] = []
        if isinstance(result, list):
            for row in result:
                if not isinstance(row, dict):
                    continue
                created_raw = row.get("created_at")
                try:
                    created_at = (
                        datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                        if isinstance(created_raw, str)
                        else datetime.utcnow()
                    )
                except (TypeError, ValueError):
                    created_at = datetime.utcnow()

                summary = str(row.get("summary", "")).strip()
                if not summary:
                    continue

                assets_field = row.get("assets")
                if isinstance(assets_field, (list, tuple)):
                    assets_list = [str(item).strip().upper() for item in assets_field if str(item).strip()]
                else:
                    assets_list = []

                entry = MemoryEntry(
                    id=str(row.get("id", "")) or "",
                    created_at=created_at,
                    assets=assets_list,
                    action=str(row.get("action", "observe")).lower(),
                    confidence=float(row.get("confidence", 0.0)),
                    summary=summary,
                    similarity=float(row.get("similarity", 0.0)),
                )
                entries.append(entry)

        entries.sort(key=lambda item: item.similarity, reverse=True)
        context = MemoryContext()
        context.extend(entries[: self._config.max_notes])
        return context
