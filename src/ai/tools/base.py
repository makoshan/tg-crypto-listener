"""Base classes for external tools integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class ToolResult:
    """Standardized tool result format for all external tools."""

    source: str  # Tool source (e.g., "Tavily", "CoinGecko")
    timestamp: str  # ISO 8601 timestamp
    success: bool  # Whether the call succeeded
    data: dict  # Structured data
    triggered: bool  # Whether result exceeds threshold
    confidence: float  # Result confidence (0.0-1.0)
    error: Optional[str] = None  # Error message if failed

    @staticmethod
    def _format_timestamp() -> str:
        """Return current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()


class BaseTool(ABC):
    """Abstract base class for all tools."""

    def __init__(self, config):
        self._config = config
        self._timeout = getattr(config, "DEEP_ANALYSIS_TOOL_TIMEOUT", 10)

    @abstractmethod
    async def fetch(self, **kwargs) -> ToolResult:
        """Fetch data from tool API."""
        pass

    def _handle_timeout(self, error: Exception) -> ToolResult:
        """Standard timeout error handling."""
        return ToolResult(
            source=self.__class__.__name__,
            timestamp=ToolResult._format_timestamp(),
            success=False,
            data={},
            triggered=False,
            confidence=0.0,
            error=f"timeout: {str(error)}",
        )
