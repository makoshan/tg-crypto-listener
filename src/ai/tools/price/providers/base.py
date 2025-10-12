"""Base classes shared by price providers."""

from __future__ import annotations

from abc import abstractmethod

from ...base import BaseTool, ToolResult


class PriceProvider(BaseTool):
    """Base interface for price data providers."""

    def __init__(self, config) -> None:
        super().__init__(config)

    @abstractmethod
    async def snapshot(self, *, asset: str) -> ToolResult:
        """Return a structured snapshot for the requested asset."""
        raise NotImplementedError

    async def fetch(self, **kwargs) -> ToolResult:
        """Bridge BaseTool.fetch to the provider snapshot implementation."""
        return await self.snapshot(**kwargs)
