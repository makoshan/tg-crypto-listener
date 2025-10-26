#!/usr/bin/env python3
"""测试 Gemini API Key 轮换配置"""

import os
import sys

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import Config


def test_primary_gemini_keys():
    """测试主要 Gemini 分析的 API Keys 配置"""
    print("=" * 60)
    print("主要 Gemini 分析 API Keys 配置")
    print("=" * 60)

    print(f"\nGEMINI_API_KEY: {Config.GEMINI_API_KEY[:20]}..." if Config.GEMINI_API_KEY else "未配置")
    print(f"\nGEMINI_API_KEYS 数量: {len(Config.GEMINI_API_KEYS)}")

    if Config.GEMINI_API_KEYS:
        for idx, key in enumerate(Config.GEMINI_API_KEYS, 1):
            print(f"  密钥 {idx}: {key[:20]}...{key[-6:]}")
    else:
        print("  ⚠️ 未配置多个 API Keys")

    print()


def test_deep_analysis_gemini_keys():
    """测试深度分析 Gemini 的 API Keys 配置"""
    print("=" * 60)
    print("深度分析 Gemini API Keys 配置")
    print("=" * 60)

    print(f"\nGEMINI_DEEP_API_KEYS 数量: {len(Config.GEMINI_DEEP_API_KEYS)}")

    if Config.GEMINI_DEEP_API_KEYS:
        for idx, key in enumerate(Config.GEMINI_DEEP_API_KEYS, 1):
            print(f"  密钥 {idx}: {key[:20]}...{key[-6:]}")
    else:
        print("  ⚠️ 未配置，将回退到主要 GEMINI_API_KEYS")
        if Config.GEMINI_API_KEYS:
            print(f"  ✅ 回退密钥数量: {len(Config.GEMINI_API_KEYS)}")

    print()


def test_deep_analysis_config():
    """测试深度分析完整配置"""
    print("=" * 60)
    print("深度分析配置摘要")
    print("=" * 60)

    deep_config = Config.get_deep_analysis_config()

    print(f"\n主提供商: {deep_config.get('provider', '未配置')}")
    print(f"备用提供商: {deep_config.get('fallback_provider', '未配置') or '无'}")

    gemini_cfg = deep_config.get('gemini', {})
    print(f"\nGemini 配置:")
    print(f"  模型: {gemini_cfg.get('model', '未配置')}")
    print(f"  超时: {gemini_cfg.get('timeout', 0)}s")
    print(f"  最大重试: {gemini_cfg.get('max_retries', 0)}")
    print(f"  API Keys 数量: {len(gemini_cfg.get('api_keys', []))}")

    api_keys = gemini_cfg.get('api_keys', [])
    if api_keys:
        for idx, key in enumerate(api_keys, 1):
            print(f"    密钥 {idx}: {key[:20]}...{key[-6:]}")

    print()


def main():
    """主测试函数"""
    print("\n🔍 Gemini API Key 轮换配置检查\n")

    test_primary_gemini_keys()
    test_deep_analysis_gemini_keys()
    test_deep_analysis_config()

    print("=" * 60)
    print("配置检查完成")
    print("=" * 60)

    # 验证是否配置正确
    errors = []

    if not Config.GEMINI_API_KEYS:
        errors.append("❌ 主要 GEMINI_API_KEYS 未配置")
    elif len(Config.GEMINI_API_KEYS) < 2:
        errors.append("⚠️ 主要 GEMINI_API_KEYS 只有 1 个密钥，无法轮换")

    deep_config = Config.get_deep_analysis_config()
    gemini_api_keys = deep_config.get('gemini', {}).get('api_keys', [])
    if not gemini_api_keys:
        errors.append("❌ 深度分析 Gemini API Keys 未配置")
    elif len(gemini_api_keys) < 2:
        errors.append("⚠️ 深度分析 Gemini API Keys 只有 1 个密钥，无法轮换")

    if errors:
        print("\n问题:")
        for error in errors:
            print(f"  {error}")
    else:
        print("\n✅ 所有配置正确！API Key 轮换已启用。")

    print()


if __name__ == "__main__":
    main()
