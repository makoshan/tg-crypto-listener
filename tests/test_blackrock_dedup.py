"""Test case for Blackrock signal duplication issue."""

from src.utils import SignalMessageDeduplicator


def test_blackrock_duplicate_detection():
    """Test that similar Blackrock signals with different event_types are now detected."""
    dedup = SignalMessageDeduplicator(
        window_minutes=60,
        similarity_threshold=0.68,
        min_common_chars=10,
    )

    # Signal 1: 巨鲸动向
    summary1 = "BlockBeats：贝莱德将价值约2.25亿美元的1021枚比特币和25707枚以太坊存入Coinbase Prime，显示其对主流加密资产的持续配置意愿，预示潜在的买盘支撑。"
    
    # Signal 2: 融资/募资
    summary2 = "Odaily星球日报：贝莱德向Coinbase Prime存入巨额BTC和ETH，表明机构对加密市场的持续兴趣和潜在买入行为，预示可能的价格上涨。"
    
    # Signal 3: 其他
    summary3 = "Lookonchain：贝莱德分别向Coinbase Prime存入价值约1.18亿美元的比特币和价值约1.07亿美元的以太坊，显示出机构持续的买入和资金转移活动。"

    metadata1 = {
        "summary": summary1,
        "action": "买入",
        "direction": "做多",
        "event_type": "巨鲸动向",
        "asset": "BTC,ETH",
        "asset_names": "比特币,以太坊",
    }

    metadata2 = {
        "summary": summary2,
        "action": "买入",
        "direction": "做多",
        "event_type": "融资/募资",
        "asset": "BTC,ETH",
        "asset_names": "比特币,以太坊",
    }

    metadata3 = {
        "summary": summary3,
        "action": "买入",
        "direction": "做多",
        "event_type": "其他",
        "asset": "BTC,ETH",
        "asset_names": "比特币,以太坊",
    }

    # First signal should not be duplicate
    result1 = dedup.is_duplicate(**metadata1)
    print(f"Signal 1 (first) duplicate check: {result1}")
    assert not result1, "First signal should not be duplicate"

    # With improved deduplication, signals with similar text should be detected
    # even if event_type differs (core metadata: action, direction, asset match)
    result2 = dedup.is_duplicate(**metadata2)
    print(f"Signal 2 (different event_type) duplicate check: {result2}")
    assert result2, "Signal 2 should be detected as duplicate (core metadata matches)"

    # Third signal should also be detected
    result3 = dedup.is_duplicate(**metadata3)
    print(f"Signal 3 (different event_type) duplicate check: {result3}")
    assert result3, "Signal 3 should be detected as duplicate (core metadata matches)"


def test_different_assets_not_duplicate():
    """Test that signals with different assets are not detected as duplicates."""
    dedup = SignalMessageDeduplicator(
        window_minutes=60,
        similarity_threshold=0.68,
        min_common_chars=10,
    )

    summary = "某机构向Coinbase存入大量资产。"
    
    # First signal: BTC
    assert not dedup.is_duplicate(
        summary=summary,
        action="买入",
        direction="做多",
        event_type="巨鲸动向",
        asset="BTC",
        asset_names="比特币",
    )
    
    # Second signal: ETH (different asset, should not be duplicate)
    result = dedup.is_duplicate(
        summary=summary,
        action="买入",
        direction="做多",
        event_type="巨鲸动向",
        asset="ETH",
        asset_names="以太坊",
    )
    assert not result, "Different assets should not be considered duplicates"


if __name__ == "__main__":
    test_blackrock_duplicate_detection()
    print("✓ Blackrock duplicate detection test passed")
    
    test_different_assets_not_duplicate()
    print("✓ Different assets test passed")
    
    print("\n✅ All tests completed successfully")

