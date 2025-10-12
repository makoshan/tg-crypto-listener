"""Tavily search API provider implementation."""

from typing import Optional
from urllib.parse import urlparse

import httpx

from ...base import ToolResult
from ...exceptions import ToolRateLimitError
from .base import SearchProvider
from src.utils import setup_logger

logger = setup_logger(__name__)


class TavilySearchProvider(SearchProvider):
    """Tavily search API implementation with domain whitelisting support."""

    API_ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._api_key = getattr(config, "TAVILY_API_KEY", None)
        self._multi_source_threshold = getattr(config, "SEARCH_MULTI_SOURCE_THRESHOLD", 3)

        if not self._api_key:
            raise ValueError("TAVILY_API_KEY 未配置")

    async def search(
        self,
        *,
        keyword: str,
        max_results: int,
        include_domains: Optional[list[str]] = None,
    ) -> ToolResult:
        """Execute Tavily search with optional domain filtering.

        Args:
            keyword: Search query
            max_results: Maximum number of results
            include_domains: Optional list of domains to restrict search to
        """
        payload = {
            "api_key": self._api_key,
            "query": keyword,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
        }

        # Add domain filtering if provided
        if include_domains:
            payload["include_domains"] = include_domains

        logger.debug(
            "Tavily 请求: keyword='%s', max_results=%d, domains=%s",
            keyword,
            max_results,
            include_domains or "all",
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self.API_ENDPOINT, json=payload)

            if response.status_code == 429:
                logger.warning("Tavily 返回 429 速率限制: keyword='%s'", keyword)
                raise ToolRateLimitError("Tavily API 超出速率限制")

            response.raise_for_status()
            data = response.json()
            logger.info(
                "Tavily 响应成功: keyword='%s', status=%d, results=%d",
                keyword,
                response.status_code,
                len(data.get("results", [])),
            )
        except httpx.TimeoutException as exc:
            logger.warning("Tavily 请求超时: keyword='%s'", keyword)
            return self._handle_timeout(exc)
        except ToolRateLimitError:
            return ToolResult(
                source="Tavily",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error="rate_limit",
            )
        except Exception as exc:
            logger.error("Tavily API 调用失败: keyword='%s' error=%s", keyword, exc)
            return ToolResult(
                source="Tavily",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=str(exc),
            )

        return self._parse_response(data, keyword)

    def _parse_response(self, data: dict, keyword: str) -> ToolResult:
        """Parse Tavily API response with improved confidence calculation."""
        results = data.get("results", [])

        # Calculate unique domains for multi-source detection
        unique_domains = set(
            urlparse(r.get("url", "")).netloc for r in results
        )
        multi_source = len(unique_domains) >= self._multi_source_threshold

        official_confirmed = self._check_official_confirmation(results)
        sentiment = self._analyze_sentiment(results)

        formatted_results = [
            {
                "title": item.get("title", ""),
                "source": urlparse(item.get("url", "")).netloc,
                "url": item.get("url", ""),
                "score": item.get("score", 0.0),
            }
            for item in results
        ]

        tool_data = {
            "keyword": keyword,
            "results": formatted_results,
            "multi_source": multi_source,
            "official_confirmed": official_confirmed,
            "sentiment": sentiment,
            "source_count": len(results),
            "unique_domains": len(unique_domains),  # Add unique domain count
        }

        # Trigger based on multi-source + high score (relaxed official requirement)
        avg_score = sum(r.get("score", 0) for r in results) / len(results) if results else 0
        triggered = multi_source and avg_score >= 0.6

        confidence = self._calculate_confidence(results, multi_source, official_confirmed)

        logger.debug(
            "Tavily 解析: keyword='%s', sources=%d, unique_domains=%d, multi_source=%s, official=%s, triggered=%s, confidence=%.2f",
            keyword,
            len(results),
            tool_data["unique_domains"],
            multi_source,
            official_confirmed,
            triggered,
            confidence,
        )

        return ToolResult(
            source="Tavily",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data=tool_data,
            triggered=triggered,
            confidence=confidence,
        )

    def _check_official_confirmation(self, results: list[dict]) -> bool:
        """Check if results contain official confirmation keywords."""
        official_keywords = [
            "官方",
            "声明",
            "公告",
            "official",
            "statement",
            "announcement",
            "confirmed",
            "press release",
        ]

        for item in results:
            title = item.get("title", "").lower()
            content = item.get("content", "").lower()
            if any(keyword in title or keyword in content for keyword in official_keywords):
                return True
        return False

    def _analyze_sentiment(self, results: list[dict]) -> dict:
        """Analyze sentiment from search results."""
        panic_keywords = ["暴跌", "崩盘", "恐慌", "hack", "exploit", "crash", "dump"]
        neutral_keywords = ["观察", "等待", "监控", "watch", "monitor", "observe"]
        optimistic_keywords = ["恢复", "稳定", "反弹", "recovery", "stable", "bounce"]

        panic = neutral = optimistic = 0
        for item in results:
            text = (item.get("title", "") + " " + item.get("content", "")).lower()
            if any(word in text for word in panic_keywords):
                panic += 1
            if any(word in text for word in neutral_keywords):
                neutral += 1
            if any(word in text for word in optimistic_keywords):
                optimistic += 1

        total = panic + neutral + optimistic
        if total == 0:
            return {"panic": 0.33, "neutral": 0.34, "optimistic": 0.33}

        return {
            "panic": round(panic / total, 2),
            "neutral": round(neutral / total, 2),
            "optimistic": round(optimistic / total, 2),
        }

    def _calculate_confidence(
        self,
        results: list[dict],
        multi_source: bool,
        official_confirmed: bool,
    ) -> float:
        """Calculate confidence with improved weighting based on test results.

        Test results showed official keyword detection is unreliable (20% rate),
        so we prioritize multi-source confirmation and Tavily scores.
        """
        if not results:
            return 0.0

        # Base confidence from average Tavily score
        avg_score = sum(item.get("score", 0.0) for item in results) / len(results)
        confidence = avg_score

        # Multi-source is now PRIMARY signal (increased weight)
        if multi_source:
            confidence = min(1.0, confidence + 0.15)

        # Official keywords demoted to SECONDARY (decreased weight)
        if official_confirmed:
            confidence = min(1.0, confidence + 0.10)

        # Bonus for result count
        if len(results) >= 5:
            confidence = min(1.0, confidence + 0.05)

        return round(confidence, 2)
