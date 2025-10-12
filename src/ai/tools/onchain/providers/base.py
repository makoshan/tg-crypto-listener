"""Base classes shared by on-chain data providers."""

from __future__ import annotations

from abc import abstractmethod

from ...base import BaseTool, ToolResult


class OnchainProvider(BaseTool):
    """Base interface for on-chain liquidity providers."""

    def __init__(self, config) -> None:
        super().__init__(config)

    @abstractmethod
    async def snapshot(self, *, asset: str) -> ToolResult:
        """Return liquidity snapshot for the requested asset."""
        raise NotImplementedError

    async def fetch(self, **kwargs) -> ToolResult:
        """Bridge BaseTool.fetch to provider snapshot implementation."""
        return await self.snapshot(**kwargs)
