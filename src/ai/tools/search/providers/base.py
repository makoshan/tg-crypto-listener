"""Base class for search providers."""

from __future__ import annotations

from abc import abstractmethod
from typing import Dict, Optional, Type

from ...base import BaseTool, ToolResult


class SearchProvider(BaseTool):
    """Abstract base class for search API providers, inherits timeout handling from BaseTool."""

    def __init__(self, config) -> None:
        super().__init__(config)

    @abstractmethod
    async def search(
        self,
        *,
        keyword: str,
        max_results: int,
        include_domains: Optional[list[str]] = None,
    ) -> ToolResult:
        """Execute search and return standardized result."""
        pass

    async def fetch(self, **kwargs) -> ToolResult:
        """Implement BaseTool.fetch() by delegating to search()."""
        return await self.search(**kwargs)


ProviderRegistry = Dict[str, Type["SearchProvider"]]
