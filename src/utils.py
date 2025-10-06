"""Utility helpers for logging, dedupe, and formatting."""

from __future__ import annotations

import logging
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Set

import os
import colorlog


def setup_logger(name: str, level: str = None) -> logging.Logger:
    """Configure a color logger that also writes to file."""
    # 从环境变量读取日志级别，默认为 INFO
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

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


def compute_sha256(text: str) -> str:
    """Return SHA256 hash for raw text."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_canonical_hash(text: str) -> str:
    """Return SHA256 hash after stripping whitespace and URLs."""
    if not text:
        return ""
    normalized = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def compute_embedding(text: str, api_key: str, model: str = "text-embedding-3-small") -> list[float] | None:
    """Generate OpenAI embedding vector for text.

    Args:
        text: Input text to embed (will be truncated to 8000 chars)
        api_key: OpenAI API key
        model: Embedding model name

    Returns:
        List of floats (1536 dimensions for text-embedding-3-small) or None on error
    """
    if not text or not text.strip():
        return None

    if not api_key:
        return None

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)

        # Truncate text to avoid token limits
        truncated_text = text[:8000]

        response = await client.embeddings.create(
            model=model,
            input=truncated_text
        )

        return response.data[0].embedding

    except ImportError:
        logger = setup_logger(__name__)
        logger.warning("OpenAI SDK not installed, skipping embedding generation")
        return None
    except Exception as exc:
        logger = setup_logger(__name__)
        logger.warning("Embedding generation failed: %s", exc)
        return None


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
    show_original: bool = False,  # 保留签名但不再展示原文
    show_translation: bool = True,
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
    context_source: str | None = None,
) -> str:
    """Compose a compact forwarding message emphasising actionable insights."""

    ai_risk_flags = ai_risk_flags or []
    ai_notes = (ai_notes or "").strip()
    ai_asset = (ai_asset or "").strip()
    ai_asset_names = (ai_asset_names or "").strip()
    translated_text = (translated_text or "").strip()
    original_text = (original_text or "").strip()

    if not show_translation:
        translated_text = ""

    parts: list[str] = ["🔔 加密新闻监听\n"]

    # 信号摘要：翻译文本与 AI 摘要分别列出，清晰紧凑
    def _normalize_for_compare(text: str) -> str:
        stripped = re.sub(r"\s+", "", text)
        stripped = re.sub(r'[，,。\\.!？?：:；;"\'“”‘’`·•\-]', "", stripped)
        return stripped.lower()

    if translated_text and original_text:
        if _normalize_for_compare(translated_text) == _normalize_for_compare(original_text):
            translated_text = ""

    summary_text = (ai_summary or "").strip()
    if not summary_text:
        summary_text = translated_text or original_text
    summary_text = (summary_text or "暂无摘要").replace("\n", " ").strip()

    parts: list[str] = []

    parts.append("⚡ 信号")
    if context_source:
        source_display = context_source
    else:
        source_display = source_channel
    parts.append(f" {source_display}：{summary_text}")
    if ai_notes:
        parts.append("")
        parts.append(f"备注: {ai_notes}")
        parts.append("")

    # 操作要点，仅当有 AI 结果时展示
    if ai_summary:
        action_key = (ai_action or "observe").lower()
        action_value = ACTION_LABELS.get(action_key, ai_action or "observe")
        confidence_text = (
            f"{ai_confidence:.2f}" if ai_confidence is not None else "未知"
        )
        parts.append("🎯 操作")

        asset_line = ""
        if ai_asset or ai_asset_names:
            asset_line = ai_asset
            if ai_asset_names and ai_asset:
                asset_line = f"{ai_asset_names} ({ai_asset})"
            elif ai_asset_names:
                asset_line = ai_asset_names
            elif ai_asset:
                asset_line = ai_asset

        direction_key = (ai_direction or "").lower()
        direction_cn = DIRECTION_LABELS.get(direction_key, ai_direction) if ai_direction else None
        strength_key = (ai_strength or "").lower()
        strength_cn = STRENGTH_LABELS.get(strength_key, ai_strength) if ai_strength else None

        line_parts: list[str] = []
        if asset_line:
            line_parts.append(asset_line)

        if action_key == "observe":
            line_parts.append(f"状态: {action_value}")
        else:
            line_parts.append(action_value)
            if direction_cn:
                line_parts.append(f"方向: {direction_cn}")

        line_parts.append(f"置信度: {confidence_text}")
        if strength_cn:
            line_parts.append(f"强度: {strength_cn}")

        parts.append("- " + "，".join(line_parts))

        event_type_label = EVENT_TYPE_LABELS.get(ai_event_type or "", None)
        if event_type_label:
            parts.append(f"- 事件类型: {event_type_label}")

        localized_flags = [
            RISK_FLAG_LABELS.get(flag, flag) for flag in ai_risk_flags if flag
        ]
        if localized_flags:
            parts.append(f"- 风险: {'、'.join(localized_flags)}")

        parts.append("")

    # 时间
    parts.append("————————————")
    parts.append(timestamp.strftime("%Y-%m-%d %H:%M:%S"))
    if context_source and context_source != source_channel:
        parts[-1] += f" | 来源频道: {source_channel}"

    return "\n".join(parts)
