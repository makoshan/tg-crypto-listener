"""记忆检索 Helper 函数"""
from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from .formatters import format_memory_evidence as _format_memory_evidence

if TYPE_CHECKING:
    from src.ai.deep_analysis.gemini import GeminiDeepAnalysisEngine
    from src.ai.signal_engine import EventPayload, SignalResult
    from src.memory.types import MemoryContext, MemoryEntry

logger = logging.getLogger(__name__)


async def fetch_memory_entries(
    *,
    engine: "GeminiDeepAnalysisEngine",
    payload: "EventPayload",
    preliminary: "SignalResult",
    limit: Optional[int] = None,
) -> list[dict]:
    """
    独立的记忆检索 Helper，可在多处复用：
    1. _tool_fetch_memories (Function Calling 工具)
    2. _node_context_gather (LangGraph 节点)

    Args:
        engine: GeminiDeepAnalysisEngine 实例
        payload: 事件载荷
        preliminary: 初步分析结果
        limit: 最大返回数量（None 使用默认值）

    Returns:
        list[dict]: 格式化的记忆条目列表（prompt_dict 格式）
    """
    if not engine._memory or not engine._memory.enabled:
        logger.debug("记忆系统未启用或不可用")
        return []

    limit = limit or engine._memory_limit
    keywords = list(payload.keywords_hit or [])
    asset_codes = _normalise_asset_codes(preliminary.asset)

    repo = engine._memory.repository
    if repo is None:
        logger.warning("记忆仓储未初始化")
        return []

    entries: Optional[Sequence[MemoryEntry]] = None

    # 处理异步 fetch_memories 方法
    if hasattr(repo, "fetch_memories") and inspect.iscoroutinefunction(repo.fetch_memories):
        kwargs = {"embedding": None, "asset_codes": asset_codes}
        parameters = inspect.signature(repo.fetch_memories).parameters
        if "keywords" in parameters:
            kwargs["keywords"] = keywords

        try:
            context = await repo.fetch_memories(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning("Supabase 记忆检索失败: %s", exc)
            return []

        if isinstance(context, tuple) and len(context) == 2:
            # MemoryContext 是 NamedTuple
            entries = context[0]  # context.entries
        else:
            entries = []

    # 处理同步 fetch_memories 方法
    elif hasattr(repo, "fetch_memories"):
        kwargs = {"embedding": None, "asset_codes": asset_codes}
        parameters = inspect.signature(repo.fetch_memories).parameters
        if "keywords" in parameters:
            kwargs["keywords"] = keywords

        try:
            context = repo.fetch_memories(**kwargs)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning("记忆检索失败: %s", exc)
            return []

        if inspect.iscoroutine(context):
            context = await context

        if isinstance(context, tuple) and len(context) == 2:
            entries = context[0]
        elif isinstance(context, Iterable):
            entries = list(context)

    # 处理 load_entries 方法（本地记忆）
    elif hasattr(repo, "load_entries"):
        try:
            entries = repo.load_entries(  # type: ignore[attr-defined]
                keywords=keywords,
                limit=limit,
                min_confidence=engine._memory_min_confidence,
            )
        except Exception as exc:
            logger.warning("本地记忆检索失败: %s", exc)
            return []

    else:
        logger.warning("未知的记忆仓储类型: %s", type(repo).__name__)
        return []

    # 转换为 prompt dict 格式
    prompt_entries = _memory_entries_to_prompt(entries)[:limit] if entries else []
    return prompt_entries


def _normalise_asset_codes(raw_value) -> list[str]:
    """标准化资产代码列表"""
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        tokens = [token.strip().upper() for token in raw_value.split(",") if token.strip()]
    elif isinstance(raw_value, Iterable):
        tokens = [str(token).strip().upper() for token in raw_value if str(token).strip()]
    else:
        tokens = []
    return [token for token in tokens if token]


def _memory_entries_to_prompt(entries: Optional[Sequence] | Iterable | None) -> list[dict]:
    """将记忆条目转换为 prompt dict 格式"""
    if not entries:
        return []

    payload: list[dict] = []
    for entry in entries:
        try:
            if hasattr(entry, "to_prompt_dict"):
                payload.append(entry.to_prompt_dict())
            else:
                payload.append(dict(entry))  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning("记忆条目转换失败: %s", exc)
            continue

    return payload


def format_memory_evidence(entries: list[dict]) -> str:
    """
    向后兼容的格式化函数，复用 helpers.formatters 中的实现
    以在节点和测试中保持统一的导入路径。
    """
    return _format_memory_evidence(entries)
