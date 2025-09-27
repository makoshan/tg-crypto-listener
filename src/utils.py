"""Utility helpers for logging, dedupe, and formatting."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Set

import colorlog


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Configure a color logger that also writes to file."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    console_handler = colorlog.StreamHandler()
    console_handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
    )
    logger.addHandler(console_handler)

    Path("./logs").mkdir(exist_ok=True)
    file_handler = logging.FileHandler("./logs/app.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(file_handler)

    return logger


class MessageDeduplicator:
    """Deduplicate messages by hash within a time window."""

    def __init__(self, window_hours: int = 24):
        self.seen_hashes: Dict[str, datetime] = {}
        self.window_hours = window_hours

    def is_duplicate(self, text: str) -> bool:
        """Return True if the message text appeared recently."""
        self._cleanup_expired()

        message_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        if message_hash in self.seen_hashes:
            return True

        self.seen_hashes[message_hash] = datetime.now()
        return False

    def _cleanup_expired(self) -> None:
        cutoff = datetime.now() - timedelta(hours=self.window_hours)
        expired = [key for key, timestamp in self.seen_hashes.items() if timestamp < cutoff]
        for key in expired:
            del self.seen_hashes[key]


def contains_keywords(text: str, keywords: Set[str]) -> bool:
    """Check if text contains any keyword (case-insensitive)."""
    if not keywords:
        return True

    text_lower = text.lower()
    return any(keyword in text_lower for keyword in keywords)


def format_forwarded_message(
    original_text: str,
    source_channel: str,
    timestamp: datetime,
    ai_summary: str | None = None,
    ai_action: str | None = None,
    ai_confidence: float | None = None,
    ai_strength: str | None = None,
    ai_risk_flags: list[str] | None = None,
    ai_notes: str | None = None,
) -> str:
    """Return formatted message ready for forwarding."""
    ai_risk_flags = ai_risk_flags or []
    ai_notes = (ai_notes or "").strip()

    parts = [
        "🔔 **加密新闻监听**\n\n",
        f"📺 **来源**: {source_channel}\n",
        f"⏰ **时间**: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n",
        "📝 **内容**:\n",
        f"{original_text}\n\n",
    ]

    if ai_summary:
        confidence_text = (
            f"置信度 {ai_confidence:.2f}"
            if ai_confidence is not None
            else "置信度未知"
        )
        strength_text = f"强度 {ai_strength}" if ai_strength else ""
        meta = " / ".join(filter(None, [confidence_text, strength_text]))
        action_text = f"建议动作: {ai_action}" if ai_action else "建议动作: 观察"
        action_line = f"{action_text} ({meta})" if meta else action_text
        parts.extend(
            [
                "🤖 **AI 信号**:\n",
                f"{ai_summary}\n",
                f"{action_line}\n",
            ]
        )
        if ai_notes:
            parts.append(f"说明: {ai_notes}\n")
        if ai_risk_flags:
            parts.append(f"风险提示: {', '.join(ai_risk_flags)}\n")
        parts.append("\n")

    return "".join(parts)
