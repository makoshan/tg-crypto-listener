#!/usr/bin/env python3
"""
Codex CLI 工具：价格数据获取
功能：调用 PriceTool.snapshot() 批量获取多个资产的价格数据

推荐用法：
    # 单个资产
    python scripts/codex_tools/fetch_price.py \
        --assets BTC

    # 多个资产
    python scripts/codex_tools/fetch_price.py \
        --assets BTC ETH SOL

备用（缺少依赖时再使用，需网络下载）：
    uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \
        --assets BTC ETH SOL
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
from src.ai.tools.price.fetcher import PriceTool


async def main():
    parser = argparse.ArgumentParser(
        description="Fetch price data for crypto assets (Codex CLI Agent)"
    )
    parser.add_argument(
        "--assets",
        nargs="+",
        required=True,
        help="Asset symbols to fetch (e.g., BTC ETH SOL USDC)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force refresh cache (skip cache)",
    )
    args = parser.parse_args()

    try:
        # Load configuration
        config = Config()

        # Initialize PriceTool
        price_tool = PriceTool(config)

        # Fetch price for each asset
        results = []
        all_success = True

        for asset in args.assets:
            asset = asset.strip().upper()
            if not asset:
                continue

            try:
                result = await price_tool.snapshot(
                    asset=asset,
                    force_refresh=args.force_refresh,
                )

                if result.success:
                    # Extract key fields from result.data
                    data = result.data or {}
                    price_info = {
                        "asset": asset,
                        "success": True,
                        "price": data.get("price"),
                        "price_change_24h": data.get("price_change_24h"),
                        "price_change_1h": data.get("price_change_1h"),
                        "price_change_7d": data.get("price_change_7d"),
                        "market_cap": data.get("market_cap"),
                        "volume_24h": data.get("volume_24h"),
                        "confidence": result.confidence,
                        "triggered": result.triggered,
                        "timestamp": result.timestamp,
                    }
                else:
                    price_info = {
                        "asset": asset,
                        "success": False,
                        "error": result.error or "Unknown error",
                    }
                    all_success = False

                results.append(price_info)

            except Exception as exc:
                results.append({
                    "asset": asset,
                    "success": False,
                    "error": str(exc),
                })
                all_success = False

        # Build output
        output = {
            "success": all_success,
            "count": len(results),
            "assets": results,
        }

        # Print JSON to stdout (for Codex Agent parsing)
        print(json.dumps(output, ensure_ascii=False, indent=2))

    except Exception as exc:
        # Print error as JSON with success=false
        error_output = {
            "success": False,
            "count": 0,
            "assets": [],
            "error": str(exc),
        }
        print(json.dumps(error_output, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
