"""Protocol tool provider registry and factory."""

from __future__ import annotations

from typing import Dict, Optional, Type

from .providers.base import ProtocolProvider
# ProtocolTool disabled - all providers removed
# from .providers.defillama import DeFiLlamaProtocolProvider

REGISTRY: Dict[str, Type[ProtocolProvider]] = {
    # ProtocolTool disabled - no providers available
    # "defillama": DeFiLlamaProtocolProvider,  # Disabled
}


def create_protocol_provider(config) -> Optional[ProtocolProvider]:
    """Create a protocol provider instance from configuration.
    
    ProtocolTool is currently disabled - always returns None.
    """
    # ProtocolTool disabled - always return None
    return None


__all__ = [
    "create_protocol_provider",
    "ProtocolProvider",
]
