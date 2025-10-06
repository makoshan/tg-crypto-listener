"""
Local Memory Store - 本地记忆快速读取（供 Gemini 使用）

支持:
- 关键词匹配检索
- 时间窗口过滤
- 返回与 SupabaseMemoryRepository 一致的结构
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from src.memory.types import MemoryEntry, MemoryContext
from src.utils import setup_logger

logger = setup_logger(__name__)


class LocalMemoryStore:
    """
    本地记忆存储（JSON 文件）

    目录结构:
    memories/
      patterns/
        listing.json      # 上币消息模式
        hack.json         # 黑客事件模式
        regulation.json   # 监管消息模式
        core.json         # 通用模式
    """

    def __init__(self, base_path: str = "./memories", lookback_hours: int = 168):
        """
        初始化本地记忆存储

        Args:
            base_path: 记忆存储根目录
            lookback_hours: 时间窗口（小时），默认 168h = 7天
        """
        self.base_path = Path(base_path)
        self.lookback_hours = lookback_hours
        self.pattern_dir = self.base_path / "patterns"

        # 确保目录存在
        self.pattern_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"LocalMemoryStore 初始化: {self.base_path} "
            f"(时间窗口: {lookback_hours}h)"
        )

    def load_entries(
        self,
        keywords: List[str],
        limit: int = 3,
        min_confidence: float = 0.6
    ) -> List[MemoryEntry]:
        """
        加载记忆条目（返回与 SupabaseMemoryRepository.fetch_memories 一致的结构）

        Args:
            keywords: 关键词列表（用于匹配文件名）
            limit: 最大返回数量
            min_confidence: 最小置信度阈值

        Returns:
            MemoryEntry 列表（按相似度降序）
        """
        if not keywords:
            return []

        patterns: List[Dict] = []

        # 加载关键词对应的模式文件
        loaded_files = []
        for keyword in keywords:
            file_path = self.pattern_dir / f"{keyword.lower()}.json"
            keyword_patterns = self._load_pattern_file(file_path)
            if keyword_patterns:
                loaded_files.append(f"{keyword.lower()}.json({len(keyword_patterns)})")
                patterns.extend(keyword_patterns)

        # 加载通用模式
        common_path = self.pattern_dir / "core.json"
        core_patterns = self._load_pattern_file(common_path)
        if core_patterns:
            loaded_files.append(f"core.json({len(core_patterns)})")
            patterns.extend(core_patterns)

        logger.debug(f"📂 加载模式文件: {', '.join(loaded_files) if loaded_files else '无'}")

        if not patterns:
            logger.info("未检索到相似历史记忆")
            return []

        # 标准化为 MemoryEntry
        entries = self._normalize_patterns(patterns)

        # 时间窗口过滤（使用 UTC aware datetime）
        from datetime import timezone
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)

        # 确保 created_at 是 aware datetime
        aware_entries = []
        for e in entries:
            if e.created_at.tzinfo is None:
                # 如果是 naive，假设为 UTC
                aware_created_at = e.created_at.replace(tzinfo=timezone.utc)
                e.created_at = aware_created_at
            aware_entries.append(e)

        entries = [e for e in aware_entries if e.created_at >= cutoff_time]

        # 置信度过滤
        entries = [e for e in entries if e.confidence >= min_confidence]

        # 按相似度降序排序
        entries.sort(key=lambda x: x.similarity, reverse=True)

        # 限制数量
        entries = entries[:limit]

        logger.info(f"检索到 {len(entries)} 条历史记忆")

        # 详细调试日志：显示检索到的记忆内容
        if entries:
            logger.debug("📚 Local Memory 检索详情:")
            for i, entry in enumerate(entries, 1):
                logger.debug(
                    f"  [{i}] ID={entry.id[:8]}... asset={entry.assets} "
                    f"action={entry.action} confidence={entry.confidence:.2f} "
                    f"similarity={entry.similarity:.2f}"
                )
                logger.debug(f"      摘要: {entry.summary[:80]}..." if len(entry.summary) > 80 else f"      摘要: {entry.summary}")

        return entries

    def _load_pattern_file(self, file_path: Path) -> List[Dict]:
        """
        加载单个模式文件

        Args:
            file_path: 文件路径

        Returns:
            模式列表
        """
        if not file_path.exists():
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            patterns = data.get("patterns", [])
            logger.debug(f"加载 {len(patterns)} 条模式从 {file_path.name}")
            return patterns
        except Exception as e:
            logger.error(f"加载模式文件失败 {file_path}: {e}")
            return []

    def _normalize_patterns(self, patterns: List[Dict]) -> List[MemoryEntry]:
        """
        将 JSON 模式标准化为 MemoryEntry

        Args:
            patterns: 原始模式列表

        Returns:
            MemoryEntry 列表
        """
        entries: List[MemoryEntry] = []

        for item in patterns:
            try:
                # 解析时间戳（确保为 UTC aware datetime）
                from datetime import timezone

                timestamp_str = item.get("timestamp") or item.get("created_at")
                if timestamp_str:
                    created_at = datetime.fromisoformat(
                        str(timestamp_str).replace("Z", "+00:00")
                    )
                else:
                    created_at = datetime.now(timezone.utc)

                # 解析资产列表
                assets = item.get("assets") or item.get("asset") or []
                if isinstance(assets, str):
                    assets_list = [
                        part.strip()
                        for part in assets.split(",")
                        if part.strip()
                    ]
                else:
                    assets_list = [str(part).strip() for part in assets if str(part).strip()]

                if not assets_list:
                    assets_list = ["NONE"]

                # 构造 MemoryEntry
                entry = MemoryEntry(
                    id=item.get("id") or str(uuid4()),
                    created_at=created_at,
                    assets=assets_list,
                    action=item.get("action", "observe"),
                    confidence=float(item.get("confidence", 0.0)),
                    similarity=float(item.get("similarity", 1.0)),  # Local 模式无真实相似度，默认 1.0
                    summary=item.get("summary") or item.get("notes", ""),
                )

                entries.append(entry)
            except Exception as e:
                logger.error(f"标准化模式失败: {e}, 数据: {item}")
                continue

        return entries

    def save_pattern(self, category: str, pattern: Dict) -> None:
        """
        保存模式（可选，仅用于定期归纳任务）

        Args:
            category: 模式分类（如 listing, hack, regulation）
            pattern: 模式数据
        """
        file_path = self.pattern_dir / f"{category.lower()}.json"

        # 加载现有模式
        existing = []
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    existing = json.load(f).get("patterns", [])
            except Exception as e:
                logger.error(f"加载现有模式失败 {file_path}: {e}")

        # 追加新模式
        existing.append(pattern)

        # 去重（基于 summary）
        unique = {p.get("summary", str(uuid4())): p for p in existing}.values()

        # 限制数量（保留最近 50 条）
        limited = sorted(
            unique,
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )[:50]

        # 保存
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"patterns": list(limited)},
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            logger.info(f"模式已保存: {file_path.name} ({len(limited)} 条)")
        except Exception as e:
            logger.error(f"保存模式失败 {file_path}: {e}")

    def get_stats(self) -> Dict[str, any]:
        """
        获取记忆统计信息

        Returns:
            统计数据（文件数、总模式数、最老记录时间等）
        """
        if not self.pattern_dir.exists():
            return {
                "total_files": 0,
                "total_patterns": 0,
                "oldest_record": None,
            }

        files = list(self.pattern_dir.glob("*.json"))
        total_patterns = 0
        oldest_time = datetime.utcnow()

        for file_path in files:
            patterns = self._load_pattern_file(file_path)
            total_patterns += len(patterns)

            for p in patterns:
                timestamp_str = p.get("timestamp") or p.get("created_at")
                if timestamp_str:
                    try:
                        ts = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
                        if ts < oldest_time:
                            oldest_time = ts
                    except Exception:
                        pass

        return {
            "total_files": len(files),
            "total_patterns": total_patterns,
            "oldest_record": oldest_time.isoformat() if total_patterns > 0 else None,
        }
