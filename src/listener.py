"""Main Telegram listener entrypoint."""

from __future__ import annotations

import asyncio
import base64
import signal
import sys
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from telethon import TelegramClient, events
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

from .ai.signal_engine import AiSignalEngine, EventPayload, SignalResult
from .ai.translator import Translator, build_translator_from_config
from .ai.gemini_client import AiServiceError
from .config import Config
from .forwarder import MessageForwarder
from .email_sender import EmailSender
from .bot_sender import BotSender
from .utils import (
    MessageIdDeduplicator,
    MessageDeduplicator,
    SignalMessageDeduplicator,
    analyze_event_intensity,
    contains_keywords,
    contains_block_keywords,
    format_forwarded_message,
    compute_canonical_hash,
    compute_sha256,
    compute_embedding,
    setup_logger,
)
from .db import AiSignalRepository, NewsEventRepository, SupabaseError, get_supabase_client
from .memory import (
    MemoryContext,
    MemoryEntry,
    MemoryRepositoryConfig,
    SupabaseMemoryRepository,
    LocalMemoryStore,
    HybridMemoryRepository,
)
from .pipeline import LangGraphMessagePipeline, PipelineDependencies, PipelineResult
from .db.models import AiSignalPayload, NewsEventPayload

logger = setup_logger(__name__)

MAX_INLINE_MEDIA_BYTES = 4_000_000  # 4MB limit for Gemini inline images (20MB total request limit)


class TelegramListener:
    """Set up Telethon client, filter, format, and forward messages."""

    def __init__(self) -> None:
        self.config = Config()
        self.client: TelegramClient | None = None
        self.forwarder: MessageForwarder | None = None
        # Immediate deduplication by message ID (prevents same message processed multiple times in 1 second)
        self.message_id_deduplicator = MessageIdDeduplicator(window_minutes=60)
        self.deduplicator = MessageDeduplicator(self.config.DEDUP_WINDOW_HOURS)
        self.signal_deduplicator: SignalMessageDeduplicator | None = None
        self.translator: Translator | None = None
        self.ai_engine = AiSignalEngine.from_config(self.config)
        self.running = False
        self.pipeline_enabled = getattr(self.config, "USE_LANGGRAPH_PIPELINE", False)
        self.pipeline: LangGraphMessagePipeline | None = None
        self.stats = {
            "total_received": 0,
            "filtered_out": 0,
            "duplicates": 0,
            "dup_memory": 0,
            "dup_hash": 0,
            "dup_semantic": 0,
            "dup_signal": 0,
            "dup_message_id": 0,  # Immediate deduplication by message ID
            "forwarded": 0,
            "errors": 0,
            "ai_processed": 0,
            "ai_actions": 0,
            "ai_errors": 0,
            "ai_skipped": 0,
            "translations": 0,
            "translation_errors": 0,
            "start_time": datetime.now(),
        }
        self.db_enabled = (
            self.config.ENABLE_DB_PERSISTENCE
            and bool(self.config.SUPABASE_URL)
            and bool(self.config.SUPABASE_SERVICE_KEY)
        )
        self._supabase_client = None
        self.news_repository: NewsEventRepository | None = None
        self.signal_repository: AiSignalRepository | None = None
        self.memory_repository: SupabaseMemoryRepository | LocalMemoryStore | HybridMemoryRepository | None = None
        self.price_tool: Any | None = None
        self.price_enabled = bool(getattr(self.config, "PRICE_ENABLED", False))
        if self.config.SIGNAL_DEDUP_ENABLED:
            self.signal_deduplicator = SignalMessageDeduplicator(
                window_minutes=self.config.SIGNAL_DEDUP_WINDOW_MINUTES,
                similarity_threshold=self.config.SIGNAL_DEDUP_SIMILARITY,
                min_common_chars=self.config.SIGNAL_DEDUP_MIN_COMMON_CHARS,
            )

    async def initialize(self) -> None:
        """Prepare Telethon client and verify configuration."""
        if not self.config.validate():
            raise ValueError("配置验证失败，请检查 .env 文件")

        Path("./session").mkdir(exist_ok=True)

        self.client = TelegramClient(
            self.config.SESSION_PATH,
            self.config.TG_API_ID,
            self.config.TG_API_HASH,
        )

        # Initialize email sender
        email_sender = EmailSender(
            smtp_host=self.config.EMAIL_SMTP_HOST,
            smtp_port=self.config.EMAIL_SMTP_PORT,
            from_email=self.config.EMAIL_FROM,
            password=self.config.EMAIL_PASSWORD,
            to_email=self.config.EMAIL_TO,
            enabled=self.config.EMAIL_ENABLED,
        )

        # Initialize bot sender
        bot_sender = BotSender(
            bot_token=self.config.BOT_TOKEN,
            user_chat_id=self.config.BOT_USER_CHAT_ID,
            enabled=self.config.BOT_ENABLED,
        )

        self.forwarder = MessageForwarder(
            self.client,
            self.config.TARGET_CHAT_ID,
            self.config.TARGET_CHAT_ID_BACKUP,
            cooldown_seconds=self.config.FORWARD_COOLDOWN_SECONDS,
            email_sender=email_sender,
            bot_sender=bot_sender,
            forward_to_channel_enabled=self.config.FORWARD_TO_CHANNEL_ENABLED,
        )

        if self.db_enabled:
            try:
                self._supabase_client = get_supabase_client(
                    self.config.SUPABASE_URL,
                    self.config.SUPABASE_SERVICE_KEY,
                    timeout=self.config.SUPABASE_TIMEOUT_SECONDS,
                )
                self.news_repository = NewsEventRepository(self._supabase_client)
                self.signal_repository = AiSignalRepository(self._supabase_client)

                # Initialize memory repository based on MEMORY_BACKEND
                if self.config.MEMORY_ENABLED:
                    memory_config = MemoryRepositoryConfig(
                        max_notes=max(1, int(self.config.MEMORY_MAX_NOTES)),
                        similarity_threshold=float(self.config.MEMORY_SIMILARITY_THRESHOLD),
                        lookback_hours=max(1, int(self.config.MEMORY_LOOKBACK_HOURS)),
                        min_confidence=max(0.0, float(self.config.MEMORY_MIN_CONFIDENCE)),
                    )

                    backend = getattr(self.config, "MEMORY_BACKEND", "supabase").lower()
                    memory_dir = getattr(self.config, "MEMORY_DIR", "./memories")

                    if backend == "local":
                        self.memory_repository = LocalMemoryStore(
                            base_path=memory_dir,
                            lookback_hours=memory_config.lookback_hours,
                        )
                        logger.info("🗄️ Local Memory 已启用 (关键词匹配)")
                    elif backend == "hybrid":
                        supabase_repo = SupabaseMemoryRepository(
                            self._supabase_client,
                            memory_config,
                        )
                        local_store = LocalMemoryStore(
                            base_path=memory_dir,
                            lookback_hours=memory_config.lookback_hours,
                        )
                        self.memory_repository = HybridMemoryRepository(
                            supabase_repo=supabase_repo,
                            local_store=local_store,
                            max_failures=3,
                        )
                        logger.info("🗄️ Hybrid Memory 已启用 (Supabase 主力 + Local 降级)")
                    else:  # supabase (default)
                        self.memory_repository = SupabaseMemoryRepository(
                            self._supabase_client,
                            memory_config,
                        )
                        logger.info("🗄️ Supabase Memory 已启用 (向量相似度)")

                logger.info("🗄️ Supabase 持久化已启用")
            except SupabaseError as exc:
                self.db_enabled = False
                logger.warning("Supabase 初始化失败，将禁用持久化: %s", exc)

        if self.config.TRANSLATION_ENABLED:
            try:
                translator = build_translator_from_config(self.config)
            except AiServiceError as exc:
                logger.warning("翻译模块初始化失败，将使用原文: %s", exc)
                translator = None

            if translator is not None:
                self.translator = translator
                if not translator.enabled:
                    logger.debug("翻译模块已启用但缺少有效凭据，消息将保持原文")
            else:
                self.translator = None

        # Initialize PriceTool if enabled
        if self.price_enabled:
            try:
                from .ai.tools.price.fetcher import PriceTool
                self.price_tool = PriceTool(self.config)
                provider = getattr(self.config, "PRICE_PROVIDER", "coingecko")
                logger.info("💰 价格工具已初始化: provider=%s", provider)
            except Exception as exc:
                logger.warning("价格工具初始化失败，将跳过价格获取: %s", exc)
                self.price_tool = None
                self.price_enabled = False

        if self.pipeline_enabled:
            self._initialize_pipeline()

        logger.info("🚀 正在连接到 Telegram...")
        await self.client.start(phone=self.config.TG_PHONE)

        me = await self.client.get_me()
        username = f"@{me.username}" if me.username else me.id
        logger.info(f"✅ 已登录为: {me.first_name} ({username})")

        await self._verify_target_channels()
        logger.info(
            "📡 开始监听 %d 个频道...",
            len(self.config.SOURCE_CHANNELS),
        )
        keywords = ", ".join(self.config.FILTER_KEYWORDS) if self.config.FILTER_KEYWORDS else "无"
        logger.info("🎯 过滤关键词: %s", keywords)

    async def _verify_target_channels(self) -> None:
        if not self.client:
            return

        try:
            target = await self.client.get_entity(self.config.TARGET_CHAT_ID)
            title = getattr(target, "title", None) or getattr(target, "username", "Unknown")
            logger.info(f"✅ 目标频道验证成功: {title}")
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(f"⚠️ 目标频道验证失败: {exc}")

    def _initialize_pipeline(self) -> None:
        if not self.pipeline_enabled:
            return
        if not self.forwarder:
            logger.warning("🚫 无法初始化 LangGraph 管线：转发器未准备就绪")
            return

        dependencies = PipelineDependencies(
            config=self.config,
            deduplicator=self.deduplicator,
            signal_deduplicator=self.signal_deduplicator,
            translator=self.translator,
            ai_engine=self.ai_engine,
            forwarder=self.forwarder,
            news_repository=self.news_repository,
            signal_repository=self.signal_repository,
            memory_repository=self.memory_repository,
            price_enabled=self.price_enabled,
            price_tool=self.price_tool,
            db_enabled=self.db_enabled,
            stats=self.stats,
            logger=logger,
            collect_keywords=self._collect_keywords,
            extract_media=self._extract_media,
            build_ai_kwargs=self._build_ai_kwargs,
            should_include_original=self._should_include_original,
            append_links=self._append_links,
            collect_links=self._collect_links,
            persist_event=self._persist_event,
            update_ai_stats=self._update_ai_stats,
        )
        self.pipeline = LangGraphMessagePipeline(dependencies)
        logger.info("🧭 LangGraph 管线已启用")

    async def start_listening(self) -> None:
        """Register handlers and start event loop."""
        if not self.client or not self.forwarder:
            raise RuntimeError("Client not initialized")

        self.running = True

        @self.client.on(events.NewMessage(chats=self.config.SOURCE_CHANNELS))
        async def message_handler(event):  # type: ignore[no-redef]
            await self._handle_new_message(event)

        logger.info("🎧 消息监听已启动，按 Ctrl+C 停止...")

        def signal_handler(signum, frame):  # pylint: disable=unused-argument
            logger.info("📡 接收到停止信号，正在关闭...")
            self.running = False
            if self.client:
                client_loop = getattr(self.client, "loop", None)
                try:
                    if client_loop and client_loop.is_running():
                        client_loop.call_soon_threadsafe(self.client.disconnect)
                    else:
                        self.client.disconnect()
                except RuntimeError:
                    logger.debug("Telethon 事件循环已关闭，跳过额外 disconnect 调用", exc_info=True)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        asyncio.create_task(self._stats_reporter())

        try:
            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            logger.info("👋 程序被用户中断")
        finally:
            await self._cleanup()

    async def _handle_new_message(self, event) -> None:
        # Immediate message ID deduplication (prevents same message processed multiple times in 1 second)
        # This check happens before both LangGraph pipeline and legacy flow
        try:
            source_chat = await event.get_chat()
            source_message_id = str(getattr(event.message, "id", ""))
            channel_id = str(getattr(source_chat, "id", "")) or getattr(source_chat, "username", None) or "unknown"
            
            if source_message_id and self.message_id_deduplicator.is_duplicate(channel_id, source_message_id):
                self.stats["duplicates"] += 1
                self.stats["dup_message_id"] += 1
                logger.debug("🔄 消息ID重复，已跳过 (channel=%s, msg_id=%s)", channel_id, source_message_id)
                return
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("消息ID去重检查失败，继续处理: %s", exc)
        
        if self.pipeline_enabled and self.pipeline:
            try:
                result = await self.pipeline.run(event)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("LangGraph 管线执行失败，回退到传统流程: %s", exc, exc_info=True)
                await self._handle_new_message_legacy(event)
                return

            self._log_pipeline_result(result)
            return

        await self._handle_new_message_legacy(event)

    def _log_pipeline_result(self, result: PipelineResult) -> None:
        if result.status == "dropped":
            logger.debug("LangGraph 管线丢弃消息: reason=%s", result.drop_reason)
        elif result.status == "forwarded":
            logger.debug("LangGraph 管线转发完成")
        elif result.status == "processed":
            logger.debug("LangGraph 管线处理完成，无需转发")
        else:
            logger.debug(
                "LangGraph 管线完成: status=%s forwarded=%s reason=%s",
                result.status,
                result.forwarded,
                result.drop_reason,
            )

    async def _handle_new_message_legacy(self, event) -> None:
        try:
            self.stats["total_received"] += 1

            message_text = event.message.text or ""
            if not message_text.strip():
                return

            source_chat = await event.get_chat()
            source_name = (
                getattr(source_chat, "title", None)
                or getattr(source_chat, "username", None)
                or str(getattr(source_chat, "id", "Unknown"))
            )

            source_message_id = str(getattr(event.message, "id", ""))
            published_at = getattr(event.message, "date", None) or datetime.now(timezone.utc)
            channel_username = getattr(source_chat, "username", None)
            
            # Get channel ID for message ID deduplication
            channel_id = str(getattr(source_chat, "id", "")) or channel_username or source_name
            
            source_url = (
                f"https://t.me/{channel_username}/{source_message_id}"
                if channel_username and source_message_id
                else None
            )

            logger.debug("📨 收到消息来自 %s (长度: %d): %.300s...", source_name, len(message_text), message_text)

            # Immediate deduplication by message ID (prevents same message processed multiple times in 1 second)
            if source_message_id and self.message_id_deduplicator.is_duplicate(channel_id, source_message_id):
                self.stats["duplicates"] += 1
                self.stats["dup_message_id"] += 1
                logger.debug("🔄 消息ID重复，已跳过 (channel=%s, msg_id=%s)", channel_id, source_message_id)
                return

            # Check blacklist keywords (filter out messages containing blocked keywords)
            if contains_block_keywords(message_text, self.config.BLOCK_KEYWORDS):
                self.stats["filtered_out"] += 1
                logger.debug("🚫 消息包含黑名单关键词，已过滤")
                return

            # Check if source is KOL whitelist (priority processing, skip keyword filter)
            is_priority_kol = self._is_priority_kol(source_name, channel_username)

            # Priority KOL: skip keyword filter
            if not is_priority_kol and not contains_keywords(message_text, self.config.FILTER_KEYWORDS):
                self.stats["filtered_out"] += 1
                logger.debug("🚫 消息被关键词过滤器拒绝")
                return

            if is_priority_kol:
                logger.warning(
                    "⭐ ============ 优先 KOL 消息 ============\n"
                    "   来源: %s\n"
                    "   特权: 跳过关键词过滤\n"
                    "   置信度门槛: 0.3 (普通 0.4)\n"
                    "   观望门槛: 0.5 (普通 0.85)\n"
                    "   去重门槛: %.2f (普通 %.2f)\n"
                    "   强制转发: %s\n"
                    "========================================",
                    source_name,
                    self.config.PRIORITY_KOL_DEDUP_THRESHOLD,
                    self.config.EMBEDDING_SIMILARITY_THRESHOLD,
                    "启用" if self.config.PRIORITY_KOL_FORCE_FORWARD else "禁用",
                )

            if self.deduplicator.is_duplicate(message_text):
                self.stats["duplicates"] += 1
                self.stats["dup_memory"] += 1
                logger.debug("🔄 重复消息，已跳过")
                return

            if not self.forwarder:
                logger.error("转发器未初始化，消息被丢弃")
                return

            event_time = datetime.now()

            hash_raw = compute_sha256(message_text)
            hash_canonical = compute_canonical_hash(message_text)
            embedding_vector: list[float] | None = None

            if self.db_enabled and self.news_repository and hash_raw:
                try:
                    existing_event_id = await self.news_repository.check_duplicate(hash_raw)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning("哈希去重检查失败，继续处理: %s", exc)
                else:
                    if existing_event_id:
                        self.stats["duplicates"] += 1
                        self.stats["dup_hash"] += 1
                        logger.debug(
                            "🔁 数据库哈希去重命中: event_id=%s", existing_event_id
                        )
                        return

            if self.config.OPENAI_API_KEY:
                try:
                    embedding_vector = await compute_embedding(
                        message_text,
                        api_key=self.config.OPENAI_API_KEY,
                        model=self.config.OPENAI_EMBEDDING_MODEL,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning("Embedding 计算失败，跳过语义去重: %s", exc)
                    embedding_vector = None
                else:
                    # Execute semantic dedup check (skip for priority KOL)
                    if self.db_enabled and self.news_repository and embedding_vector:
                        if is_priority_kol:
                            logger.debug(
                                "⭐ 白名单 KOL 跳过早期语义去重检查: source=%s",
                                source_name,
                            )
                        else:
                            try:
                                # Use stricter threshold for early dedup to save AI costs
                                threshold = self.config.EMBEDDING_SIMILARITY_THRESHOLD
                                similar = await self.news_repository.check_duplicate_by_embedding(
                                    embedding=embedding_vector,
                                    threshold=threshold,
                                    time_window_hours=self.config.EMBEDDING_TIME_WINDOW_HOURS,
                                )
                                if similar:
                                    self.stats["duplicates"] += 1
                                    self.stats["dup_semantic"] += 1
                                    logger.info(
                                        "🔁 早期语义去重命中: event_id=%s similarity=%.3f content_preview=%s",
                                        similar["id"],
                                        similar["similarity"],
                                        similar.get("content_text", "")[:50],
                                    )
                                    return
                            except Exception as exc:  # pylint: disable=broad-except
                                logger.warning("早期语义去重检查失败，继续处理: %s", exc)

            translated_text = None
            language = "unknown"
            translation_confidence = 0.0
            keywords_hit = self._collect_keywords(message_text)
            media_payload: list[dict[str, Any]] = []

            if self.translator:
                try:
                    translation = await self.translator.translate(message_text)
                    language = translation.language or "unknown"
                    translation_confidence = translation.confidence
                    translated_text = translation.text
                    if translation.translated:
                        self.stats["translations"] += 1
                except AiServiceError as exc:
                    self.stats["translation_errors"] += 1
                    logger.warning("翻译失败，使用原文: %s", exc)

            if translated_text and translated_text != message_text:
                keywords_hit = self._collect_keywords(message_text, translated_text)

            memory_context: MemoryContext | None = None
            signal_result: SignalResult | None = None
            if self.ai_engine:
                media_payload = await self._extract_media(event.message)
                historical_reference_entries: list[dict[str, str | float]]
                if self.config.MEMORY_ENABLED and self.memory_repository:
                    try:
                        # Different backends require different inputs
                        backend_type = type(self.memory_repository).__name__
                        logger.debug(
                            "🧠 记忆检索开始: backend=%s keywords=%s",
                            backend_type,
                            keywords_hit,
                        )

                        if isinstance(self.memory_repository, LocalMemoryStore):
                            # Local: keyword-based, no embedding needed
                            memory_entries = self.memory_repository.load_entries(
                                keywords=keywords_hit,
                                limit=self.config.MEMORY_MAX_NOTES,
                                min_confidence=self.config.MEMORY_MIN_CONFIDENCE,
                            )
                            memory_context = MemoryContext(entries=memory_entries)
                            logger.info(
                                "🧠 Local Memory 检索完成: 找到 %d 条记录",
                                len(memory_entries),
                            )
                        elif isinstance(self.memory_repository, HybridMemoryRepository):
                            # Hybrid: try Supabase (embedding), fallback to Local (keywords)
                            memory_context = await self.memory_repository.fetch_memories(
                                embedding=embedding_vector,
                                asset_codes=None,
                                keywords=keywords_hit,
                            )
                            logger.info(
                                "🧠 Hybrid Memory 检索完成: 找到 %d 条记录",
                                len(memory_context.entries) if memory_context else 0,
                            )
                        else:
                            # Supabase: vector similarity (requires embedding)
                            if embedding_vector:
                                memory_context = await self.memory_repository.fetch_memories(
                                    embedding=embedding_vector,
                                    asset_codes=None,
                                )
                                logger.info(
                                    "🧠 Supabase Memory 检索完成: 找到 %d 条记录",
                                    len(memory_context.entries) if memory_context else 0,
                                )
                            else:
                                memory_context = None
                                logger.debug("🧠 无 embedding，跳过 Supabase 记忆检索")
                    except (SupabaseError, Exception) as exc:
                        logger.warning("记忆检索失败，跳过历史参考: %s", exc)
                        memory_context = None
                if memory_context and not memory_context.is_empty():
                    historical_reference_entries = memory_context.to_prompt_payload(current_time=event_time)
                    logger.info(
                        "🧠 记忆注入 Prompt: %d 条历史参考",
                        len(historical_reference_entries),
                    )
                    # 详细显示每条记忆的内容
                    if logger.isEnabledFor(10):  # DEBUG level
                        logger.debug("📚 记忆详情（完整）:")
                        for i, entry in enumerate(memory_context.entries, 1):
                            logger.debug(
                                f"  [{i}] ID={entry.id[:8]}... assets={entry.assets} "
                                f"action={entry.action} confidence={entry.confidence:.2f} "
                                f"similarity={entry.similarity:.2f} time={entry.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
                            )
                            logger.debug(f"      摘要: {entry.summary}")
                            logger.debug(f"      event_type: {entry.event_type}")
                    else:
                        # INFO level: 显示结构化的记忆摘要
                        logger.info("📚 历史记忆上下文:")
                        for i, entry in enumerate(memory_context.entries, 1):
                            logger.info(
                                f"  [{i}] {entry.assets} conf={entry.confidence:.2f} sim={entry.similarity:.2f} summary={entry.summary[:80]}"
                            )

                        # 统计分析：资产分布和操作建议模式
                        asset_counts = {}
                        action_counts = {}
                        for entry in memory_context.entries:
                            for asset in (entry.assets or "").split(","):
                                asset = asset.strip()
                                if asset:
                                    asset_counts[asset] = asset_counts.get(asset, 0) + 1
                            if entry.action:
                                action_counts[entry.action] = action_counts.get(entry.action, 0) + 1

                        logger.info("📊 记忆统计: 资产=%s, 操作=%s",
                                   dict(sorted(asset_counts.items(), key=lambda x: x[1], reverse=True)[:3]),
                                   action_counts)
                else:
                    historical_reference_entries = []
                    logger.debug("🧠 无历史记忆，使用空上下文")
                payload = EventPayload(
                    text=message_text,
                    source=source_name,
                    timestamp=event_time,
                    translated_text=translated_text,
                    language=language,
                    translation_confidence=translation_confidence,
                    keywords_hit=keywords_hit,
                    historical_reference=(
                        {
                            "entries": historical_reference_entries,
                            "enabled": True,
                        }
                        if self.config.MEMORY_ENABLED
                        else {}
                    ),
                    media=media_payload,
                    is_priority_kol=is_priority_kol,
                )
                signal_result = await self.ai_engine.analyse(payload)
                if signal_result:
                    self._update_ai_stats(signal_result)

            if signal_result and is_priority_kol:
                # Priority KOL: normalize confidence to 1.0 for downstream gating and persistence
                signal_result.confidence = 1.0

            duplicate_match: Optional[tuple[MemoryEntry, float, float, int]] = None
            if (
                signal_result
                and signal_result.status == "success"
                and memory_context
            ):
                duplicate_match = self._detect_memory_duplicate(
                    signal_result=signal_result,
                    memory_context=memory_context,
                    event_time=event_time,
                )

            if duplicate_match:
                matched_entry, similarity_score, summary_ratio, asset_overlap = duplicate_match
                self.stats["duplicates"] += 1
                self.stats["dup_memory"] += 1
                logger.info(
                    "🧠 历史记忆判定重复，跳过转发: source=%s matched_id=%s similarity=%.2f summary_ratio=%.2f asset_overlap=%d",
                    source_name,
                    matched_entry.id or "unknown",
                    similarity_score,
                    summary_ratio,
                    asset_overlap,
                )
                logger.debug("🧠 匹配记忆摘要: %s", matched_entry.summary)
                await self._persist_event(
                    source_name,
                    message_text,
                    translated_text,
                    signal_result,
                    False,
                    source_message_id=source_message_id,
                    source_url=source_url,
                    published_at=published_at,
                    processed_at=event_time,
                    language=language,
                    keywords_hit=keywords_hit,
                    translation_confidence=translation_confidence,
                    media_refs=media_payload,
                    hash_raw=hash_raw,
                    hash_canonical=hash_canonical,
                    embedding=embedding_vector,
                    is_priority_kol=is_priority_kol,
                    price_snapshot=None,
                )
                return

            # Fetch price if enabled and asset is detected
            price_snapshot: dict[str, Any] | None = None
            logger.debug("💰 价格获取检查: price_enabled=%s, price_tool=%s, signal_result=%s, asset=%s",
                        self.price_enabled, bool(self.price_tool), bool(signal_result),
                        signal_result.asset if signal_result else None)
            if self.price_enabled and self.price_tool and signal_result and signal_result.asset and signal_result.asset != "NONE":
                try:
                    # Support multiple assets (comma-separated)
                    assets = [a.strip() for a in signal_result.asset.split(",") if a.strip()]
                    if len(assets) == 1:
                        # Single asset - keep existing behavior
                        logger.info("💰 开始获取价格: asset=%s", signal_result.asset)
                        price_result = await self.price_tool.snapshot(asset=signal_result.asset)
                        if price_result.success and price_result.data:
                            price_snapshot = price_result.data
                            metrics = price_snapshot.get("metrics", {})
                            price_usd = metrics.get("price_usd")

                            # Price consistency validation
                            if price_usd is not None and self._validate_price_consistency(
                                signal_result.asset, price_usd, text
                            ):
                                logger.info("💰 价格获取成功: asset=%s price=$%s",
                                           signal_result.asset,
                                           price_usd)
                            else:
                                logger.warning("⚠️ 价格一致性验证失败，清除价格数据: asset=%s price=$%s",
                                             signal_result.asset, price_usd)
                                price_snapshot = None
                        else:
                            logger.warning("💰 价格获取返回失败: asset=%s error=%s",
                                         signal_result.asset, price_result.error if price_result else "unknown")
                    else:
                        # Multiple assets - fetch up to 3 prices to avoid too many requests
                        assets_to_fetch = assets[:3]
                        if len(assets) > 3:
                            logger.info("💰 资产数量过多，仅获取前3个: %s (共%d个)", assets_to_fetch, len(assets))
                        else:
                            logger.info("💰 开始获取多个资产价格: assets=%s", assets_to_fetch)

                        price_snapshots = []
                        for asset in assets_to_fetch:
                            try:
                                price_result = await self.price_tool.snapshot(asset=asset)
                                if price_result.success and price_result.data:
                                    price_snapshots.append({
                                        "asset": asset,
                                        "data": price_result.data
                                    })
                                    metrics = price_result.data.get("metrics", {})
                                    price_usd = metrics.get("price_usd")
                                    logger.info("💰 价格获取成功: asset=%s price=$%s", asset, price_usd)
                            except Exception as exc:
                                logger.warning("价格获取异常: asset=%s error=%s", asset, exc)

                        if price_snapshots:
                            # Store multiple price snapshots
                            price_snapshot = {
                                "multiple": True,
                                "snapshots": price_snapshots
                            }
                        else:
                            logger.warning("💰 所有资产价格获取失败: assets=%s", assets_to_fetch)
                except Exception as exc:
                    logger.warning("价格获取异常: asset=%s error=%s", signal_result.asset, exc)

            should_skip_forward = False
            if signal_result and signal_result.status != "error":
                # 忽略下架/退市类型的事件
                if signal_result.event_type == "delisting":
                    should_skip_forward = True
                    self.stats["ai_skipped"] += 1
                    logger.info(
                        "⏭️  忽略下架/退市新闻，跳过转发: source=%s event_type=%s asset=%s",
                        source_name,
                        signal_result.event_type,
                        signal_result.asset,
                    )
                # Use configurable thresholds (from .env)
                confidence_threshold = (
                    self.config.AI_MIN_CONFIDENCE_KOL if is_priority_kol
                    else self.config.AI_MIN_CONFIDENCE
                )
                observe_threshold = (
                    self.config.AI_OBSERVE_THRESHOLD_KOL if is_priority_kol
                    else self.config.AI_OBSERVE_THRESHOLD
                )

                low_confidence_skip = signal_result.confidence < confidence_threshold
                neutral_skip = (
                    self.config.AI_SKIP_NEUTRAL_FORWARD
                    and signal_result.status == "skip"
                    and signal_result.summary != "AI disabled"
                )
                # 二次过滤：观望类信号且置信度低于阈值不转发（KOL 消息降低阈值）
                low_value_observe = (
                    signal_result.action == "observe"
                    and signal_result.confidence < observe_threshold
                )
                if low_confidence_skip or neutral_skip or low_value_observe:
                    should_skip_forward = True
                    self.stats["ai_skipped"] += 1
                    reason = "低价值观望信号" if low_value_observe else "低优先级"
                    logger.info(
                        "🤖 AI 评估为%s，跳过转发: source=%s action=%s confidence=%.2f (KOL=%s threshold=%.2f)",
                        reason,
                        source_name,
                        signal_result.action,
                        signal_result.confidence,
                        is_priority_kol,
                        confidence_threshold,
                    )

            if should_skip_forward:
                await self._persist_event(
                    source_name,
                    message_text,
                    translated_text,
                    signal_result,
                    False,
                    source_message_id=source_message_id,
                    source_url=source_url,
                    published_at=published_at,
                    processed_at=event_time,
                    language=language,
                    keywords_hit=keywords_hit,
                    translation_confidence=translation_confidence,
                    media_refs=media_payload,
                    hash_raw=hash_raw,
                    hash_canonical=hash_canonical,
                    embedding=embedding_vector,
                    is_priority_kol=is_priority_kol,
                    price_snapshot=price_snapshot,
                )
                return

            ai_kwargs = self._build_ai_kwargs(signal_result, source_name, is_priority_kol)
            if not ai_kwargs:
                # Priority KOL with FORCE_FORWARD: allow forwarding even without full AI kwargs
                if is_priority_kol and self.config.PRIORITY_KOL_FORCE_FORWARD and signal_result:
                    logger.warning(
                        "⭐ 白名单 KOL 强制转发模式: 即使 AI 分析不完整也转发 source=%s",
                        source_name,
                    )
                    # Build minimal kwargs for priority KOL
                    ai_kwargs = {
                        "ai_summary": signal_result.summary or f"[{source_name}] {message_text[:100]}...",
                        "ai_action": signal_result.action or "observe",
                        "ai_confidence": 1.0,
                        "ai_event_type": signal_result.event_type or "general",
                        "ai_asset": signal_result.asset or "NONE",
                    }
                else:
                    # AI 分析未返回成功结果（可能是 asset=NONE 或其他原因）
                    self.stats["ai_skipped"] += 1
                    reason = (
                        f"status={signal_result.status}"
                        if signal_result and signal_result.status != "success"
                        else "缺少 AI 摘要"
                    )
                    logger.info(
                        "🤖 AI 分析未通过，跳过转发: source=%s reason=%s",
                        source_name,
                        reason,
                    )
                    await self._persist_event(
                        source_name,
                        message_text,
                        translated_text,
                        signal_result,
                        False,
                        source_message_id=source_message_id,
                        source_url=source_url,
                        published_at=published_at,
                        processed_at=event_time,
                        language=language,
                        keywords_hit=keywords_hit,
                        translation_confidence=translation_confidence,
                        media_refs=media_payload,
                        hash_raw=hash_raw,
                        hash_canonical=hash_canonical,
                        embedding=embedding_vector,
                        is_priority_kol=is_priority_kol,
                        price_snapshot=price_snapshot,
                    )
                    return

            if is_priority_kol:
                ai_kwargs["ai_confidence"] = 1.0

            if self.signal_deduplicator and ai_kwargs.get("ai_summary"):
                if self.signal_deduplicator.is_duplicate(
                    summary=str(ai_kwargs.get("ai_summary") or ""),
                    action=str(ai_kwargs.get("ai_action") or ""),
                    direction=str(ai_kwargs.get("ai_direction") or ""),
                    event_type=str(ai_kwargs.get("ai_event_type") or ""),
                    asset=str(ai_kwargs.get("ai_asset") or ""),
                    asset_names=str(ai_kwargs.get("ai_asset_names") or ""),
                ):
                    self.stats["duplicates"] += 1
                    self.stats["dup_signal"] += 1
                    logger.info(
                        "🔄 信号内容与近期重复，跳过转发: source=%s",
                        source_name,
                    )
                    return

            show_original = self._should_include_original(
                original_text=message_text,
                translated_text=translated_text,
                signal_result=signal_result,
            )

            formatted_message = format_forwarded_message(
                source_channel=source_name,
                timestamp=event_time,
                translated_text=translated_text,
                original_text=message_text,
                show_original=show_original,
                show_translation=self.config.FORWARD_INCLUDE_TRANSLATION,
                price_snapshot=price_snapshot,
                **ai_kwargs,
            )
            links: list[str] = []
            if signal_result:
                links = self._collect_links(
                    signal_result,
                    formatted_message,
                    translated_text,
                    message_text,
                )
            if links:
                formatted_message = self._append_links(
                    formatted_message,
                    links,
                )

            success = await self.forwarder.forward_message(
                formatted_message,
                link_preview=False,
            )
            if success:
                self.stats["forwarded"] += 1
                if is_priority_kol:
                    logger.warning(
                        "⭐ ✅ 白名单 KOL 消息已成功转发: source=%s confidence=%.2f action=%s",
                        source_name,
                        signal_result.confidence if signal_result else 0.0,
                        signal_result.action if signal_result else "unknown",
                    )
                else:
                    logger.info("📤 已转发来自 %s 的消息", source_name)
            else:
                self.stats["errors"] += 1
                if is_priority_kol:
                    logger.error("⭐ ❌ 白名单 KOL 消息转发失败: source=%s", source_name)
                else:
                    logger.error("❌ 消息转发失败")

            await self._persist_event(
                source_name,
                message_text,
                translated_text,
                signal_result,
                success,
                source_message_id=source_message_id,
                source_url=source_url,
                published_at=published_at,
                processed_at=event_time,
                language=language,
                keywords_hit=keywords_hit,
                translation_confidence=translation_confidence,
                media_refs=media_payload,
                hash_raw=hash_raw,
                hash_canonical=hash_canonical,
                embedding=embedding_vector,
                is_priority_kol=is_priority_kol,
                price_snapshot=price_snapshot,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.stats["errors"] += 1
            logger.error(f"❌ 处理消息时出错: {exc}")

    def _append_links(self, message: str, links: list[str]) -> str:
        if not links:
            return message
        normalized = [link for link in links if link]
        if not normalized:
            return message
        link_lines = "\n".join(f"source: {link}" for link in normalized)
        return f"{message}\n{link_lines}"

    def _collect_links(
        self,
        signal_result: SignalResult,
        formatted_message: str,
        translated_text: str | None,
        original_text: str | None,
    ) -> list[str]:
        candidate_sources = [
            "\n".join(signal_result.links),
            signal_result.summary,
            signal_result.notes,
            translated_text,
            original_text,
            formatted_message,
        ]
        seen: set[str] = set()
        collected: list[str] = []
        for source in candidate_sources:
            for link in self._extract_links(source):
                if link not in seen:
                    seen.add(link)
                    collected.append(link)
        return collected

    @staticmethod
    def _extract_links(text: str | None) -> list[str]:
        if not text:
            return []
        # Match URLs, excluding common Markdown artifacts
        pattern = re.compile(r"https?://[\w\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)
        matches = pattern.findall(text)
        normalized = []
        for match in matches:
            # Strip Markdown link syntax residue: ](url) or [text](url)
            cleaned = match.lstrip('](').rstrip(r'.,!?)\]"\'')
            # Skip if still contains invalid prefixes
            if cleaned and not cleaned.startswith((']', '[')):
                normalized.append(cleaned)
        return normalized

    def _build_ai_kwargs(
        self,
        signal_result: SignalResult | None,
        source: str,
        is_priority_kol: bool = False,
    ) -> dict[str, object]:
        if not signal_result:
            logger.debug("AI 结果为空，source=%s", source)
            return {}

        if signal_result.status == "error":
            logger.warning(
                "AI 分析返回错误状态，source=%s error=%s",
                source,
                signal_result.error or "unknown",
            )
            return {}

        if signal_result.status != "success":
            # Priority KOL: allow skip status if FORCE_FORWARD enabled
            if is_priority_kol and self.config.PRIORITY_KOL_FORCE_FORWARD:
                logger.debug(
                    "⭐ 白名单 KOL 允许非成功状态: status=%s source=%s",
                    signal_result.status,
                    source,
                )
                # Continue to check summary
            else:
                logger.debug(
                    "AI 状态为 %s，非成功结果，source=%s",
                    signal_result.status,
                    source,
                )
                return {}

        if not signal_result.summary:
            # Priority KOL: allow missing summary if FORCE_FORWARD enabled
            if is_priority_kol and self.config.PRIORITY_KOL_FORCE_FORWARD:
                logger.warning(
                    "⭐ 白名单 KOL 缺少摘要但强制转发，source=%s",
                    source,
                )
                # Return empty dict to trigger force forward logic in caller
                return {}
            else:
                raw_preview = (signal_result.raw_response or "").strip()
                if len(raw_preview) > 160:
                    raw_preview = raw_preview[:157] + "..."
                logger.info(
                    "AI 返回缺少摘要，source=%s action=%s raw=%s",
                    source,
                    signal_result.action,
                    raw_preview,
                )
                return {}

        return {
            "ai_summary": signal_result.summary,
            "ai_action": signal_result.action,
            "ai_direction": signal_result.direction,
            "ai_event_type": signal_result.event_type,
            "ai_asset": signal_result.asset,
            "ai_asset_names": signal_result.asset_names,
            "ai_confidence": signal_result.confidence,
            "ai_strength": signal_result.strength,
            "ai_timeframe": signal_result.timeframe,
            "ai_risk_flags": signal_result.risk_flags,
            "ai_notes": signal_result.notes,
            "ai_alert": signal_result.alert or None,
            "ai_severity": signal_result.severity or None,
        }

    def _should_include_original(
        self,
        *,
        original_text: str | None,
        translated_text: str | None,
        signal_result: SignalResult | None,
    ) -> bool:
        if not original_text or not original_text.strip():
            return False

        # 无 AI 结果或状态异常，保留原文便于人工判断
        if not signal_result or signal_result.status != "success":
            return True

        # 资产未识别或信号置信度偏低，展示原文供复核
        asset_code = (signal_result.asset or "").strip().upper()
        if not asset_code or asset_code == "NONE":
            return True
        if signal_result.confidence < 0.4:
            return True

        normalized_flags = {flag for flag in signal_result.risk_flags}
        if {"data_incomplete", "confidence_low"} & normalized_flags:
            return True

        notes = (signal_result.notes or "").strip()
        if notes and "原文" in notes:
            return True

        # 若缺少译文，只在摘要中展示原文即可，无需重复
        if not translated_text or translated_text.strip() == "":
            return False

        return False

    def _update_ai_stats(self, signal_result: SignalResult) -> None:
        if signal_result.status == "error":
            self.stats["ai_errors"] += 1
            return
        if signal_result.status == "skip" and signal_result.summary == "AI disabled":
            return
        self.stats["ai_processed"] += 1
        if signal_result.status == "success" and signal_result.should_execute_hot_path:
            self.stats["ai_actions"] += 1

    async def _persist_event(
        self,
        source_name: str,
        original_text: str,
        translated_text: str | None,
        signal_result: SignalResult | None,
        forwarded: bool,
        *,
        source_message_id: str,
        source_url: str | None,
        published_at: datetime,
        processed_at: datetime,
        language: str,
        keywords_hit: list[str],
        translation_confidence: float,
        media_refs: list[dict[str, Any]],
        hash_raw: str | None = None,
        hash_canonical: str | None = None,
        embedding: list[float] | None = None,
        is_priority_kol: bool = False,
        price_snapshot: dict[str, Any] | None = None,
    ) -> None:
        if not self.db_enabled or not self.news_repository:
            return

        if not original_text.strip():
            return

        # Log persistence attempt with context
        status_label = "已转发" if forwarded else "已跳过"
        ai_info = ""
        if signal_result:
            ai_info = f" ai_confidence={signal_result.confidence:.2f} action={signal_result.action}"
        logger.debug(
            "🗄️ 持久化尝试: source=%s status=%s%s",
            source_name,
            status_label,
            ai_info,
        )

        try:
            hash_raw = hash_raw or compute_sha256(original_text)
            hash_canonical = hash_canonical or compute_canonical_hash(original_text)

            embedding_vector = embedding
            if embedding_vector is None and self.config.OPENAI_API_KEY:
                embedding_vector = await compute_embedding(
                    original_text,
                    api_key=self.config.OPENAI_API_KEY,
                    model=self.config.OPENAI_EMBEDDING_MODEL,
                )

            ingest_status = "forwarded" if forwarded else "processed"
            metadata = {
                "forwarded": forwarded,
                "source": source_name,
                "language_detected": language,
                "translation_confidence": translation_confidence,
                "processed_at": processed_at.replace(microsecond=0).isoformat(),
            }
            if embedding_vector:
                metadata["embedding_model"] = self.config.OPENAI_EMBEDDING_MODEL
                metadata["embedding_generated_at"] = datetime.now().isoformat()
            if signal_result:
                metadata.update(
                    {
                        "ai_status": signal_result.status,
                        "ai_confidence": signal_result.confidence,
                        "ai_strength": signal_result.strength,
                        "ai_direction": signal_result.direction,
                        "ai_alert": signal_result.alert,
                        "ai_severity": signal_result.severity,
                    }
                )
                if signal_result.error:
                    metadata["ai_error"] = signal_result.error

            # Level 1: Exact hash dedup (persistence phase)
            news_event_id = await self.news_repository.check_duplicate(hash_raw)
            if news_event_id:
                logger.debug("🗄️ 持久化阶段 - 精确去重命中: event_id=%s", news_event_id)
                return

            # Level 2: Semantic embedding dedup (persistence phase)
            if embedding_vector and not is_priority_kol:
                intensity = analyze_event_intensity(
                    original_text,
                    translated_text or "",
                )
                threshold = self.config.EMBEDDING_SIMILARITY_THRESHOLD
                time_window_hours = self.config.EMBEDDING_TIME_WINDOW_HOURS
                if intensity["has_high_impact"]:
                    threshold = max(threshold, 0.95)
                    time_window_hours = min(time_window_hours, 3) if time_window_hours is not None else 3
                    logger.info(
                        "⚠️ 高影响事件启用宽松语义去重: threshold=%.2f window=%sh",
                        threshold,
                        time_window_hours,
                    )
                threshold = max(0.0, min(1.0, threshold))
                if time_window_hours is None:
                    time_window_hours = 72  # 默认值
                time_window_hours = max(1, int(time_window_hours))
                similar = await self.news_repository.check_duplicate_by_embedding(
                    embedding=embedding_vector,
                    threshold=threshold,
                    time_window_hours=time_window_hours,
                )
                if similar:
                    logger.info(
                        "🗄️ 持久化阶段 - 语义去重命中: event_id=%s similarity=%.3f content_preview=%s (消息已在前面因 AI 评估被跳过)",
                        similar["id"],
                        similar["similarity"],
                        similar.get("content_text", "")[:50],
                    )
                    return
            elif is_priority_kol and embedding_vector:
                logger.debug(
                    "⭐ 白名单 KOL 跳过语义去重: source=%s",
                    source_name,
                )

            if not news_event_id:
                payload = NewsEventPayload(
                    source=source_name,
                    source_message_id=source_message_id,
                    source_url=source_url,
                    published_at=published_at,
                    content_text=original_text,
                    translated_text=translated_text,
                    summary=signal_result.summary if signal_result else None,
                    language=language or "unknown",
                    media_refs=media_refs,
                    hash_raw=hash_raw,
                    hash_canonical=hash_canonical,
                    embedding=embedding_vector,  # Add embedding vector
                    keywords_hit=list(dict.fromkeys(keywords_hit or [])),
                    ingest_status=ingest_status,
                    metadata=metadata,
                    price_snapshot=price_snapshot,
                )
                news_event_id = await self.news_repository.insert_event(payload)

            if not news_event_id:
                logger.warning("⚠️ 未能写入新闻事件到数据库")
                return

            if not signal_result or signal_result.status != "success" or not self.signal_repository:
                logger.debug(
                    "🗄️ 跳过 AI signal 持久化: signal_result=%s status=%s has_repo=%s",
                    signal_result is not None,
                    signal_result.status if signal_result else "N/A",
                    self.signal_repository is not None,
                )
                return

            model_name = self.config.AI_MODEL_NAME or "unknown"
            published_at_aware = (
                published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
            )
            processed_at_aware = (
                processed_at if processed_at.tzinfo else processed_at.replace(tzinfo=timezone.utc)
            )
            latency_delta = processed_at_aware - published_at_aware
            latency_ms = max(
                0,
                int(latency_delta.total_seconds() * 1000),
            )

            try:
                confidence_value = float(signal_result.confidence)
            except (TypeError, ValueError):
                logger.warning(
                    "AI 置信度值无法转换为浮点数，使用默认 0.0: %s",
                    signal_result.confidence,
                )
                confidence_value = 0.0

            signal_payload = AiSignalPayload(
                news_event_id=news_event_id,
                model_name=model_name,
                summary_cn=signal_result.summary,
                event_type=signal_result.event_type,
                assets=signal_result.asset,
                asset_names=signal_result.asset_names or None,
                action=signal_result.action,
                direction=signal_result.direction,
                confidence=confidence_value,
                strength=signal_result.strength,
                risk_flags=signal_result.risk_flags,
                notes=signal_result.notes or None,
                links=signal_result.links,
                execution_path="hot" if signal_result.should_execute_hot_path else "cold",
                should_alert=forwarded,
                latency_ms=latency_ms,
                raw_response=signal_result.raw_response or None,
                price_snapshot=price_snapshot,
            )
            await self.signal_repository.insert_signal(signal_payload)
            logger.debug(
                "🗄️ 持久化成功: news_event_id=%s forwarded=%s ai_confidence=%.2f",
                news_event_id,
                forwarded,
                confidence_value,
            )
        except SupabaseError as exc:
            logger.warning("🗄️ Supabase 写入失败: %s", exc)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("🗄️ 持久化流程异常: %s", exc)

    def _detect_memory_duplicate(
        self,
        *,
        signal_result: SignalResult,
        memory_context: MemoryContext,
        event_time: datetime,
    ) -> Optional[tuple[MemoryEntry, float, float, int]]:
        """Return matching historical memory entry when the signal is considered duplicate."""
        if (
            not self.config.MEMORY_ENABLED
            or not self.config.MEMORY_DUPLICATE_ENABLED
            or not memory_context
            or memory_context.is_empty()
        ):
            return None

        normalized_current = SignalMessageDeduplicator._normalize_text(signal_result.summary or "")
        if not normalized_current or len(normalized_current) < 10:
            return None

        similarity_threshold = max(0.0, min(1.0, float(self.config.MEMORY_DUPLICATE_SIMILARITY)))
        summary_ratio_threshold = max(0.0, min(1.0, float(self.config.MEMORY_DUPLICATE_SUMMARY_RATIO)))
        lookback_hours = max(1, int(self.config.MEMORY_DUPLICATE_LOOKBACK_HOURS))
        min_overlap = max(0, int(self.config.MEMORY_DUPLICATE_MIN_ASSET_OVERLAP))

        asset_tokens = {
            token.strip().upper()
            for token in (signal_result.asset or "").split(",")
            if token.strip()
        }

        event_time_aware = event_time if event_time.tzinfo else event_time.replace(tzinfo=timezone.utc)

        for entry in memory_context.entries:
            entry_summary = entry.summary or ""
            normalized_entry = SignalMessageDeduplicator._normalize_text(entry_summary)
            if not normalized_entry:
                continue

            summary_ratio = SequenceMatcher(None, normalized_current, normalized_entry).ratio()
            if entry.similarity < similarity_threshold and summary_ratio < summary_ratio_threshold:
                continue

            entry_time = entry.created_at
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            age_hours = abs((event_time_aware - entry_time).total_seconds()) / 3600.0
            if age_hours > lookback_hours:
                continue

            entry_assets = {asset.strip().upper() for asset in entry.assets if asset}
            asset_overlap = len(asset_tokens & entry_assets) if asset_tokens else len(entry_assets)
            if asset_tokens and asset_overlap < min_overlap:
                continue

            return entry, entry.similarity, summary_ratio, asset_overlap

        return None

    def _collect_keywords(self, *texts: str) -> list[str]:
        hits: list[str] = []
        available = [text for text in texts if text]
        if not available:
            return hits
        for keyword in self.config.FILTER_KEYWORDS:
            if not keyword:
                continue
            lower_kw = keyword.lower()
            for text in available:
                if lower_kw in text.lower():
                    hits.append(keyword)
                    break
        return hits

    def _validate_price_consistency(self, asset: str, price_usd: float, message_text: str) -> bool:
        """
        Validate price consistency between AI-identified asset and fetched price.

        This helps catch cases where AI misidentifies a low-cap token as a major asset.
        For example: "币安人生" (0.22 USDT) being identified as "BNB" ($1089).

        Args:
            asset: Asset symbol (e.g., "BNB")
            price_usd: Fetched price in USD
            message_text: Original message text

        Returns:
            True if price appears consistent, False otherwise
        """
        import re

        # Known major assets and their expected price ranges (min, max)
        # This helps detect obvious mismatches
        MAJOR_ASSET_PRICE_RANGES = {
            "BTC": (10000, 200000),      # Bitcoin
            "ETH": (1000, 10000),        # Ethereum
            "BNB": (200, 2000),          # Binance Coin
            "SOL": (10, 500),            # Solana
            "ADA": (0.2, 5),             # Cardano
            "AVAX": (10, 200),           # Avalanche
            "DOT": (5, 100),             # Polkadot
            "MATIC": (0.5, 10),          # Polygon
            "LINK": (5, 100),            # Chainlink
            "UNI": (5, 50),              # Uniswap
            "ATOM": (5, 100),            # Cosmos
            "XRP": (0.3, 5),             # Ripple
            "DOGE": (0.05, 1),           # Dogecoin
            "SHIB": (0.000005, 0.0001),  # Shiba Inu
        }

        # Check if asset is a known major asset
        if asset in MAJOR_ASSET_PRICE_RANGES:
            min_price, max_price = MAJOR_ASSET_PRICE_RANGES[asset]
            if not (min_price <= price_usd <= max_price):
                logger.warning(
                    "❌ 价格范围异常: asset=%s price=$%s 预期范围 $%s-$%s",
                    asset, price_usd, min_price, max_price
                )
                return False

        # Check for price mentions in message text that drastically differ
        # Look for patterns like "0.22 USDT", "$0.22", "价格 0.22"
        price_patterns = [
            r'(\d+\.?\d*)\s*(?:USDT|USD|美元|刀)',
            r'\$\s*(\d+\.?\d*)',
            r'价格.*?(\d+\.?\d*)',
            r'突破.*?(\d+\.?\d*)',
        ]

        mentioned_prices = []
        for pattern in price_patterns:
            matches = re.findall(pattern, message_text, re.IGNORECASE)
            for match in matches:
                try:
                    mentioned_price = float(match)
                    mentioned_prices.append(mentioned_price)
                except ValueError:
                    continue

        if mentioned_prices:
            # Check if any mentioned price drastically differs from fetched price
            # Allow 50x difference as threshold (to account for volatility and different contexts)
            for mentioned_price in mentioned_prices:
                if mentioned_price > 0:
                    ratio = max(price_usd, mentioned_price) / min(price_usd, mentioned_price)
                    if ratio > 50:
                        logger.warning(
                            "❌ 消息中的价格与获取的价格差异过大: asset=%s 获取价格=$%s 消息价格=$%s 差异=%.1fx",
                            asset, price_usd, mentioned_price, ratio
                        )
                        return False

        return True

    async def _extract_media(self, message) -> list[dict[str, Any]]:
        """Download image-like media as base64 for AI prompt consumption."""
        media_payload: list[dict[str, Any]] = []
        if not message or not getattr(message, "media", None):
            return media_payload

        try:
            if getattr(message, "photo", None):
                media_bytes = await message.download_media(bytes)
                if media_bytes and len(media_bytes) <= MAX_INLINE_MEDIA_BYTES:
                    media_payload.append(
                        {
                            "type": "photo",
                            "mime_type": "image/jpeg",
                            "size_bytes": len(media_bytes),
                            "base64": base64.b64encode(media_bytes).decode("ascii"),
                        }
                    )
                elif media_bytes:
                    media_payload.append(
                        {
                            "type": "photo_reference",
                            "mime_type": "image/jpeg",
                            "size_bytes": len(media_bytes),
                            "note": "image too large to inline",
                        }
                    )

            document = getattr(message, "document", None)
            if document:
                mime_type = getattr(document, "mime_type", "") or ""
                if mime_type.startswith("image/"):
                    media_bytes = await message.download_media(bytes)
                    if media_bytes and len(media_bytes) <= MAX_INLINE_MEDIA_BYTES:
                        file_name = None
                        for attribute in getattr(document, "attributes", []) or []:
                            file_name = getattr(attribute, "file_name", None) or file_name
                        media_payload.append(
                            {
                                "type": "image_document",
                                "mime_type": mime_type,
                                "size_bytes": len(media_bytes),
                                "file_name": file_name,
                                "base64": base64.b64encode(media_bytes).decode("ascii"),
                            }
                        )
                    elif media_bytes:
                        media_payload.append(
                            {
                                "type": "image_document_reference",
                                "mime_type": mime_type,
                                "size_bytes": len(media_bytes),
                                "note": "image too large to inline",
                            }
                        )
                else:
                    media_payload.append(
                        {
                            "type": "document_reference",
                            "mime_type": mime_type,
                            "size_bytes": getattr(document, "size", None),
                        }
                    )
        except Exception as exc:  # pragma: no cover - network/file issues
            logger.warning("媒体提取失败，将跳过附件: %s", exc)

        return media_payload

    async def _stats_reporter(self) -> None:
        while self.running:
            await asyncio.sleep(300)
            runtime = datetime.now() - self.stats["start_time"]
            logger.info(
                "\n📊 **运行统计** (运行时间: %s)\n"
                "   • 总接收: %s\n"
                "   • 已转发: %s\n"
                "   • 关键词过滤: %s\n"
                "   • 重复消息: %s (消息ID: %s / 内存: %s / 哈希: %s / 语义: %s / 信号: %s)\n"
                "   • 错误次数: %s\n"
                "   • 翻译成功: %s\n"
                "   • 翻译错误: %s\n"
                "   • AI 已处理: %s\n"
                "   • AI 行动: %s\n"
                "   • AI 跳过: %s\n"
                "   • AI 错误: %s\n",
                str(runtime).split(".")[0],
                self.stats["total_received"],
                self.stats["forwarded"],
                self.stats["filtered_out"],
                self.stats["duplicates"],
                self.stats["dup_message_id"],
                self.stats["dup_memory"],
                self.stats["dup_hash"],
                self.stats["dup_semantic"],
                self.stats["dup_signal"],
                self.stats["errors"],
                self.stats["translations"],
                self.stats["translation_errors"],
                self.stats["ai_processed"],
                self.stats["ai_actions"],
                self.stats["ai_skipped"],
                self.stats["ai_errors"],
            )

    async def _cleanup(self) -> None:
        logger.info("🧹 正在清理资源...")
        if self.client:
            await self.client.disconnect()
        logger.info("✅ 清理完成")

    def _is_priority_kol(self, source_name: str | None, channel_username: str | None) -> bool:
        """Check if source is in priority KOL whitelist."""
        if not self.config.PRIORITY_KOL_HANDLES:
            return False

        candidates = []
        if source_name:
            candidates.append(source_name.lower().strip())
        if channel_username:
            candidates.append(channel_username.lower().strip().lstrip("@"))

        return any(handle in self.config.PRIORITY_KOL_HANDLES for handle in candidates)


async def main() -> None:
    try:
        listener = TelegramListener()
        await listener.initialize()
        await listener.start_listening()
    except SessionPasswordNeededError:
        logger.error("❌ 账号需要两步验证密码，请手动登录一次")
        sys.exit(1)
    except PhoneCodeInvalidError:
        logger.error("❌ 手机验证码无效，请重试")
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(f"❌ 程序启动失败: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
