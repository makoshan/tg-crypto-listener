"""Search tool module with provider registry."""

from __future__ import annotations

from typing import Type

from .providers.base import ProviderRegistry, SearchProvider
from .providers.tavily import TavilySearchProvider

# Registry for search providers (extensible for future providers)
REGISTRY: ProviderRegistry = {
    "tavily": TavilySearchProvider,
}


def create_search_provider(config) -> SearchProvider:
    """Create search provider instance based on configuration.

    Args:
        config: Configuration object with DEEP_ANALYSIS_SEARCH_PROVIDER attribute

    Returns:
        SearchProvider instance

    Raises:
        ValueError: If provider is unknown
    """
    provider_key = getattr(config, "DEEP_ANALYSIS_SEARCH_PROVIDER", "tavily").lower()
    provider_cls: Type[SearchProvider] | None = REGISTRY.get(provider_key)

    if provider_cls is None:
        raise ValueError(f"未知搜索 Provider: {provider_key}")

    return provider_cls(config)


__all__ = [
    "create_search_provider",
    "SearchProvider",
]
