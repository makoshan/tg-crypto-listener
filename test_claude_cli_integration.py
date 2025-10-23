#!/usr/bin/env python3
"""Test Claude CLI deep analysis engine integration."""

import asyncio
import logging
import os
import sys
from datetime import datetime

# Set up test environment variables
os.environ["DEEP_ANALYSIS_ENABLED"] = "true"
os.environ["DEEP_ANALYSIS_PROVIDER"] = "claude_cli"
os.environ["CLAUDE_CLI_PATH"] = "claude"
os.environ["CLAUDE_CLI_TIMEOUT"] = "60"
os.environ["CLAUDE_CLI_RETRY_ATTEMPTS"] = "1"
os.environ["CLAUDE_CLI_ALLOWED_TOOLS"] = "Bash,Read"

from src.config import Config
from src.ai.signal_engine import EventPayload, SignalResult
from src.ai.deep_analysis.factory import create_deep_analysis_engine

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def test_config():
    """Test that Config loads Claude CLI settings correctly."""
    logger.info("=" * 60)
    logger.info("Testing Config class...")
    logger.info("=" * 60)

    # Check basic config attributes
    assert hasattr(Config, "CLAUDE_CLI_PATH"), "Missing CLAUDE_CLI_PATH"
    assert hasattr(Config, "CLAUDE_CLI_TIMEOUT"), "Missing CLAUDE_CLI_TIMEOUT"
    assert hasattr(Config, "CLAUDE_CLI_RETRY_ATTEMPTS"), "Missing CLAUDE_CLI_RETRY_ATTEMPTS"
    assert hasattr(Config, "CLAUDE_CLI_ALLOWED_TOOLS"), "Missing CLAUDE_CLI_ALLOWED_TOOLS"
    assert hasattr(Config, "CLAUDE_CLI_EXTRA_ARGS"), "Missing CLAUDE_CLI_EXTRA_ARGS"
    assert hasattr(Config, "CLAUDE_CLI_WORKDIR"), "Missing CLAUDE_CLI_WORKDIR"

    logger.info("✅ Config class has all required attributes")
    logger.info(f"  CLAUDE_CLI_PATH: {Config.CLAUDE_CLI_PATH}")
    logger.info(f"  CLAUDE_CLI_TIMEOUT: {Config.CLAUDE_CLI_TIMEOUT}")
    logger.info(f"  CLAUDE_CLI_RETRY_ATTEMPTS: {Config.CLAUDE_CLI_RETRY_ATTEMPTS}")
    logger.info(f"  CLAUDE_CLI_ALLOWED_TOOLS: {Config.CLAUDE_CLI_ALLOWED_TOOLS}")
    logger.info(f"  CLAUDE_CLI_EXTRA_ARGS: {Config.CLAUDE_CLI_EXTRA_ARGS}")
    logger.info(f"  CLAUDE_CLI_WORKDIR: {Config.CLAUDE_CLI_WORKDIR}")

    # Check deep_analysis_config
    deep_config = Config.get_deep_analysis_config()
    logger.info(f"\nDeep analysis config enabled: {deep_config.get('enabled')}")
    logger.info(f"Deep analysis provider: {deep_config.get('provider')}")

    assert "claude_cli" in deep_config, "Missing claude_cli in deep_analysis_config"
    claude_cli_cfg = deep_config["claude_cli"]

    logger.info(f"\nClaude CLI configuration from get_deep_analysis_config():")
    logger.info(f"  cli_path: {claude_cli_cfg.get('cli_path')}")
    logger.info(f"  timeout: {claude_cli_cfg.get('timeout')}")
    logger.info(f"  max_retries: {claude_cli_cfg.get('max_retries')}")
    logger.info(f"  allowed_tools: {claude_cli_cfg.get('allowed_tools')}")
    logger.info(f"  extra_args: {claude_cli_cfg.get('extra_args')}")
    logger.info(f"  working_directory: {claude_cli_cfg.get('working_directory')}")

    logger.info("\n✅ Config validation passed!")
    return True


def dummy_parse_callback(json_text: str) -> SignalResult:
    """Dummy callback for parsing JSON responses."""
    return SignalResult(
        status="success",
        summary="Test summary",
        event_type="other",
        asset="BTC",
        action="observe",
        confidence=0.5,
        risk_flags=[],
        notes="Test notes",
        raw_response=json_text,
    )


def test_factory():
    """Test that factory can create Claude CLI engine."""
    logger.info("\n" + "=" * 60)
    logger.info("Testing factory.create_deep_analysis_engine()...")
    logger.info("=" * 60)

    try:
        engine = create_deep_analysis_engine(
            provider="claude_cli",
            config=Config,
            parse_callback=dummy_parse_callback,
            memory_bundle=None,
        )

        logger.info(f"✅ Successfully created engine: {type(engine).__name__}")
        logger.info(f"  Provider name: {engine.provider_name}")
        logger.info(f"  CLI path: {engine._cli_path}")
        logger.info(f"  Timeout: {engine._timeout}")
        logger.info(f"  Max retries: {engine._max_retries}")
        logger.info(f"  Allowed tools: {engine._allowed_tools}")
        logger.info(f"  Extra args: {engine._extra_args}")
        logger.info(f"  Working directory: {engine._working_directory}")

        return True
    except Exception as exc:
        logger.error(f"❌ Failed to create engine: {exc}", exc_info=True)
        return False


async def test_engine_prompt_building():
    """Test that engine can build prompts correctly."""
    logger.info("\n" + "=" * 60)
    logger.info("Testing engine prompt building...")
    logger.info("=" * 60)

    try:
        engine = create_deep_analysis_engine(
            provider="claude_cli",
            config=Config,
            parse_callback=dummy_parse_callback,
            memory_bundle=None,
        )

        # Create test payload
        payload = EventPayload(
            text="Test news: Bitcoin price reaches new high",
            source="test_source",
            timestamp=datetime.now(),
            translated_text="测试新闻：比特币价格创新高",
            language="en",
            translation_confidence=0.9,
            keywords_hit=["bitcoin", "price"],
        )

        # Create preliminary result
        preliminary = SignalResult(
            status="success",
            summary="Bitcoin price analysis",
            event_type="other",
            asset="BTC",
            action="observe",
            confidence=0.7,
            risk_flags=[],
            notes="Preliminary analysis",
        )

        # Test prompt building
        prompt = engine._build_cli_prompt(payload, preliminary)

        logger.info(f"✅ Successfully built prompt")
        logger.info(f"  Prompt length: {len(prompt)} characters")
        logger.info(f"  Contains tool guidelines: {'工具使用守则' in prompt}")
        logger.info(f"  Contains JSON requirement: {'JSON' in prompt}")

        # Show first 500 chars of prompt
        logger.info(f"\n  Prompt preview:\n{prompt[:500]}...")

        return True
    except Exception as exc:
        logger.error(f"❌ Failed to build prompt: {exc}", exc_info=True)
        return False


def main():
    """Run all integration tests."""
    logger.info("Starting Claude CLI integration tests...\n")

    results = []

    # Test 1: Config
    try:
        results.append(("Config validation", test_config()))
    except Exception as exc:
        logger.error(f"Config test failed: {exc}", exc_info=True)
        results.append(("Config validation", False))

    # Test 2: Factory
    try:
        results.append(("Factory creation", test_factory()))
    except Exception as exc:
        logger.error(f"Factory test failed: {exc}", exc_info=True)
        results.append(("Factory creation", False))

    # Test 3: Prompt building
    try:
        results.append(("Prompt building", asyncio.run(test_engine_prompt_building())))
    except Exception as exc:
        logger.error(f"Prompt building test failed: {exc}", exc_info=True)
        results.append(("Prompt building", False))

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Test Summary")
    logger.info("=" * 60)

    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status}: {test_name}")

    total_passed = sum(1 for _, passed in results if passed)
    total_tests = len(results)

    logger.info(f"\nTotal: {total_passed}/{total_tests} tests passed")

    if total_passed == total_tests:
        logger.info("\n🎉 All integration tests passed!")
        return 0
    else:
        logger.error(f"\n❌ {total_tests - total_passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
