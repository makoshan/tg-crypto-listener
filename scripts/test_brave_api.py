#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
from typing import Any, Dict, List, Optional

import httpx


API_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _filter_by_domains(results: List[Dict[str, Any]], include_domains: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not include_domains:
        return results
    allow = {d.lower() for d in include_domains}
    filtered = []
    for item in results:
        host = (
            (item.get("meta_url") or {}).get("host")
            or (item.get("meta_url") or {}).get("hostname")
            or (item.get("source") or {}).get("host")
            or ""
        ).lower()
        if host in allow:
            filtered.append(item)
    return filtered


def _normalize(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in results:
        host = (
            (item.get("meta_url") or {}).get("host")
            or (item.get("meta_url") or {}).get("hostname")
            or (item.get("source") or {}).get("host")
            or ""
        )
        normalized.append(
            {
                "title": item.get("title", ""),
                "source": host,
                "url": item.get("url", ""),
                "score": 0.0,
            }
        )
    return normalized


async def brave_search(
    *,
    api_key: str,
    query: str,
    count: int,
    include_domains: Optional[List[str]],
    timeout: float = 10.0,
) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": count}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(API_ENDPOINT, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    web = data.get("web") or {}
    results = web.get("results") or []
    results = _filter_by_domains(results, include_domains)
    normalized = _normalize(results)

    return {
        "query": query,
        "count": len(normalized),
        "results": normalized,
        "raw_keys": list(data.keys()),
        "http_status": resp.status_code,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test Brave Web Search API")
    parser.add_argument("--q", "--query", dest="query", required=True, help="Search query")
    parser.add_argument("--count", type=int, default=5, help="Max results to request")
    parser.add_argument(
        "--include-domains",
        nargs="*",
        default=None,
        help="Whitelist domains (space-separated)",
    )
    parser.add_argument("--api-key", dest="api_key", default=os.getenv("BRAVE_API_KEY", ""))
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Missing BRAVE_API_KEY (env or --api-key)")

    result = await brave_search(
        api_key=args.api_key,
        query=args.query,
        count=args.count,
        include_domains=args.include_domains,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())


