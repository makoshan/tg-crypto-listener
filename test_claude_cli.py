#!/usr/bin/env python3
"""Test script to verify Claude CLI basic functionality."""

import asyncio
import json
import sys
from pathlib import Path


async def test_claude_cli_basic():
    """Test basic Claude CLI invocation and JSON output."""

    # Simple test prompt
    prompt = """请返回一个有效的 JSON 对象，表示对以下加密货币事件的分析：

事件：Binance 宣布上线 ABC 代币，明天开盘

要求：
1. 直接返回 JSON，不要包含 markdown 标记或解释文字
2. JSON 格式如下：
{
  "summary": "简体中文摘要",
  "event_type": "listing",
  "asset": "ABC",
  "action": "buy",
  "confidence": 0.75,
  "notes": "分析理由"
}

请立即返回 JSON，不要添加任何额外内容。"""

    print("=" * 80)
    print("🧪 Claude CLI 基础功能测试")
    print("=" * 80)
    print(f"\n📝 测试提示词长度: {len(prompt)} 字符\n")

    # Try to find claude CLI
    claude_paths = [
        "claude",  # In PATH
        str(Path.home() / ".local/bin/claude"),
        "/usr/local/bin/claude",
    ]

    claude_cli = None
    for path in claude_paths:
        try:
            proc = await asyncio.create_subprocess_exec(
                path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()
            if proc.returncode == 0:
                claude_cli = path
                print(f"✅ 找到 Claude CLI: {path}")
                break
        except FileNotFoundError:
            continue

    if not claude_cli:
        print("❌ 未找到 Claude CLI")
        print("\n请确保已安装 Claude CLI:")
        print("  npm install -g @anthropic-ai/claude-cli")
        print("或者:")
        print("  brew install claude-cli")
        return False

    # Test 1: Basic execution with JSON output
    print("\n" + "=" * 80)
    print("测试 1: 基本 JSON 输出")
    print("=" * 80)

    try:
        start_time = asyncio.get_event_loop().time()

        # Execute Claude CLI
        # Use --print for non-interactive output
        # Use --dangerously-skip-permissions to bypass permission checks (for testing)
        # IMPORTANT: Claude CLI requires prompt via stdin, not as argument
        process = await asyncio.create_subprocess_exec(
            claude_cli,
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Send prompt via stdin
        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=60.0  # 60 second timeout
        )

        elapsed = asyncio.get_event_loop().time() - start_time

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            print(f"❌ Claude CLI 失败 (exit={process.returncode})")
            print(f"stderr: {stderr_text[:500]}")
            return False

        output = stdout.decode("utf-8", errors="replace").strip()

        print(f"⏱️  执行耗时: {elapsed:.2f}s")
        print(f"📄 输出长度: {len(output)} 字符")
        print(f"\n原始输出:\n{'-'*80}")
        print(output[:500])
        if len(output) > 500:
            print(f"\n... (省略 {len(output) - 500} 字符)")
        print("-" * 80)

        # Try to parse JSON
        try:
            # Remove markdown code fences if present
            cleaned = output
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                    cleaned = "\n".join(lines[1:-1]).strip()

            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip(" :\n")

            data = json.loads(cleaned)

            print(f"\n✅ JSON 解析成功!")
            print(f"📊 解析结果:")
            for key, value in data.items():
                print(f"  - {key}: {value}")

            # Validate required fields
            required = ["summary", "event_type", "asset", "action", "confidence"]
            missing = [f for f in required if f not in data]

            if missing:
                print(f"\n⚠️  缺少必需字段: {missing}")
                return False

            print(f"\n✅ 所有必需字段都存在")
            print(f"✅ 测试 1 通过 ✨")
            return True

        except json.JSONDecodeError as e:
            print(f"\n❌ JSON 解析失败: {e}")
            print(f"位置: 行 {e.lineno}, 列 {e.colno}")
            return False

    except asyncio.TimeoutError:
        print("❌ Claude CLI 超时 (60s)")
        return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_claude_cli_with_tools():
    """Test Claude CLI with tool calling capabilities."""

    prompt = """分析以下加密货币事件，并使用搜索工具验证：

事件：Coinbase 宣布支持 XYZ 代币交易

任务：
1. 使用 bash 命令验证这个消息（例如 curl 搜索新闻）
2. 返回 JSON 分析结果

JSON 格式：
{
  "summary": "简体中文摘要",
  "event_type": "listing",
  "asset": "XYZ",
  "action": "observe",
  "confidence": 0.5,
  "notes": "包含你执行的验证步骤和结果"
}

注意：
- 可以执行 bash 命令来验证消息
- 在 notes 中记录你执行的命令和结果
- 直接返回 JSON，不要包含 markdown 或额外说明"""

    print("\n" + "=" * 80)
    print("测试 2: 工具调用能力（可选）")
    print("=" * 80)
    print("⚠️  此测试需要 Claude CLI 支持 --full-auto 模式")
    print("如果你的 Claude CLI 版本不支持，可以跳过此测试\n")

    # Try to find claude CLI
    claude_cli = "claude"

    try:
        start_time = asyncio.get_event_loop().time()

        # Execute Claude CLI with auto mode (if supported)
        # Use --dangerously-skip-permissions for full auto execution
        process = await asyncio.create_subprocess_exec(
            claude_cli,
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--allowedTools", "Bash,Read",  # Allow tool execution
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Send prompt via stdin
        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=90.0  # Longer timeout for tool execution
        )

        elapsed = asyncio.get_event_loop().time() - start_time

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            print(f"⚠️  Claude CLI 失败或不支持 --full-auto 模式")
            print(f"这是正常的，Claude CLI 可能不支持完全自动模式")
            return None  # Not a failure, just not supported

        output = stdout.decode("utf-8", errors="replace").strip()

        print(f"⏱️  执行耗时: {elapsed:.2f}s")
        print(f"📄 输出长度: {len(output)} 字符")

        try:
            # Clean and parse JSON
            cleaned = output
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if len(lines) >= 3:
                    cleaned = "\n".join(lines[1:-1]).strip()

            data = json.loads(cleaned)
            print(f"\n✅ 测试 2 通过: Claude CLI 支持工具调用")
            print(f"Notes 内容: {data.get('notes', '')[:200]}")
            return True

        except json.JSONDecodeError:
            print(f"⚠️  输出不是有效 JSON，可能需要调整参数")
            return None

    except Exception as e:
        print(f"ℹ️  测试 2 跳过: {e}")
        return None


async def test_claude_cli_price_query():
    """Test Claude CLI with price query tool for multiple assets."""

    prompt = """使用价格查询工具获取以下加密货币的当前价格：BTC, XAUT, ETH

任务：
1. 使用以下命令查询价格：
   python scripts/codex_tools/fetch_price.py --assets BTC XAUT ETH

2. 解析输出并返回分析结果

输出 JSON 格式：
{
  "summary": "简体中文摘要，包含各币种价格",
  "event_type": "market_data",
  "asset": "BTC",
  "action": "observe",
  "confidence": 0.9,
  "notes": "包含执行的命令、各币种的价格数据（BTC, XAUT, ETH 的价格）"
}

重要：
- 必须执行价格查询命令
- 在 notes 中记录所有三个币种的价格
- 在 summary 中简要总结价格情况
- 直接返回 JSON，不要额外说明"""

    print("\n" + "=" * 80)
    print("测试 3: 价格工具调用（多个币种）")
    print("=" * 80)
    print("📝 查询资产: BTC, XAUT, ETH\n")

    try:
        start_time = asyncio.get_event_loop().time()

        process = await asyncio.create_subprocess_exec(
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--allowedTools", "Bash",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        process.stdin.write(prompt.encode("utf-8"))
        process.stdin.close()

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=120.0
        )

        elapsed = asyncio.get_event_loop().time() - start_time

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            print(f"❌ Claude CLI 失败 (exit={process.returncode})")
            print(f"stderr: {stderr_text[:500]}")
            return False

        output = stdout.decode("utf-8", errors="replace").strip()

        print(f"⏱️  执行耗时: {elapsed:.2f}s")
        print(f"📄 输出长度: {len(output)} 字符")
        print(f"\n原始输出:\n{'-'*80}")
        print(output[:1000])
        if len(output) > 1000:
            print(f"\n... (省略 {len(output) - 1000} 字符)")
        print("-" * 80)

        # Parse JSON
        try:
            cleaned = output
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if len(lines) >= 3 and lines[-1].startswith("```"):
                    cleaned = "\n".join(lines[1:-1]).strip()

            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip(" :\n")

            data = json.loads(cleaned)

            print(f"\n✅ JSON 解析成功!")
            print(f"📊 解析结果:")
            print(f"  - summary: {data.get('summary', '')[:100]}")
            print(f"  - asset: {data.get('asset')}")
            print(f"  - action: {data.get('action')}")
            print(f"  - confidence: {data.get('confidence')}")

            notes = data.get("notes", "")
            print(f"\n📝 Notes 内容:")
            print(f"  {notes[:400]}")
            if len(notes) > 400:
                print(f"  ... (省略 {len(notes) - 400} 字符)")

            # Check if all three assets were queried
            has_btc = "BTC" in notes or "btc" in notes.lower()
            has_xaut = "XAUT" in notes or "xaut" in notes.lower()
            has_eth = "ETH" in notes or "eth" in notes.lower()

            print(f"\n🔍 检测到的币种:")
            print(f"  {'✅' if has_btc else '❌'} BTC")
            print(f"  {'✅' if has_xaut else '❌'} XAUT")
            print(f"  {'✅' if has_eth else '❌'} ETH")

            if has_btc and has_xaut and has_eth:
                print(f"\n✅ 成功查询所有三个币种的价格！")
                return True
            elif has_btc or has_xaut or has_eth:
                print(f"\n⚠️  部分币种查询成功")
                return True
            else:
                print(f"\n⚠️  未检测到价格查询结果")
                return False

        except json.JSONDecodeError as e:
            print(f"\n❌ JSON 解析失败: {e}")
            return False

    except asyncio.TimeoutError:
        print("❌ Claude CLI 超时 (120s)")
        return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n🚀 开始 Claude CLI 测试套件\n")

    # Test 1: Basic functionality (required)
    result1 = await test_claude_cli_basic()

    if not result1:
        print("\n" + "=" * 80)
        print("❌ 基础测试失败，请检查 Claude CLI 安装和配置")
        print("=" * 80)
        return 1

    # Test 2: Tool calling (optional)
    result2 = await test_claude_cli_with_tools()

    # Test 3: Price query tool (multi-asset)
    result3 = await test_claude_cli_price_query()

    print("\n" + "=" * 80)
    print("📊 测试总结")
    print("=" * 80)
    print(f"✅ 基础 JSON 输出: {'通过' if result1 else '失败'}")
    print(f"{'✅' if result2 else '⚠️ '} 工具调用能力: {'支持' if result2 else '不支持或未测试'}")
    print(f"{'✅' if result3 else '⚠️ '} 价格查询工具: {'支持' if result3 else '不支持或未测试'}")

    print("\n💡 建议:")
    if result1 and result2 and result3:
        print("  ✨ Claude CLI 完全可用，支持深度分析！")
        print("  ✨ 支持批量价格查询（BTC, XAUT, ETH 等）")
    elif result1 and result2:
        print("  - Claude CLI 支持工具调用")
        print("  - 价格查询需要进一步调试")
    elif result1:
        print("  - Claude CLI 可用于基础分析")
        print("  - 如需工具调用，考虑使用 Codex CLI 或检查 Claude CLI 版本")
    print("=" * 80)

    return 0 if result1 else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  测试被用户中断")
        sys.exit(130)
