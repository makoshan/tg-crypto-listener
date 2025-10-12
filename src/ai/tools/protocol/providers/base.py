"""Base classes shared by protocol data providers."""

from __future__ import annotations

from abc import abstractmethod

from ...base import BaseTool, ToolResult


class ProtocolProvider(BaseTool):
    """Base interface for protocol-level data providers."""

    def __init__(self, config) -> None:
        super().__init__(config)

    @abstractmethod
    async def snapshot(self, *, slug: str) -> ToolResult:
        """Return protocol snapshot for the requested slug."""
        raise NotImplementedError

    async def fetch(self, **kwargs) -> ToolResult:
        """Bridge BaseTool.fetch to snapshot implementation."""
        return await self.snapshot(**kwargs)
