"""Memory repository wrapper that merges multiple Supabase sources."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from src.db.supabase_client import SupabaseClient, SupabaseError
from src.utils import setup_logger

from .repository import MemoryRepositoryConfig, SupabaseMemoryRepository
from .types import MemoryContext, MemoryEntry


class MultiSourceMemoryRepository:
    """Aggregate memories from primary (news events) and secondary (docs) sources."""

    def __init__(
        self,
        primary: SupabaseMemoryRepository,
        *,
        secondary_client: SupabaseClient | None,
        secondary_table: str,
        config: MemoryRepositoryConfig,
        secondary_similarity_threshold: float,
        secondary_max_results: int,
    ) -> None:
        self._primary = primary
        self._secondary_client = secondary_client
        self._secondary_table = secondary_table or "docs"
        self._config = config
        self._secondary_similarity_threshold = float(secondary_similarity_threshold)
        self._secondary_max_results = int(secondary_max_results)
        self._logger = setup_logger(__name__)

    async def fetch_memories(
        self,
        *,
        embedding: Sequence[float] | None,
        asset_codes: Iterable[str] | None = None,
    ) -> MemoryContext:
        """Fetch memories from multiple sources and merge them."""

        self._logger.info(
            "🔄 MultiSource 开始检索: 主库 + 副库 (embedding维度=%d, asset_codes=%s)",
            len(embedding) if embedding else 0,
            list(asset_codes) if asset_codes else []
        )

        primary_context = await self._primary.fetch_memories(
            embedding=embedding,
            asset_codes=asset_codes,
        )

        entries: list[MemoryEntry] = list(primary_context.entries)
        self._logger.info("📊 主库返回 %d 条记忆", len(entries))

        if self._secondary_client and embedding:
            self._logger.info("🔍 准备调用副库检索 (embedding维度=%d)", len(embedding))
            try:
                secondary_entries = await self._fetch_from_secondary(
                    embedding=embedding,
                    asset_codes=asset_codes,
                )
            except SupabaseError as exc:
                self._logger.warning("❌ 副库记忆检索失败 (SupabaseError): %s", exc)
            except Exception as exc:  # pragma: no cover - defensive logging
                self._logger.warning("❌ 副库记忆检索出现异常: %s", exc, exc_info=True)
            else:
                self._logger.info("✅ 副库返回 %d 条记忆，合并中...", len(secondary_entries))
                entries.extend(secondary_entries)
        elif not self._secondary_client:
            self._logger.debug("⚠️  副库客户端未初始化，跳过副库检索")
        elif not embedding:
            self._logger.debug("⚠️  无 embedding，跳过副库检索")

        merged = self._merge_and_rank(entries)
        self._logger.info(
            "🎯 MultiSource 合并完成: 主库(%d) + 副库 → 去重排序后(%d) → 截取前%d条",
            len(primary_context.entries),
            len(merged),
            self._config.max_notes
        )

        context = MemoryContext()
        context.extend(merged[: self._config.max_notes])
        return context

    async def _fetch_from_secondary(
        self,
        *,
        embedding: Sequence[float],
        asset_codes: Iterable[str] | None,
    ) -> list[MemoryEntry]:
        """Retrieve documents from the secondary Supabase source."""

        params: dict[str, Any] = {
            "query_embedding": list(embedding),
            "match_threshold": float(self._secondary_similarity_threshold),
            "match_count": int(self._secondary_max_results),
        }

        self._logger.info(
            "🔎 调用副库 RPC match_documents: 阈值=%.3f, 最大返回=%d, 维度=%d, table=%s",
            params["match_threshold"],
            params["match_count"],
            len(params["query_embedding"]),
            self._secondary_table,
        )

        response = await self._secondary_client.rpc("match_documents", params)

        self._logger.info(
            "📥 副库 RPC 返回: type=%s, 原始结果数=%d",
            type(response).__name__,
            len(response) if isinstance(response, list) else 0
        )

        if not isinstance(response, list):
            self._logger.warning("⚠️  副库 RPC 返回非列表类型: %s", type(response).__name__)
            return []

        if not response:
            self._logger.info("ℹ️  副库返回空列表（可能无匹配文档）")
            return []

        target_assets = {
            str(code).strip().upper()
            for code in (asset_codes or [])
            if str(code).strip()
        }

        entries: list[MemoryEntry] = []
        filtered_count = 0
        invalid_count = 0

        for idx, row in enumerate(response):
            if not isinstance(row, dict):
                invalid_count += 1
                continue

            entry = self._build_entry_from_secondary(row)
            if entry is None:
                invalid_count += 1
                self._logger.debug("副库第 %d 行: 构建 entry 失败，跳过", idx)
                continue

            if target_assets and entry.assets:
                if not target_assets.intersection(entry.assets):
                    filtered_count += 1
                    self._logger.debug(
                        "副库记忆过滤 [%d]: 未命中资产过滤 %s -> %s",
                        idx,
                        target_assets,
                        entry.assets,
                    )
                    continue

            entries.append(entry)
            self._logger.debug(
                "✅ 副库第 %d 行: 添加记忆 id=%s, similarity=%.3f, assets=%s",
                idx, entry.id, entry.similarity, entry.assets
            )

        self._logger.info(
            "📊 副库处理完成: 原始%d条 → 有效%d条 (过滤%d, 无效%d)",
            len(response), len(entries), filtered_count, invalid_count
        )
        return entries

    def _build_entry_from_secondary(self, row: dict[str, Any]) -> MemoryEntry | None:
        """Convert secondary RPC row into MemoryEntry."""

        summary = self._format_summary(row)
        if not summary:
            return None

        created_at = self._parse_timestamp(
            row.get("published_at") or row.get("created_at")
        )

        assets = self._extract_assets(row)
        similarity = float(row.get("similarity", 0.0))

        prefix = self._secondary_table or "docs"
        entry = MemoryEntry(
            id=f"{prefix}:{row.get('id', '')}",
            created_at=created_at,
            assets=assets,
            action="inform",
            confidence=similarity,
            summary=summary,
            similarity=similarity,
        )
        return entry

    def _parse_timestamp(self, value: Any) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                self._logger.debug("副库时间解析失败: %s", value)
        return datetime.now(tz=timezone.utc)

    def _extract_assets(self, row: dict[str, Any]) -> list[str]:
        tags = row.get("tags")

        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                self._logger.debug("副库 tags 字段解析失败（非 JSON）: %s", tags)
                tags = None

        assets: set[str] = set()
        if isinstance(tags, dict):
            for key in ("entities", "tickers", "assets"):
                values = tags.get(key)
                if isinstance(values, (list, tuple, set)):
                    for token in values:
                        normalized = str(token).strip().upper()
                        if normalized:
                            assets.add(normalized)

        return sorted(assets)

    def _format_summary(self, row: dict[str, Any]) -> str:
        """Build a concise summary string for secondary entries."""

        summary = str(
            row.get("ai_summary_cn")
            or row.get("summary")
            or row.get("content_text")
            or ""
        ).strip()

        if not summary:
            return ""

        max_length = 360
        if len(summary) > max_length:
            summary = summary[: max_length - 3].rstrip() + "..."

        metadata_bits: list[str] = []
        source = row.get("source")
        if source:
            metadata_bits.append(f"来源: {source}")

        author = row.get("source_author")
        if author:
            metadata_bits.append(f"作者: {author}")

        url = row.get("canonical_url")
        if url:
            metadata_bits.append(f"链接: {url}")

        if metadata_bits:
            summary = f"{summary}（{', '.join(metadata_bits)}）"

        return summary

    def _merge_and_rank(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        self._logger.debug("🔀 开始合并排序: 输入 %d 条记忆", len(entries))

        deduped = self._deduplicate(entries)
        self._logger.debug("🔀 去重后: %d 条记忆", len(deduped))

        secondary_prefix = f"{self._secondary_table}:"
        primary_count = sum(1 for e in deduped if not e.id.startswith(secondary_prefix))
        secondary_count = len(deduped) - primary_count

        self._logger.debug(
            "🔀 来源分布: 主库 %d 条, 副库 %d 条",
            primary_count, secondary_count
        )

        sorted_entries = sorted(
            deduped,
            key=lambda item: (
                round(item.similarity, 6),
                item.created_at.timestamp(),
                0 if item.id and not item.id.startswith(secondary_prefix) else 1,
            ),
            reverse=True,
        )

        if self._logger.isEnabledFor(10):  # DEBUG level
            self._logger.debug("🔀 排序后前5条:")
            for i, entry in enumerate(sorted_entries[:5], 1):
                source = "主库" if not entry.id.startswith(secondary_prefix) else "副库"
                self._logger.debug(
                    "  [%d] %s sim=%.3f %s %s",
                    i, source, entry.similarity, entry.assets, entry.id[:20]
                )

        return sorted_entries

    def _deduplicate(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        unique: dict[str, MemoryEntry] = {}
        seen_summaries: set[str] = set()

        for entry in entries:
            if not entry.summary:
                continue

            summary_hash = hashlib.sha256(entry.summary.encode("utf-8")).hexdigest()
            if entry.id in unique or summary_hash in seen_summaries:
                continue
            unique[entry.id] = entry
            seen_summaries.add(summary_hash)

        return list(unique.values())
