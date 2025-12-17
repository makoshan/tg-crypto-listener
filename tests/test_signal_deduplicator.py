"""Unit tests for SignalMessageDeduplicator."""

from datetime import datetime, timedelta
import pytest

from src.utils import SignalMessageDeduplicator


class TestSignalMessageDeduplicator:
    """Test suite for signal-level deduplication logic."""

    def test_basic_duplicate_detection(self):
        """Test that identical summaries are detected as duplicates."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        summary = "EWCL NEWS：特朗普宣布取消与加拿大贸易谈判，此举可能加剧地缘政治紧张，引发市场避险情绪，对比特币、以太坊和 Solana 等主流加密货币构成利空。"
        metadata = {
            "action": "卖出",
            "direction": "做空",
            "event_type": "宏观动向",
            "asset": "BTC,ETH,SOL",
            "asset_names": "比特币,以太坊,索拉纳",
        }

        # First call should not be duplicate
        assert not dedup.is_duplicate(summary=summary, **metadata)

        # Second call with identical content should be duplicate
        assert dedup.is_duplicate(summary=summary, **metadata)

    def test_similar_summaries_detected(self):
        """Test that similar but not identical summaries are detected."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        summary1 = "EWCL NEWS：特朗普宣布取消与加拿大贸易谈判，此举可能加剧地缘政治紧张，引发市场避险情绪，对比特币、以太坊和 Solana 等主流加密货币构成利空。"
        summary2 = "EWCL NEWS：特朗普总统宣布与加拿大结束贸易谈判，加剧了地缘政治不确定性，可能引发市场避险情绪，对加密货币市场构成利空。"

        metadata = {
            "action": "卖出",
            "direction": "做空",
            "event_type": "宏观动向",
            "asset": "BTC,ETH,SOL",
            "asset_names": "比特币,以太坊,索拉纳",
        }

        # First summary
        assert not dedup.is_duplicate(summary=summary1, **metadata)

        # Similar summary should be detected as duplicate
        assert dedup.is_duplicate(summary=summary2, **metadata)

    def test_different_metadata_not_duplicate(self):
        """Test that same summary with different metadata is not duplicate."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        summary = "特朗普宣布新的贸易政策，可能影响加密货币市场。"

        # First signal: sell BTC
        assert not dedup.is_duplicate(
            summary=summary,
            action="卖出",
            direction="做空",
            event_type="宏观动向",
            asset="BTC",
            asset_names="比特币",
        )

        # Same summary but different action: buy ETH (should not be duplicate)
        assert not dedup.is_duplicate(
            summary=summary,
            action="买入",
            direction="做多",
            event_type="宏观动向",
            asset="ETH",
            asset_names="以太坊",
        )

    def test_window_expiration(self):
        """Test that old entries are cleaned up after time window."""
        dedup = SignalMessageDeduplicator(
            window_minutes=1,  # Very short window for testing
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        summary = "测试消息，用于验证时间窗口过期逻辑。"
        metadata = {
            "action": "observe",
            "direction": "",
            "event_type": "general",
            "asset": "NONE",
            "asset_names": "",
        }

        # Add entry
        assert not dedup.is_duplicate(summary=summary, **metadata)

        # Simulate time passing by manually adjusting timestamp
        if dedup.entries:
            dedup.entries[0].timestamp = datetime.now() - timedelta(minutes=2)

        # After window expiry, should not be duplicate
        assert not dedup.is_duplicate(summary=summary, **metadata)

    def test_dissimilar_summaries_not_duplicate(self):
        """Test that completely different summaries are not detected as duplicates."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        summary1 = "比特币价格突破新高，市场情绪乐观。"
        summary2 = "以太坊完成重大升级，Gas费用降低。"

        metadata = {
            "action": "观察",
            "direction": "",
            "event_type": "general",
            "asset": "NONE",
            "asset_names": "",
        }

        assert not dedup.is_duplicate(summary=summary1, **metadata)
        assert not dedup.is_duplicate(summary=summary2, **metadata)

    def test_empty_summary_not_duplicate(self):
        """Test that empty summaries are handled gracefully."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        # Empty summary should return False (not duplicate)
        assert not dedup.is_duplicate(
            summary="",
            action="observe",
            direction="",
            event_type="general",
            asset="NONE",
            asset_names="",
        )

    def test_normalization_removes_numbers_and_punctuation(self):
        """Test that normalization removes dynamic content like numbers and URLs."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        # Summaries differ only in numbers (prices) and URLs
        summary1 = "比特币价格上涨至 $110,979.5344，上涨 2.18%。详情：https://example.com/1"
        summary2 = "比特币价格上涨至 $111,217.4779，上涨 2.21%。详情：https://example.com/2"

        metadata = {
            "action": "买入",
            "direction": "做多",
            "event_type": "价格波动",
            "asset": "BTC",
            "asset_names": "比特币",
        }

        assert not dedup.is_duplicate(summary=summary1, **metadata)

        # Should be detected as duplicate since only numbers/URLs differ
        assert dedup.is_duplicate(summary=summary2, **metadata)

    def test_case_insensitive_metadata(self):
        """Test that metadata comparison is case-insensitive."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        summary = "新的监管政策出台，影响 DeFi 领域。"

        # First signal with lowercase metadata
        assert not dedup.is_duplicate(
            summary=summary,
            action="observe",
            direction="",
            event_type="regulation",
            asset="none",
            asset_names="",
        )

        # Same signal with mixed-case metadata should be duplicate
        assert dedup.is_duplicate(
            summary=summary,
            action="OBSERVE",
            direction="",
            event_type="REGULATION",
            asset="NONE",
            asset_names="",
        )

    def test_min_common_chars_threshold(self):
        """Test that min_common_chars prevents false positives on short matches."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.90,  # Very high similarity required
            min_common_chars=30,  # Require many common characters
        )

        summary1 = "A" * 100  # 100 'A's
        summary2 = "A" * 50 + "B" * 50  # 50 'A's + 50 'B's

        metadata = {
            "action": "observe",
            "direction": "",
            "event_type": "test",
            "asset": "NONE",
            "asset_names": "",
        }

        assert not dedup.is_duplicate(summary=summary1, **metadata)

        # Even with high text overlap, different character sets prevent duplicate detection
        # if min_common_chars is not met
        is_dup = dedup.is_duplicate(summary=summary2, **metadata)
        # This depends on SequenceMatcher ratio; with very different char sets,
        # it should not be flagged as duplicate
        assert not is_dup

    def test_multiple_signals_in_sequence(self):
        """Test handling multiple signals in sequence."""
        dedup = SignalMessageDeduplicator(
            window_minutes=60,
            similarity_threshold=0.68,
            min_common_chars=5,  # Lower threshold to ensure detection
        )

        # First signal
        assert not dedup.is_duplicate(
            summary="比特币价格突破历史新高",
            action="买入",
            direction="做多",
            event_type="价格波动",
            asset="BTC",
            asset_names="比特币",
        )

        # Different asset - not duplicate
        assert not dedup.is_duplicate(
            summary="以太坊完成合并升级",
            action="买入",
            direction="做多",
            event_type="技术升级",
            asset="ETH",
            asset_names="以太坊",
        )

        # Different asset and action - not duplicate
        assert not dedup.is_duplicate(
            summary="Solana 网络遭受攻击",
            action="卖出",
            direction="做空",
            event_type="安全事件",
            asset="SOL",
            asset_names="索拉纳",
        )

        # Similar to first signal with same metadata - should be duplicate
        assert dedup.is_duplicate(
            summary="比特币价格再创新高",
            action="买入",
            direction="做多",
            event_type="价格波动",
            asset="BTC",
            asset_names="比特币",
        )

    def test_balancer_hack_near_duplicates_across_sources(self):
        """Two Balancer hack alerts from different sources should dedup despite direction/asset_names differences."""
        dedup = SignalMessageDeduplicator(
            window_minutes=360,
            similarity_threshold=0.68,
            min_common_chars=10,
        )

        summary1 = (
            "BlockBeats：Balancer协议遭受史诗级黑客攻击，被盗资产已超9800万美元且攻击仍在持续，"
            "涉及以太坊、Base、Polygon、Sonic、Arbitrum、Optimism等多条链。事件对DeFi生态造成系统性冲击，BAL代币价格大幅下跌8.124%（1h），ETH同步下挫5.246%（24h）。"
        )
        summary2 = (
            "Lookonchain：Balancer协议遭受史诗级黑客攻击，被盗资金从7000万美元飙升至1.166亿美元，"
            "涉及以太坊、Base、Polygon等多条链。BAL代币价格剧烈下跌8.07%（1h）、6.986%（24h）。"
        )

        # First source
        assert not dedup.is_duplicate(
            summary=summary1,
            action="observe",
            direction="",  # possibly empty
            event_type="hack",
            asset="BAL",
            asset_names="Balancer",
        )

        # Second source with slightly different metadata that should no longer block dedup
        assert dedup.is_duplicate(
            summary=summary2,
            action="observe",
            direction="neutral",  # benign difference
            event_type="hack",
            asset="BAL",
            asset_names="",  # benign difference
        )
