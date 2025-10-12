"""External tools integration for deep analysis."""

from .base import BaseTool, ToolResult
from .exceptions import ToolFetchError, ToolRateLimitError, ToolTimeoutError
from .search.fetcher import SearchTool

__all__ = [
    "BaseTool",
    "ToolResult",
    "SearchTool",
    "ToolFetchError",
    "ToolTimeoutError",
    "ToolRateLimitError",
]
