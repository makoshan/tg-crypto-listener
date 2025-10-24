#!/usr/bin/env python3
"""
Codex CLI 工具：新闻搜索
功能：调用 SearchTool.fetch() 并输出标准 JSON 格式供 Agent 解析

推荐用法：
    python scripts/codex_tools/search_news.py \
        --query "Binance ABC token listing official announcement" \
        --max-results 6

备用（缺少依赖时再使用，需网络下载）：
    uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \
        --query "Binance ABC token listing official announcement" \
        --max-results 6
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config import Config
from src.ai.tools.search.fetcher import SearchTool


async def main():
    parser = argparse.ArgumentParser(
        description="Search news using Tavily API for Codex CLI Agent"
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Search query string",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=6,
        help="Maximum number of results (default: 6)",
    )
    parser.add_argument(
        "--domains",
        nargs="*",
        help="Optional list of domains to restrict search (e.g., coindesk.com cointelegraph.com)",
    )
    args = parser.parse_args()

    try:
        # Load configuration
        config = Config()

        # Initialize SearchTool
        search_tool = SearchTool(config)

        # Execute search
        result = await search_tool.fetch(
            keyword=args.query,
            max_results=args.max_results,
            include_domains=args.domains,
        )

        # Convert ToolResult to JSON output
        if result.success:
            output = {
                "success": True,
                "data": result.data,
                "confidence": result.confidence,
                "triggered": result.triggered,
                "error": None,
            }
        else:
            output = {
                "success": False,
                "data": None,
                "confidence": 0.0,
                "triggered": False,
                "error": result.error or "Search failed with unknown error",
            }

        # Print JSON to stdout (for Codex Agent parsing)
        print(json.dumps(output, ensure_ascii=False, indent=2))

    except Exception as exc:
        # Print error as JSON with success=false
        error_output = {
            "success": False,
            "data": None,
            "confidence": 0.0,
            "triggered": False,
            "error": str(exc),
        }
        print(json.dumps(error_output, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
