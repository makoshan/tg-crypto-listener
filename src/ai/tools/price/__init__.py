"""Price tool provider registry and factory."""

from __future__ import annotations

from typing import Dict, Type

from .providers.base import PriceProvider
from .providers.coingecko import CoinGeckoPriceProvider
from .providers.coinmarketcap import CoinMarketCapPriceProvider

REGISTRY: Dict[str, Type[PriceProvider]] = {
    "coingecko": CoinGeckoPriceProvider,
    "coinmarketcap": CoinMarketCapPriceProvider,
}


def create_price_provider(config) -> PriceProvider:
    """Create a price provider instance based on configuration."""
    provider_key = getattr(config, "DEEP_ANALYSIS_PRICE_PROVIDER", "coingecko").lower()
    provider_cls = REGISTRY.get(provider_key)

    if provider_cls is None:
        raise ValueError(f"未知价格 Provider: {provider_key}")

    return provider_cls(config)


__all__ = [
    "create_price_provider",
    "PriceProvider",
]
