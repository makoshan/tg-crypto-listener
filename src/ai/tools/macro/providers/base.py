"""Base classes shared by macro data providers."""

from __future__ import annotations

from abc import abstractmethod

from ...base import BaseTool, ToolResult


class MacroProvider(BaseTool):
    """Base interface for macro-economic data providers."""

    def __init__(self, config) -> None:
        super().__init__(config)

    @abstractmethod
    async def snapshot(self, *, indicator: str) -> ToolResult:
        """Return a structured snapshot for the requested macro indicator."""
        raise NotImplementedError

    async def fetch(self, **kwargs) -> ToolResult:
        """Bridge BaseTool.fetch to provider-specific snapshot implementation."""
        return await self.snapshot(**kwargs)
