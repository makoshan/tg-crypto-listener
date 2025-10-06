"""Typed structures for AI memory context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List


@dataclass
class MemoryEntry:
    """Single memory item retrieved from persistent storage."""

    id: str
    created_at: datetime
    assets: List[str]
    action: str
    confidence: float
    summary: str
    similarity: float

    def to_prompt_dict(self) -> dict[str, str | float]:
        """Serialize entry for prompt injection."""
        timestamp = self.created_at.strftime("%Y-%m-%d %H:%M")
        assets_display = ",".join(self.assets) if self.assets else "未识别"
        return {
            "id": self.id,
            "timestamp": timestamp,
            "assets": assets_display,
            "action": self.action,
            "confidence": round(self.confidence, 3),
            "similarity": round(self.similarity, 3),
            "summary": self.summary,
        }


@dataclass
class MemoryContext:
    """Container holding memories ready for prompt usage."""

    entries: List[MemoryEntry] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.entries

    def extend(self, items: Iterable[MemoryEntry]) -> None:
        self.entries.extend(items)

    def to_prompt_payload(self) -> list[dict[str, str | float]]:
        """Return JSON-serializable payload for prompts."""
        return [entry.to_prompt_dict() for entry in self.entries]
