"""Gemini deep analysis engine implementation with Function Calling support."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence, TypedDict

from src.ai.gemini_function_client import (
    GeminiFunctionCallingClient,
    GeminiFunctionResponse,
    FunctionCall,
    AiServiceError,
)
from src.memory.factory import MemoryBackendBundle
from src.memory.types import MemoryContext, MemoryEntry

from .base import DeepAnalysisEngine, DeepAnalysisError, build_deep_analysis_messages

logger = logging.getLogger(__name__)


# LangGraph State Definition
class DeepAnalysisState(TypedDict, total=False):
    """State object for tool-enhanced deep analysis LangGraph."""

    # Input
    payload: "EventPayload"
    preliminary: "SignalResult"

    # Evidence slots
    search_evidence: Optional[dict]
    memory_evidence: Optional[dict]

    # Control flow
    next_tools: list[str]
    search_keywords: str  # AI-generated search keywords
    tool_call_count: int
    max_tool_calls: int

    # Output
    final_response: str


class GeminiDeepAnalysisEngine(DeepAnalysisEngine):
    """Execute deep analysis via Gemini 2.5 Pro Function Calling."""

    def __init__(
        self,
        *,
        client: GeminiFunctionCallingClient,
        memory_bundle: MemoryBackendBundle | None,
        parse_json_callback,
        max_function_turns: int,
        memory_limit: int,
        memory_min_confidence: float,
        config=None,
    ) -> None:
        super().__init__(provider_name="gemini", parse_json_callback=parse_json_callback)
        self._client = client
        self._memory = memory_bundle
        self._max_function_turns = max(1, int(max_function_turns))
        self._memory_limit = max(1, int(memory_limit))
        self._memory_min_confidence = float(memory_min_confidence)
        self._tools = self._build_tools()

        # Store config for tool-enhanced flow
        self._config = config or SimpleNamespace()
        self._search_tool = None

        # Daily quota tracking for cost control
        self._tool_call_daily_limit = getattr(config, "DEEP_ANALYSIS_TOOL_DAILY_LIMIT", 50)
        self._tool_call_count_today = 0
        self._tool_call_reset_date = datetime.now(timezone.utc).date()

        # Initialize search tool if enabled
        tool_search_enabled = getattr(config, "TOOL_SEARCH_ENABLED", False) if config else False
        logger.debug("GeminiDeepAnalysisEngine 初始化: config=%s, TOOL_SEARCH_ENABLED=%s", type(config).__name__ if config else None, tool_search_enabled)

        if config and tool_search_enabled:
            try:
                from src.ai.tools import SearchTool

                self._search_tool = SearchTool(config)
                provider = getattr(config, "DEEP_ANALYSIS_SEARCH_PROVIDER", "tavily")
                logger.info("🔍 搜索工具已初始化，Provider=%s", provider)
            except ValueError as exc:
                logger.warning("⚠️ 搜索工具初始化失败: %s", exc)
                self._search_tool = None
            except Exception as exc:
                logger.warning("⚠️ 搜索工具初始化异常: %s", exc)
                self._search_tool = None
        else:
            logger.debug("搜索工具未初始化: config存在=%s, TOOL_SEARCH_ENABLED=%s", config is not None, tool_search_enabled)

    async def analyse(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        """Execute deep analysis with optional tool-enhanced flow."""

        # Check if tool-enhanced flow is enabled
        tools_enabled = getattr(self._config, "DEEP_ANALYSIS_TOOLS_ENABLED", False)

        if not tools_enabled:
            # Fallback to traditional Function Calling flow
            logger.debug("工具增强流程未启用，使用传统 Function Calling 流程")
            return await self._analyse_with_function_calling(payload, preliminary)

        # Tool-enhanced flow with LangGraph
        max_calls = getattr(self._config, "DEEP_ANALYSIS_MAX_TOOL_CALLS", 3)
        logger.info(
            "🔧 工具增强流程启用 (max_calls=%s, timeout=%ss, daily_limit=%s)",
            max_calls,
            getattr(self._config, "DEEP_ANALYSIS_TOOL_TIMEOUT", "n/a"),
            self._tool_call_daily_limit,
        )

        try:
            logger.info("=== 启动 LangGraph 工具增强深度分析 ===")
            from .graph import build_deep_graph

            graph = build_deep_graph(self)

            initial_state = DeepAnalysisState(
                payload=payload,
                preliminary=preliminary,
                search_evidence=None,
                memory_evidence=None,
                next_tools=[],
                search_keywords="",
                tool_call_count=0,
                max_tool_calls=max_calls,
                final_response="",
            )

            final_state = await graph.ainvoke(initial_state)
            final_payload = final_state.get("final_response")

            if not final_payload:
                raise DeepAnalysisError("LangGraph 未返回最终结果")

            result = self._parse_json(final_payload)
            logger.info("=== LangGraph 深度分析完成 ===")
            return result

        except Exception as exc:
            logger.error(
                "LangGraph 工具编排失败，降级到传统流程: %s",
                exc,
                exc_info=True,
            )
            return await self._analyse_with_function_calling(payload, preliminary)

    async def _analyse_with_function_calling(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        """Traditional Function Calling implementation (backward compatible)."""
        conversation = build_deep_analysis_messages(payload, preliminary)
        try:
            response = await self._run_tool_loop(conversation, payload, preliminary)
        except AiServiceError as exc:
            logger.warning("Gemini Function Calling 失败: %s", exc)
            raise DeepAnalysisError(str(exc)) from exc

        if not response or not response.text:
            raise DeepAnalysisError("Gemini 返回空响应")

        return self._parse_json(response.text)

    async def _run_tool_loop(
        self,
        messages: list[dict[str, str]],
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> GeminiFunctionResponse:
        conversation = list(messages)

        for turn in range(self._max_function_turns):
            logger.debug("Gemini Function Calling 回合 %s/%s", turn + 1, self._max_function_turns)
            response = await self._client.generate_content_with_tools(
                conversation,
                tools=self._tools,
            )

            if response.function_calls:
                tool_tasks = [
                    self._dispatch_tool(call, payload, preliminary)
                    for call in response.function_calls
                ]
                tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)
                for call, result in zip(response.function_calls, tool_results):
                    if isinstance(result, Exception):
                        logger.warning("Function %s 执行失败: %s", call.name, result)
                        payload_json = json.dumps(
                            {"success": False, "error": str(result)},
                            ensure_ascii=False,
                        )
                    else:
                        payload_json = json.dumps(result, ensure_ascii=False)
                    conversation.append(
                        {
                            "role": "tool",
                            "name": call.name,
                            "content": payload_json,
                        }
                    )
                continue

            return response

        logger.warning("达到 Function Calling 回合上限，返回最后一次响应")
        return response

    async def _dispatch_tool(
        self,
        call: FunctionCall,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> dict[str, Any]:
        name = call.name.strip().lower()
        if name == "fetch_memories":
            return await self._tool_fetch_memories(call, payload, preliminary)

        return {
            "success": False,
            "error": f"未知函数: {call.name}",
        }

    async def _tool_fetch_memories(
        self,
        call: FunctionCall,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> dict[str, Any]:
        if not self._memory or not self._memory.enabled:
            return {"success": False, "error": "memory_disabled"}

        args = call.args or {}
        keywords = _normalise_keywords(args.get("keywords")) or list(payload.keywords_hit or [])
        limit = int(args.get("limit", self._memory_limit))
        limit = max(1, min(limit, self._memory_limit))

        asset_field = args.get("asset_codes") or preliminary.asset
        asset_codes = _normalise_asset_codes(asset_field)

        repo = self._memory.repository
        if repo is None:
            return {"success": False, "error": "memory_repository_unavailable"}

        entries: Sequence[MemoryEntry] | MemoryContext | None = None

        if hasattr(repo, "fetch_memories") and inspect.iscoroutinefunction(repo.fetch_memories):
            kwargs = {"embedding": None, "asset_codes": asset_codes}
            parameters = inspect.signature(repo.fetch_memories).parameters
            if "keywords" in parameters:
                kwargs["keywords"] = keywords
            try:
                context = await repo.fetch_memories(**kwargs)  # type: ignore[arg-type]
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Supabase 记忆检索失败: %s", exc)
                return {"success": False, "error": str(exc)}
            if isinstance(context, MemoryContext):
                entries = context.entries
            else:
                entries = []
        elif hasattr(repo, "fetch_memories"):
            kwargs = {"embedding": None, "asset_codes": asset_codes}
            parameters = inspect.signature(repo.fetch_memories).parameters
            if "keywords" in parameters:
                kwargs["keywords"] = keywords
            try:
                context = repo.fetch_memories(**kwargs)  # type: ignore[arg-type]
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("记忆检索失败: %s", exc)
                return {"success": False, "error": str(exc)}
            if asyncio.iscoroutine(context):
                context = await context
            if isinstance(context, MemoryContext):
                entries = context.entries
            elif isinstance(context, Iterable):
                entries = list(context)
        elif hasattr(repo, "load_entries"):
            try:
                entries = repo.load_entries(  # type: ignore[attr-defined]
                    keywords=keywords,
                    limit=limit,
                    min_confidence=self._memory_min_confidence,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("本地记忆检索失败: %s", exc)
                return {"success": False, "error": str(exc)}
        else:
            logger.warning("未知的记忆仓储类型: %s", type(repo).__name__)
            return {"success": False, "error": "unsupported_memory_repository"}

        prompt_entries = _memory_entries_to_prompt(entries)[:limit] if entries else []
        return {
            "success": True,
            "entries": prompt_entries,
        }

    def _build_tools(self) -> list[Any]:
        declaration = {
            "name": "fetch_memories",
            "description": "根据资产或关键词检索历史行情记忆，用于辅助深度分析。",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "asset_codes": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "相关的资产代码列表（如 BTC、ETH）。",
                    },
                    "keywords": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                        "description": "用于匹配历史模式的关键词。",
                    },
                    "limit": {
                        "type": "INTEGER",
                        "description": "最大返回记忆数量，默认使用配置值。",
                    },
                },
            },
        }

        try:
            from google.genai.types import FunctionDeclaration, Tool  # type: ignore
        except ImportError:  # pragma: no cover - optional dependency
            return [{"function_declarations": [declaration]}]

        return [
            Tool(
                function_declarations=[
                    FunctionDeclaration(**declaration),
                ]
            )
        ]


def _normalise_keywords(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        return [token.strip() for token in raw_value.split(",") if token.strip()]
    if isinstance(raw_value, Iterable):
        return [str(token).strip() for token in raw_value if str(token).strip()]
    return []


def _normalise_asset_codes(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        tokens = [token.strip().upper() for token in raw_value.split(",") if token.strip()]
    elif isinstance(raw_value, Iterable):
        tokens = [str(token).strip().upper() for token in raw_value if str(token).strip()]
    else:
        tokens = []
    return [token for token in tokens if token]


def _memory_entries_to_prompt(entries: Sequence[MemoryEntry] | Iterable[MemoryEntry] | None) -> list[dict[str, Any]]:
    if not entries:
        return []
    payload: list[dict[str, Any]] = []
    for entry in entries:
        try:
            payload.append(entry.to_prompt_dict())
        except AttributeError:
            payload.append(dict(entry))  # type: ignore[arg-type]
    return payload


if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import EventPayload, SignalResult
else:  # pragma: no cover - runtime fallback to avoid circular import issues
    try:
        from src.ai.signal_engine import EventPayload, SignalResult
    except Exception:  # noqa: BLE001 - best effort fallback during bootstrap
        EventPayload = Any  # type: ignore[assignment]
        SignalResult = Any  # type: ignore[assignment]
