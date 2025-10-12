"""External tools integration for deep analysis."""

from .base import BaseTool, ToolResult
from .exceptions import ToolFetchError, ToolRateLimitError, ToolTimeoutError
from .macro.fetcher import MacroTool
from .price.fetcher import PriceTool
from .search.fetcher import SearchTool

__all__ = [
    "BaseTool",
    "ToolResult",
    "SearchTool",
    "PriceTool",
    "MacroTool",
    "ToolFetchError",
    "ToolTimeoutError",
    "ToolRateLimitError",
]
