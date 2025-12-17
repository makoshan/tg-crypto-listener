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
        if record and "id" in record and record["id"] is not None:
            return int(record["id"])
        return None

    async def check_duplicate_by_embedding(
        self,
        embedding: List[float],
        threshold: float = 0.92,
        time_window_hours: int = 72,
    ) -> Optional[Dict[str, Any]]:
        """Check for semantically similar events using vector similarity.
        
        Uses unified search_memory RPC with match_count=1, min_confidence=0 for deduplication.
        Only returns vector matches (not keyword matches) with similarity >= threshold.
        """
        if not embedding:
            return None

        try:
            # 使用统一的 search_memory RPC，match_count=1 用于去重，min_confidence=0 不过滤 AI 信号
            response = await self._client.rpc(
                "search_memory",
                {
                    "query_embedding": embedding,
                    "query_keywords": None,  # 去重场景只需要向量相似度
                    "match_threshold": threshold,
                    "match_count": 1,  # 去重只需要最相似的 1 个结果
                    "min_confidence": 0.0,  # 不过滤 AI 信号置信度
                    "time_window_hours": time_window_hours,
                    "asset_filter": None,  # 去重不过滤资产
                },
            )
        except SupabaseError:
            return None
        except Exception:
            return None

        if isinstance(response, list) and response:
            # 只处理 vector 类型的匹配结果（keyword 匹配不用于去重）
            vector_hits = [r for r in response if (r.get("match_type") or "").lower() == "vector"]
            if vector_hits:
                result = vector_hits[0]
                similarity = result.get("similarity")
                # search_memory 的 vector hits 应该总是有 similarity，但为了安全仍需判空
                if similarity is None:
                    return None

                # 有些情况下 RPC 返回的 news_event_id 可能为 None（例如清理历史或视图不一致），
                # 此时不应强制转换为 int(None)
                news_event_id_raw = result.get("news_event_id")
                if news_event_id_raw is None:
                    return None

                return {
                    "id": int(news_event_id_raw),
                    "content_text": result.get("content_text", ""),
                    "similarity": float(similarity),
                }
        return None

    async def insert_event(self, payload: NewsEventPayload) -> Optional[int]:
        logger = setup_logger(__name__)
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
        
        logger.debug(
            "🗄️ 准备插入 news_events - source=%s, content_len=%d, has_embedding=%s",
            payload.source,
            len(payload.content_text),
            payload.embedding is not None,
        )
        
        try:
            record = await self._client.insert("news_events", _strip_none(data))
        except SupabaseError as exc:
            logger.error(
                "❌ insert_event Supabase 错误 - source=%s, error=%s, data_keys=%s",
                payload.source,
                str(exc),
                list(data.keys()),
            )
            return None
        except Exception as exc:
            logger.error(
                "❌ insert_event 未预期的错误 - source=%s, error=%s, error_type=%s",
                payload.source,
                str(exc),
                type(exc).__name__,
            )
            return None
        
        if record and "id" in record and record["id"] is not None:
            event_id = int(record["id"])
            logger.debug(
                "✅ insert_event 成功 - source=%s, event_id=%d",
                payload.source,
                event_id,
            )
            return event_id

        # Log diagnostic info when insert fails to return an ID
        logger.warning(
            "📊 insert_event 返回空 ID - source=%s, record_type=%s, has_id_key=%s, id_value=%s, record_keys=%s, record_sample=%s",
            payload.source,
            type(record).__name__ if record else "None",
            "id" in record if record else False,
            record.get("id") if record else None,
            list(record.keys()) if isinstance(record, dict) else None,
            {k: (str(v)[:50] if v is not None else None) for k, v in record.items()} if isinstance(record, dict) else None,
        )
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
        if record and "id" in record and record["id"] is not None:
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
        if record and "id" in record and record["id"] is not None:
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
        
        # 限制最大时间窗口为 168 小时（7天）以避免免费 Supabase 查询超时
        # 免费计划通常有较短的 statement timeout（10-60秒），查询过长时间窗口的数据会导致超时
        MAX_TIME_WINDOW_HOURS = 168  # 7 days
        original_time_window = time_window_hours
        if time_window_hours > MAX_TIME_WINDOW_HOURS:
            self._logger.warning(
                f"⚠️  MemoryRepository.search_memory: 时间窗口 {time_window_hours}h 超过最大限制 "
                f"{MAX_TIME_WINDOW_HOURS}h（7天），已自动调整为 {MAX_TIME_WINDOW_HOURS}h 以避免查询超时。"
                f"提示：免费 Supabase 计划建议使用较短的时间窗口（≤72h）。"
            )
            time_window_hours = MAX_TIME_WINDOW_HOURS

        params: Dict[str, Any] = {
            "query_embedding": embedding_1536,
            "query_keywords": keywords or [],
            "match_threshold": float(match_threshold),
            "match_count": int(max(match_count, 1)),
            "min_confidence": float(min_confidence),
            "time_window_hours": int(max(time_window_hours, 1)),
            "asset_filter": asset_codes or None,
        }

        # ????? search_memory RPC??? search_memory_events?
        self._logger.info(
            f"?? MemoryRepository: ?? RPC 'search_memory' - "
            f"embedding={'?' if embedding_1536 else '?'}, "
            f"keywords={len(keywords or [])}, "
            f"assets={len(asset_codes or [])}, "
            f"threshold={match_threshold:.2f}, "
            f"time_window={time_window_hours}h, "
            f"min_confidence={min_confidence:.2f}, "
            f"match_count={match_count}"
        )

        try:
            response = await self._client.rpc("search_memory", params)
        except SupabaseError as exc:
            error_msg = str(exc)
            # 检测数据库 statement timeout 错误（错误代码 57014）
            is_timeout = "57014" in error_msg or "statement timeout" in error_msg.lower() or "canceling statement" in error_msg.lower()
            
            if is_timeout:
                self._logger.warning(
                    f"⏱️  MemoryRepository.search_memory: RPC 查询超时（数据库 statement timeout） - "
                    f"查询参数: time_window_hours={time_window_hours}h, match_count={match_count}, "
                    f"threshold={match_threshold:.2f}\n"
                    f"💡 优化建议：\n"
                    f"   1. 减少时间窗口（建议 ≤72h，已在代码中限制最大 168h）\n"
                    f"   2. 减少 match_count（当前 {match_count}，建议 ≤5）\n"
                    f"   3. 提高相似度阈值以过滤更多结果（当前 {match_threshold:.2f}）\n"
                    f"   4. 免费 Supabase 计划 statement timeout 较短，考虑升级或使用付费计划\n"
                    f"   5. 检查数据库索引是否已正确创建（news_events.created_at, ai_signals.created_at）\n"
                    f"错误详情: {exc}"
                )
            else:
                self._logger.warning(
                    f"❌  MemoryRepository.search_memory: RPC 调用失败 - error={exc}"
                )
            return {"hits": [], "stats": {"total": 0, "vector": 0, "keyword": 0}, "notes": str(exc)}
        except Exception as exc:  # pragma: no cover - safety net
            self._logger.warning(
                f"??  MemoryRepository.search_memory: ????????????? - error={exc}"
            )
            return {"hits": [], "stats": {"total": 0, "vector": 0, "keyword": 0}, "notes": str(exc)}

        if not isinstance(response, list):
            self._logger.warning(
                f"??  MemoryRepository.search_memory: ???????????????????? - type={type(response)}"
            )
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
            f"?? MemoryRepository.search_memory: Supabase ???? - "
            f"total={total}, vector={num_vector}, keyword={num_keyword}"
        )
        
        # ????? hits ?????????????????
        if hits and keywords:
            keyword_matches = [h for h in hits if h.get("match_type", "").lower() == "keyword"]
            if keyword_matches:
                self._logger.info(
                    f"?? ??????? ({len(keyword_matches)} ?): "
                    f"keywords={keywords}"
                )
                for idx, hit in enumerate(keyword_matches[:3], 1):  # ?????3??????
                    similarity = hit.get("similarity") or hit.get("combined_score") or 0.0
                    event_id = hit.get("news_event_id", "N/A")
                    content_preview = (
                        (hit.get("translated_text") or hit.get("content_text") or "")[:50]
                    ).replace("\n", " ")
                    self._logger.info(
                        f"  [{idx}] event_id={event_id}, similarity={similarity:.3f}, "
                        f"preview={content_preview}..."
                    )

        return {
            "hits": hits,
            "stats": {"total": total, "vector": num_vector, "keyword": num_keyword},
        }
