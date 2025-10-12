"""Macro tool provider registry and factory."""

from __future__ import annotations

from typing import Dict, Type

from .providers.base import MacroProvider
from .providers.fred import FREDMacroProvider

REGISTRY: Dict[str, Type[MacroProvider]] = {
    "fred": FREDMacroProvider,
}


def create_macro_provider(config) -> MacroProvider:
    """Create a macro provider instance based on configuration."""
    provider_key = getattr(config, "DEEP_ANALYSIS_MACRO_PROVIDER", "fred").lower()
    provider_cls = REGISTRY.get(provider_key)

    if provider_cls is None:
        raise ValueError(f"未知宏观数据 Provider: {provider_key}")

    return provider_cls(config)


__all__ = [
    "create_macro_provider",
    "MacroProvider",
]
