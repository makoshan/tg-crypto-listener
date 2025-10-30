from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from src.db.supabase_client import get_supabase_client, SupabaseError
from src.db.repositories import MemoryRepository
from src.utils import setup_logger


logger = setup_logger(__name__)


# Simple in-process cache to avoid repeated Supabase RPC under burst traffic
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 90


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

    # 输入为空短路
    if not (asset_codes or (keywords and len([k for k in (keywords or []) if k.strip()]) > 0)):
        return {"notes": "empty inputs: no asset_codes or keywords"}

    # 规范化 cache key（不包含 embedding 内容，降低碰撞成本）
    norm_assets = tuple(sorted((asset_codes or [])[:6]))
    norm_kws = tuple(sorted([k.strip().lower() for k in (keywords or []) if k and k.strip()][:6]))
    cache_key = f"a:{','.join(norm_assets)}|k:{','.join(norm_kws)}|t:{time_window_hours}|m:{match_count}"

    # 命中缓存
    try:
        import time
        cached = _CACHE.get(cache_key)
        if cached and (int(time.time()) - int(cached.get("ts", 0))) <= _CACHE_TTL_SECONDS:
            logger.debug("🔍 fetch_memory_evidence: 命中缓存 key=%s", cache_key)
            return dict(cached.get("value", {}))
    except Exception:
        pass

    # 尝试 Supabase
    if supabase_url and supabase_key:
        try:
            logger.info(
                "🔍 fetch_memory_evidence: 开始统一检索协调 - "
                f"embedding={'有' if embedding_1536 else '无'}, "
                f"keywords={len(keywords or [])}, "
                f"keywords_list={keywords[:5] if keywords else []}{'...' if keywords and len(keywords) > 5 else ''}, "
                f"assets={asset_codes or []}"
            )
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
            stats = search_res.get("stats", {})
            if hits:
                result["supabase_hits"] = hits
                # 附带统计信息到 notes，避免上层再拼
                result["notes"] = (
                    f"supabase hits: total={stats.get('total', 0)}, "
                    f"vector={stats.get('vector', 0)}, keyword={stats.get('keyword', 0)}"
                )
                logger.info(
                    f"✅ fetch_memory_evidence: Supabase 统一检索成功 - "
                    f"total={stats.get('total', 0)}, "
                    f"vector={stats.get('vector', 0)}, "
                    f"keyword={stats.get('keyword', 0)}"
                )
                # 写入缓存
                try:
                    _CACHE[cache_key] = {"ts": int(time.time()), "value": dict(result)}
                except Exception:
                    pass
                return result
            else:
                logger.info(
                    f"⚠️  fetch_memory_evidence: Supabase 返回空结果，降级到本地关键词 - "
                    f"stats={stats}"
                )
        except (SupabaseError, Exception) as exc:  # pragma: no cover - 网络/服务异常
            logger.warning(
                f"⚠️  fetch_memory_evidence: Supabase 统一检索失败，降级到本地关键词 - error={exc}"
            )
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

    # 缓存本地降级结果，避免瞬时重复调用
    try:
        _CACHE[cache_key] = {"ts": int(time.time()), "value": dict(result)}
    except Exception:
        pass

    return result


