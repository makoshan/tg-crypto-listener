#!/usr/bin/env python3
"""Test Gemini API key rotation configuration."""

import sys
sys.path.insert(0, "/home/mako/tg-crypto-listener")

from src.config import Config

config = Config()

print("=" * 60)
print("Gemini API Key Configuration")
print("=" * 60)
print(f"GEMINI_API_KEY: {config.GEMINI_API_KEY[:20]}..." if config.GEMINI_API_KEY else "GEMINI_API_KEY: (empty)")
print(f"GEMINI_API_KEYS count: {len(config.GEMINI_API_KEYS)}")
for i, key in enumerate(config.GEMINI_API_KEYS, 1):
    print(f"  [{i}] {key[:20]}...")
print()
print(f"AI_ENABLED: {config.AI_ENABLED}")
print(f"AI_PROVIDER: {config.AI_PROVIDER}")
print(f"AI_API_KEY: {config.AI_API_KEY[:20]}..." if config.AI_API_KEY else "AI_API_KEY: (empty)")
print()

# Test GeminiClient initialization
from src.ai.signal_engine import AiSignalEngine

print("=" * 60)
print("Testing AiSignalEngine initialization")
print("=" * 60)

try:
    engine = AiSignalEngine.from_config(config)
    print(f"✅ AiSignalEngine created successfully")
    print(f"   Enabled: {engine.enabled}")
    print(f"   Provider: {engine._provider_label}")

    # Check if client has key rotator
    if hasattr(engine, '_client') and engine._client:
        client = engine._client
        if hasattr(client, '_key_rotator') and client._key_rotator:
            print(f"   ✅ Key rotator initialized")
            print(f"   Keys count: {client._key_rotator.key_count}")
        else:
            print(f"   ⚠️ No key rotator (single key mode)")
    else:
        print(f"   ⚠️ No client available")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
