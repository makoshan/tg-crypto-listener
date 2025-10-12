"""Context Gather node for retrieving historical memory."""
import logging
from typing import Any, Dict

from .base import BaseNode
from ..helpers.memory import fetch_memory_entries, format_memory_evidence

logger = logging.getLogger(__name__)


class ContextGatherNode(BaseNode):
    """Node for gathering historical memory context."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Gather memory context from historical events."""
        logger.info("🧠 Context Gather: 获取历史记忆")

        entries = await fetch_memory_entries(
            engine=self.engine,
            payload=state["payload"],
            preliminary=state["preliminary"],
        )

        memory_text = format_memory_evidence(entries)
        logger.info("🧠 Context Gather: 找到 %d 条历史事件", len(entries))

        return {
            "memory_evidence": {
                "entries": entries,
                "formatted": memory_text,
                "count": len(entries),
            }
        }
