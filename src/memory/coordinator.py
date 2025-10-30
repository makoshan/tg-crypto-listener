from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from src.db.supabase_client import get_supabase_client, SupabaseError
from src.db.repositories import MemoryRepository
from src.utils import setup_logger


logger = setup_logger(__name__)


async def fetch_memory_evidence(
    *,
    config: Any,
    embedding_1536: Optional[List[float]] | None,
    keywords: Optional[List[str]] | None,
    asset_codes: Optional[List[str]] | None,
    match_threshold: float = 0.85,
    min_confidence: float = 0.6,
    time_window_hours: int = 72,
    match_count: int = 5,
) -> Dict[str, Any]:
    """协调器：优先 Supabase 统一检索，空/异常降级到本地关键词。

    返回：{"supabase_hits"?: [...], "local_keyword"?: [...], "notes"?: str}
    不抛异常。
    """

    supabase_url = getattr(config, "SUPABASE_URL", None) or os.getenv("SUPABASE_URL", "").strip()
    supabase_key = getattr(config, "SUPABASE_SERVICE_KEY", None) or os.getenv("SUPABASE_SERVICE_KEY", "").strip()

    result: Dict[str, Any] = {}

    # 尝试 Supabase
    if supabase_url and supabase_key:
        try:
            client = get_supabase_client(url=supabase_url, service_key=supabase_key)
            repo = MemoryRepository(client)
            search_res = await repo.search_memory(
                embedding_1536=embedding_1536,
                keywords=keywords,
                asset_codes=asset_codes,
                match_threshold=match_threshold,
                min_confidence=min_confidence,
                time_window_hours=time_window_hours,
                match_count=match_count,
            )

            hits = search_res.get("hits", [])
            if hits:
                result["supabase_hits"] = hits
                # 附带统计信息到 notes，避免上层再拼
                stats = search_res.get("stats", {})
                result["notes"] = (
                    f"supabase hits: total={stats.get('total', 0)}, "
                    f"vector={stats.get('vector', 0)}, keyword={stats.get('keyword', 0)}"
                )
                return result
            else:
                logger.info("memory.coordinator: supabase empty, degrade to local keyword")
        except (SupabaseError, Exception) as exc:  # pragma: no cover - 网络/服务异常
            logger.info("memory.coordinator: supabase failed, degrade to local keyword: %s", exc)
            result["notes"] = f"supabase error: {exc}"

    # 本地关键词兜底（轻量策略：仅回传有效关键词列表）
    normalized_kws: List[str] = []
    for kw in (keywords or []):
        norm = (kw or "").strip().lower()
        if norm and norm not in normalized_kws:
            normalized_kws.append(norm)

    if normalized_kws:
        result["local_keyword"] = normalized_kws[: match_count or 5]
    else:
        result.setdefault("notes", "no keywords provided for local fallback")

    return result


