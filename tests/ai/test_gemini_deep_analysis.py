#!/usr/bin/env python3
"""Test Gemini 2.5 Pro deep analysis engine."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

import json
from src.config import Config
from src.ai.signal_engine import EventPayload, SignalResult
from src.ai.deep_analysis.factory import create_deep_analysis_engine
from src.memory import create_memory_backend


async def test_gemini_deep_analysis():
    """Test Gemini 2.5 Pro deep analysis with a sample event."""

    print("=" * 60)
    print("🧪 测试 Gemini 2.5 Pro 深度分析引擎")
    print("=" * 60)

    # Load config
    config = Config()
    print(f"\n✅ 配置加载成功")
    print(f"   - 深度分析启用: {config.DEEP_ANALYSIS_ENABLED}")
    print(f"   - 主引擎: {config.DEEP_ANALYSIS_PROVIDER}")
    print(f"   - 备用引擎: {config.DEEP_ANALYSIS_FALLBACK_PROVIDER or '无'}")
    print(f"   - Gemini 模型: {config.GEMINI_DEEP_MODEL}")
    print(f"   - 超时时间: {config.GEMINI_DEEP_TIMEOUT_SECONDS}秒")

    # Create memory backend
    memory_bundle = create_memory_backend(config)
    print(f"\n✅ Memory Backend 初始化成功")

    # Create a simple parse callback
    def parse_callback(text: str) -> SignalResult:
        """Simple JSON parser for test."""
        print(f"\n📄 解析响应文本 (长度={len(text)}):")
        print(f"   {text[:500]}..." if len(text) > 500 else f"   {text}")

        if not text.strip():
            print("⚠️  警告: 响应文本为空")
            return SignalResult(
                status="error",
                error="Empty response from Gemini",
                summary="Analysis failed",
            )

        # Remove markdown code blocks
        cleaned_text = text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text[3:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]

        data = json.loads(cleaned_text.strip())
        return SignalResult(
            status="success",
            action=data.get("action", "observe"),
            confidence=float(data.get("confidence", 0.0)),
            summary=data.get("summary", ""),
            asset=data.get("asset", ""),
            event_type=data.get("event_type", "other"),
            direction=data.get("direction", "neutral"),
            strength=data.get("strength", "low"),
            risk_flags=data.get("risk_flags", []),
            notes=data.get("reasoning", ""),
        )

    # Create deep analysis engine
    try:
        engine = create_deep_analysis_engine(
            provider="gemini",
            config=config,
            parse_callback=parse_callback,
            memory_bundle=memory_bundle
        )
        print(f"✅ Gemini 深度分析引擎创建成功")
    except Exception as e:
        print(f"❌ 引擎创建失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Create test payload
    payload = EventPayload(
        text="Binance announces listing of $DOGE with 50x leverage trading",
        source="Test Source",
        timestamp=datetime.now(),
        translated_text="币安宣布上线 $DOGE 并支持 50 倍杠杆交易",
        language="en",
        translation_confidence=0.95,
        keywords_hit=["binance", "listing", "leverage"],
    )

    # Create preliminary result (simulating Gemini Flash Lite output)
    preliminary = SignalResult(
        status="success",
        action="buy",
        confidence=0.85,
        summary="Major exchange listing with high leverage",
        asset="DOGE",
        event_type="listing",
        direction="long",
        strength="high",
        risk_flags=[],
        notes="Binance listing typically drives significant price movement",
    )

    print(f"\n📤 测试数据:")
    print(f"   - 消息: {payload.text}")
    print(f"   - 初步判断: {preliminary.action} (confidence={preliminary.confidence})")
    print(f"   - 资产: {preliminary.asset}")

    # Run deep analysis
    print(f"\n🚀 开始深度分析...")
    try:
        result = await engine.analyse(payload, preliminary)

        print(f"\n✅ 深度分析成功!")
        print(f"=" * 60)
        print(f"📊 分析结果:")
        print(f"   - 状态: {result.status}")
        print(f"   - 行动: {result.action}")
        print(f"   - 置信度: {result.confidence}")
        print(f"   - 资产: {result.asset}")
        print(f"   - 事件类型: {result.event_type}")
        print(f"   - 方向: {result.direction}")
        print(f"   - 强度: {result.strength}")
        print(f"   - 风险标记: {result.risk_flags}")
        print(f"   - 摘要: {result.summary}")
        print(f"   - 备注: {result.notes[:200]}..." if len(result.notes) > 200 else f"   - 备注: {result.notes}")
        print(f"=" * 60)

        return True

    except Exception as e:
        print(f"\n❌ 深度分析失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_gemini_deep_analysis())
    sys.exit(0 if success else 1)
