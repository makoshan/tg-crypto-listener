#!/usr/bin/env python3
"""测试低市值代币和 Binance Alpha 过滤逻辑"""

from datetime import datetime
from src.ai.signal_engine import build_signal_prompt, EventPayload


def test_binance_alpha_prompt():
    """测试 Binance Alpha 相关消息的 prompt 是否包含特殊处理规则"""
    payload = EventPayload(
        text='Binance Alpha 将于 10 月 21 日上线 SigmaDotMoney (SIGMA)，符合条件用户可通过 Alpha 活动页面领取空投。',
        source='test_channel',
        timestamp=datetime.now(),
        translated_text='Binance Alpha will launch SigmaDotMoney (SIGMA) on October 21.',
        language='zh',
        translation_confidence=0.9,
    )

    messages = build_signal_prompt(payload)
    system_prompt = messages[0]['content']

    print("=" * 80)
    print("测试 1: Binance Alpha 消息 prompt 检查")
    print("=" * 80)

    # 检查是否包含 Binance Alpha 规则
    checks = [
        ('Binance Alpha 特殊处理', 'Binance Alpha 特殊处理' in system_prompt),
        ('降低 confidence', 'confidence 0.2-0.3' in system_prompt or 'confidence ≤0.5' in system_prompt),
        ('市值较小提示', '市值较小' in system_prompt or '投机性强' in system_prompt),
    ]

    for check_name, result in checks:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {check_name}")

    return all(result for _, result in checks)


def test_low_market_cap_prompt():
    """测试低市值代币的 prompt 是否包含风险控制规则"""
    payload = EventPayload(
        text='某小市值代币 XYZ 即将上线 DEX，预计市值 500 万美元',
        source='test_channel',
        timestamp=datetime.now(),
        language='zh',
    )

    messages = build_signal_prompt(payload)
    system_prompt = messages[0]['content']

    print("\n" + "=" * 80)
    print("测试 2: 低市值代币 prompt 检查")
    print("=" * 80)

    # 检查是否包含低市值规则
    checks = [
        ('低市值代币风险控制', '低市值代币风险控制' in system_prompt),
        ('5000万美元阈值', '5000万美元' in system_prompt or '市值 <' in system_prompt),
        ('1000万美元阈值', '1000万美元' in system_prompt),
        ('liquidity_risk 标记', 'liquidity_risk' in system_prompt),
    ]

    for check_name, result in checks:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {check_name}")

    return all(result for _, result in checks)


def test_excluded_event_types():
    """测试深度分析排除事件类型配置"""
    print("\n" + "=" * 80)
    print("测试 3: 深度分析排除事件类型检查")
    print("=" * 80)

    # 检查 signal_engine.py
    with open('src/ai/signal_engine.py', 'r') as f:
        content = f.read()

    import re
    match = re.search(r'excluded_event_types\s*=\s*\{([^}]+)\}', content)
    if match:
        excluded_str = match.group(0)
        excluded = eval(excluded_str.split('=')[1].strip())

        checks = [
            ('包含 airdrop', 'airdrop' in excluded),
            ('包含 macro', 'macro' in excluded),
            ('包含 scam_alert', 'scam_alert' in excluded),
            ('总数正确 (6个)', len(excluded) == 6),
        ]

        for check_name, result in checks:
            status = "✅ 通过" if result else "❌ 失败"
            print(f"{status}: {check_name}")

        print(f"\n完整列表: {sorted(excluded)}")
        return all(result for _, result in checks)

    return False


def main():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("低市值代币和 Binance Alpha 过滤逻辑测试")
    print("=" * 80)

    results = []
    results.append(("Binance Alpha prompt", test_binance_alpha_prompt()))
    results.append(("低市值代币 prompt", test_low_market_cap_prompt()))
    results.append(("深度分析排除配置", test_excluded_event_types()))

    print("\n" + "=" * 80)
    print("测试总结")
    print("=" * 80)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {test_name}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败")
        return 1


if __name__ == '__main__':
    exit(main())
