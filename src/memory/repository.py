"""Supabase-backed memory retrieval implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from ..db.supabase_client import SupabaseClient, SupabaseError
from ..utils import setup_logger
from .types import MemoryContext, MemoryEntry


logger = setup_logger(__name__)


@dataclass
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

        # Debug: 记录完整的 RPC 调用参数
        logger.debug(
            f"调用 search_memory_events RPC: "
            f"match_threshold={params['match_threshold']}, "
            f"match_count={params['match_count']}, "
            f"min_confidence={params['min_confidence']}, "
            f"time_window_hours={params['time_window_hours']}, "
            f"asset_filter={params.get('asset_filter', [])}, "
            f"embedding维度={len(params['query_embedding'])}"
        )

        try:
            result = await self._client.rpc("search_memory_events", params)
            logger.debug(f"RPC 返回结果类型: {type(result)}, 数量: {len(result) if isinstance(result, list) else 'N/A'}")
        except SupabaseError as exc:
            logger.warning("Supabase RPC search_memory_events 失败: %s", exc)
            return MemoryContext()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("记忆检索出现未知异常: %s", exc)
            return MemoryContext()

        entries: list[MemoryEntry] = []
        if isinstance(result, list):
            logger.debug(f"开始处理 {len(result)} 行 RPC 结果")
            for idx, row in enumerate(result):
                if not isinstance(row, dict):
                    logger.debug(f"第 {idx} 行: 跳过（非字典类型）")
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
                    logger.debug(f"第 {idx} 行: 跳过（summary 为空）- row={row}")
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
                logger.debug(
                    f"第 {idx} 行: 添加记忆 - id={entry.id[:8]}..., "
                    f"similarity={entry.similarity:.3f}, confidence={entry.confidence:.3f}, "
                    f"assets={entry.assets}, summary={entry.summary[:50]}..."
                )
                entries.append(entry)

        logger.debug(f"总共处理得到 {len(entries)} 条有效记忆")
        entries.sort(key=lambda item: item.similarity, reverse=True)
        top_entries = entries[: self._config.max_notes]
        if top_entries:
            logger.info(
                "检索到 %d 条历史记忆 (阈值=%.2f, 时间窗口=%dh)",
                len(top_entries),
                self._config.similarity_threshold,
                self._config.lookback_hours,
            )
        else:
            logger.debug(
                "未检索到相似历史记忆 (阈值=%.2f, 时间窗口=%dh)",
                self._config.similarity_threshold,
                self._config.lookback_hours,
            )

        context = MemoryContext()
        context.extend(top_entries)
        return context
