"""SearchTool fetcher with caching support."""

from __future__ import annotations

import hashlib
import time
from typing import Dict, Optional, Tuple

import src.ai.tools.search as search_module

from ..base import ToolResult
from src.utils import setup_logger

logger = setup_logger(__name__)


class SearchTool:
    """Search tool facade with caching and provider hot-swapping support."""

    def __init__(self, config) -> None:
        self._config = config
        self._provider = search_module.create_search_provider(config)
        self._max_results = getattr(config, "SEARCH_MAX_RESULTS", 5)

        # Caching configuration
        self._cache: Dict[str, Tuple[ToolResult, float]] = {}
        self._cache_ttl = getattr(config, "SEARCH_CACHE_TTL_SECONDS", 600)  # 10 minutes

    async def fetch(
        self,
        *,
        keyword: str,
        max_results: Optional[int] = None,
        include_domains: Optional[list[str]] = None,
    ) -> ToolResult:
        """Fetch search results with caching.

        Args:
            keyword: Search query
            max_results: Maximum number of results (defaults to config value)
            include_domains: Optional list of domains to restrict search to

        Returns:
            ToolResult with search data
        """
        target = max_results or self._max_results
        domains_display = ",".join(include_domains) if include_domains else "all"
        logger.info(
            "🔧 搜索请求: keyword='%s', max_results=%d, domains=%s",
            keyword,
            target,
            domains_display,
        )

        # Check cache
        cache_key = self._generate_cache_key(keyword, target, include_domains)
        if cache_key in self._cache:
            cached_result, cached_time = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                logger.info(
                    "🔧 使用缓存的搜索结果: keyword='%s', max_results=%d, domains=%s",
                    keyword,
                    target,
                    domains_display,
                )
                return cached_result
            else:
                # Clean expired cache
                del self._cache[cache_key]

        # Call provider
        result = await self._provider.search(
            keyword=keyword,
            max_results=target,
            include_domains=include_domains,
        )

        # Store in cache if successful
        if result.success:
            self._cache[cache_key] = (result, time.time())
            data = result.data or {}
            logger.info(
                "🔧 搜索成功: keyword='%s', sources=%d, multi_source=%s, official=%s, triggered=%s, confidence=%.2f",
                keyword,
                data.get("source_count", 0),
                data.get("multi_source"),
                data.get("official_confirmed"),
                result.triggered,
                result.confidence,
            )
        else:
            logger.warning(
                "🔧 搜索失败: keyword='%s', error=%s",
                keyword,
                result.error or "unknown",
            )

        return result

    def _generate_cache_key(
        self,
        keyword: str,
        max_results: int,
        include_domains: Optional[list[str]],
    ) -> str:
        """Generate cache key from search parameters."""
        domains_str = ",".join(sorted(include_domains)) if include_domains else ""
        cache_input = f"{keyword}:{max_results}:{domains_str}"
        return hashlib.md5(cache_input.encode()).hexdigest()

    def refresh_provider(self) -> None:
        """Reload provider after configuration change (for runtime updates)."""
        self._provider = search_module.create_search_provider(self._config)
        logger.info("搜索 Provider 已刷新")
