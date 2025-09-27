"""AI Signal Engine orchestrating Gemini inference."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from ..utils import setup_logger
from .gemini_client import AiServiceError, GeminiClient, GeminiResponse

logger = setup_logger(__name__)

ALLOWED_ACTIONS = {"buy", "sell", "observe"}
ALLOWED_DIRECTIONS = {"long", "short", "neutral"}
ALLOWED_STRENGTH = {"low", "medium", "high"}
ALLOWED_RISK_FLAGS = {
    "price_volatility",
    "liquidity_risk",
    "regulation_risk",
    "confidence_low",
    "data_incomplete",
}


@dataclass
class EventPayload:
    """Normalized data sent to the AI layer."""

    text: str
    source: str
    timestamp: datetime
    translated_text: Optional[str] = None
    language: str = "unknown"
    translation_confidence: float = 0.0
    keywords_hit: list[str] = field(default_factory=list)
    historical_reference: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalResult:
    """AI decision packaged for downstream consumers."""

    status: str
    summary: str = ""
    action: str = "observe"
    direction: str = "neutral"
    confidence: float = 0.0
    strength: str = "low"
    risk_flags: list[str] = field(default_factory=list)
    raw_response: str = ""
    notes: str = ""
    error: Optional[str] = None

    @property
    def should_execute_hot_path(self) -> bool:
        return (
            self.status == "success"
            and self.action in {"buy", "sell"}
        )


class AiSignalEngine:
    """Coordinate optional AI powered signal generation."""

    def __init__(
        self,
        enabled: bool,
        client: Optional[GeminiClient],
        threshold: float,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self.enabled = enabled and client is not None
        self._client = client
        self._threshold = threshold
        self._semaphore = semaphore
        if not self.enabled:
            logger.debug("AiSignalEngine 未启用或缺少客户端，所有消息将跳过 AI 分析")

    @classmethod
    def from_config(cls, config: Any) -> "AiSignalEngine":
        if not getattr(config, "AI_ENABLED", False):
            logger.debug("配置关闭 AI 功能，采用传统转发流程")
            return cls(False, None, getattr(config, "AI_SIGNAL_THRESHOLD", 0.0), asyncio.Semaphore(1))

        try:
            client = GeminiClient(
                api_key=getattr(config, "GEMINI_API_KEY", ""),
                model_name=getattr(config, "AI_MODEL_NAME", "gemini-2.5-flash-lite"),
                timeout=getattr(config, "AI_TIMEOUT_SECONDS", 8.0),
            )
        except AiServiceError as exc:
            logger.warning("AI 初始化失败，将以降级模式运行: %s", exc, exc_info=True)
            return cls(False, None, getattr(config, "AI_SIGNAL_THRESHOLD", 0.0), asyncio.Semaphore(1))

        concurrency = max(1, int(getattr(config, "AI_MAX_CONCURRENCY", 1)))
        return cls(True, client, getattr(config, "AI_SIGNAL_THRESHOLD", 0.0), asyncio.Semaphore(concurrency))

    async def analyse(self, payload: EventPayload) -> SignalResult:
        if not self.enabled or not self._client:
            logger.debug("AI 已禁用，source=%s 的消息直接跳过", payload.source)
            return SignalResult(status="skip", summary="AI disabled")

        prompt = build_signal_prompt(payload)
        logger.debug(
            "AI 分析开始: source=%s len=%d lang=%s preview=%s",
            payload.source,
            len(payload.text),
            payload.language,
            payload.text[:80].replace("\n", " "),
        )

        async with self._semaphore:
            try:
                response = await self._client.generate_signal(prompt)
            except AiServiceError as exc:
                logger.warning("AI 调用失败: %s", exc, exc_info=True)
                return SignalResult(status="error", error=str(exc))

        logger.debug("AI 返回长度: %d", len(response.text))
        return self._parse_response(response)

    def _parse_response(self, response: GeminiResponse) -> SignalResult:
        raw_text = response.text.strip()

        try:
            data = json.loads(raw_text)
            logger.debug(
                "AI JSON 解析成功: action=%s confidence=%.2f",
                data.get("action"),
                float(data.get("confidence", 0.0)),
            )
            summary = str(data.get("summary", "")).strip()
            action = str(data.get("action", "observe")).lower()
            direction = str(data.get("direction", "neutral")).lower()
            strength = str(data.get("strength", "low")).lower()
            confidence = float(data.get("confidence", 0.0))
            risk_flags = data.get("risk_flags", []) or []
            if not isinstance(risk_flags, list):
                risk_flags = [str(risk_flags)]
            notes = str(data.get("notes", "")).strip()
        except json.JSONDecodeError:
            logger.debug(
                "AI 返回无法解析为 JSON，使用纯文本摘要: %s",
                raw_text[:120].replace("\n", " "),
            )
            summary = raw_text
            action = "observe"
            direction = "neutral"
            strength = "low"
            confidence = 0.0
            risk_flags = []
            notes = ""

        action = action if action in ALLOWED_ACTIONS else "observe"
        direction = direction if direction in ALLOWED_DIRECTIONS else "neutral"
        if strength not in ALLOWED_STRENGTH:
            strength = "low"

        confidence = max(0.0, min(1.0, round(confidence, 2)))
        filtered_flags = []
        for flag in risk_flags:
            if not isinstance(flag, str):
                continue
            value = flag.strip()
            if value in ALLOWED_RISK_FLAGS:
                filtered_flags.append(value)
        if not filtered_flags and confidence < 0.3:
            filtered_flags.append("confidence_low")

        status = "success" if (confidence >= self._threshold and action in {"buy", "sell"}) else "skip"

        return SignalResult(
            status=status,
            summary=summary,
            action=action,
            direction=direction,
            confidence=confidence,
            strength=strength,
            risk_flags=filtered_flags,
            raw_response=raw_text,
            notes=notes,
        )


def build_signal_prompt(payload: EventPayload) -> str:
    context = {
        "source": payload.source,
        "timestamp": payload.timestamp.isoformat(),
        "language": payload.language,
        "translation_confidence": payload.translation_confidence,
        "original_text": payload.text,
        "translated_text": payload.translated_text or payload.text,
        "keywords_hit": payload.keywords_hit,
        "historical_reference": payload.historical_reference,
    }

    context_json = json.dumps(context, ensure_ascii=False)

    return (
        "你是加密交易台的分析师，需从多语种快讯中提取可交易信号。"
        "请基于给定 JSON 进行分析，若文本包含多条信息，请逐条判断后给出综合结论。"
        "输出要求：仅返回 JSON 字符串，不得添加 `json`、Markdown 或解释性文字。"
        "JSON 字段如下：summary, event_type(listing|delisting|hack|regulation|funding|whale|liquidation|other),"
        "action(buy|sell|observe), direction(long|short|neutral), confidence(0-1，保留两位小数),"
        "strength(low|medium|high), risk_flags(数组，取值限于 price_volatility/liquidity_risk/regulation_risk/confidence_low/data_incomplete),"
        "notes(简短中文描述，可为空)。"
        "规则：1) 金额或影响力不足(如<3000美元、信息模糊) → action=observe, confidence≤0.3。"
        "2) liquidations/whale 需对比 historical_reference 判定是否显著，说明理由。"
        "3) 若存在宏观/政策/名人言论等潜在驱动，请在 notes 中指出可能的交易机会。"
        "4) 信息缺失或不确定时，在 risk_flags 加 confidence_low 或 data_incomplete。"
        "5) 输出必须为简体中文。"
        "输入 JSON："
        f"{context_json}"
    )
