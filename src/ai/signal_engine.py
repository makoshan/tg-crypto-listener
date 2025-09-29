"""AI Signal Engine orchestrating OpenAI-compatible inference."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from ..utils import setup_logger
from .gemini_client import AiServiceError

try:  # pragma: no cover - optional dependency
    import httpx
except ImportError:  # pragma: no cover - runtime fallback
    httpx = None  # type: ignore

logger = setup_logger(__name__)

ALLOWED_ACTIONS = {"buy", "sell", "observe"}
ALLOWED_DIRECTIONS = {"long", "short", "neutral"}
ALLOWED_STRENGTH = {"low", "medium", "high"}
ALLOWED_EVENT_TYPES = {
    "listing",
    "delisting",
    "hack",
    "regulation",
    "funding",
    "whale",
    "liquidation",
    "partnership",
    "product_launch",
    "governance",
    "macro",
    "celebrity",
    "airdrop",
    "other",
}
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
    media: list[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SignalResult:
    """AI decision packaged for downstream consumers."""

    status: str
    summary: str = ""
    event_type: str = "other"
    asset: str = ""
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


@dataclass
class OpenAIChatResponse:
    """Structured response returned by OpenAI-compatible models."""

    text: str


class OpenAIChatClient:
    """Generic client for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        *,
        base_url: str,
        timeout: float,
        max_retries: int,
        retry_backoff_seconds: float,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if not api_key:
            raise AiServiceError("AI API key is required")
        if httpx is None:
            raise AiServiceError("httpx 未安装，请先在环境中安装该依赖")

        normalized_base = (base_url or "").strip()
        if not normalized_base:
            normalized_base = "https://api.openai.com/v1"
        self._endpoint = normalized_base.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model_name
        self._timeout = float(timeout)
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff = max(0.0, float(retry_backoff_seconds))
        self._headers: Dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            for key, value in extra_headers.items():
                if key and value is not None:
                    self._headers[str(key)] = str(value)

    async def generate_signal(self, messages: Sequence[Dict[str, str]]) -> OpenAIChatResponse:
        """Execute prompt against OpenAI-compatible API and return text."""

        if not messages:
            raise AiServiceError("消息列表不能为空")

        payload = {
            "model": self._model,
            "messages": list(messages),
        }

        last_exc: Exception | None = None
        last_error_message = "AI 调用失败"
        last_error_temporary = False

        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._endpoint,
                        headers=self._headers,
                        json=payload,
                    )
                response.raise_for_status()
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException as exc:  # type: ignore[attr-defined]
                last_exc = exc
                last_error_message = "AI 请求超时"
                last_error_temporary = True
                logger.warning(
                    "AI 请求超时 (attempt %s/%s)",
                    attempt + 1,
                    self._max_retries + 1,
                )
            except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
                last_exc = exc
                status_code = exc.response.status_code
                last_error_message = f"AI 服务端返回错误状态码: {status_code}"
                last_error_temporary = status_code == 429 or 500 <= status_code < 600
                logger.warning(
                    "AI HTTP 状态错误 (attempt %s/%s): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    last_error_message,
                )
                logger.debug("AI 响应内容: %s", exc.response.text)
            except httpx.RequestError as exc:  # type: ignore[attr-defined]
                last_exc = exc
                last_error_message = "AI 网络连接异常"
                last_error_temporary = True
                logger.warning(
                    "AI 网络异常 (attempt %s/%s): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                )
            else:
                try:
                    data = response.json()
                except json.JSONDecodeError as exc:
                    raise AiServiceError("AI 返回非 JSON 内容") from exc

                choices = data.get("choices", [])
                if not choices:
                    raise AiServiceError("AI 返回缺少 choices 字段")
                first_choice = choices[0] or {}
                message = first_choice.get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
                if not content:
                    raise AiServiceError("AI 返回空内容")
                return OpenAIChatResponse(text=str(content))

            if attempt < self._max_retries and self._retry_backoff > 0:
                backoff = self._retry_backoff * (2 ** attempt)
                logger.debug(
                    "AI 将在 %.2f 秒后重试 (attempt %s/%s)",
                    backoff,
                    attempt + 1,
                    self._max_retries + 1,
                )
                await asyncio.sleep(backoff)

        raise AiServiceError(last_error_message, temporary=last_error_temporary) from last_exc


class AiSignalEngine:
    """Coordinate optional AI powered signal generation."""

    def __init__(
        self,
        enabled: bool,
        client: Optional[OpenAIChatClient],
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

        provider_raw = str(getattr(config, "AI_PROVIDER", "gemini")).strip().lower()
        provider_alias = {
            "chatgpt": "openai",
            "gpt": "openai",
            "openai": "openai",
            "deepseek": "deepseek",
            "qwen": "qwen",
            "千问": "qwen",
            "qianwen": "qwen",
            "gemini": "gemini",
        }
        provider = provider_alias.get(provider_raw, provider_raw or "gemini")

        api_key = (
            getattr(config, "AI_API_KEY", None)
            or getattr(config, "GEMINI_API_KEY", None)
            or ""
        )

        if not api_key:
            logger.warning("AI 已启用但未提供 API Key，自动降级为跳过 AI 分析")
            return cls(False, None, getattr(config, "AI_SIGNAL_THRESHOLD", 0.0), asyncio.Semaphore(1))

        base_url = getattr(config, "AI_BASE_URL", "").strip()
        if not base_url:
            base_url = {
                "openai": "https://api.openai.com/v1",
                "deepseek": "https://api.deepseek.com",
                "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            }.get(provider, "https://api.openai.com/v1")

        extra_headers: Dict[str, str] = {}
        raw_headers = getattr(config, "AI_EXTRA_HEADERS", "")
        if raw_headers:
            try:
                parsed = json.loads(raw_headers)
            except (TypeError, ValueError):
                logger.warning("AI_EXTRA_HEADERS 不是有效的 JSON，将忽略该配置")
            else:
                if isinstance(parsed, dict):
                    extra_headers = {
                        str(key): str(value)
                        for key, value in parsed.items()
                        if value is not None
                    }

        try:
            client = OpenAIChatClient(
                api_key=str(api_key),
                model_name=getattr(config, "AI_MODEL_NAME", "gpt-4o-mini"),
                base_url=base_url,
                timeout=getattr(config, "AI_TIMEOUT_SECONDS", 8.0),
                max_retries=getattr(config, "AI_RETRY_ATTEMPTS", 1),
                retry_backoff_seconds=getattr(config, "AI_RETRY_BACKOFF_SECONDS", 1.5),
                extra_headers=extra_headers or None,
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

        messages = build_signal_prompt(payload)
        logger.debug(
            "AI 分析开始: source=%s len=%d lang=%s preview=%s",
            payload.source,
            len(payload.text),
            payload.language,
            payload.text[:80].replace("\n", " "),
        )

        async with self._semaphore:
            try:
                response = await self._client.generate_signal(messages)
            except AiServiceError as exc:
                is_temporary = getattr(exc, "temporary", False)
                logger.warning(
                    "AI 调用失败: %s",
                    exc,
                    exc_info=not is_temporary,
                )
                return SignalResult(status="error", error=str(exc))

        logger.debug("AI 返回长度: %d", len(response.text))
        return self._parse_response(response)

    def _parse_response(self, response: OpenAIChatResponse) -> SignalResult:
        raw_text = response.text.strip()
        normalized_text = self._prepare_json_text(raw_text)

        asset = ""
        try:
            data = json.loads(normalized_text)
            logger.debug(
                "AI JSON 解析成功: action=%s confidence=%.2f",
                data.get("action"),
                float(data.get("confidence", 0.0)),
            )
            summary = str(data.get("summary", "")).strip()
            event_type = str(data.get("event_type", "other")).lower()
            asset_field = data.get("asset", "")
            action = str(data.get("action", "observe")).lower()
            direction = str(data.get("direction", "neutral")).lower()
            strength = str(data.get("strength", "low")).lower()
            confidence = float(data.get("confidence", 0.0))
            risk_flags = data.get("risk_flags", []) or []
            if not isinstance(risk_flags, list):
                risk_flags = [str(risk_flags)]
            notes = str(data.get("notes", "")).strip()
            if isinstance(asset_field, (list, tuple)):
                asset = ",".join(str(item).strip() for item in asset_field if str(item).strip())
            else:
                asset = str(asset_field).strip()
        except json.JSONDecodeError:
            logger.debug(
                "AI 返回无法解析为 JSON，使用纯文本摘要: %s",
                normalized_text[:120].replace("\n", " "),
            )
            summary = "AI 返回格式异常，已忽略原始内容"
            event_type = "other"
            asset = ""
            action = "observe"
            direction = "neutral"
            strength = "low"
            confidence = 0.0
            risk_flags = ["confidence_low"]
            notes = ""

        event_type = event_type if event_type in ALLOWED_EVENT_TYPES else "other"
        action = action if action in ALLOWED_ACTIONS else "observe"
        direction = direction if direction in ALLOWED_DIRECTIONS else "neutral"
        if strength not in ALLOWED_STRENGTH:
            strength = "low"

        asset = asset.upper().strip()
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

        # Enforce a 60% floor even if configuration threshold is lower
        effective_threshold = max(self._threshold, 0.6)
        status = "success" if (confidence >= effective_threshold and action in {"buy", "sell"}) else "skip"

        return SignalResult(
            status=status,
            summary=summary,
            event_type=event_type,
            asset=asset,
            action=action,
            direction=direction,
            confidence=confidence,
            strength=strength,
            risk_flags=filtered_flags,
            raw_response=raw_text,
            notes=notes,
        )

    @staticmethod
    def _prepare_json_text(text: str) -> str:
        """Strip Markdown/code fences and return best-effort JSON payload."""
        candidate = text.strip()
        if candidate.startswith("```") and candidate.endswith("```"):
            candidate = candidate[3:-3].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip("\n :")
        if candidate.lower().startswith("python"):
            candidate = candidate[6:].strip("\n :")
        candidate = candidate.lstrip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate
        return candidate


def build_signal_prompt(payload: EventPayload) -> list[dict[str, str]]:
    context = {
        "source": payload.source,
        "timestamp": payload.timestamp.isoformat(),
        "language": payload.language,
        "translation_confidence": payload.translation_confidence,
        "original_text": payload.text,
        "translated_text": payload.translated_text or payload.text,
        "keywords_hit": payload.keywords_hit,
        "historical_reference": payload.historical_reference,
        "media_attachments": payload.media,
    }

    context_json = json.dumps(context, ensure_ascii=False)

    system_prompt = (
        "你是加密交易台的资深分析师。"
        "需从多语种快讯中快速提炼可交易信号，并严格使用 JSON 结构输出结果。"
        "输出字段固定为 summary、event_type、asset、action、direction、confidence、strength、risk_flags、notes。"
        "event_type 仅能取 listing、delisting、hack、regulation、funding、whale、liquidation、partnership、product_launch、governance、macro、celebrity、airdrop、other。"
        "action 为 buy、sell 或 observe；direction 为 long、short 或 neutral。"
        "confidence 范围 0-1，保留两位小数，并与 high/medium/low 的 strength 保持一致性。"
        "risk_flags 为数组，枚举 price_volatility、liquidity_risk、regulation_risk、confidence_low、data_incomplete。"
        "所有字符串输出使用简体中文，禁止返回 Markdown、额外文本或解释。"
    )

    user_prompt = (
        "请结合以下事件上下文给出最具操作性的建议，若包含多条信息需综合判断：\n"
        f"```json\n{context_json}\n```\n"
        "返回仅包含上述字段的 JSON 字符串，必要时在 notes 中说明原因或疑点。"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
