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

    def to_prompt_dict(self, current_time: datetime | None = None) -> dict[str, str | float]:
        """Serialize entry for prompt injection.

        Args:
            current_time: Current message timestamp to calculate time delta.
                         If None, uses now(UTC).
        """
        from datetime import timezone

        timestamp = self.created_at.strftime("%Y-%m-%d %H:%M")
        assets_display = ",".join(self.assets) if self.assets else "未识别"

        # Calculate time difference if current_time is provided
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        # Ensure both timestamps are timezone-aware
        created_aware = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=timezone.utc)
        current_aware = current_time if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)

        time_delta_hours = (current_aware - created_aware).total_seconds() / 3600

        return {
            "id": self.id,
            "timestamp": timestamp,
            "hours_ago": round(time_delta_hours, 1),  # New field: how long ago this happened
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

    def to_prompt_payload(self, current_time: datetime | None = None) -> list[dict[str, str | float]]:
        """Return JSON-serializable payload for prompts.

        Args:
            current_time: Current message timestamp to calculate time deltas.
        """
        return [entry.to_prompt_dict(current_time=current_time) for entry in self.entries]
