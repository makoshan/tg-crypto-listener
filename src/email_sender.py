"""Simple email notification sender."""

from __future__ import annotations

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .utils import setup_logger

logger = setup_logger(__name__)


class EmailSender:
    """Send email notifications using SMTP."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        from_email: str,
        password: str,
        to_email: str,
        enabled: bool = True,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.from_email = from_email
        self.password = password
        self.to_email = to_email
        self.enabled = enabled

        if not self.enabled:
            logger.info("📧 邮件推送已禁用")
        elif not all([smtp_host, from_email, password, to_email]):
            logger.warning("⚠️ 邮件配置不完整，邮件推送将被禁用")
            self.enabled = False
        else:
            logger.info(f"📧 邮件推送已启用，目标: {to_email}")

    async def send_email(
        self,
        subject: str,
        body: str,
        html: bool = False,
    ) -> bool:
        """Send an email notification.

        Args:
            subject: Email subject
            body: Email body (plain text or HTML)
            html: If True, treat body as HTML

        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_email
            msg["To"] = self.to_email
            msg["Subject"] = subject

            # Attach body
            mime_type = "html" if html else "plain"
            msg.attach(MIMEText(body, mime_type, "utf-8"))

            # Send via SMTP (choose SSL or STARTTLS based on port)
            if self.smtp_port == 465:
                # Use SSL for port 465 (commonly used by 163, QQ, etc.)
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                    server.login(self.from_email, self.password)
                    server.send_message(msg)
            else:
                # Use STARTTLS for port 587/25 (Gmail, Outlook, etc.)
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.from_email, self.password)
                    server.send_message(msg)

            logger.info(f"✅ 邮件已发送: {subject}")
            return True

        except Exception as exc:  # pylint: disable=broad-except
            logger.error(f"❌ 邮件发送失败: {exc}")
            return False
