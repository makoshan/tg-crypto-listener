#!/usr/bin/env python3
"""
Test script to reproduce news_events insertion issue from logs.

Based on the log entry:
0|tg-listener  | 2025-11-02 17:29:57,257 - src.db.repositories - DEBUG - 🗄️ 准备插入 news_events - source=BlockBeats, content_len=199, has_embedding=True
0|tg-listener  | ⚠️ Supabase insert 返回 id=None - table=news_events, record_keys=['id', 'created_at', 'updated_at', 'source', 'source_message_id', 'source_url', 'language', 'published_at', 'content_text', 'summary', 'translated_text', 'hash_raw', 'hash_canonical', 'embedding', 'keywords_hit', 'ingest_status', 'metadata', 'price_snapshot']
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

from src.db.models import NewsEventPayload
from src.db.repositories import NewsEventRepository
from src.db.supabase_client import get_supabase_client

# Configure logging to see debug output
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_test_payload() -> NewsEventPayload:
    """Create a test payload based on the logs."""
    # Sample data from logs
    return NewsEventPayload(
        source="BlockBeats",
        source_message_id="149830",
        source_url="https://t.me/theblockbeats/149830",
        published_at=datetime(2025, 11, 2, 9, 28, 2, tzinfo=timezone.utc),
        content_text="**PlanB：比特币月度RSI为66，走势稳健向上**\n\nBlockBeats 消息，11 月 2日，分析师 PlanB 在社交媒体上表示，比特币月度 RSI 为 66，显示其走势稳健向上。他强调，这一指标表明市场情绪积极，但投资者仍需关注宏观经济因素及市场动态。",
        summary="分析师PlanB指出比特币月度RSI为66，显示其走势稳健向上，表明市场情绪积极，但需关注宏观经济及市场动态。",
        translated_text="**PlanB：比特币月度RSI为66，走势稳健向上**\n\nBlockBeats 消息，11 月 2日，分析师 PlanB 在社交媒体上表示，比特币月度 RSI 为 66，显示其走势稳健向上。他强调，这一指标表明市场情绪积极，但投资者仍需关注宏观经济因素及市场动态。",
        language="zh",
        hash_raw="f5eb095a1c41a1de1f8f47aaeed3b468364b5a2a98c0522b23b8cfe843f51234",
        hash_canonical="abc247ce9a04ff4bb8742647c07729f5638d9ca7eb0702b641d2f3e5a1234567",
        embedding=[0.033319943, -0.018751279, -0.007883526, 0.015720056] + [0.0] * 1532,  # 1536-dim embedding
        keywords_hit=["比特币"],
        ingest_status="processed",
        metadata={
            "source": "BlockBeats",
            "ai_alert": "",
            "ai_status": "success",
            "ai_confidence": 0.50,
            "ai_strength": "medium",
            "ai_direction": "neutral",
            "ai_severity": "low",
            "language_detected": "zh",
            "translation_confidence": 1.0,
            "processed_at": "2025-11-02T09:29:57+00:00",
        },
        price_snapshot=None,
    )


async def test_insert_basic():
    """Test basic insert functionality."""
    logger.info("=" * 80)
    logger.info("Test 1: Basic Insert Test")
    logger.info("=" * 80)

    # Load environment
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        return False

    logger.info("✅ Environment loaded")
    logger.info(f"   Supabase URL: {supabase_url}")
    logger.info(f"   Service Key: {supabase_key[:20]}...")

    # Create client and repository
    client = get_supabase_client(supabase_url, supabase_key)
    repository = NewsEventRepository(client)

    # Create test payload
    payload = create_test_payload()
    logger.info(f"✅ Created test payload:")
    logger.info(f"   Source: {payload.source}")
    logger.info(f"   Message ID: {payload.source_message_id}")
    logger.info(f"   Content length: {len(payload.content_text)}")
    logger.info(f"   Has embedding: {payload.embedding is not None}")
    logger.info(f"   Embedding length: {len(payload.embedding) if payload.embedding else 0}")

    # Attempt insert
    logger.info("🚀 Attempting insert...")
    event_id = await repository.insert_event(payload)

    if event_id:
        logger.info(f"✅ Insert successful! Event ID: {event_id}")
        return True
    else:
        logger.error("❌ Insert failed - returned None")
        return False


async def test_insert_minimal():
    """Test insert with minimal required fields."""
    logger.info("=" * 80)
    logger.info("Test 2: Minimal Fields Insert Test")
    logger.info("=" * 80)

    # Load environment
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        return False

    # Create client and repository
    client = get_supabase_client(supabase_url, supabase_key)
    repository = NewsEventRepository(client)

    # Create minimal payload (no embedding)
    payload = NewsEventPayload(
        source="TestSource",
        source_message_id="test_minimal_001",
        source_url="https://test.com/minimal",
        published_at=datetime.now(timezone.utc),
        content_text="This is a minimal test message for debugging insert issues.",
        summary="Minimal test",
        translated_text=None,
        language="en",
        hash_raw="minimal_test_hash_001",
        hash_canonical="minimal_test_canonical_001",
        embedding=None,  # No embedding
        keywords_hit=["test"],
        ingest_status="processed",
        metadata={"test": True},
        price_snapshot=None,
    )

    logger.info(f"✅ Created minimal payload:")
    logger.info(f"   Source: {payload.source}")
    logger.info(f"   Has embedding: {payload.embedding is not None}")

    # Attempt insert
    logger.info("🚀 Attempting insert...")
    event_id = await repository.insert_event(payload)

    if event_id:
        logger.info(f"✅ Insert successful! Event ID: {event_id}")
        return True
    else:
        logger.error("❌ Insert failed - returned None")
        return False


async def test_insert_direct_client():
    """Test direct insert using SupabaseClient without repository."""
    logger.info("=" * 80)
    logger.info("Test 3: Direct Client Insert Test")
    logger.info("=" * 80)

    # Load environment
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        return False

    # Create client
    client = get_supabase_client(supabase_url, supabase_key)

    # Create minimal data dict for direct insert
    now = datetime.now(timezone.utc)
    data = {
        "source": "DirectTest",
        "source_message_id": "direct_test_001",
        "source_url": "https://test.com/direct",
        "published_at": now.isoformat(),
        "content_text": "Direct client insert test message.",
        "summary": "Direct test",
        "language": "en",
        "hash_raw": "direct_test_hash_001",
        "hash_canonical": "direct_test_canonical_001",
        "keywords_hit": ["test"],
        "ingest_status": "processed",
        "metadata": {"test": True, "method": "direct"},
    }

    logger.info("✅ Created direct insert data:")
    logger.info(f"   Data keys: {list(data.keys())}")

    # Attempt direct insert
    logger.info("🚀 Attempting direct insert...")
    try:
        record = await client.insert("news_events", data)
        if record and record.get("id"):
            logger.info(f"✅ Direct insert successful! Record ID: {record['id']}")
            logger.info(f"   Record keys: {list(record.keys())}")
            return True
        else:
            logger.error(f"❌ Direct insert returned None or no ID")
            logger.error(f"   Record: {record}")
            return False
    except Exception as e:
        logger.error(f"❌ Direct insert exception: {e}")
        return False


async def main():
    """Run all tests."""
    logger.info("🧪 Starting news_events insertion tests")
    logger.info("=" * 80)

    results = []

    # Test 1: Basic insert with embedding
    try:
        result = await test_insert_basic()
        results.append(("Basic Insert", result))
    except Exception as e:
        logger.error(f"❌ Test 1 failed with exception: {e}", exc_info=True)
        results.append(("Basic Insert", False))

    await asyncio.sleep(1)

    # Test 2: Minimal insert
    try:
        result = await test_insert_minimal()
        results.append(("Minimal Insert", result))
    except Exception as e:
        logger.error(f"❌ Test 2 failed with exception: {e}", exc_info=True)
        results.append(("Minimal Insert", False))

    await asyncio.sleep(1)

    # Test 3: Direct client insert
    try:
        result = await test_insert_direct_client()
        results.append(("Direct Client Insert", result))
    except Exception as e:
        logger.error(f"❌ Test 3 failed with exception: {e}", exc_info=True)
        results.append(("Direct Client Insert", False))

    # Summary
    logger.info("=" * 80)
    logger.info("📊 Test Summary")
    logger.info("=" * 80)
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        logger.info(f"{status} - {test_name}")

    total_tests = len(results)
    passed_tests = sum(1 for _, success in results if success)
    logger.info(f"\nTotal: {passed_tests}/{total_tests} tests passed")

    return passed_tests == total_tests


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
