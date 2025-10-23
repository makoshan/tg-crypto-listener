#!/usr/bin/env python3
"""Test Claude CLI tool calling capabilities with real tool scripts."""

import asyncio
import json
import sys


async def test_claude_cli_with_search_tool():
    """Test Claude CLI with actual search_news.py tool."""

    prompt = """你需要验证以下加密货币事件的真实性：

事件：Binance 宣布上线 SOL 代币

任务：
1. 使用以下命令搜索新闻验证这个消息：
   uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py --query "Binance SOL listing" --max-results 3

2. 根据搜索结果返回分析

输出 JSON 格式：
{
  "summary": "简体中文摘要（基于搜索结果）",
  "event_type": "listing",
  "asset": "SOL",
  "action": "buy|sell|observe",
  "confidence": 0.0,
  "notes": "包含搜索命令、结果摘要、是否找到官方确认"
}

重要：
- 必须执行搜索命令
- 在 notes 中记录搜索结果（找到多少条、是否官方确认等）
- 根据搜索结果调整 confidence
- 直接返回 JSON，不要额外说明"""

    print("=" * 80)
    print("🧪 Claude CLI 工具调用测试（搜索工具）")
    print("=" * 80)
    print(f"\n📝 测试提示词长度: {len(prompt)} 字符\n")

    try:
        start_time = asyncio.get_event_loop().time()

        # Execute Claude CLI with Bash tool enabled
        process = await asyncio.create_subprocess_exec(
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--allowedTools", "Bash",  # Enable Bash tool for executing commands
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=120.0  # Longer timeout for tool execution
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
        print(output[:800])
        if len(output) > 800:
            print(f"\n... (省略 {len(output) - 800} 字符)")
        print("-" * 80)

        # Try to parse JSON
        try:
            # Clean output
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
            for key, value in data.items():
                if key == "notes":
                    notes_preview = value[:150] + "..." if len(value) > 150 else value
                    print(f"  - {key}: {notes_preview}")
                else:
                    print(f"  - {key}: {value}")

            # Check if tool was actually executed
            notes = data.get("notes", "")
            if "搜索" in notes or "search" in notes.lower() or "uvx" in notes:
                print(f"\n✅ 检测到工具执行证据")
                print(f"✅ Claude CLI 支持工具调用！")
                return True
            else:
                print(f"\n⚠️  未检测到工具执行证据，可能只是基于知识回答")
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


async def test_claude_cli_simple_bash():
    """Test Claude CLI with simple bash command."""

    prompt = """执行以下任务并返回结果：

1. 使用 bash 命令获取当前日期：date +%Y-%m-%d
2. 返回 JSON 格式的结果

JSON 格式：
{
  "summary": "当前日期",
  "result": "执行命令得到的日期",
  "notes": "包含你执行的命令和输出"
}

直接返回 JSON，不要额外说明。"""

    print("\n" + "=" * 80)
    print("🧪 Claude CLI 简单 Bash 命令测试")
    print("=" * 80)

    try:
        start_time = asyncio.get_event_loop().time()

        process = await asyncio.create_subprocess_exec(
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--allowedTools", "Bash",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=60.0
        )

        elapsed = asyncio.get_event_loop().time() - start_time

        if process.returncode != 0:
            print(f"❌ 测试失败")
            return False

        output = stdout.decode("utf-8", errors="replace").strip()

        print(f"⏱️  执行耗时: {elapsed:.2f}s")
        print(f"📄 输出长度: {len(output)} 字符")
        print(f"\n原始输出:\n{'-'*80}")
        print(output)
        print("-" * 80)

        # Check if date command was executed
        if "date" in output.lower() or "20" in output:  # Year pattern
            print(f"\n✅ 检测到 Bash 命令执行!")
            return True
        else:
            print(f"\n⚠️  未检测到命令执行")
            return False

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False


async def main():
    """Run all tool tests."""
    print("\n🚀 开始 Claude CLI 工具调用测试套件\n")

    # Test 1: Simple bash command
    result1 = await test_claude_cli_simple_bash()

    # Test 2: Search tool (more complex)
    result2 = await test_claude_cli_with_search_tool()

    print("\n" + "=" * 80)
    print("📊 测试总结")
    print("=" * 80)
    print(f"{'✅' if result1 else '❌'} 简单 Bash 命令: {'通过' if result1 else '失败'}")
    print(f"{'✅' if result2 else '❌'} 搜索工具调用: {'通过' if result2 else '失败'}")

    if result1 and result2:
        print("\n💡 结论:")
        print("  ✨ Claude CLI 完全支持工具调用！")
        print("  ✨ 可以用于深度分析引擎！")
        print(f"\n  速度对比:")
        print(f"  - Claude CLI: ~8-15秒（预估）")
        print(f"  - Codex CLI: 12-16秒")
        print(f"  - Gemini: 5-10秒（预估）")
    elif result1:
        print("\n💡 结论:")
        print("  ⚠️  Claude CLI 支持简单 Bash 命令")
        print("  ⚠️  复杂工具调用可能需要调整")
    else:
        print("\n💡 结论:")
        print("  ❌ Claude CLI 可能不支持工具调用")
        print("  ℹ️  检查 --allowedTools 参数是否正确")

    print("=" * 80)

    return 0 if (result1 or result2) else 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  测试被用户中断")
        sys.exit(130)
