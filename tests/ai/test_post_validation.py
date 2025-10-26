"""Tests for post-AI validation rules in signal_engine.py."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.ai.signal_engine import AiSignalEngine, SignalResult


@pytest.fixture
def mock_engine():
    """Create a minimal AiSignalEngine for testing validation rules."""
    return AiSignalEngine(
        enabled=False,
        client=None,
        threshold=0.4,
        semaphore=asyncio.Semaphore(1),
        provider_label="TEST",
    )


class TestPostValidationRules:
    """Test suite for _apply_post_validation_rules method."""

    def test_stale_event_forces_low_confidence(self, mock_engine):
        """Test that stale_event flag forces confidence <= 0.4."""
        result = SignalResult(
            status="success",
            summary="测试消息",
            event_type="product_launch",
            asset="POLY",
            action="buy",
            direction="long",
            confidence=0.80,
            strength="high",
            risk_flags=["stale_event", "speculative"],
            notes="消息已过期 24 小时以上，市场可能已反应。",
        )

        mock_engine._apply_post_validation_rules(result)

        assert result.confidence == 0.35, "stale_event 应强制置信度降至 0.35"
        assert result.action == "observe", "stale_event 应强制改为 observe"
        assert result.direction == "neutral", "stale_event 应强制方向为 neutral"
        assert "【后置验证修正】" in result.notes, "应添加修正说明"

    def test_stale_event_forces_observe_action(self, mock_engine):
        """Test that stale_event flag forces action=observe."""
        result = SignalResult(
            status="success",
            summary="测试",
            asset="BTC",
            action="sell",
            direction="short",
            confidence=0.75,
            risk_flags=["stale_event"],
        )

        mock_engine._apply_post_validation_rules(result)

        assert result.action == "observe"
        assert result.direction == "neutral"

    def test_no_asset_but_buy_action(self, mock_engine):
        """Test that asset=NONE with buy/sell action is corrected."""
        result = SignalResult(
            status="success",
            summary="测试",
            asset="NONE",
            action="buy",
            direction="long",
            confidence=0.70,
        )

        mock_engine._apply_post_validation_rules(result)

        assert result.action == "observe", "asset=NONE 应强制改为 observe"
        assert result.confidence <= 0.40, "asset=NONE 应降低置信度"
        assert "无可交易标的" in result.notes

    def test_future_event_with_buy_action(self, mock_engine):
        """Test that future events (未发行/将推出) force observe."""
        result = SignalResult(
            status="success",
            summary="测试",
            asset="POLY",
            action="buy",
            direction="long",
            confidence=0.80,
            notes="Polymarket 确认将推出原生 POLY 代币",
        )

        mock_engine._apply_post_validation_rules(result)

        assert result.action == "observe", "未发行代币应强制改为 observe"
        assert result.confidence <= 0.40, "未发行代币应降低置信度"
        assert "代币未发行" in result.notes

    def test_speculative_high_confidence_buy(self, mock_engine):
        """Test that speculative + high confidence buy/sell is corrected."""
        result = SignalResult(
            status="success",
            summary="测试",
            asset="BTC",
            action="buy",
            direction="long",
            confidence=0.85,
            risk_flags=["speculative", "vague_timeline"],
        )

        mock_engine._apply_post_validation_rules(result)

        assert result.action == "observe", "投机性内容应改为 observe"
        assert result.confidence <= 0.55, "投机性内容应降低置信度"
        assert "投机性内容" in result.notes

    def test_low_confidence_high_strength_mismatch(self, mock_engine):
        """Test that low confidence + high strength + buy/sell is corrected."""
        result = SignalResult(
            status="success",
            summary="测试",
            asset="ETH",
            action="sell",
            direction="short",
            confidence=0.45,
            strength="high",
        )

        mock_engine._apply_post_validation_rules(result)

        assert result.action == "observe", "低置信度不应有高强度操作"
        assert result.strength == "low", "低置信度应改为低强度"
        assert "置信度与操作强度不匹配" in result.notes

    def test_no_modification_for_valid_signal(self, mock_engine):
        """Test that valid signals are not modified."""
        original_notes = "正常的交易信号"
        result = SignalResult(
            status="success",
            summary="测试",
            asset="BTC",
            action="buy",
            direction="long",
            confidence=0.75,
            strength="high",
            notes=original_notes,
        )

        mock_engine._apply_post_validation_rules(result)

        # Should not modify a valid signal
        assert result.action == "buy"
        assert result.confidence == 0.75
        assert result.strength == "high"
        assert "【后置验证修正】" not in result.notes
        assert result.notes == original_notes

    def test_multiple_violations_combined(self, mock_engine):
        """Test that multiple violations are all corrected."""
        result = SignalResult(
            status="success",
            summary="测试",
            asset="NONE",
            action="buy",
            direction="long",
            confidence=0.80,
            strength="high",
            risk_flags=["stale_event", "speculative"],
            notes="代币将推出",
        )

        mock_engine._apply_post_validation_rules(result)

        # Should apply all applicable corrections
        assert result.action == "observe"
        assert result.confidence <= 0.40
        assert "【后置验证修正】" in result.notes
        # Should mention multiple issues
        assert any(
            keyword in result.notes
            for keyword in ["消息过期", "无可交易标的", "代币未发行"]
        )

    def test_polymarket_case_from_issue(self, mock_engine):
        """Test the exact Polymarket case that was problematic."""
        result = SignalResult(
            status="success",
            summary="Polymarket 类似于 NFT 市场的炒作",
            event_type="product_launch",
            asset="POLY",
            action="buy",
            direction="long",
            confidence=0.80,
            strength="high",
            risk_flags=["speculative", "stale_event"],
            notes="Polymarket 确认将推出原生 POLY 代币及空投。消息已过期 24 小时以上，市场可能已反应。",
        )

        mock_engine._apply_post_validation_rules(result)

        # This signal should be heavily corrected
        assert result.action == "observe", "应强制改为观察"
        assert result.confidence <= 0.40, "应强制降低置信度到 0.35-0.40"
        assert result.status == "skip", "最终应被过滤"
        assert "【后置验证修正】" in result.notes, "应有修正说明"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
