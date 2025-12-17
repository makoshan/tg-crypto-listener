#!/usr/bin/env python3
"""Test script to verify AI signal insertion to Supabase."""

import asyncio
import os
from dotenv import load_dotenv

from src.db.supabase_client import SupabaseClient
from src.db.repositories import AiSignalRepository
from src.db.models import AiSignalPayload

load_dotenv()


async def test_signal_insertion():
    """Test inserting an AI signal to verify database connectivity."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not key:
        print("❌ 缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY 环境变量")
        return

    print(f"📡 连接到 Supabase: {url}")
    print(f"🔑 Service Key 长度: {len(key)}")

    client = SupabaseClient(rest_url=url, service_key=key, timeout=10.0)
    repo = AiSignalRepository(client)

    # 测试插入一条 AI signal
    payload = AiSignalPayload(
        news_event_id=1,  # 假设 news_event_id=1 存在
        model_name="test-model",
        summary_cn="测试信号 - 验证数据库连接",
        event_type="listing",
        assets="BTC",
        action="buy",
        direction="bullish",
        confidence=0.85,
        strength="medium",
        risk_flags=[],
        notes="测试备注",
        links=[],
        execution_path="hot",
        should_alert=True,
        latency_ms=100,
    )

    try:
        signal_id = await repo.insert_signal(payload)
        if signal_id:
            print(f"✅ 成功插入 AI signal, ID: {signal_id}")
        else:
            print("⚠️ 插入返回 None，可能失败")
    except Exception as e:
        print(f"❌ 插入失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_signal_insertion())
