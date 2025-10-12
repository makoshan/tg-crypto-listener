"""On-chain tool provider registry and factory."""

from __future__ import annotations

from typing import Dict, Type

from .providers.base import OnchainProvider
from .providers.defillama import DeFiLlamaOnchainProvider

REGISTRY: Dict[str, Type[OnchainProvider]] = {
    "defillama": DeFiLlamaOnchainProvider,
}


def create_onchain_provider(config) -> OnchainProvider:
    """Create an on-chain provider instance from configuration."""
    provider_key = getattr(config, "DEEP_ANALYSIS_ONCHAIN_PROVIDER", "defillama").lower()
    provider_cls = REGISTRY.get(provider_key)

    if provider_cls is None:
        raise ValueError(f"未知链上数据 Provider: {provider_key}")

    return provider_cls(config)


__all__ = [
    "create_onchain_provider",
    "OnchainProvider",
]
