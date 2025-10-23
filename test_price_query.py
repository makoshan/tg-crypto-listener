#!/usr/bin/env python3
"""测试查询多个币种价格的功能"""

import asyncio
from src.config import Config
from src.ai.tools.price.fetcher import PriceTool


async def test_multi_asset_price():
    """测试查询多个资产价格"""

    # 加载配置
    config = Config()

    # 创建价格工具
    price_tool = PriceTool(config)

    print("=" * 80)
    print("🧪 测试查询多个币种价格")
    print("=" * 80)

    # 测试 1: 查询单个资产
    print("\n测试 1: 查询单个资产 (BTC)")
    print("-" * 80)
    result = await price_tool.snapshot(asset="BTC")

    if result.success:
        print(f"✅ 成功")
        data = result.data
        if data:
            metrics = data.get("metrics", {})
            print(f"  资产: {data.get('asset')}")
            print(f"  名称: {data.get('asset_name')}")
            print(f"  价格: ${metrics.get('price_usd', 'N/A'):,.2f}" if isinstance(metrics.get('price_usd'), (int, float)) else f"  价格: {metrics.get('price_usd', 'N/A')}")
            print(f"  24h 涨跌: {metrics.get('price_change_24h', 'N/A')}%")
            print(f"  市值: ${metrics.get('market_cap', 0):,.0f}" if isinstance(metrics.get('market_cap'), (int, float)) else f"  市值: {metrics.get('market_cap', 'N/A')}")
    else:
        print(f"❌ 失败: {result.error}")

    # 测试 2: 查询 XAUT
    print("\n测试 2: 查询 XAUT (Tether Gold)")
    print("-" * 80)
    result = await price_tool.snapshot(asset="XAUT")

    if result.success:
        print(f"✅ 成功")
        data = result.data
        if data:
            metrics = data.get("metrics", {})
            print(f"  资产: {data.get('asset')}")
            print(f"  名称: {data.get('asset_name')}")
            print(f"  价格: ${metrics.get('price_usd', 'N/A'):,.2f}" if isinstance(metrics.get('price_usd'), (int, float)) else f"  价格: {metrics.get('price_usd', 'N/A')}")
            print(f"  24h 涨跌: {metrics.get('price_change_24h', 'N/A')}%")
            print(f"  市值: ${metrics.get('market_cap', 0):,.0f}" if isinstance(metrics.get('market_cap'), (int, float)) else f"  市值: {metrics.get('market_cap', 'N/A')}")
    else:
        print(f"❌ 失败: {result.error}")

    # 测试 3: 批量查询（如果支持）
    print("\n测试 3: 尝试批量查询")
    print("-" * 80)
    print("注意: 当前 PriceTool.snapshot() 一次只支持单个资产")
    print("如需查询多个资产，需要循环调用")

    assets = ["BTC", "XAUT", "ETH"]
    results = {}

    for asset in assets:
        result = await price_tool.snapshot(asset=asset)
        if result.success and result.data:
            metrics = result.data.get("metrics", {})
            results[asset] = {
                "price": metrics.get("price_usd"),
                "change_24h": metrics.get("price_change_24h"),
                "name": result.data.get("asset_name"),
            }

    print("\n批量查询结果:")
    for asset, data in results.items():
        price = f"${data['price']:,.2f}" if isinstance(data['price'], (int, float)) else str(data['price'])
        change = f"{data['change_24h']:+.2f}%" if isinstance(data['change_24h'], (int, float)) else str(data['change_24h'])
        name = data['name'] or "Unknown"
        print(f"  {asset:6} ({name:20}): {price:>15}  24h: {change}")

    print("\n" + "=" * 80)
    print("✅ 测试完成")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_multi_asset_price())
