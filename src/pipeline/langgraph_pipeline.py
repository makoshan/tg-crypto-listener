"""LangGraph-powered message processing pipeline."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from ..ai.signal_engine import AiSignalEngine, EventPayload, SignalResult
from ..ai.gemini_client import AiServiceError
from ..ai.translator import Translator
from ..config import Config
from ..db import AiSignalRepository, NewsEventRepository, SupabaseError
from ..forwarder import MessageForwarder
from ..memory import (
    HybridMemoryRepository,
    LocalMemoryStore,
    MemoryContext,
    SupabaseMemoryRepository,
)
from ..memory.types import MemoryEntry
from ..utils import (
    MessageDeduplicator,
    compute_canonical_hash,
    compute_embedding,
    compute_sha256,
    contains_keywords,
    format_forwarded_message,
)


@dataclass
class PipelineResult:
    """Final outcome emitted after graph execution."""

    status: str
    drop_reason: Optional[str] = None
    forwarded: bool = False
    signal_result: Optional[SignalResult] = None


@dataclass
class PipelineDependencies:
    """External services shared across graph nodes."""

    config: Config
    deduplicator: MessageDeduplicator
    translator: Optional[Translator]
    ai_engine: Optional[AiSignalEngine]
    forwarder: Optional[MessageForwarder]
    news_repository: Optional[NewsEventRepository]
    signal_repository: Optional[AiSignalRepository]
    memory_repository: Optional[
        SupabaseMemoryRepository | LocalMemoryStore | HybridMemoryRepository
    ]
    db_enabled: bool
    stats: Dict[str, Any]
    logger: Any
    collect_keywords: Callable[..., List[str]]
    extract_media: Callable[[Any], Awaitable[List[Dict[str, Any]]]]
    build_ai_kwargs: Callable[[Optional[SignalResult], str], Dict[str, Any]]
    should_include_original: Callable[..., bool]
    append_links: Callable[[str, List[str]], str]
    collect_links: Callable[[SignalResult, str, Optional[str], Optional[str]], List[str]]
    persist_event: Callable[..., Awaitable[None]]
    update_ai_stats: Callable[[SignalResult], None]


@dataclass
class RawEventState:
    telegram_event: Any
    source_name: str = ""
    source_message_id: str = ""
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    processed_at: datetime = field(default_factory=datetime.utcnow)
    channel_username: Optional[str] = None


@dataclass
class ContentState:
    original_text: str = ""
    translated_text: Optional[str] = None
    language: str = "unknown"
    translation_confidence: float = 0.0
    keywords: List[str] = field(default_factory=list)


@dataclass
class HashState:
    raw: str = ""
    canonical: str = ""


@dataclass
class DedupState:
    memory: bool = False
    hash: bool = False
    semantic: bool = False
    similar_event: Optional[str] = None


@dataclass
class RoutingState:
    drop_reason: Optional[str] = None
    forwarded: bool = False
    should_persist: bool = False
    ai_skipped: bool = False


@dataclass
class ControlState:
    status: str = "processing"
    drop: bool = False
    errors: List[str] = field(default_factory=list)


class PipelineState(TypedDict, total=False):
    raw_event: RawEventState
    content: ContentState
    hashes: HashState
    dedup: DedupState
    routing: RoutingState
    control: ControlState
    embedding: Optional[List[float]]
    media: List[Dict[str, Any]]
    memory_context: Optional[MemoryContext]
    signal_result: Optional[SignalResult]
    historical_reference: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class LangGraphMessagePipeline:
    """Compile and execute the LangGraph message workflow."""

    def __init__(self, dependencies: PipelineDependencies) -> None:
        self.deps = dependencies
        self.state_graph = None
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(PipelineState)
        graph.add_node("ingest", self._node_ingest)
        graph.add_node("keyword_filter", self._node_keyword_filter)
        graph.add_node("dedup_memory", self._node_dedup_memory)
        graph.add_node("dedup_hash", self._node_dedup_hash)
        graph.add_node("dedup_semantic", self._node_dedup_semantic)
        graph.add_node("media_extract", self._node_media_extract)
        graph.add_node("translation", self._node_translation)
        graph.add_node("keyword_collect", self._node_keyword_collect)
        graph.add_node("memory_fetch", self._node_memory_fetch)
        graph.add_node("ai_signal", self._node_ai_signal)
        graph.add_node("forward", self._node_forward)
        graph.add_node("persistence", self._node_persistence)
        graph.add_node("finalize", self._node_finalize)

        graph.add_edge(START, "ingest")
        graph.add_edge("ingest", "keyword_filter")
        graph.add_conditional_edges(
            "keyword_filter",
            self._route_by_drop,
            {
                "drop": "finalize",
                "continue": "dedup_memory",
            },
        )
        graph.add_conditional_edges(
            "dedup_memory",
            self._route_by_drop,
            {
                "drop": "finalize",
                "continue": "dedup_hash",
            },
        )
        graph.add_conditional_edges(
            "dedup_hash",
            self._route_by_drop,
            {
                "drop": "finalize",
                "continue": "dedup_semantic",
            },
        )
        graph.add_conditional_edges(
            "dedup_semantic",
            self._route_by_drop,
            {
                "drop": "finalize",
                "continue": "media_extract",
            },
        )
        graph.add_edge("media_extract", "translation")
        graph.add_edge("translation", "keyword_collect")
        graph.add_edge("keyword_collect", "memory_fetch")
        graph.add_edge("memory_fetch", "ai_signal")
        graph.add_edge("ai_signal", "forward")
        graph.add_edge("forward", "persistence")
        graph.add_edge("persistence", "finalize")
        graph.add_edge("finalize", END)

        self.state_graph = graph
        return graph.compile()

    async def run(self, telegram_event: Any) -> PipelineResult:
        """Execute the pipeline for a single Telegram event."""

        initial_state: PipelineState = {
            "raw_event": RawEventState(
                telegram_event=telegram_event,
                processed_at=datetime.now(timezone.utc),
            ),
            "content": ContentState(),
            "hashes": HashState(),
            "dedup": DedupState(),
            "routing": RoutingState(),
            "control": ControlState(),
        }

        final_state = await self._graph.ainvoke(initial_state)
        control = final_state.get("control", ControlState())
        routing = final_state.get("routing", RoutingState())
        signal_result = final_state.get("signal_result")
        status = control.status
        if control.drop and status == "processing":
            status = "dropped"
        return PipelineResult(
            status=status,
            drop_reason=routing.drop_reason,
            forwarded=routing.forwarded,
            signal_result=signal_result,
        )

    # --------------------------------------------------------------------- #
    # Graph routing helpers
    # --------------------------------------------------------------------- #
    def _route_by_drop(self, state: PipelineState) -> str:
        control = state.get("control")
        if control and control.drop:
            return "drop"
        return "continue"

    # --------------------------------------------------------------------- #
    # Graph nodes
    # --------------------------------------------------------------------- #
    async def _node_ingest(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        raw_event = state.get("raw_event")
        content = state.get("content") or ContentState()
        routing = state.get("routing") or RoutingState()

        if not raw_event:
            control.drop = True
            control.status = "dropped"
            routing.drop_reason = "missing_event"
            return {
                "control": control,
                "routing": routing,
            }

        event = raw_event.telegram_event
        message = getattr(event, "message", None)
        message_text = ""
        if message:
            message_text = getattr(message, "text", "") or ""

        if not message_text.strip():
            deps.logger.debug("📭 忽略无文本内容的消息")
            control.drop = True
            control.status = "dropped"
            routing.drop_reason = "empty_message"
            deps.stats["filtered_out"] = deps.stats.get("filtered_out", 0) + 1
            return {
                "control": control,
                "routing": routing,
            }

        try:
            source_chat = await event.get_chat()
        except Exception as exc:  # pylint: disable=broad-except
            deps.logger.warning("⚠️ 获取消息来源失败: %s", exc)
            source_chat = None

        source_name = (
            getattr(source_chat, "title", None)
            or getattr(source_chat, "username", None)
            or str(getattr(source_chat, "id", "Unknown"))
        )
        channel_username = getattr(source_chat, "username", None)
        source_message_id = str(getattr(message, "id", ""))
        published_at = getattr(message, "date", None) or datetime.now(timezone.utc)
        source_url = (
            f"https://t.me/{channel_username}/{source_message_id}"
            if channel_username and source_message_id
            else None
        )

        deps.logger.debug(
            "📨 LangGraph ingest: source=%s len=%d preview=%s",
            source_name,
            len(message_text),
            message_text[:120].replace("\n", " "),
        )

        raw_event.source_name = source_name
        raw_event.source_message_id = source_message_id
        raw_event.source_url = source_url
        raw_event.published_at = (
            published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        )
        raw_event.channel_username = channel_username
        raw_event.processed_at = datetime.now(timezone.utc)

        content.original_text = message_text
        hashes = state.get("hashes") or HashState()
        hashes.raw = compute_sha256(message_text)
        hashes.canonical = compute_canonical_hash(message_text)

        # Update stats for total received (ingest success)
        deps.stats["total_received"] = deps.stats.get("total_received", 0) + 1

        return {
            "raw_event": raw_event,
            "content": content,
            "hashes": hashes,
            "routing": routing,
            "control": control,
        }

    async def _node_keyword_filter(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()
        content = state.get("content") or ContentState()

        if not contains_keywords(content.original_text, deps.config.FILTER_KEYWORDS):
            deps.stats["filtered_out"] = deps.stats.get("filtered_out", 0) + 1
            routing.drop_reason = "keyword_filter"
            control.drop = True
            control.status = "dropped"
            deps.logger.debug("🚫 关键词过滤触发")

        return {
            "control": control,
            "routing": routing,
        }

    async def _node_dedup_memory(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()
        dedup = state.get("dedup") or DedupState()
        content = state.get("content") or ContentState()

        if control.drop:
            return {
                "control": control,
                "routing": routing,
                "dedup": dedup,
            }

        if deps.deduplicator.is_duplicate(content.original_text):
            dedup.memory = True
            routing.drop_reason = "memory_dedup"
            control.drop = True
            control.status = "dropped"
            deps.stats["duplicates"] = deps.stats.get("duplicates", 0) + 1
            deps.logger.debug("🔄 内存去重命中，跳过消息")

        return {
            "control": control,
            "routing": routing,
            "dedup": dedup,
        }

    async def _node_dedup_hash(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()
        dedup = state.get("dedup") or DedupState()
        hashes = state.get("hashes") or HashState()

        if control.drop or not deps.db_enabled or not deps.news_repository:
            return {
                "control": control,
                "routing": routing,
                "dedup": dedup,
            }

        try:
            existing_event_id = await deps.news_repository.check_duplicate(hashes.raw)
        except Exception as exc:  # pylint: disable=broad-except
            deps.logger.warning("哈希去重检查失败，忽略: %s", exc)
            return {
                "control": control,
                "routing": routing,
                "dedup": dedup,
            }

        if existing_event_id:
            dedup.hash = True
            dedup.similar_event = existing_event_id
            routing.drop_reason = "hash_dedup"
            control.drop = True
            control.status = "dropped"
            deps.stats["duplicates"] = deps.stats.get("duplicates", 0) + 1
            deps.logger.debug("🔁 数据库哈希去重命中: event_id=%s", existing_event_id)

        return {
            "control": control,
            "routing": routing,
            "dedup": dedup,
        }

    async def _node_dedup_semantic(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()
        dedup = state.get("dedup") or DedupState()
        content = state.get("content") or ContentState()

        if control.drop or not deps.db_enabled or not deps.news_repository:
            return {
                "control": control,
                "routing": routing,
                "dedup": dedup,
            }

        embedding: Optional[List[float]] = state.get("embedding")
        if embedding is None and deps.config.OPENAI_API_KEY:
            embedding = await compute_embedding(
                content.original_text,
                api_key=deps.config.OPENAI_API_KEY,
                model=deps.config.OPENAI_EMBEDDING_MODEL,
            )

        if embedding:
            try:
                similar = await deps.news_repository.check_duplicate_by_embedding(
                    embedding=embedding,
                    threshold=deps.config.EMBEDDING_SIMILARITY_THRESHOLD,
                    time_window_hours=deps.config.EMBEDDING_TIME_WINDOW_HOURS,
                )
            except Exception as exc:  # pylint: disable=broad-except
                deps.logger.warning("语义去重检查失败: %s", exc)
            else:
                if similar:
                    dedup.semantic = True
                    dedup.similar_event = str(similar.get("id"))
                    routing.drop_reason = "semantic_dedup"
                    control.drop = True
                    control.status = "dropped"
                    deps.stats["duplicates"] = deps.stats.get("duplicates", 0) + 1
                    deps.logger.info(
                        "🔁 语义去重命中: event_id=%s similarity=%.3f",
                        similar.get("id"),
                        similar.get("similarity"),
                    )

        return {
            "control": control,
            "routing": routing,
            "dedup": dedup,
            "embedding": embedding,
        }

    async def _node_media_extract(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        raw_event = state.get("raw_event")
        control = state.get("control") or ControlState()

        if control.drop or not raw_event:
            return {
                "control": control,
            }

        media_payload = await deps.extract_media(getattr(raw_event.telegram_event, "message", None))
        return {
            "media": media_payload,
            "control": control,
        }

    async def _node_translation(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        content = state.get("content") or ContentState()

        if control.drop or not deps.translator:
            return {
                "control": control,
                "content": content,
            }

        try:
            translation = await deps.translator.translate(content.original_text)
        except AiServiceError as exc:
            deps.stats["translation_errors"] = deps.stats.get("translation_errors", 0) + 1
            deps.logger.warning("翻译失败，使用原文: %s", exc)
            return {
                "control": control,
                "content": content,
            }
        except Exception as exc:  # pylint: disable=broad-except
            deps.stats["translation_errors"] = deps.stats.get("translation_errors", 0) + 1
            deps.logger.warning("翻译模块异常: %s", exc)
            return {
                "control": control,
                "content": content,
            }

        content.language = translation.language or "unknown"
        content.translation_confidence = getattr(translation, "confidence", 0.0)
        content.translated_text = translation.text
        if getattr(translation, "translated", False):
            deps.stats["translations"] = deps.stats.get("translations", 0) + 1

        return {
            "control": control,
            "content": content,
        }

    async def _node_keyword_collect(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        content = state.get("content") or ContentState()
        control = state.get("control") or ControlState()

        if control.drop:
            return {
                "content": content,
                "control": control,
            }

        translated = content.translated_text
        if translated and translated != content.original_text:
            content.keywords = deps.collect_keywords(content.original_text, translated)
        else:
            content.keywords = deps.collect_keywords(content.original_text)

        return {
            "content": content,
            "control": control,
        }

    async def _node_memory_fetch(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        content = state.get("content") or ContentState()
        embedding = state.get("embedding")

        if control.drop or not deps.config.MEMORY_ENABLED or not deps.memory_repository:
            return {
                "control": control,
                "memory_context": None,
            }

        memory_context: Optional[MemoryContext] = None

        try:
            if isinstance(deps.memory_repository, LocalMemoryStore):
                memory_entries = deps.memory_repository.load_entries(
                    keywords=content.keywords,
                    limit=deps.config.MEMORY_MAX_NOTES,
                    min_confidence=deps.config.MEMORY_MIN_CONFIDENCE,
                )
                memory_context = MemoryContext(entries=memory_entries)
            elif isinstance(deps.memory_repository, HybridMemoryRepository):
                memory_context = await deps.memory_repository.fetch_memories(
                    embedding=embedding,
                    asset_codes=None,
                    keywords=content.keywords,
                )
            else:
                if embedding:
                    memory_context = await deps.memory_repository.fetch_memories(
                        embedding=embedding,
                        asset_codes=None,
                    )
        except (SupabaseError, Exception) as exc:  # pylint: disable=broad-except
            deps.logger.warning("记忆检索失败，跳过历史参考: %s", exc)
            memory_context = None

        historical_reference: List[Dict[str, Any]] = []
        if memory_context and not memory_context.is_empty():
            historical_reference = memory_context.to_prompt_payload()
            deps.logger.info("🧠 Memory 检索完成: %d 条记录", len(historical_reference))
            if deps.logger.isEnabledFor(10):  # DEBUG
                for i, entry in enumerate(memory_context.entries, 1):
                    if isinstance(entry, MemoryEntry):
                        deps.logger.debug(
                            "  [%d] %s conf=%.2f sim=%.2f summary=%s",
                            i,
                            ",".join(entry.assets),
                            entry.confidence,
                            entry.similarity,
                            entry.summary,
                        )
        else:
            deps.logger.debug("🧠 无历史记忆，使用空上下文")

        return {
            "control": control,
            "memory_context": memory_context,
            "historical_reference": historical_reference,
        }

    async def _node_ai_signal(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()
        raw_event = state.get("raw_event") or RawEventState(telegram_event=None)
        content = state.get("content") or ContentState()

        if control.drop:
            return {
                "control": control,
                "routing": routing,
                "signal_result": None,
            }

        if not deps.ai_engine:
            deps.logger.debug("AI 引擎未初始化，跳过 AI 分析")
            return {
                "control": control,
                "routing": routing,
                "signal_result": None,
            }

        media_payload = state.get("media") or []
        historical_reference = state.get("historical_reference") or []

        payload = EventPayload(
            text=content.original_text,
            source=raw_event.source_name,
            timestamp=raw_event.processed_at or datetime.now(timezone.utc),
            translated_text=content.translated_text,
            language=content.language,
            translation_confidence=content.translation_confidence,
            keywords_hit=content.keywords,
            historical_reference={"entries": historical_reference, "enabled": bool(historical_reference)}
            if deps.config.MEMORY_ENABLED
            else {},
            media=media_payload,
        )

        try:
            signal_result = await deps.ai_engine.analyse(payload)
        except Exception as exc:  # pylint: disable=broad-except
            deps.stats["ai_errors"] = deps.stats.get("ai_errors", 0) + 1
            control.status = "error"
            control.errors.append(str(exc))
            deps.logger.error("AI 分析失败: %s", exc)
            signal_result = None
        else:
            if signal_result:
                deps.update_ai_stats(signal_result)

        return {
            "control": control,
            "routing": routing,
            "signal_result": signal_result,
        }

    async def _node_forward(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()
        raw_event = state.get("raw_event") or RawEventState(telegram_event=None)
        content = state.get("content") or ContentState()
        hashes = state.get("hashes") or HashState()
        signal_result = state.get("signal_result")

        if control.drop:
            routing.should_persist = False
            return {
                "control": control,
                "routing": routing,
            }

        routing.should_persist = True
        should_skip_forward = False

        if signal_result and signal_result.status != "error":
            low_confidence_skip = signal_result.confidence < 0.4
            neutral_skip = (
                deps.config.AI_SKIP_NEUTRAL_FORWARD
                and signal_result.status == "skip"
                and signal_result.summary != "AI disabled"
            )
            if low_confidence_skip or neutral_skip:
                should_skip_forward = True
                deps.stats["ai_skipped"] = deps.stats.get("ai_skipped", 0) + 1
                deps.logger.info(
                    "🤖 AI 评估为低优先级，跳过转发: source=%s action=%s confidence=%.2f",
                    raw_event.source_name,
                    signal_result.action,
                    signal_result.confidence,
                )

        if should_skip_forward:
            routing.forwarded = False
            routing.ai_skipped = True
            return {
                "control": control,
                "routing": routing,
            }

        ai_kwargs = deps.build_ai_kwargs(signal_result, raw_event.source_name)
        if not ai_kwargs:
            deps.stats["ai_skipped"] = deps.stats.get("ai_skipped", 0) + 1
            deps.logger.info(
                "🤖 缺少 AI 摘要，跳过转发: source=%s",
                raw_event.source_name,
            )
            routing.forwarded = False
            routing.ai_skipped = True
            return {
                "control": control,
                "routing": routing,
            }

        show_original = deps.should_include_original(
            original_text=content.original_text,
            translated_text=content.translated_text,
            signal_result=signal_result,
        )
        formatted_message = format_forwarded_message(
            source_channel=raw_event.source_name,
            timestamp=raw_event.processed_at or datetime.now(timezone.utc),
            translated_text=content.translated_text,
            original_text=content.original_text,
            show_original=show_original,
            show_translation=deps.config.FORWARD_INCLUDE_TRANSLATION,
            **ai_kwargs,
        )

        links: List[str] = []
        if signal_result:
            links = deps.collect_links(
                signal_result,
                formatted_message,
                content.translated_text,
                content.original_text,
            )
        if links:
            formatted_message = deps.append_links(formatted_message, links)

        forward_success = False
        if deps.forwarder:
            forward_success = await deps.forwarder.forward_message(
                formatted_message,
                link_preview=False,
            )

        if forward_success:
            deps.stats["forwarded"] = deps.stats.get("forwarded", 0) + 1
            deps.logger.info("📤 已转发来自 %s 的消息", raw_event.source_name)
        else:
            deps.stats["errors"] = deps.stats.get("errors", 0) + 1
            deps.logger.error("❌ 消息转发失败")

        routing.forwarded = forward_success
        return {
            "control": control,
            "routing": routing,
            "signal_result": signal_result,
            "hashes": hashes,
        }

    async def _node_persistence(self, state: PipelineState) -> PipelineState:
        deps = self.deps
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()
        raw_event = state.get("raw_event") or RawEventState(telegram_event=None)
        content = state.get("content") or ContentState()
        signal_result = state.get("signal_result")
        embedding = state.get("embedding")
        media_refs = state.get("media") or []
        hashes = state.get("hashes") or HashState()

        if control.drop or not routing.should_persist:
            return {
                "control": control,
                "routing": routing,
            }

        if not deps.db_enabled or not deps.news_repository:
            return {
                "control": control,
                "routing": routing,
            }

        try:
            await deps.persist_event(
                raw_event.source_name,
                content.original_text,
                content.translated_text,
                signal_result,
                routing.forwarded,
                source_message_id=raw_event.source_message_id,
                source_url=raw_event.source_url,
                published_at=raw_event.published_at or datetime.now(timezone.utc),
                processed_at=raw_event.processed_at or datetime.now(timezone.utc),
                language=content.language,
                keywords_hit=content.keywords,
                translation_confidence=content.translation_confidence,
                media_refs=media_refs,
                hash_raw=hashes.raw or None,
                hash_canonical=hashes.canonical or None,
                embedding=embedding,
            )
        except Exception as exc:  # pylint: disable=broad-except
            deps.logger.warning("持久化流程异常: %s", exc)

        return {
            "control": control,
            "routing": routing,
        }

    async def _node_finalize(self, state: PipelineState) -> PipelineState:
        control = state.get("control") or ControlState()
        routing = state.get("routing") or RoutingState()

        if control.drop:
            control.status = "dropped"
        elif control.status == "processing":
            control.status = "forwarded" if routing.forwarded else "processed"

        return {
            "control": control,
            "routing": routing,
        }
