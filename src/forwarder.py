"""Message forwarding logic."""

from __future__ import annotations

import asyncio
from typing import Optional

from telethon import TelegramClient
from telethon.errors import ChatWriteForbiddenError, FloodWaitError

from .utils import setup_logger
from .email_sender import EmailSender
from .bot_sender import BotSender

logger = setup_logger(__name__)


class MessageForwarder:
    """Forward formatted messages to primary/backup chats."""

    def __init__(
        self,
        client: TelegramClient,
        target_chat_id: str,
        backup_chat_id: Optional[str] = None,
        cooldown_seconds: float = 1.0,
        email_sender: Optional[EmailSender] = None,
        bot_sender: Optional[BotSender] = None,
        forward_to_channel_enabled: bool = True,
    ) -> None:
        self.client = client
        self.target_chat_id = target_chat_id
        self.backup_chat_id = backup_chat_id
        self.retry_count = 3
        self.retry_delay = 5
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self.email_sender = email_sender
        self.bot_sender = bot_sender
        self.forward_to_channel_enabled = forward_to_channel_enabled

    async def forward_message(
        self,
        formatted_message: str,
        *,
        link_preview: bool = False,
    ) -> bool:
        """Attempt to forward message to primary chat, then backup."""
        telegram_sent = False

        # Skip channel forwarding if disabled
        if not self.forward_to_channel_enabled:
            logger.info("📢 频道转发已禁用，跳过转发步骤")
            telegram_sent = True  # Set to True to trigger bot/email notifications
        else:
            # Original channel forwarding logic
            for attempt in range(self.retry_count):
                try:
                    await self.client.send_message(
                        self.target_chat_id,
                        formatted_message,
                        link_preview=link_preview,
                    )
                    logger.info(f"✅ 消息已转发到 {self.target_chat_id}")
                    telegram_sent = True
                    if self.cooldown_seconds:
                        await asyncio.sleep(self.cooldown_seconds)
                    break
                except FloodWaitError as exc:
                    logger.warning(f"⚠️ 触发频率限制，等待 {exc.seconds} 秒")
                    await asyncio.sleep(exc.seconds)
                except ChatWriteForbiddenError:
                    logger.error(f"❌ 无权限发送到 {self.target_chat_id}")
                    if self.backup_chat_id:
                        telegram_sent = await self._try_backup_channel(
                            formatted_message,
                            link_preview=link_preview,
                        )
                    break
                except Exception as exc:  # pylint: disable=broad-except
                    logger.error(f"❌ 发送失败 (尝试 {attempt + 1}/{self.retry_count}): {exc}")
                    if attempt < self.retry_count - 1:
                        await asyncio.sleep(self.retry_delay)

            # Try backup if primary failed
            if not telegram_sent and self.backup_chat_id:
                telegram_sent = await self._try_backup_channel(
                    formatted_message,
                    link_preview=link_preview,
                )

        # Send email notification if enabled
        if self.email_sender and telegram_sent:
            await self.email_sender.send_email(
                subject="🚀 加密货币信号通知",
                body=formatted_message,
                html=False,
            )

        # Send bot notification if enabled
        if self.bot_sender and telegram_sent:
            await self.bot_sender.send_message(
                message=formatted_message,
                disable_web_page_preview=not link_preview,
            )

        return telegram_sent

    async def _try_backup_channel(
        self,
        message: str,
        *,
        link_preview: bool = False,
    ) -> bool:
        """Attempt to forward message to backup chat."""
        if not self.backup_chat_id:
            return False

        try:
            await self.client.send_message(
                self.backup_chat_id,
                f"🔄 备用频道转发:\n\n{message}",
                link_preview=link_preview,
            )
            logger.info(f"✅ 已转发到备用频道 {self.backup_chat_id}")
            if self.cooldown_seconds:
                await asyncio.sleep(self.cooldown_seconds)
            return True
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"❌ 备用频道也发送失败: {exc}")
            return False
