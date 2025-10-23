"""Test Telegram Bot sending functionality."""

import asyncio
from telegram import Bot


async def test_bot_send():
    """Send a test message via Bot."""
    bot_token = "8055941192:AAEFHI7ugqsPaffGUOX-dJyiBqcu-ZbAGEc"
    chat_id = "74347848"

    bot = Bot(token=bot_token)

    test_message = """🧪 Bot 测试消息

这是一条测试消息，验证 Bot 推送功能是否正常工作。

📊 测试信息：
• Bot Token: ✅ 已配置
• Chat ID: 74347848 (@makoshan)
• 推送状态: 正常

如果你收到这条消息，说明 Bot 配置成功！🎉"""

    try:
        message = await bot.send_message(
            chat_id=chat_id,
            text=test_message,
        )
        print(f"✅ 测试消息发送成功！")
        print(f"   Message ID: {message.message_id}")
        print(f"   发送时间: {message.date}")
        print(f"\n请检查 Telegram 查看是否收到消息。")
        return True
    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(test_bot_send())
