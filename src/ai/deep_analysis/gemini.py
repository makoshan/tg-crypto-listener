"""Gemini deep analysis engine implementation with Function Calling support."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, Iterable, Sequence

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
    ) -> None:
        super().__init__(provider_name="gemini", parse_json_callback=parse_json_callback)
        self._client = client
        self._memory = memory_bundle
        self._max_function_turns = max(1, int(max_function_turns))
        self._memory_limit = max(1, int(memory_limit))
        self._memory_min_confidence = float(memory_min_confidence)
        self._tools = self._build_tools()

    async def analyse(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
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


from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import EventPayload, SignalResult
