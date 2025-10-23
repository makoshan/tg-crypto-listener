"""Telegram Bot notification sender."""

from __future__ import annotations

from telegram import Bot
from telegram.error import TelegramError

from .utils import setup_logger

logger = setup_logger(__name__)


class BotSender:
    """Send notifications via Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        user_chat_id: str,
        enabled: bool = True,
    ) -> None:
        self.bot_token = bot_token
        self.user_chat_id = user_chat_id
        self.enabled = enabled
        self.bot = None

        if not self.enabled:
            logger.info("🤖 Bot 推送已禁用")
        elif not all([bot_token, user_chat_id]):
            logger.warning("⚠️ Bot 配置不完整，Bot 推送将被禁用")
            self.enabled = False
        else:
            self.bot = Bot(token=bot_token)
            logger.info(f"🤖 Bot 推送已启用，目标用户: {user_chat_id}")

    async def send_message(
        self,
        message: str,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> bool:
        """Send a message via Telegram Bot.

        Args:
            message: Message text to send
            parse_mode: Parse mode (Markdown, MarkdownV2, HTML, or None)
            disable_web_page_preview: Disable link previews

        Returns:
            True if message sent successfully, False otherwise
        """
        if not self.enabled or not self.bot:
            return False

        try:
            await self.bot.send_message(
                chat_id=self.user_chat_id,
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            logger.info(f"✅ Bot 消息已发送到用户 {self.user_chat_id}")
            return True

        except TelegramError as exc:
            logger.error(f"❌ Bot 消息发送失败: {exc}")
            return False
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"❌ Bot 消息发送异常: {exc}")
            return False
