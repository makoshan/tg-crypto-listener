"""
Claude CLI Deep Analysis Memory Handler

专门为 Claude CLI 深度分析设计的记忆管理系统
基于 Anthropic Memory Tool API (context-management-2025-06-27)
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils import setup_logger

from .memory_tool_handler import MemoryToolHandler, SecurityError

logger = setup_logger(__name__)


class ClaudeDeepAnalysisMemoryHandler(MemoryToolHandler):
    """
    Claude CLI 深度分析专用记忆处理器

    扩展自 MemoryToolHandler，添加深度分析特定功能:
    - 按事件类型组织的记忆检索
    - 自动化的案例研究存储
    - 学习洞察更新
    - 资产档案管理
    """

    # 支持的事件类型分析类别（基础 slug）
    SUPPORTED_EVENT_TYPES = {
        "hack",        # 黑客攻击
        "regulation",  # 监管事件
        "partnership", # 合作事件
        "listing",     # 上线事件
        "market",      # 市场走势
        "technical",   # 技术更新
    }

    EVENT_ALIASES = {
        "security": "hack",
        "security_breach": "hack",
        "regulatory_action": "regulation",
        "collaboration": "partnership",
        "integration": "partnership",
        "product": "technical",
        "launch": "listing",
    }

    DANGEROUS_PATTERNS = ("../", "..\\", "%2e%2e", "..%2f")

    def __init__(
        self,
        base_path: str = "./memories/claude_cli_deep_analysis",
        max_file_size: int = 51200,  # 50KB
        auto_cleanup: bool = True,
        cleanup_days: int = 30,
    ):
        """
        初始化 Claude CLI 深度分析记忆处理器

        Args:
            base_path: 记忆存储根目录
            max_file_size: 单个文件最大大小（字节）
            auto_cleanup: 是否自动清理过期文件
            cleanup_days: 文件过期天数
        """
        super().__init__(base_path=base_path, backend=None)
        self.max_file_size = max_file_size
        self.auto_cleanup = auto_cleanup
        self.cleanup_days = cleanup_days

        # 初始化目录结构
        self._init_directory_structure()

        logger.info(
            "Claude CLI 深度分析记忆系统已初始化: base_path=%s, max_file_size=%dKB, cleanup_days=%d",
            base_path,
            max_file_size // 1024,
            cleanup_days,
        )

    # ------------------------------------------------------------------
    # Memory Tool overrides (专用安全策略 & 功能)
    # ------------------------------------------------------------------
    def _validate_path(self, path: str) -> Path:
        """强化路径校验，允许 /memories/ 前缀并阻断穿越攻击。"""
        original_path = path or ""

        if original_path.startswith("/memories/"):
            normalized_path = original_path[len("/memories/") :]
        else:
            normalized_path = original_path

        lowered = normalized_path.replace("\\", "/").lower()
        if any(token in lowered for token in self.DANGEROUS_PATTERNS):
            raise SecurityError(f"检测到危险路径模式: {original_path}")

        return super()._validate_path(normalized_path)

    def _insert(self, path: str, line_number: int, text: str) -> Dict[str, Any]:
        """支持自动建档、末尾追加与大小限制的安全插入。"""
        full_path = self._validate_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        if not full_path.exists():
            full_path.touch()

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            if line_number == -1:
                insert_index = len(lines)
            else:
                if line_number < 1 or line_number > len(lines) + 1:
                    return {
                        "success": False,
                        "error": f"行号超出范围: {line_number} (文件共 {len(lines)} 行)"
                    }
                insert_index = line_number - 1

            insert_text = text if text.endswith("\n") else text + "\n"
            lines.insert(insert_index, insert_text)

            preview = insert_text.strip().replace("\n", " ")
            logger.warning(f"[Claude Deep Memory] 插入: {path}:{line_number} | 内容: {preview[:80]}...")

            with open(full_path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            self._enforce_file_size(full_path)

            return {
                "success": True,
                "message": f"Text inserted at line {line_number} in {path}"
            }
        except Exception as exc:  # pragma: no cover - I/O failure
            return {"success": False, "error": f"插入失败: {exc}"}

    def _sanitize_content(self, content: str) -> str:
        """强化内容审查，过滤敏感信息和超长文本。"""
        sanitized = super()._sanitize_content(content)

        sensitive_patterns = [
            (r"sk-[a-z0-9]{20,}", "[SK_FILTERED]"),
            (r"[A-Za-z0-9]{40,}", "[API_KEY_FILTERED]"),
            (r"\b\d{16}\b", "[CARD_FILTERED]"),
            (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[EMAIL_FILTERED]"),
        ]

        for pattern, replacement in sensitive_patterns:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)

        if len(sanitized) > self.max_file_size:
            sanitized = sanitized[: self.max_file_size] + "\n\n[truncated]"

        return sanitized

    def _init_directory_structure(self):
        """初始化记忆目录结构"""
        subdirs = [
            "assets",
            "patterns",
            "case_studies/by_asset",
            "case_studies/by_event_type",
            "market_patterns",
            "learning_insights",
            "context",
        ]

        for subdir in subdirs:
            dir_path = self.base_path / subdir
            dir_path.mkdir(parents=True, exist_ok=True)

        logger.debug("记忆目录结构初始化完成")

    def retrieve_similar_analyses(
        self,
        asset: str,
        event_type: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        检索相似的历史分析案例

        Args:
            asset: 资产代码 (e.g., "BTC", "ETH")
            event_type: 事件类型 (e.g., "listing", "hack", "regulation")
            limit: 最大返回数量

        Returns:
            历史分析案例列表
        """
        results = []

        normalized_event = self._normalize_event_type(event_type)
        safe_asset = self._normalize_asset_code(asset)

        # 1. 读取资产特定的分析历史
        if asset and asset != "NONE":
            asset_path = f"assets/{safe_asset}/{normalized_event}_analysis.md"
            asset_memory = self.execute_tool_use({
                "command": "view",
                "path": asset_path
            })
            if asset_memory.get("success"):
                results.append({
                    "type": "asset_specific",
                    "content": asset_memory.get("content", ""),
                    "source": asset_path,
                })
                logger.debug("检索到资产特定记忆: %s", asset_path)

        # 2. 读取事件类型通用规律
        pattern_path = f"patterns/{normalized_event}_analysis.md"
        pattern_memory = self.execute_tool_use({
            "command": "view",
            "path": pattern_path
        })
        if pattern_memory.get("success"):
            results.append({
                "type": "pattern",
                "content": pattern_memory.get("content", ""),
                "source": pattern_path,
            })
            logger.debug("检索到事件规律记忆: %s", pattern_path)

        # 3. 读取历史案例研究
        case_studies_path = f"case_studies/by_event_type/{normalized_event}/"
        case_studies = self.execute_tool_use({
            "command": "view",
            "path": case_studies_path
        })
        if case_studies.get("success"):
            results.append({
                "type": "case_studies",
                "content": case_studies.get("content", ""),
                "source": case_studies_path,
            })
            logger.debug("检索到案例研究记忆: %s", case_studies_path)

        logger.info(
            "检索到 %d 条历史分析记忆: asset=%s, event_type=%s",
            len(results),
            safe_asset,
            normalized_event,
        )

        return results[:limit]

    def store_analysis_memory(
        self,
        asset: str,
        event_type: str,
        analysis_data: Dict[str, Any],
    ):
        """
        存储深度分析记忆

        Args:
            asset: 资产代码
            event_type: 事件类型
            analysis_data: 分析数据字典，包含:
                - timestamp: 时间戳
                - event_summary: 事件摘要
                - preliminary_confidence: 初步置信度
                - preliminary_action: 初步操作
                - final_confidence: 最终置信度
                - adjustment_reason: 调整理由
                - verification_summary: 验证摘要
                - key_insights: 关键洞察
                - improvement_suggestions: 改进建议
        """
        if not asset or asset == "NONE":
            logger.warning("资产为空，跳过记忆存储")
            return

        safe_asset = self._normalize_asset_code(asset)
        normalized_event = self._normalize_event_type(event_type)

        # 更新资产特定的分析记录
        asset_path = f"assets/{safe_asset}/{normalized_event}_analysis.md"
        memory_entry = self._format_memory_entry(analysis_data)

        # 确保资产目录存在
        asset_dir = self.base_path / "assets" / safe_asset
        asset_dir.mkdir(parents=True, exist_ok=True)

        # 追加到文件末尾
        result = self.execute_tool_use({
            "command": "insert",
            "path": asset_path,
            "insert_line": -1,  # 末尾插入
            "insert_text": memory_entry
        })

        if result.get("success"):
            logger.info(
                "✅ 分析记忆已存储: asset=%s, event_type=%s, path=%s",
                asset,
                event_type,
                asset_path,
            )
        else:
            logger.error(
                "❌ 分析记忆存储失败: asset=%s, event_type=%s, error=%s",
                asset,
                event_type,
                result.get("error"),
            )

    def update_analysis_insights(
        self,
        insight_type: str,
        insights: str,
    ):
        """
        更新分析洞察

        Args:
            insight_type: 洞察类型 (e.g., "successful_predictions", "failed_predictions")
            insights: 洞察内容
        """
        insight_path = f"learning_insights/{insight_type}.md"

        # 检查文件是否存在
        full_path = self.base_path / insight_path.lstrip("/")
        if not full_path.exists():
            # 创建新文件
            result = self.execute_tool_use({
                "command": "create",
                "path": insight_path,
                "file_text": f"# {insight_type.replace('_', ' ').title()}\n\n{insights}"
            })
        else:
            # 更新现有文件
            result = self.execute_tool_use({
                "command": "str_replace",
                "path": insight_path,
                "old_str": "# " + insight_type.replace('_', ' ').title() + "\n\n",
                "new_str": f"# {insight_type.replace('_', ' ').title()}\n\n{insights}\n\n"
            })

        if result.get("success"):
            self._enforce_file_size(full_path)
            logger.info("✅ 洞察已更新: type=%s, path=%s", insight_type, insight_path)
        else:
            logger.error("❌ 洞察更新失败: type=%s, error=%s", insight_type, result.get("error"))

    def _format_memory_entry(self, data: Dict[str, Any]) -> str:
        """
        格式化记忆条目

        Args:
            data: 分析数据字典

        Returns:
            格式化的 Markdown 文本
        """
        timestamp = data.get('timestamp', datetime.now().isoformat())
        event_summary = data.get('event_summary', 'Unknown Event')
        preliminary_confidence = data.get('preliminary_confidence', 0.0)
        preliminary_action = data.get('preliminary_action', 'observe')
        final_confidence = data.get('final_confidence', 0.0)
        adjustment_reason = data.get('adjustment_reason', 'No adjustment')
        verification_summary = data.get('verification_summary', 'No verification')
        key_insights = data.get('key_insights', 'No insights')
        improvement_suggestions = data.get('improvement_suggestions', 'No suggestions')

        return f"""
## {timestamp}: {event_summary}
- **初步分析**: confidence={preliminary_confidence:.2f}, action={preliminary_action}
- **深度分析调整**: confidence={final_confidence:.2f}, reason="{adjustment_reason}"
- **验证结果**: {verification_summary}
- **关键洞察**: {key_insights}
- **改进建议**: {improvement_suggestions}

"""

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _normalize_event_type(self, event_type: str) -> str:
        """将事件类型映射为允许的分析类别."""
        if not event_type:
            return "market"

        lowered = event_type.strip().lower()
        lowered = lowered.replace("_analysis", "").replace("-", "_")
        lowered = re.sub(r"[^a-z_]", "", lowered)

        if lowered in self.SUPPORTED_EVENT_TYPES:
            return lowered

        if lowered in self.EVENT_ALIASES:
            return self.EVENT_ALIASES[lowered]

        return "market"

    def _normalize_asset_code(self, asset: str) -> str:
        """统一资产代码命名，移除非法字符."""
        sanitized = re.sub(r"[^A-Z0-9\-_/]", "", (asset or "").upper())
        return sanitized or "UNKNOWN"

    def _enforce_file_size(self, filepath: Path) -> None:
        """确保文件未超过设定大小."""
        try:
            size = filepath.stat().st_size
            if size > self.max_file_size:
                logger.warning(
                    "记忆文件超过大小限制，将截断: %s (size=%d bytes, limit=%d)",
                    filepath,
                    size,
                    self.max_file_size,
                )
                with open(filepath, "rb") as f:
                    content = f.read(self.max_file_size)
                with open(filepath, "wb") as f:
                    f.write(content + b"\n\n[truncated]")
        except Exception as exc:  # pragma: no cover - I/O failure
            logger.error("检查记忆文件大小失败: %s", exc)

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        获取记忆系统统计信息

        Returns:
            统计信息字典
        """
        stats = {
            "total_files": 0,
            "total_size_mb": 0.0,
            "assets_count": 0,
            "patterns_count": 0,
            "case_studies_count": 0,
            "learning_insights_count": 0,
            "last_cleanup": "never",
        }

        try:
            # 统计文件数量和大小
            total_size = 0
            for filepath in self.base_path.rglob("*.md"):
                stats["total_files"] += 1
                total_size += filepath.stat().st_size

                # 分类统计
                relative_path = filepath.relative_to(self.base_path)
                if relative_path.parts[0] == "assets":
                    stats["assets_count"] += 1
                elif relative_path.parts[0] == "patterns":
                    stats["patterns_count"] += 1
                elif relative_path.parts[0] == "case_studies":
                    stats["case_studies_count"] += 1
                elif relative_path.parts[0] == "learning_insights":
                    stats["learning_insights_count"] += 1

            stats["total_size_mb"] = round(total_size / 1024 / 1024, 2)

        except Exception as exc:
            logger.error("获取记忆统计信息失败: %s", exc)

        return stats

    def cleanup_old_memories(self) -> int:
        """
        清理过期的记忆文件

        Returns:
            清理的文件数量
        """
        if not self.auto_cleanup:
            logger.debug("自动清理已禁用")
            return 0

        from datetime import datetime, timedelta

        cutoff_date = datetime.now() - timedelta(days=self.cleanup_days)
        cleaned_count = 0

        try:
            # 归档目录
            archive_path = self.base_path / "archive"
            archive_path.mkdir(exist_ok=True)

            # 只清理 context 目录中的临时文件
            context_path = self.base_path / "context"
            if context_path.exists():
                for filepath in context_path.glob("*.md"):
                    last_modified = datetime.fromtimestamp(filepath.stat().st_mtime)
                    if last_modified < cutoff_date:
                        # 归档而非直接删除
                        archive_dest = archive_path / filepath.name
                        filepath.rename(archive_dest)
                        cleaned_count += 1
                        logger.debug(
                            "归档过期记忆文件: %s (last modified: %s)",
                            filepath.name,
                            last_modified.strftime("%Y-%m-%d"),
                        )

            if cleaned_count > 0:
                logger.info("✅ 清理了 %d 个过期记忆文件", cleaned_count)

        except Exception as exc:
            logger.error("清理记忆文件失败: %s", exc)

        return cleaned_count
