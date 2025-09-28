"""Main Telegram listener entrypoint."""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

from .ai.signal_engine import AiSignalEngine, EventPayload, SignalResult
from .ai.translator import Translator
from .ai.gemini_client import AiServiceError
from .config import Config
from .forwarder import MessageForwarder
from .utils import (
    MessageDeduplicator,
    contains_keywords,
    format_forwarded_message,
    setup_logger,
)

logger = setup_logger(__name__)


class TelegramListener:
    """Set up Telethon client, filter, format, and forward messages."""

    def __init__(self) -> None:
        self.config = Config()
        self.client: TelegramClient | None = None
        self.forwarder: MessageForwarder | None = None
        self.deduplicator = MessageDeduplicator(self.config.DEDUP_WINDOW_HOURS)
        self.translator: Translator | None = None
        self.ai_engine = AiSignalEngine.from_config(self.config)
        self.running = False
        self.stats = {
            "total_received": 0,
            "filtered_out": 0,
            "duplicates": 0,
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

        self.forwarder = MessageForwarder(
            self.client,
            self.config.TARGET_CHAT_ID,
            self.config.TARGET_CHAT_ID_BACKUP,
        )

        if self.config.TRANSLATION_ENABLED:
            try:
                self.translator = Translator(
                    enabled=self.config.TRANSLATION_ENABLED,
                    api_key=self.config.DEEPL_API_KEY,
                    timeout=self.config.TRANSLATION_TIMEOUT_SECONDS,
                    api_url=self.config.DEEPL_API_URL,
                )
                if self.translator and not self.translator.enabled:
                    logger.debug("翻译模块已配置但 Deepl Key 缺失，翻译将被跳过")
            except AiServiceError as exc:
                logger.warning("翻译模块初始化失败，将使用原文: %s", exc)
                self.translator = None

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
                self.client.disconnect()

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

            logger.debug("📨 收到消息来自 %s: %.100s...", source_name, message_text)

            if not contains_keywords(message_text, self.config.FILTER_KEYWORDS):
                self.stats["filtered_out"] += 1
                logger.debug("🚫 消息被关键词过滤器拒绝")
                return

            if self.deduplicator.is_duplicate(message_text):
                self.stats["duplicates"] += 1
                logger.debug("🔄 重复消息，已跳过")
                return

            if not self.forwarder:
                logger.error("转发器未初始化，消息被丢弃")
                return

            event_time = datetime.now()

            translated_text = None
            language = "unknown"
            translation_confidence = 0.0
            keywords_hit = self._collect_keywords(message_text)

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

            display_text = message_text
            signal_result: SignalResult | None = None
            if self.ai_engine:
                payload = EventPayload(
                    text=message_text,
                    source=source_name,
                    timestamp=event_time,
                    translated_text=translated_text,
                    language=language,
                    translation_confidence=translation_confidence,
                    keywords_hit=keywords_hit,
                    historical_reference={},
                )
                signal_result = await self.ai_engine.analyse(payload)
                if signal_result:
                    self._update_ai_stats(signal_result)

            should_skip_forward = False
            if signal_result and signal_result.status != "error":
                low_confidence_skip = signal_result.confidence < 0.6
                neutral_skip = (
                    self.config.AI_SKIP_NEUTRAL_FORWARD
                    and signal_result.status == "skip"
                    and signal_result.summary != "AI disabled"
                )
                if low_confidence_skip or neutral_skip:
                    should_skip_forward = True
                    self.stats["ai_skipped"] += 1
                    logger.info(
                        "🤖 AI 评估为低优先级，跳过转发: source=%s action=%s confidence=%.2f",
                        source_name,
                        signal_result.action,
                        signal_result.confidence,
                    )

            if translated_text and translated_text != message_text:
                display_text = f"{translated_text}\n\n—— 原文 ——\n{message_text}"

            if should_skip_forward:
                await self._persist_event(
                    source_name,
                    message_text,
                    translated_text,
                    signal_result,
                    False,
                )
                return

            ai_kwargs = self._build_ai_kwargs(signal_result)

            formatted_message = format_forwarded_message(
                display_text,
                source_name,
                event_time,
                **ai_kwargs,
            )

            success = await self.forwarder.forward_message(formatted_message)
            if success:
                self.stats["forwarded"] += 1
                logger.info("📤 已转发来自 %s 的消息", source_name)
            else:
                self.stats["errors"] += 1
                logger.error("❌ 消息转发失败")

            await self._persist_event(
                source_name,
                message_text,
                translated_text,
                signal_result,
                success,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.stats["errors"] += 1
            logger.error(f"❌ 处理消息时出错: {exc}")

    def _build_ai_kwargs(self, signal_result: SignalResult | None) -> dict[str, object]:
        if not signal_result:
            return {}
        if signal_result.status == "error":
            return {}
        if signal_result.status != "success":
            return {}
        if not signal_result.summary:
            return {}
        return {
            "ai_summary": signal_result.summary,
            "ai_action": signal_result.action,
            "ai_direction": signal_result.direction,
            "ai_event_type": signal_result.event_type,
            "ai_asset": signal_result.asset,
            "ai_confidence": signal_result.confidence,
            "ai_strength": signal_result.strength,
            "ai_risk_flags": signal_result.risk_flags,
            "ai_notes": signal_result.notes,
        }

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
    ) -> None:
        """Reserved for future Supabase integration."""
        _ = (source_name, original_text, translated_text, signal_result, forwarded)
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

    async def _stats_reporter(self) -> None:
        while self.running:
            await asyncio.sleep(300)
            runtime = datetime.now() - self.stats["start_time"]
            logger.info(
                "\n📊 **运行统计** (运行时间: %s)\n   • 总接收: %s\n   • 已转发: %s\n   • 关键词过滤: %s\n   • 重复消息: %s\n   • 错误次数: %s\n   • 翻译成功: %s\n   • 翻译错误: %s\n   • AI 已处理: %s\n   • AI 行动: %s\n   • AI 跳过: %s\n   • AI 错误: %s\n",
                str(runtime).split(".")[0],
                self.stats["total_received"],
                self.stats["forwarded"],
                self.stats["filtered_out"],
                self.stats["duplicates"],
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
