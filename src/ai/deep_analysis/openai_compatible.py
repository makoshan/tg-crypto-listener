"""OpenAI Compatible API deep analysis engine (Qwen, OpenAI, DeepSeek, etc.)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Optional

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from .base import DeepAnalysisEngine, DeepAnalysisError, build_deep_analysis_messages

if TYPE_CHECKING:
    from src.ai.signal_engine import EventPayload, SignalResult

logger = logging.getLogger(__name__)


class OpenAICompatibleEngine(DeepAnalysisEngine):
    """
    统一的 OpenAI 兼容 API 深度分析引擎

    支持的 Provider：
    - Qwen (千问): https://dashscope.aliyuncs.com/compatible-mode/v1
    - OpenAI: https://api.openai.com/v1
    - DeepSeek: https://api.deepseek.com/v1

    核心设计：
    - 与 Gemini 深度分析代码逻辑完全相同
    - 复用 Gemini 的提示词和工具定义（通过 build_deep_analysis_messages）
    - 仅 API 调用层不同
    - 本质都是 API 引擎，区别于 CLI Agent 的黑盒执行方式
    """

    def __init__(
        self,
        *,
        provider: str,  # "qwen" | "openai" | "deepseek" | "minimax"
        api_key: str,
        base_url: str,
        model: str,
        enable_search: bool = False,  # 千问特色：enable_search=True
        timeout: float = 30.0,
        max_function_turns: int = 6,
        parse_json_callback,
        memory_bundle=None,
        config=None,
    ) -> None:
        super().__init__(
            provider_name=provider,
            parse_json_callback=parse_json_callback,
        )

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self.model = model
        self.provider = provider
        self.enable_search = enable_search
        self.timeout = timeout
        self.max_function_turns = max(1, int(max_function_turns))

        self._memory = memory_bundle
        self._config = config

        # Tool initialization (same as Gemini)
        self._search_tool = None
        self._price_tool = None
        self._macro_tool = None
        self._onchain_tool = None
        self._protocol_tool = None

        # Initialize tools if enabled
        if config:
            self._init_tools(config)

        logger.info(
            f"✅ {provider.upper()} 深度分析引擎已初始化: "
            f"model={model}, enable_search={enable_search}, "
            f"max_turns={self.max_function_turns}"
        )

    def _init_tools(self, config):
        """Initialize tools (same logic as Gemini)."""

        # Search tool
        if getattr(config, "TOOL_SEARCH_ENABLED", False):
            try:
                from src.ai.tools import SearchTool
                self._search_tool = SearchTool(config)
                logger.info("🔍 搜索工具已初始化")
            except Exception as exc:
                logger.warning(f"⚠️ 搜索工具初始化失败: {exc}")

        # Price tool
        if getattr(config, "TOOL_PRICE_ENABLED", False):
            try:
                from src.ai.tools import PriceTool
                self._price_tool = PriceTool(config)
                logger.info("💰 价格工具已初始化")
            except Exception as exc:
                logger.warning(f"⚠️ 价格工具初始化失败: {exc}")

        # Macro tool
        if getattr(config, "TOOL_MACRO_ENABLED", False):
            try:
                from src.ai.tools import MacroTool
                self._macro_tool = MacroTool(config)
                logger.info("🌐 宏观工具已初始化")
            except Exception as exc:
                logger.warning(f"⚠️ 宏观工具初始化失败: {exc}")

        # Onchain tool
        if getattr(config, "TOOL_ONCHAIN_ENABLED", False):
            try:
                from src.ai.tools import OnchainTool
                self._onchain_tool = OnchainTool(config)
                logger.info("⛓️ 链上工具已初始化")
            except Exception as exc:
                logger.warning(f"⚠️ 链上工具初始化失败: {exc}")

        # Protocol tool
        if getattr(config, "TOOL_PROTOCOL_ENABLED", False):
            try:
                from src.ai.tools import ProtocolTool
                self._protocol_tool = ProtocolTool(config)
                logger.info("🏛️ 协议工具已初始化")
            except Exception as exc:
                logger.warning(f"⚠️ 协议工具初始化失败: {exc}")

    async def analyse(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        """
        执行深度分析（与 Gemini 逻辑相同）

        流程：
        1. 构建分析上下文和工具定义（复用 build_deep_analysis_messages）
        2. Tool Calling Loop: 自主决策和调用工具
        3. 解析最终 JSON 信号
        """

        capabilities = {
            "provider": self.provider,
            "tool_enabled": self._has_tools(),
            "search_enabled": bool(self._search_tool),
            "price_enabled": bool(self._price_tool),
            "macro_enabled": bool(self._macro_tool),
            "onchain_enabled": bool(self._onchain_tool),
            "protocol_enabled": bool(self._protocol_tool),
            "notes": "OpenAI 兼容引擎，可使用 Function Calling 工具" if self._has_tools() else "OpenAI 兼容引擎，当前未启用任何工具",
        }

        # 1. 构建分析消息（复用 Gemini 的提示词逻辑）
        messages = build_deep_analysis_messages(
            payload=payload,
            preliminary=preliminary,
            history_limit=2,
            additional_context={
                "analysis_capabilities": capabilities,
            },
        )

        # 2. 构建工具定义（如果工具已启用）
        tools = self._build_tools() if self._has_tools() else None

        # 3. Function Calling Loop
        turn = 0
        planning_complete = False

        while turn < self.max_function_turns and not planning_complete:
            turn += 1
            logger.debug(f"🔄 {self.provider.upper()} 深度分析 - 回合 {turn}/{self.max_function_turns}")

            try:
                # 构建请求参数
                request_kwargs = {
                    "model": self.model,
                    "messages": messages,
                }

                # 千问特色：enable_search
                if self.provider == "qwen" and self.enable_search:
                    request_kwargs["extra_body"] = {"enable_search": True}

                # 如果有工具，添加工具定义
                if tools:
                    request_kwargs["tools"] = tools

                # 调用 API
                response = await self.client.chat.completions.create(**request_kwargs)

                message = response.choices[0].message

                # 检查是否有工具调用
                if message.tool_calls:
                    logger.debug(f"🔧 工具调用数量: {len(message.tool_calls)}")

                    # 将 AI 消息添加到历史
                    messages.append({
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                }
                            }
                            for tc in message.tool_calls
                        ]
                    })

                    # 执行工具调用
                    for tool_call in message.tool_calls:
                        tool_name = tool_call.function.name
                        tool_args_str = tool_call.function.arguments

                        try:
                            tool_args = json.loads(tool_args_str)
                        except json.JSONDecodeError:
                            tool_args = {}

                        logger.debug(f"  - {tool_name}({json.dumps(tool_args, ensure_ascii=False)})")

                        # 执行工具
                        tool_result = await self._execute_tool(tool_name, tool_args)

                        # 添加工具结果到消息历史
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        })
                else:
                    # 没有工具调用，完成规划
                    planning_complete = True
                    final_content = message.content or ""

                    logger.debug(f"✅ {self.provider.upper()} 深度分析完成，总回合数: {turn}")

                    # 解析 JSON 信号
                    return self._parse_json(final_content)

            except Exception as exc:
                logger.error(f"❌ {self.provider.upper()} API 调用失败 (回合 {turn}): {exc}")
                if turn >= self.max_function_turns:
                    raise DeepAnalysisError(f"{self.provider.upper()} 深度分析失败: {exc}") from exc
                # 继续下一回合

        # 超过最大回合数
        raise DeepAnalysisError(
            f"{self.provider.upper()} 深度分析超过最大回合数 {self.max_function_turns}"
        )

    def _has_tools(self) -> bool:
        """检查是否有启用的工具"""
        return any([
            self._search_tool,
            self._price_tool,
            self._macro_tool,
            self._onchain_tool,
            self._protocol_tool,
        ])

    def _build_tools(self) -> list[dict]:
        """
        构建工具定义（OpenAI Function Calling 格式）

        复用 Gemini 的工具逻辑，但使用 OpenAI 的工具格式
        """
        tools = []

        if self._search_tool:
            tools.append({
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
                                "description": "最大结果数（默认 5）",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                },
            })

        if self._price_tool:
            tools.append({
                "type": "function",
                "function": {
                    "name": "get_price",
                    "description": "获取加密货币实时价格和市场数据",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "asset": {
                                "type": "string",
                                "description": "资产代码（如 BTC, ETH, SOL）",
                            },
                        },
                        "required": ["asset"],
                    },
                },
            })

        if self._macro_tool:
            tools.append({
                "type": "function",
                "function": {
                    "name": "get_macro_data",
                    "description": "获取宏观经济指标（如利率、通胀、DXY 等）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "indicators": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "指标列表（如 ['DFF', 'DXY', 'CPIAUCSL']）",
                            },
                        },
                        "required": ["indicators"],
                    },
                },
            })

        if self._onchain_tool:
            tools.append({
                "type": "function",
                "function": {
                    "name": "get_onchain_data",
                    "description": "获取链上数据（如持仓、交易量、TVL 等）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "asset": {
                                "type": "string",
                                "description": "资产代码",
                            },
                            "metrics": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "指标列表（如 ['tvl', 'volume', 'holders']）",
                            },
                        },
                        "required": ["asset"],
                    },
                },
            })

        if self._protocol_tool:
            tools.append({
                "type": "function",
                "function": {
                    "name": "get_protocol_data",
                    "description": "获取 DeFi 协议数据（如 TVL、用户数、收益率）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "protocol": {
                                "type": "string",
                                "description": "协议名称（如 uniswap, aave, lido）",
                            },
                        },
                        "required": ["protocol"],
                    },
                },
            })

        return tools

    async def _execute_tool(self, tool_name: str, tool_args: dict) -> dict:
        """
        执行工具调用（与 Gemini 逻辑相同）

        Returns:
            工具执行结果（dict 格式）
        """
        try:
            if tool_name == "search_news" and self._search_tool:
                query = tool_args.get("query", "")
                max_results = tool_args.get("max_results", 5)

                result = await self._search_tool.fetch(
                    keyword=query,
                    max_results=max_results,
                )

                return {
                    "success": result.success,
                    "tool": "search_news",
                    "query": query,
                    "data": result.data if result.success else [],
                    "confidence": result.confidence,
                    "error": result.error if not result.success else None,
                }

            elif tool_name == "get_price" and self._price_tool:
                asset = tool_args.get("asset", "")

                result = await self._price_tool.snapshot(asset=asset)

                return {
                    "success": result.success,
                    "tool": "get_price",
                    "asset": asset,
                    "data": result.data if result.success else {},
                    "error": result.error if not result.success else None,
                }

            elif tool_name == "get_macro_data" and self._macro_tool:
                # MacroTool.snapshot() takes a single indicator, not a list
                indicators = tool_args.get("indicators", [])

                # If multiple indicators requested, fetch first one only
                # TODO: Consider supporting multiple indicators in the future
                indicator = indicators[0] if indicators else ""

                result = await self._macro_tool.snapshot(indicator=indicator)

                return {
                    "success": result.success,
                    "tool": "get_macro_data",
                    "indicator": indicator,
                    "data": result.data if result.success else {},
                    "error": result.error if not result.success else None,
                }

            elif tool_name == "get_onchain_data" and self._onchain_tool:
                asset = tool_args.get("asset", "")
                # Note: OnchainTool.snapshot() doesn't take metrics parameter
                # metrics = tool_args.get("metrics", [])

                result = await self._onchain_tool.snapshot(asset=asset)

                return {
                    "success": result.success,
                    "tool": "get_onchain_data",
                    "asset": asset,
                    "data": result.data if result.success else {},
                    "error": result.error if not result.success else None,
                }

            elif tool_name == "get_protocol_data" and self._protocol_tool:
                protocol = tool_args.get("protocol", "")

                result = await self._protocol_tool.snapshot(slug=protocol)

                return {
                    "success": result.success,
                    "tool": "get_protocol_data",
                    "protocol": protocol,
                    "data": result.data if result.success else {},
                    "error": result.error if not result.success else None,
                }

            else:
                logger.warning(f"⚠️ 未知工具或工具未启用: {tool_name}")
                return {
                    "success": False,
                    "error": f"Unknown tool or tool not enabled: {tool_name}",
                }

        except Exception as exc:
            logger.error(f"❌ 工具执行失败 ({tool_name}): {exc}")
            return {
                "success": False,
                "error": str(exc),
            }
