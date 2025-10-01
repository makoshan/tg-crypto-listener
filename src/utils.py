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


ACTION_LABELS = {
    "buy": "买入",
    "sell": "卖出",
    "observe": "观望",
}

STRENGTH_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
}

DIRECTION_LABELS = {
    "long": "做多",
    "short": "做空",
    "neutral": "中性",
}

EVENT_TYPE_LABELS = {
    "listing": "上线/挂牌",
    "delisting": "下架/退市",
    "hack": "安全/攻击",
    "regulation": "监管/政策",
    "funding": "融资/募资",
    "whale": "巨鲸动向",
    "liquidation": "清算/爆仓",
    "partnership": "合作/集成",
    "product_launch": "产品发布/主网",
    "governance": "治理提案",
    "macro": "宏观动向",
    "celebrity": "名人言论",
    "airdrop": "空投激励",
    "other": "其他",
}

RISK_FLAG_LABELS = {
    "price_volatility": "价格波动",
    "liquidity_risk": "流动性风险",
    "regulation_risk": "合规风险",
    "confidence_low": "置信度低",
    "data_incomplete": "信息不完整",
}


def format_forwarded_message(
    *,
    source_channel: str,
    timestamp: datetime,
    translated_text: str | None = None,
    original_text: str | None = None,
    show_original: bool = False,
    ai_summary: str | None = None,
    ai_action: str | None = None,
    ai_direction: str | None = None,
    ai_event_type: str | None = None,
    ai_asset: str | None = None,
    ai_asset_names: str | None = None,
    ai_confidence: float | None = None,
    ai_strength: str | None = None,
    ai_risk_flags: list[str] | None = None,
    ai_notes: str | None = None,
) -> str:
    """Compose a compact forwarding message emphasising actionable insights."""

    ai_risk_flags = ai_risk_flags or []
    ai_notes = (ai_notes or "").strip()
    ai_asset = (ai_asset or "").strip()
    ai_asset_names = (ai_asset_names or "").strip()
    translated_text = (translated_text or "").strip()
    original_text = (original_text or "").strip()

    parts: list[str] = ["🔔 **加密新闻监听**\n\n"]

    # 信号摘要：翻译文本与 AI 摘要分别列出，便于快速浏览
    summary_segments: list[str] = []
    if translated_text:
        summary_segments.append(translated_text)
    elif original_text:
        summary_segments.append(original_text)

    if ai_summary and ai_summary not in summary_segments:
        summary_segments.append(ai_summary)

    if summary_segments:
        parts.append("⚡ **信号摘要**\n")
        for segment in summary_segments:
            parts.append(f"- {segment}\n")
        parts.append("\n")

    # 操作要点，仅当有 AI 结果时展示
    if ai_summary:
        action_value = ACTION_LABELS.get(ai_action or "observe", ai_action or "observe")
        confidence_text = (
            f"{ai_confidence:.2f}" if ai_confidence is not None else "未知"
        )
        parts.append("🎯 **操作要点**\n")

        if ai_asset or ai_asset_names:
            asset_line = ai_asset
            if ai_asset_names and ai_asset:
                asset_line = f"{ai_asset_names} ({ai_asset})"
            elif ai_asset_names:
                asset_line = ai_asset_names
            elif ai_asset:
                asset_line = ai_asset
            else:
                asset_line = "未识别"
            parts.append(f"- **标的**: {asset_line}\n")

        parts.append(f"- **动作**: {action_value}")
        if ai_direction:
            direction_cn = DIRECTION_LABELS.get(ai_direction, ai_direction)
            parts[-1] += f"（方向: {direction_cn}）"
        parts[-1] += "\n"

        parts.append(f"- **置信度**: {confidence_text}")
        if ai_strength:
            strength_cn = STRENGTH_LABELS.get(ai_strength, ai_strength)
            parts[-1] += f" · 强度: {strength_cn}"
        parts[-1] += "\n"

        localized_flags = [
            RISK_FLAG_LABELS.get(flag, flag) for flag in ai_risk_flags if flag
        ]
        if localized_flags:
            parts.append(f"- ⚠️ **风险**: {'、'.join(localized_flags)}\n")

        if ai_notes:
            parts.append(f"- 📝 **备注**: {ai_notes}\n")

        parts.append("\n")

    # 来源与时间
    parts.append(f"📡 **来源**: {source_channel}\n")
    parts.append(f"🕒 **时间**: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 原文视情况展示
    if show_original and original_text:
        parts.append("\n🧾 **原文**\n")
        parts.append(f"```\n{original_text}\n```\n")

    return "".join(parts)
