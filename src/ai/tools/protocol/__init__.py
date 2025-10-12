"""Protocol tool provider registry and factory."""

from __future__ import annotations

from typing import Dict, Type

from .providers.base import ProtocolProvider
from .providers.defillama import DeFiLlamaProtocolProvider

REGISTRY: Dict[str, Type[ProtocolProvider]] = {
    "defillama": DeFiLlamaProtocolProvider,
}


def create_protocol_provider(config) -> ProtocolProvider:
    """Create a protocol provider instance from configuration."""
    provider_key = getattr(
        config,
        "DEEP_ANALYSIS_PROTOCOL_PROVIDER",
        "defillama",
    ).lower()
    provider_cls = REGISTRY.get(provider_key)
    if provider_cls is None:
        raise ValueError(f"未知协议 Provider: {provider_key}")
    return provider_cls(config)


__all__ = [
    "create_protocol_provider",
    "ProtocolProvider",
]
