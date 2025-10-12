"""External tools integration for deep analysis."""

from .base import BaseTool, ToolResult
from .exceptions import ToolFetchError, ToolRateLimitError, ToolTimeoutError
from .macro.fetcher import MacroTool
from .onchain.fetcher import OnchainTool
from .price.fetcher import PriceTool
from .protocol.fetcher import ProtocolTool
from .search.fetcher import SearchTool

__all__ = [
    "BaseTool",
    "ToolResult",
    "SearchTool",
    "PriceTool",
    "MacroTool",
    "OnchainTool",
    "ProtocolTool",
    "ToolFetchError",
    "ToolTimeoutError",
    "ToolRateLimitError",
]
