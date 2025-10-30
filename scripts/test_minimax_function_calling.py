#!/usr/bin/env python3
"""
测试 MiniMax OpenAI 兼容 API 的函数调用功能。

使用方法:
    # 使用环境变量中的配置
    uvx --with-requirements requirements.txt python scripts/test_minimax_function_calling.py
    
    # 或者手动设置环境变量
    MINIMAX_API_KEY=your_key MINIMAX_BASE_URL=https://api.minimax.io/v1 MINIMAX_MODEL=MiniMax-M2 \
        uvx --with-requirements requirements.txt python scripts/test_minimax_function_calling.py

环境变量:
    MINIMAX_API_KEY: MiniMax API 密钥（必需）
    MINIMAX_BASE_URL: OpenAI 兼容 API 端点（默认: https://api.minimax.io/v1）
    MINIMAX_MODEL: 模型名称（默认: MiniMax-M2）
    
    如果未设置 MINIMAX_API_KEY，会尝试使用 OPENAI_API_KEY

注意事项:
    - Base URL 必须指向 OpenAI 兼容端点，不是 Claude API 端点
    - 确保使用支持 Function Calling 的模型
"""

import json
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from openai import OpenAI

# 加载环境变量
load_dotenv()


def get_weather(city: str) -> dict:
    """模拟天气查询函数"""
    # 这里应该调用真实的天气 API，现在只是返回模拟数据
    weather_data = {
        "Beijing": {"temperature": "15°C", "condition": "晴天", "humidity": "45%"},
        "Shanghai": {"temperature": "18°C", "condition": "多云", "humidity": "60%"},
        "Guangzhou": {"temperature": "25°C", "condition": "小雨", "humidity": "75%"},
    }
    return weather_data.get(city, {"temperature": "未知", "condition": "未知", "humidity": "未知"})


def main():
    """主函数：测试 MiniMax 函数调用"""
    
    # 从环境变量获取配置
    api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")
    # 默认使用 OpenAI 兼容 API 端点
    base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
    model = os.getenv("MINIMAX_MODEL", "MiniMax-M2")
    
    if not api_key:
        print("❌ 错误: 未找到 MINIMAX_API_KEY 或 OPENAI_API_KEY")
        print("请在 .env 文件中设置 MINIMAX_API_KEY 或 OPENAI_API_KEY")
        sys.exit(1)
    
    # 检查 base_url 是否是 OpenAI 兼容端点
    if "/anthropic" in base_url or "/claude" in base_url:
        print("⚠️  警告: Base URL 似乎是 Claude API 端点，而不是 OpenAI 兼容端点")
        print(f"   当前 Base URL: {base_url}")
        print("   应该使用 OpenAI 兼容端点，例如: https://api.minimax.io/v1")
        print()
    
    print(f"🔧 配置信息:")
    print(f"  - Base URL: {base_url}")
    print(f"  - Model: {model}")
    print(f"  - API Key: {api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else '****'}")
    print()
    
    # 提示用户如何设置正确的配置
    if base_url != "https://api.minimax.io/v1":
        print("💡 提示: 如果遇到 404 错误，请检查:")
        print("   1. MINIMAX_BASE_URL 应该指向 OpenAI 兼容端点")
        print("   2. 确认 MiniMax 账户支持 OpenAI 兼容 API")
        print()
    
    # 初始化客户端
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    
    # 定义工具（函数）
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取指定城市的天气信息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "城市名称，例如：北京、上海、广州"
                        }
                    },
                    "required": ["city"]
                }
            }
        }
    ]
    
    # 测试消息
    messages = [
        {
            "role": "user",
            "content": "北京今天天气怎么样？"
        }
    ]
    
    print("📤 发送请求...")
    print(f"  用户消息: {messages[0]['content']}")
    print()
    
    try:
        # 调用 API
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
        )
        
        print("📥 收到响应:")
        print(f"  完整响应对象: {json.dumps(response.model_dump(), indent=2, ensure_ascii=False)}")
        print()
        
        # 检查是否有错误
        if hasattr(response, 'base_resp') and response.base_resp:
            if response.base_resp.get('status_code') != 0:
                error_msg = response.base_resp.get('status_msg', '未知错误')
                status_code = response.base_resp.get('status_code', 'N/A')
                print(f"❌ API 错误:")
                print(f"   状态码: {status_code}")
                print(f"   错误信息: {error_msg}")
                if status_code == 2049 or 'invalid api key' in error_msg.lower():
                    print()
                    print("💡 提示:")
                    print("   1. 请确认 MINIMAX_API_KEY 是否正确")
                    print("   2. MiniMax OpenAI 兼容 API 可能需要使用专门的 API Key")
                    print("   3. 确认你的 MiniMax 账户已开通 OpenAI 兼容 API 服务")
                sys.exit(1)
        
        if not response.choices or len(response.choices) == 0:
            print("❌ 响应中没有 choices")
            sys.exit(1)
        
        message = response.choices[0].message
        print(f"  消息内容: {message.content}")
        print(f"  角色: {message.role}")
        
        # 检查是否有工具调用
        if message.tool_calls:
            print(f"  工具调用数量: {len(message.tool_calls)}")
            print()
            
            for i, tool_call in enumerate(message.tool_calls, 1):
                print(f"  工具调用 #{i}:")
                print(f"    ID: {tool_call.id}")
                print(f"    类型: {tool_call.type}")
                print(f"    函数名: {tool_call.function.name}")
                print(f"    参数: {tool_call.function.arguments}")
                
                # 解析参数并执行函数
                try:
                    args = json.loads(tool_call.function.arguments)
                    city = args.get("city", "")
                    
                    print(f"    执行函数: get_weather(city='{city}')")
                    weather_result = get_weather(city)
                    print(f"    函数结果: {json.dumps(weather_result, ensure_ascii=False, indent=6)}")
                    
                    # 添加助手消息和工具结果到消息历史
                    messages.append({
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": tool_call.function.arguments,
                                }
                            }
                        ]
                    })
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(weather_result, ensure_ascii=False),
                    })
                    
                    # 再次调用 API 获取最终回复
                    print()
                    print("📤 发送工具结果，获取最终回复...")
                    final_response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=tools,
                    )
                    
                    final_message = final_response.choices[0].message
                    print(f"📥 最终回复: {final_message.content}")
                    
                except json.JSONDecodeError as e:
                    print(f"    ❌ 参数解析失败: {e}")
                    
        else:
            print("  ⚠️ 未检测到工具调用")
            print("  这可能意味着模型选择了直接回答，而不是调用函数")
            
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
