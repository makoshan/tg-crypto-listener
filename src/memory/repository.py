"""Supabase-backed memory retrieval implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from ..db.repositories import MemoryRepository as UnifiedMemoryRepository
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
        # 使用统一的 MemoryRepository 来调用新的 search_memory RPC
        self._unified_repo = UnifiedMemoryRepository(client)

    async def fetch_memories(
        self,
        *,
        embedding: Sequence[float] | None,
        asset_codes: Iterable[str] | None = None,
        keywords: Iterable[str] | None = None,
    ) -> MemoryContext:
        """Retrieve similar events from Supabase via unified search_memory RPC.

        Args:
            embedding: Vector representing current message semantics.
            asset_codes: Optional list of asset codes to narrow the search.
            keywords: Optional list of keywords for keyword-based search fallback.
        """
        # 如果没有 embedding，使用关键词检索；如果都没有，返回空
        if embedding is None and not keywords:
            return MemoryContext()

        try:
            if embedding is not None:
                if len(embedding) == 0:
                    logger.debug("🔍 跳过 Supabase 检索：embedding 向量为空")
                    embedding = None
        except TypeError:  # pragma: no cover - tolerate non-sized sequences
            logger.debug("🔍 无法获取 embedding 长度，继续执行 Supabase 检索")

        # 准备关键词列表
        keyword_list = [kw.strip() for kw in (keywords or []) if kw and kw.strip()]
        asset_list = [code.strip().upper() for code in (asset_codes or []) if code and code.strip()]

        logger.info(
            f"🔍 SupabaseMemoryRepository: 开始统一检索 (RPC: search_memory) - "
            f"threshold={self._config.similarity_threshold:.2f}, "
            f"count={self._config.max_notes}, "
            f"min_confidence={self._config.min_confidence:.2f}, "
            f"time_window={self._config.lookback_hours}h, "
            f"assets={asset_list or '无'}, "
            f"keywords={keyword_list if keyword_list else '无'}, "
            f"embedding={'有' if embedding else '无'}"
        )

        try:
            # 调用统一的 search_memory RPC（向量优先，自动降级关键词）
            search_result = await self._unified_repo.search_memory(
                embedding_1536=list(embedding) if embedding else None,
                keywords=keyword_list if keyword_list else None,
                asset_codes=asset_list if asset_list else None,
                match_threshold=float(self._config.similarity_threshold),
                min_confidence=float(self._config.min_confidence),
                time_window_hours=int(self._config.lookback_hours),
                match_count=int(self._config.max_notes),
            )

            hits = search_result.get("hits", [])
            stats = search_result.get("stats", {})
            total_hits = stats.get("total", 0)
            vector_hits = stats.get("vector", 0)
            keyword_hits = stats.get("keyword", 0)

            logger.info(
                f"✅ SupabaseMemoryRepository: 统一检索完成 - "
                f"total={total_hits}, vector={vector_hits}, keyword={keyword_hits}"
            )

            # 展示返回的 hits 摘要信息
            if hits:
                logger.info(f"📋 RPC 返回结果摘要 ({len(hits)} 条):")
                for idx, hit in enumerate(hits[:5], 1):  # 最多显示前5条
                    match_type = hit.get("match_type", "unknown")
                    similarity = hit.get("similarity") or hit.get("combined_score") or 0.0
                    event_id = hit.get("news_event_id", "N/A")
                    content_preview = (
                        (hit.get("translated_text") or hit.get("content_text") or "")[:60]
                    ).replace("\n", " ")
                    logger.info(
                        f"  [{idx}] match_type={match_type}, similarity={similarity:.3f}, "
                        f"event_id={event_id}, preview={content_preview}..."
                    )
                if len(hits) > 5:
                    logger.info(f"  ... 还有 {len(hits) - 5} 条结果")

            if total_hits == 0:
                logger.debug(
                    f"⚠️ 统一检索返回空结果 - 可能原因:\n"
                    f"   1) 时间窗口太短 ({self._config.lookback_hours}h)\n"
                    f"   2) 相似度阈值太高 ({self._config.similarity_threshold})\n"
                    f"   3) 置信度阈值太高 ({self._config.min_confidence})\n"
                    f"   4) 数据库中无匹配记录"
                )
                return MemoryContext()

        except SupabaseError as exc:
            logger.warning(f"⚠️  SupabaseMemoryRepository: RPC search_memory 调用失败 - {exc}")
            return MemoryContext()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(f"⚠️  SupabaseMemoryRepository: 记忆检索出现未知异常 - {exc}")
            return MemoryContext()

        # 根据 news_event_id 查询完整的信号信息
        entries: list[MemoryEntry] = []
        logger.info(f"🔍 开始处理 {len(hits)} 条 RPC 返回结果，构建 MemoryEntry...")
        for idx, hit in enumerate(hits):
            if not isinstance(hit, dict):
                logger.warning(f"⏭️  第 {idx} 条: 跳过（非字典类型）")
                continue

            news_event_id = hit.get("news_event_id")
            if not news_event_id:
                logger.warning(f"⏭️  第 {idx} 条: 跳过（缺少 news_event_id）")
                continue

            # 根据 news_event_id 查询 ai_signals 获取完整信息
            try:
                signal_record = await self._client.select_one(
                    "ai_signals",
                    filters={"news_event_id": news_event_id},
                    columns="id,summary_cn,assets,action,confidence,created_at",
                )

                if not signal_record:
                    # 如果没有信号记录，尝试使用 RPC 返回的数据或查询 news_events
                    logger.info(f"⚠️  第 {idx} 条 (event_id={news_event_id}): 未找到 ai_signals，尝试从 RPC 数据或 news_events 获取")
                    
                    # 优先使用 RPC 返回的文本字段（避免额外的数据库查询）
                    summary = (
                        hit.get("translated_text")
                        or hit.get("content_text")
                        or ""
                    )
                    
                    # 尝试从 news_events 获取更多信息（包括 created_at）
                    event_record = await self._client.select_one(
                        "news_events",
                        filters={"id": news_event_id},
                        columns="id,created_at,summary,translated_text",
                    )
                    
                    if event_record:
                        # 如果找到 event_record，优先使用数据库中的文本（可能更完整）
                        summary = (
                            event_record.get("summary")
                            or event_record.get("translated_text")
                            or summary  # 回退到 RPC 返回的数据
                        )
                        created_raw = event_record.get("created_at")
                    else:
                        # 如果没有 event_record，使用 RPC 返回的 created_at
                        created_raw = hit.get("created_at")
                        logger.info(f"⚠️  第 {idx} 条 (event_id={news_event_id}): 未找到 news_events 记录，使用 RPC 返回的数据")
                    
                    # 只要有 summary，就创建条目
                    if summary and summary.strip():
                        try:
                            created_at = (
                                datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                                if isinstance(created_raw, str) and created_raw
                                else datetime.utcnow()
                            )
                        except (TypeError, ValueError):
                            created_at = datetime.utcnow()

                        entry = MemoryEntry(
                            id=str(news_event_id),
                            created_at=created_at,
                            assets=[],
                            action="observe",
                            confidence=0.0,
                            summary=summary.strip(),
                            similarity=float(hit.get("similarity") or hit.get("combined_score") or 0.0),
                        )
                        entries.append(entry)
                        logger.info(
                            f"✅ 第 {idx} 条: 创建 MemoryEntry - event_id={news_event_id}, "
                            f"summary_len={len(summary)}, source={'news_events' if event_record else 'RPC'}"
                        )
                    else:
                        logger.warning(
                            f"⏭️  第 {idx} 条 (event_id={news_event_id}): 跳过（summary 为空） - "
                            f"RPC translated_text={bool(hit.get('translated_text'))}, "
                            f"RPC content_text={bool(hit.get('content_text'))}, "
                            f"event_record={'存在' if event_record else '不存在'}"
                        )
                    continue

                # 解析信号记录
                created_raw = signal_record.get("created_at") or hit.get("created_at")
                try:
                    created_at = (
                        datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                        if isinstance(created_raw, str)
                        else datetime.utcnow()
                    )
                except (TypeError, ValueError):
                    created_at = datetime.utcnow()

                summary = str(signal_record.get("summary_cn", "")).strip()
                if not summary:
                    summary = hit.get("translated_text") or hit.get("content_text") or ""
                    summary = summary.strip()

                if not summary:
                    logger.warning(f"⏭️  第 {idx} 条: 跳过（summary 为空）- news_event_id={news_event_id}")
                    continue

                assets_field = signal_record.get("assets")
                if isinstance(assets_field, str):
                    assets_list = [
                        item.strip().upper()
                        for item in assets_field.split(",")
                        if item.strip()
                    ]
                elif isinstance(assets_field, (list, tuple)):
                    assets_list = [str(item).strip().upper() for item in assets_field if str(item).strip()]
                else:
                    assets_list = []

                entry = MemoryEntry(
                    id=str(signal_record.get("id", news_event_id)),
                    created_at=created_at,
                    assets=assets_list,
                    action=str(signal_record.get("action", "observe")).lower(),
                    confidence=float(signal_record.get("confidence", 0.0)),
                    summary=summary,
                    similarity=float(hit.get("similarity") or hit.get("combined_score") or 0.0),
                )
                logger.info(
                    f"✅ 第 {idx} 条: 添加记忆 - id={entry.id[:8]}..., "
                    f"match_type={hit.get('match_type', 'unknown')}, "
                    f"similarity={entry.similarity:.3f}, confidence={entry.confidence:.3f}, "
                    f"assets={entry.assets}, action={entry.action}"
                )
                entries.append(entry)

            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(f"⚠️  第 {idx} 条: 查询信号信息失败 - news_event_id={news_event_id}, error={exc}")
                continue

        logger.info(f"📈 总共处理得到 {len(entries)} 条有效记忆（从 {len(hits)} 条 RPC 结果中）")
        entries.sort(key=lambda item: item.similarity, reverse=True)
        top_entries = entries[: self._config.max_notes]
        if top_entries:
            logger.info(
                f"✅ SupabaseMemoryRepository: 最终返回 {len(top_entries)} 条历史记忆 "
                f"(阈值={self._config.similarity_threshold:.2f}, 时间窗口={self._config.lookback_hours}h)"
            )
            # 展示最终返回的记忆条目详情
            logger.info("📝 返回的记忆条目:")
            for idx, entry in enumerate(top_entries, 1):
                summary_preview = entry.summary[:80].replace("\n", " ") if entry.summary else ""
                logger.info(
                    f"  [{idx}] id={entry.id[:8]}..., assets={entry.assets}, "
                    f"action={entry.action}, confidence={entry.confidence:.3f}, "
                    f"similarity={entry.similarity:.3f}\n"
                    f"      summary: {summary_preview}{'...' if len(entry.summary) > 80 else ''}"
                )
        else:
            logger.warning(
                f"⚠️  SupabaseMemoryRepository: 未检索到相似历史记忆 "
                f"(RPC 返回 {len(hits)} 条，但处理后得到 {len(entries)} 条有效条目) - "
                f"阈值={self._config.similarity_threshold:.2f}, "
                f"时间窗口={self._config.lookback_hours}h"
            )

        context = MemoryContext()
        context.extend(top_entries)
        logger.info(f"📤 SupabaseMemoryRepository: 返回 MemoryContext，包含 {len(context.entries)} 条记忆")
        return context
