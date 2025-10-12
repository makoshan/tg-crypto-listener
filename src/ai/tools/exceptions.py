"""Custom exceptions for tool operations."""


class ToolFetchError(Exception):
    """Base class for tool fetch errors."""

    pass


class ToolTimeoutError(ToolFetchError):
    """Tool API timeout error."""

    pass


class ToolRateLimitError(ToolFetchError):
    """Tool API rate limit exceeded."""

    pass
