"""Brave Web Search API provider implementation."""

from __future__ import annotations

from typing import Optional

import httpx

from ...base import ToolResult
from .base import SearchProvider
from src.utils import setup_logger

logger = setup_logger(__name__)


class BraveSearchProvider(SearchProvider):
    """Brave Web Search API implementation with client-side domain filtering.

    Notes:
        - Brave 不支持服务端 include_domains 参数，域名限制需在客户端完成
        - 建议优先在查询中使用 `site:domain.com` 语法锁定站点
    """

    API_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._api_key = getattr(config, "BRAVE_API_KEY", None)
        self._multi_source_threshold = getattr(config, "SEARCH_MULTI_SOURCE_THRESHOLD", 3)

        if not self._api_key:
            raise ValueError("BRAVE_API_KEY 未配置")

    async def search(
        self,
        *,
        keyword: str,
        max_results: int,
        include_domains: Optional[list[str]] = None,
    ) -> ToolResult:
        params = {"q": keyword, "count": max_results}
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }

        logger.debug(
            "Brave 请求: keyword='%s', max_results=%d, domains=%s",
            keyword,
            max_results,
            include_domains or "all",
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self.API_ENDPOINT, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                logger.info(
                    "Brave 响应成功: keyword='%s', status=%d",
                    keyword,
                    response.status_code,
                )
        except httpx.TimeoutException as exc:
            logger.warning("Brave 请求超时: keyword='%s'", keyword)
            return self._handle_timeout(exc)
        except Exception as exc:
            logger.error("Brave API 调用失败: keyword='%s' error=%s", keyword, exc)
            return ToolResult(
                source="Brave",
                timestamp=ToolResult._format_timestamp(),
                success=False,
                data={},
                triggered=False,
                confidence=0.0,
                error=str(exc),
            )

        return self._parse_response(data, keyword, include_domains)

    def _parse_response(
        self, data: dict, keyword: str, include_domains: Optional[list[str]]
    ) -> ToolResult:
        web = data.get("web") or {}
        raw_results = web.get("results") or []

        # Client-side domain filtering (Brave 不支持 include_domains 参数)
        allowed = {d.lower() for d in (include_domains or [])}

        def _extract_host(item: dict) -> str:
            meta_url = item.get("meta_url") or {}
            return (meta_url.get("host") or meta_url.get("hostname") or "").lower()

        results: list[dict] = []
        for item in raw_results:
            host = _extract_host(item)
            if allowed and host not in allowed:
                continue
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    # Brave 无统一 score，使用 0.0 占位，后续可引入启发式
                    "score": 0.0,
                    # 提供内容字段以供情绪/官方检测（可能为空）
                    "content": item.get("description", "") or item.get("snippet", ""),
                    "_host": host,
                }
            )

        unique_domains = {r.get("_host", "") for r in results if r.get("_host")}
        multi_source = len(unique_domains) >= self._multi_source_threshold

        official_confirmed = self._check_official_confirmation(results)
        sentiment = self._analyze_sentiment(results)

        formatted_results = [
            {
                "title": r.get("title", ""),
                "source": r.get("_host", ""),
                "url": r.get("url", ""),
                "score": r.get("score", 0.0),
            }
            for r in results
        ]

        tool_data = {
            "keyword": keyword,
            "results": formatted_results,
            "multi_source": multi_source,
            "official_confirmed": official_confirmed,
            "sentiment": sentiment,
            "source_count": len(results),
            "unique_domains": len(unique_domains),
        }

        # Brave 无评分，触发仅基于多源
        triggered = multi_source
        confidence = self._calculate_confidence(results, multi_source, official_confirmed)

        logger.debug(
            "Brave 解析: keyword='%s', sources=%d, unique_domains=%d, multi_source=%s, official=%s, triggered=%s, confidence=%.2f",
            keyword,
            len(results),
            tool_data["unique_domains"],
            multi_source,
            official_confirmed,
            triggered,
            confidence,
        )

        return ToolResult(
            source="Brave",
            timestamp=ToolResult._format_timestamp(),
            success=True,
            data=tool_data,
            triggered=triggered,
            confidence=confidence,
        )

    # 复用 Tavily 的检测/情绪/置信度算法：为了最小改动，直接复制实现
    def _check_official_confirmation(self, results: list[dict]) -> bool:
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
            title = (item.get("title", "") or "").lower()
            content = (item.get("content", "") or "").lower()
            if any(keyword in title or keyword in content for keyword in official_keywords):
                return True
        return False

    def _analyze_sentiment(self, results: list[dict]) -> dict:
        panic_keywords = ["暴跌", "崩盘", "恐慌", "hack", "exploit", "crash", "dump"]
        neutral_keywords = ["观察", "等待", "监控", "watch", "monitor", "observe"]
        optimistic_keywords = ["恢复", "稳定", "反弹", "recovery", "stable", "bounce"]

        panic = neutral = optimistic = 0
        for item in results:
            text = ((item.get("title", "") or "") + " " + (item.get("content", "") or "")).lower()
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
        if not results:
            return 0.0

        # Brave 无评分，基础置信度设为中性偏低
        confidence = 0.5

        if multi_source:
            confidence = min(1.0, confidence + 0.15)
        if official_confirmed:
            confidence = min(1.0, confidence + 0.10)
        if len(results) >= 5:
            confidence = min(1.0, confidence + 0.05)

        return round(confidence, 2)


