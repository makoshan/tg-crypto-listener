#!/usr/bin/env python3
"""
千问 (Qwen) OpenAI Compatible API 测试脚本

测试内容：
1. 基础 JSON 输出能力
2. Function Calling 工具调用
3. 内置联网搜索功能 (enable_search)
4. 批量价格查询模拟
5. 延迟和稳定性测试

运行方式：
    python3 test_qwen_api.py

环境变量要求：
    DASHSCOPE_API_KEY=sk-xxx
"""

import asyncio
import json
import os
import time
from typing import Optional, Dict, Any

from openai import AsyncOpenAI


class QwenTester:
    """千问 API 测试类"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-plus",
    ):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("DASHSCOPE_API_KEY is required")

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url,
        )
        self.model = model

    async def test_basic_json_output(self) -> Dict[str, Any]:
        """测试 1: 基础 JSON 输出"""
        print("\n" + "=" * 60)
        print("测试 1: 基础 JSON 输出")
        print("=" * 60)

        prompt = """你是加密交易信号分析专家。请分析以下事件并输出 JSON 格式的交易信号。

事件: Binance 宣布上线 ABC 代币，明天开盘

输出格式（必须是有效 JSON，不要包含 markdown 标记）:
{
  "summary": "简体中文摘要",
  "event_type": "listing",
  "asset": "ABC",
  "action": "buy",
  "confidence": 0.75,
  "notes": "分析理由"
}

请直接输出 JSON，不要包含其他文本。"""

        start = time.time()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = time.time() - start

        result_text = response.choices[0].message.content
        print(f"原始响应:\n{result_text}")
        print(f"\n耗时: {elapsed:.2f}s")

        # 解析 JSON
        try:
            # 移除可能的 markdown 代码块
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            result_json = json.loads(result_text)
            print(f"解析后 JSON:\n{json.dumps(result_json, ensure_ascii=False, indent=2)}")

            # 验证必需字段
            required_fields = ["summary", "event_type", "asset", "action", "confidence"]
            missing = [f for f in required_fields if f not in result_json]
            if missing:
                print(f"❌ 缺少字段: {missing}")
                return {"success": False, "error": f"Missing fields: {missing}"}

            print("✅ 所有必需字段齐全")
            return {
                "success": True,
                "elapsed": elapsed,
                "result": result_json,
            }

        except json.JSONDecodeError as e:
            print(f"❌ JSON 解析失败: {e}")
            return {"success": False, "error": str(e)}

    async def test_function_calling(self) -> Dict[str, Any]:
        """测试 2: Function Calling 工具调用"""
        print("\n" + "=" * 60)
        print("测试 2: Function Calling 工具调用")
        print("=" * 60)

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_news",
                    "description": "搜索加密货币相关新闻，验证消息真实性",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "最大结果数",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_price",
                    "description": "获取加密货币实时价格",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "asset": {
                                "type": "string",
                                "description": "资产代码，如 BTC, ETH",
                            },
                        },
                        "required": ["asset"],
                    },
                },
            },
        ]

        messages = [
            {
                "role": "user",
                "content": """请验证这条消息的真实性：Binance 宣布上线 XYZ 代币。

请使用 search_news 工具搜索相关新闻，然后使用 get_price 工具查询 XYZ 价格。

最后输出 JSON 格式的分析结果:
{
  "summary": "验证结果摘要",
  "event_type": "listing",
  "asset": "XYZ",
  "action": "buy/sell/observe",
  "confidence": 0.0-1.0,
  "notes": "详细分析，包含调用的工具和获取的数据"
}""",
            }
        ]

        start = time.time()
        turn = 0
        max_turns = 6

        while turn < max_turns:
            turn += 1
            print(f"\n--- 回合 {turn} ---")

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
            )

            message = response.choices[0].message
            messages.append(message)

            # 检查是否有工具调用
            if message.tool_calls:
                print(f"工具调用数量: {len(message.tool_calls)}")
                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)
                    print(f"  - {func_name}({json.dumps(func_args, ensure_ascii=False)})")

                    # 模拟工具执行
                    if func_name == "search_news":
                        tool_result = json.dumps(
                            {
                                "success": True,
                                "results": [
                                    {
                                        "title": "Binance 官方公告：上线 XYZ 交易对",
                                        "url": "https://binance.com/announcements/xyz",
                                        "source": "Binance Official",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                    elif func_name == "get_price":
                        tool_result = json.dumps(
                            {
                                "success": True,
                                "asset": func_args["asset"],
                                "price": 1.23,
                                "change_24h": 15.6,
                            },
                            ensure_ascii=False,
                        )
                    else:
                        tool_result = json.dumps({"success": False, "error": "Unknown tool"})

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        }
                    )
            else:
                # 没有工具调用，输出最终结果
                elapsed = time.time() - start
                final_content = message.content
                print(f"\n最终响应:\n{final_content}")
                print(f"\n总耗时: {elapsed:.2f}s")
                print(f"总回合数: {turn}")

                # 尝试解析 JSON
                try:
                    if "```json" in final_content:
                        final_content = final_content.split("```json")[1].split("```")[0].strip()
                    elif "```" in final_content:
                        final_content = final_content.split("```")[1].split("```")[0].strip()

                    result_json = json.loads(final_content)
                    print(f"解析后 JSON:\n{json.dumps(result_json, ensure_ascii=False, indent=2)}")
                    print("✅ Function Calling 测试成功")
                    return {
                        "success": True,
                        "elapsed": elapsed,
                        "turns": turn,
                        "result": result_json,
                    }
                except json.JSONDecodeError as e:
                    print(f"❌ JSON 解析失败: {e}")
                    return {"success": False, "error": str(e)}

        print(f"❌ 超过最大回合数 {max_turns}")
        return {"success": False, "error": "Max turns exceeded"}

    async def test_enable_search(self) -> Dict[str, Any]:
        """测试 3: 内置联网搜索功能 (千问特色)"""
        print("\n" + "=" * 60)
        print("测试 3: 内置联网搜索功能 (enable_search)")
        print("=" * 60)

        prompt = """请搜索并验证：Binance 是否在 2024 年 10 月宣布上线 PENGU 代币？

请使用千问的内置搜索功能（enable_search=True）查找相关新闻和公告。

最后输出 JSON 格式的验证结果:
{
  "summary": "验证结果摘要",
  "event_type": "listing",
  "asset": "PENGU",
  "action": "buy/sell/observe",
  "confidence": 0.0-1.0,
  "notes": "详细分析，包含搜索到的证据来源"
}"""

        start = time.time()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"enable_search": True},  # 千问特色：启用内置搜索
        )
        elapsed = time.time() - start

        result_text = response.choices[0].message.content
        print(f"原始响应:\n{result_text}")
        print(f"\n耗时: {elapsed:.2f}s")

        # 尝试解析 JSON
        try:
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            result_json = json.loads(result_text)
            print(f"解析后 JSON:\n{json.dumps(result_json, ensure_ascii=False, indent=2)}")
            print("✅ enable_search 测试成功")
            return {
                "success": True,
                "elapsed": elapsed,
                "result": result_json,
            }
        except json.JSONDecodeError as e:
            print(f"⚠️  JSON 解析失败，但搜索功能可能已工作: {e}")
            return {
                "success": False,
                "error": str(e),
                "note": "enable_search 可能已执行，但输出格式需调整",
            }

    async def test_batch_price_query(self) -> Dict[str, Any]:
        """测试 4: 批量价格查询"""
        print("\n" + "=" * 60)
        print("测试 4: 批量价格查询")
        print("=" * 60)

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_batch_prices",
                    "description": "批量获取多个加密货币的实时价格",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "assets": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "资产代码列表，如 ['BTC', 'ETH', 'SOL']",
                            },
                        },
                        "required": ["assets"],
                    },
                },
            },
        ]

        messages = [
            {
                "role": "user",
                "content": """请查询 BTC, ETH, SOL 三个币种的当前价格。

使用 get_batch_prices 工具批量查询，然后输出 JSON 格式的结果:
{
  "summary": "价格查询结果摘要",
  "action": "observe",
  "confidence": 0.9,
  "notes": "包含实际查询到的价格数据"
}""",
            }
        ]

        start = time.time()
        turn = 0
        max_turns = 3

        while turn < max_turns:
            turn += 1
            print(f"\n--- 回合 {turn} ---")

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
            )

            message = response.choices[0].message
            messages.append(message)

            if message.tool_calls:
                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)
                    print(f"  - {func_name}({json.dumps(func_args, ensure_ascii=False)})")

                    # 模拟批量价格查询
                    if func_name == "get_batch_prices":
                        assets = func_args["assets"]
                        tool_result = json.dumps(
                            {
                                "success": True,
                                "prices": {
                                    "BTC": {"price": 98765.43, "change_24h": 2.3},
                                    "ETH": {"price": 3456.78, "change_24h": 1.8},
                                    "SOL": {"price": 234.56, "change_24h": -0.5},
                                },
                            },
                            ensure_ascii=False,
                        )
                    else:
                        tool_result = json.dumps({"success": False, "error": "Unknown tool"})

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_result,
                        }
                    )
            else:
                elapsed = time.time() - start
                final_content = message.content
                print(f"\n最终响应:\n{final_content}")
                print(f"\n总耗时: {elapsed:.2f}s")
                print(f"总回合数: {turn}")

                try:
                    if "```json" in final_content:
                        final_content = final_content.split("```json")[1].split("```")[0].strip()
                    elif "```" in final_content:
                        final_content = final_content.split("```")[1].split("```")[0].strip()

                    result_json = json.loads(final_content)
                    print(f"解析后 JSON:\n{json.dumps(result_json, ensure_ascii=False, indent=2)}")

                    # 检查是否包含所有查询的币种
                    detected_assets = []
                    notes = result_json.get("notes", "")
                    for asset in ["BTC", "ETH", "SOL"]:
                        if asset in notes:
                            detected_assets.append(asset)
                            print(f"✅ 检测到币种: {asset}")

                    if len(detected_assets) == 3:
                        print("✅ 批量价格查询测试成功")
                        return {
                            "success": True,
                            "elapsed": elapsed,
                            "turns": turn,
                            "result": result_json,
                        }
                    else:
                        print(f"⚠️  只检测到 {len(detected_assets)}/3 个币种")
                        return {
                            "success": False,
                            "error": f"Only detected {len(detected_assets)}/3 assets",
                        }
                except json.JSONDecodeError as e:
                    print(f"❌ JSON 解析失败: {e}")
                    return {"success": False, "error": str(e)}

        print(f"❌ 超过最大回合数 {max_turns}")
        return {"success": False, "error": "Max turns exceeded"}

    async def run_all_tests(self):
        """运行所有测试"""
        print(f"\n{'=' * 60}")
        print(f"千问 (Qwen) API 测试套件")
        print(f"模型: {self.model}")
        print(f"Base URL: {self.client.base_url}")
        print(f"{'=' * 60}")

        results = {}

        # 测试 1: 基础 JSON 输出
        results["basic_json"] = await self.test_basic_json_output()

        # 测试 2: Function Calling
        results["function_calling"] = await self.test_function_calling()

        # 测试 3: enable_search (千问特色)
        results["enable_search"] = await self.test_enable_search()

        # 测试 4: 批量价格查询
        results["batch_price"] = await self.test_batch_price_query()

        # 汇总结果
        print("\n" + "=" * 60)
        print("测试结果汇总")
        print("=" * 60)

        for test_name, result in results.items():
            status = "✅ PASS" if result.get("success") else "❌ FAIL"
            elapsed = result.get("elapsed", 0)
            print(f"{test_name:20s} {status:10s} {elapsed:6.2f}s")

        success_count = sum(1 for r in results.values() if r.get("success"))
        total_count = len(results)
        print(f"\n总计: {success_count}/{total_count} 通过")

        return results


async def main():
    """主函数"""
    try:
        tester = QwenTester(
            model="qwen-plus",  # 或 qwen-max, qwen-turbo
        )
        results = await tester.run_all_tests()

        # 返回退出码
        success_count = sum(1 for r in results.values() if r.get("success"))
        exit(0 if success_count == len(results) else 1)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())
