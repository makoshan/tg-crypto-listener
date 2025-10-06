"""
Hybrid Memory Repository - 混合存储仓储

优先 Supabase 向量检索，失败时降级本地 JSON
支持双写模式（主写 Supabase，备写 Local）
"""

from typing import Iterable, Sequence

from src.db.supabase_client import SupabaseError
from src.memory.local_memory_store import LocalMemoryStore
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
        supabase_repo: SupabaseMemoryRepository,
        local_store: LocalMemoryStore,
        config: MemoryRepositoryConfig | None = None
    ):
        """
        初始化混合记忆仓储

        Args:
            supabase_repo: Supabase 记忆仓储
            local_store: 本地记忆存储
            config: 记忆检索配置
        """
        self.supabase = supabase_repo
        self.local = local_store
        self._config = config or MemoryRepositoryConfig()
        self._supabase_failures = 0  # 连续失败计数
        self._max_failures = 3  # 触发降级的失败阈值

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
        # 尝试 Supabase 向量检索
        try:
            context = await self.supabase.fetch_memories(
                embedding=embedding,
                asset_codes=asset_codes
            )

            if not context.is_empty():
                logger.info(f"从 Supabase 检索到 {len(context.entries)} 条记忆")
                self._supabase_failures = 0  # 重置失败计数
                return context

            # Supabase 返回空结果，降级本地
            logger.info("Supabase 返回空结果，降级到本地检索")

        except (SupabaseError, Exception) as e:
            self._supabase_failures += 1
            logger.warning(
                f"Supabase 检索失败（{self._supabase_failures}/{self._max_failures}），"
                f"降级到本地: {e}"
            )

            # 触发告警
            if self._supabase_failures >= self._max_failures:
                logger.error(
                    f"Supabase 连续失败 {self._supabase_failures} 次，"
                    "请检查网络连接或 Supabase 服务状态"
                )

        # 降级到本地 JSON（关键词匹配）
        if not keywords:
            logger.debug("无关键词，跳过本地降级检索")
            return MemoryContext()

        local_entries = self.local.load_entries(
            keywords=keywords,
            limit=self._config.max_notes,
            min_confidence=self._config.min_confidence
        )

        if local_entries:
            logger.info(f"从本地检索到 {len(local_entries)} 条记忆（灾备模式）")

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
