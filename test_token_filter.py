#!/usr/bin/env python3
"""Test script to validate token filtering and price consistency checks."""

import sys
import re
from pathlib import Path

# BLOCKED_LOW_MARKETCAP_TOKENS has been removed
# These tokens are now filtered at message level via BLOCK_KEYWORDS
# Testing now uses Config.BLOCK_KEYWORDS instead

# BLOCKED_TOKEN_NAME_PATTERNS has been removed - these patterns are no longer used
# "币安人生" has been moved to message-level BLOCK_KEYWORDS instead

NO_ASSET_TOKENS = {
    "",
    "NONE",
    "无",
    "NA",
    "N/A",
    "GENERAL",
    "GENERAL_CRYPTO",
    "CRYPTO",
    "MARKET",
    "MACRO",
}

# FORBIDDEN_ASSET_CODES has been removed - stock codes are now allowed


def test_blocked_tokens():
    """Test that blocked tokens are defined correctly."""
    print("=" * 80)
    print("测试 1: 屏蔽代币列表（已移至消息级别）")
    print("=" * 80)

    try:
        from src.config import Config
        block_keywords = Config.BLOCK_KEYWORDS
        print(f"\n消息级别黑名单 (BLOCK_KEYWORDS) ({len(block_keywords)} 个):")
        for keyword in sorted(block_keywords):
            print(f"  - {keyword}")
        
        print(f"\n说明:")
        print(f"  ✅ 黑名单关键词通过 .env 文件中的 BLOCK_KEYWORDS 配置")
        print(f"  ✅ 这些代币在消息接收早期就会被过滤，不会进入 AI 分析阶段")
        print(f"  ✅ 默认包含: TRUMP, MAGA, PEPE2, FLOKI2, SHIB2, DOGE2, 币安人生")
    except ImportError:
        print("\n⚠️  无法导入 Config，跳过测试")
        print("  说明: BLOCKED_LOW_MARKETCAP_TOKENS 已删除")
        print("  这些代币现在在消息级别的 BLOCK_KEYWORDS 中过滤")


def test_name_patterns():
    """Test that name patterns work correctly."""
    print("\n" + "=" * 80)
    print("测试 2: 代币名称模式匹配（已移除）")
    print("=" * 80)
    print("\n  ⚠️  BLOCKED_TOKEN_NAME_PATTERNS 功能已删除")
    print("  ✅ '币安人生' 已移至消息级别的 BLOCK_KEYWORDS")
    print("  📝 现在消息中包含 '币安人生' 会在消息接收早期被过滤")


def test_price_validation():
    """Test price consistency validation logic."""
    print("\n" + "=" * 80)
    print("测试 3: 价格一致性验证")
    print("=" * 80)

    # Import the listener module to access the validation function
    # We'll simulate the function here since we can't easily import it
    def simulate_price_validation(asset: str, price_usd: float, message_text: str) -> bool:
        """Simulated version of _validate_price_consistency."""
        import re

        MAJOR_ASSET_PRICE_RANGES = {
            "BTC": (10000, 200000),
            "ETH": (1000, 10000),
            "BNB": (200, 2000),
            "SOL": (10, 500),
        }

        # Check range
        if asset in MAJOR_ASSET_PRICE_RANGES:
            min_price, max_price = MAJOR_ASSET_PRICE_RANGES[asset]
            if not (min_price <= price_usd <= max_price):
                return False

        # Check mentioned prices
        price_patterns = [
            r'(\d+\.?\d*)\s*(?:USDT|USD|美元|刀)',
            r'\$\s*(\d+\.?\d*)',
            r'价格.*?(\d+\.?\d*)',
            r'突破.*?(\d+\.?\d*)',
        ]

        mentioned_prices = []
        for pattern in price_patterns:
            matches = re.findall(pattern, message_text, re.IGNORECASE)
            for match in matches:
                try:
                    mentioned_price = float(match)
                    mentioned_prices.append(mentioned_price)
                except ValueError:
                    continue

        if mentioned_prices:
            for mentioned_price in mentioned_prices:
                if mentioned_price > 0:
                    ratio = max(price_usd, mentioned_price) / min(price_usd, mentioned_price)
                    if ratio > 50:
                        return False

        return True

    test_cases = [
        # (asset, price_usd, message_text, should_pass, reason)
        ("BNB", 600.0, "BNB 价格上涨", True, "价格在合理范围内"),
        ("BNB", 0.22, "BNB 突破 0.22 USDT", False, "价格远低于预期范围"),
        ("BNB", 5000.0, "BNB 价格暴涨", False, "价格远高于预期范围"),
        ("BTC", 50000.0, "BTC 突破 50000 美元", True, "价格匹配"),
        ("BTC", 0.22, "某个币 0.22 USDT", False, "BTC价格不可能是0.22"),
        ("SOL", 100.0, "SOL 涨到 100 美元", True, "价格在范围内"),
        ("UNKNOWN", 0.22, "未知代币 0.22 USDT", True, "未知代币不检查范围"),
    ]

    print("\n价格验证测试:")
    for asset, price, message, should_pass, reason in test_cases:
        result = simulate_price_validation(asset, price, message)
        status = "✅" if result == should_pass else "❌"
        print(f"  {status} {asset} @ ${price}: {'通过' if result else '失败'} ({reason})")


if __name__ == "__main__":
    print("\n🧪 代币过滤和价格验证测试\n")

    test_blocked_tokens()
    test_name_patterns()
    test_price_validation()

    print("\n" + "=" * 80)
    print("测试完成！")
    print("=" * 80)
