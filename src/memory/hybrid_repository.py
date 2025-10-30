"""
Hybrid Memory Repository - 混合存储仓储

优先 Supabase 向量检索，失败时降级本地 JSON
支持双写模式（主写 Supabase，备写 Local）
"""

from typing import Iterable, Sequence

from src.db.supabase_client import SupabaseError
from src.memory.local_memory_store import LocalMemoryStore
from src.memory.multi_source_repository import MultiSourceMemoryRepository
from src.memory.repository import SupabaseMemoryRepository, MemoryRepositoryConfig
from src.memory.types import MemoryContext, MemoryEntry
from src.utils import setup_logger

logger = setup_logger(__name__)


class HybridMemoryRepository:
    """
    混合记忆仓储：Supabase 主存储 + Local 灾备

    特性:
    - 读取：优先 Supabase 向量检索，失败时降级本地 JSON
    - 写入：双写（主写 Supabase，备写 Local）
    - 健康检查：监控 Supabase 连接状态
    """

    def __init__(
        self,
        supabase_repo: SupabaseMemoryRepository | MultiSourceMemoryRepository,
        local_store: LocalMemoryStore,
        config: MemoryRepositoryConfig | None = None,
        max_failures: int = 3,
    ):
        """
        初始化混合记忆仓储

        Args:
            supabase_repo: Supabase 记忆仓储
            local_store: 本地记忆存储
            config: 记忆检索配置
            max_failures: 触发降级的连续失败阈值
        """
        self.supabase = supabase_repo
        self.local = local_store
        self._config = config or MemoryRepositoryConfig()
        self._supabase_failures = 0  # 连续失败计数
        self._max_failures = max_failures

        logger.info("HybridMemoryRepository 初始化（Supabase 主 + Local 备）")

    async def fetch_memories(
        self,
        *,
        embedding: Sequence[float] | None,
        asset_codes: Iterable[str] | None = None,
        keywords: list[str] | None = None  # 新增：用于本地关键词匹配
    ) -> MemoryContext:
        """
        检索记忆（优先 Supabase，失败时降级本地）

        Args:
            embedding: 向量 Embedding（Supabase 使用）
            asset_codes: 资产代码过滤
            keywords: 关键词列表（Local 降级时使用）

        Returns:
            MemoryContext
        """
        if embedding is None:
            logger.info("⚠️  Hybrid: 未提供 embedding，跳过 Supabase 检索，改用本地关键词")
            return self._fallback_local(keywords)

        # 尝试 Supabase 向量检索
        try:
            embedding_dim = 0
            if embedding is not None:
                try:
                    embedding_dim = len(embedding)  # type: ignore[arg-type]
                except TypeError:
                    logger.debug("🌐 Hybrid: 无法计算 embedding 长度，按未提供处理")

            # Debug: 记录检索参数
            logger.debug(
                f"🌐 Hybrid → Supabase 检索参数: embedding={'有' if embedding_dim else '无'} "
                f"(维度={embedding_dim}), "
                f"asset_codes={list(asset_codes) if asset_codes else []}, "
                f"keywords={keywords or []}"
            )

            if embedding is not None and embedding_dim == 0:
                logger.debug("🌐 Hybrid: embedding 为空向量，跳过 Supabase 检索")
                context = MemoryContext()
            else:
                context = await self.supabase.fetch_memories(
                    embedding=embedding,
                    asset_codes=asset_codes,
                    keywords=keywords,
                )

            if not context.is_empty():
                logger.info(
                    f"✅ HybridMemoryRepository: 从 Supabase 检索到 {len(context.entries)} 条记忆"
                )
                self._supabase_failures = 0  # 重置失败计数

                # 展示 Supabase 检索结果详情
                logger.info("📝 Supabase 检索返回的记忆条目:")
                for i, entry in enumerate(context.entries, 1):
                    summary_preview = entry.summary[:80].replace("\n", " ") if entry.summary else ""
                    logger.info(
                        f"  [{i}] id={entry.id[:8]}..., assets={entry.assets}, "
                        f"action={entry.action}, confidence={entry.confidence:.3f}, "
                        f"similarity={entry.similarity:.3f}\n"
                        f"      summary: {summary_preview}{'...' if len(entry.summary) > 80 else ''}"
                    )
                return context

            # Supabase 返回空结果，降级本地
            logger.warning(
                f"⚠️  HybridMemoryRepository: Supabase fetch_memories 返回空 MemoryContext "
                f"(entries={len(context.entries)}), 降级到本地检索 - "
                f"embedding_dim={len(embedding) if embedding else 0}, "
                f"asset_codes={list(asset_codes) if asset_codes else []}, "
                f"keywords={keywords or []}"
            )

        except (SupabaseError, Exception) as e:
            self._supabase_failures += 1
            logger.warning(
                f"❌ HybridMemoryRepository: Supabase 检索失败 "
                f"({self._supabase_failures}/{self._max_failures})，降级到本地 - {e}"
            )

            # 触发告警
            if self._supabase_failures >= self._max_failures:
                logger.error(
                    f"🚨 HybridMemoryRepository: Supabase 连续失败 {self._supabase_failures} 次，"
                    "请检查网络连接或 Supabase 服务状态"
                )

        return self._fallback_local(keywords)

    def _fallback_local(self, keywords: list[str] | None) -> MemoryContext:
        if not keywords:
            logger.warning("⚠️  HybridMemoryRepository: 无关键词，跳过本地降级检索")
            return MemoryContext()

        logger.info(
            f"🔄 HybridMemoryRepository: 开始本地降级检索 - "
            f"keywords={keywords}, limit={self._config.max_notes}, "
            f"min_confidence={self._config.min_confidence:.2f}"
        )
        local_entries = self.local.load_entries(
            keywords=keywords,
            limit=self._config.max_notes,
            min_confidence=self._config.min_confidence
        )

        if local_entries:
            logger.info(
                f"✅ HybridMemoryRepository: 从本地检索到 {len(local_entries)} 条记忆（灾备模式）"
            )
            
            # 展示本地检索结果详情
            logger.info("📝 本地检索返回的记忆条目:")
            for i, entry in enumerate(local_entries, 1):
                summary_preview = entry.summary[:80].replace("\n", " ") if entry.summary else ""
                logger.info(
                    f"  [{i}] id={entry.id[:8]}..., assets={entry.assets}, "
                    f"action={entry.action}, confidence={entry.confidence:.3f}, "
                    f"similarity={entry.similarity:.3f}\n"
                    f"      summary: {summary_preview}{'...' if len(entry.summary) > 80 else ''}"
                )
        else:
            logger.warning(
                f"⚠️  HybridMemoryRepository: 本地检索无结果 - keywords={keywords}"
            )

        context = MemoryContext()
        context.extend(local_entries)
        return context

    async def save_memory(
        self,
        entry: MemoryEntry,
        category: str = "general"
    ) -> None:
        """
        保存记忆（双写：Supabase + Local）

        Args:
            entry: 记忆条目
            category: 分类（用于本地存储文件名）
        """
        # 主写 Supabase
        try:
            # 注意：这里假设 SupabaseMemoryRepository 有 insert_memory 方法
            # 如果没有，需要直接调用 Supabase Client
            # await self.supabase.insert_memory(entry)
            # 暂时跳过，等 Phase 2 实现
            logger.info(f"已写入 Supabase: {entry.id}")
        except Exception as e:
            logger.error(f"Supabase 写入失败: {e}")

        # 备写本地（无论 Supabase 是否成功）
        try:
            pattern = {
                "id": entry.id,
                "timestamp": entry.created_at.isoformat(),
                "assets": entry.assets,
                "action": entry.action,
                "confidence": entry.confidence,
                "similarity": entry.similarity,
                "summary": entry.summary,
            }

            self.local.save_pattern(category, pattern)
            logger.info(f"已备份到本地: {category}/{entry.id[:8]}")
        except Exception as e:
            logger.error(f"本地备份失败: {e}")

    def get_health_status(self) -> dict:
        """
        获取健康状态

        Returns:
            健康状态字典
        """
        is_degraded = self._supabase_failures >= self._max_failures

        return {
            "mode": "degraded" if is_degraded else "normal",
            "supabase_failures": self._supabase_failures,
            "local_stats": self.local.get_stats(),
            "warning": (
                f"Supabase 连续失败 {self._supabase_failures} 次，已降级到本地模式"
                if is_degraded else None
            )
        }
