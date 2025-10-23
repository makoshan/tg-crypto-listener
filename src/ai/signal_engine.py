"""AI Signal Engine orchestrating OpenAI-compatible inference."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from ..utils import analyze_event_intensity, setup_logger
from ..memory import MemoryBackendBundle, create_memory_backend
from .gemini_client import AiServiceError, GeminiClient
from .deep_analysis import (
    DeepAnalysisEngine,
    DeepAnalysisError,
    create_deep_analysis_engine,
)

try:  # pragma: no cover - optional dependency
    import httpx
except ImportError:  # pragma: no cover - runtime fallback
    httpx = None  # type: ignore

logger = setup_logger(__name__)

ALLOWED_ACTIONS = {"buy", "sell", "observe"}
ALLOWED_DIRECTIONS = {"long", "short", "neutral"}
ALLOWED_STRENGTH = {"low", "medium", "high"}
ALLOWED_TIMEFRAMES = {"short", "medium", "long"}  # 短期（<1周）、中期（1周-1月）、长期（>1月）
NO_ASSET_TOKENS = {
    "",
    "NONE",
    "无",
    "NA",
    "N/A",
    "GENERAL",
    "GENERAL_CRYPTO",
    "CRYPTO",
    "MARKET",
    "MACRO",
}
FORBIDDEN_ASSET_PREFIXES = {"SP", "DJ", "ND", "HSI", "CSI", "FTSE"}
FORBIDDEN_ASSET_CODES = {
    "SPX",
    "SP500",
    "S&P500",
    "TSLA",
    "AAPL",
    "MSFT",
    "META",
    "AMZN",
    "NVDA",
    "BABA",
}
ASSET_CODE_REGEX = re.compile(r"^[A-Z0-9]{2,10}$")
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
    "scam_alert",      # 疑似骗局或高风险投机（rug pull、pump & dump 等）
    "other",
}
ALLOWED_RISK_FLAGS = {
    "price_volatility",
    "liquidity_risk",
    "regulation_risk",
    "confidence_low",
    "data_incomplete",
    "vague_timeline",      # 时间线模糊（"即将"、"近期"、"不久"等）
    "speculative",         # 投机性/无实质内容（"大事件"、"重要更新"等）
    "unverifiable",        # 无法验证的声明或预期
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
    is_priority_kol: bool = False


@dataclass
class SignalResult:
    """AI decision packaged for downstream consumers."""

    status: str
    summary: str = ""
    event_type: str = "other"
    asset: str = ""
    asset_names: str = ""
    action: str = "observe"
    direction: str = "neutral"
    confidence: float = 0.0
    strength: str = "low"
    timeframe: str = "medium"  # short/medium/long - 建议持仓时间范围
    risk_flags: list[str] = field(default_factory=list)
    raw_response: str = ""
    notes: str = ""
    error: Optional[str] = None
    links: list[str] = field(default_factory=list)
    alert: str = ""
    severity: str = ""

    @property
    def should_execute_hot_path(self) -> bool:
        return (
            self.status == "success"
            and self.action in {"buy", "sell"}
        )

    def is_high_value_signal(
        self,
        *,
        confidence_threshold: float = 0.75,
    ) -> bool:
        """Determine if signal qualifies for Claude deep analysis.

        Args:
            confidence_threshold: Minimum confidence for high-value classification

        Returns:
            True if signal meets high-value criteria
        """
        if self.status != "success":
            return False

        # Only trigger Claude for high confidence signals
        return self.confidence >= confidence_threshold


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
    """Coordinate optional AI powered signal generation with dual-engine routing."""

    def __init__(
        self,
        enabled: bool,
        client: Optional[OpenAIChatClient],
        threshold: float,
        semaphore: asyncio.Semaphore,
        *,
        provider_label: str = "AI",
        deep_analysis_engine: Optional[DeepAnalysisEngine] = None,
        deep_analysis_fallback: Optional[DeepAnalysisEngine] = None,
        deep_analysis_min_interval: float = 25.0,
        high_value_threshold: float = 0.75,
    ) -> None:
        self.enabled = enabled and client is not None
        self._client = client
        self._threshold = threshold
        self._semaphore = semaphore
        self._provider_label = provider_label or "AI"
        self._high_value_threshold = high_value_threshold
        self._deep_min_interval = float(deep_analysis_min_interval)
        self._last_deep_call_time: float = 0.0
        self._deep_enabled: bool = False
        self._deep_engine: DeepAnalysisEngine | None = None
        self._deep_fallback_engine: DeepAnalysisEngine | None = None
        self._deep_provider_label: str = ""
        self._deep_fallback_label: str = ""
        self._memory_bundle: MemoryBackendBundle | None = None
        self.attach_deep_analysis_engine(deep_analysis_engine, fallback=deep_analysis_fallback)

        if not self.enabled:
            logger.debug("AiSignalEngine 未启用或缺少客户端，所有消息将跳过 AI 分析")

    def attach_deep_analysis_engine(
        self,
        engine: Optional[DeepAnalysisEngine],
        *,
        fallback: Optional[DeepAnalysisEngine] = None,
    ) -> None:
        self._deep_engine = engine
        self._deep_fallback_engine = fallback
        self._deep_provider_label = engine.provider_name if engine else ""
        self._deep_fallback_label = fallback.provider_name if fallback else ""
        self._deep_enabled = engine is not None

        if engine:
            logger.info("🤖 深度分析已启用 (provider=%s)", self._deep_provider_label or "unknown")
        if fallback:
            logger.info("🔁 深度分析备用引擎已配置 (provider=%s)", self._deep_fallback_label or "unknown")


    @classmethod
    def from_config(cls, config: Any) -> "AiSignalEngine":
        if not getattr(config, "AI_ENABLED", False):
            logger.debug("配置关闭 AI 功能，采用传统转发流程")
            return cls(
                False,
                None,
                getattr(config, "AI_SIGNAL_THRESHOLD", 0.0),
                asyncio.Semaphore(1),
                provider_label="AI",
            )

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
        provider_label = provider.upper() if provider else "AI"

        api_key = (
            getattr(config, "AI_API_KEY", None)
            or getattr(config, "GEMINI_API_KEY", None)
            or ""
        )

        if not api_key:
            logger.warning("AI 已启用但未提供 API Key，自动降级为跳过 AI 分析")
            return cls(
                False,
                None,
                getattr(config, "AI_SIGNAL_THRESHOLD", 0.0),
                asyncio.Semaphore(1),
                provider_label=provider_label,
            )

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
            # Use native GeminiClient for Gemini (supports multimodal)
            if provider == "gemini":
                # Get all Gemini API keys for rotation
                api_keys = getattr(config, "GEMINI_API_KEYS", [])
                client = GeminiClient(
                    api_key=str(api_key),
                    model_name=getattr(config, "AI_MODEL_NAME", "gemini-2.0-flash-exp"),
                    timeout=getattr(config, "AI_TIMEOUT_SECONDS", 8.0),
                    max_retries=getattr(config, "AI_RETRY_ATTEMPTS", 1),
                    retry_backoff_seconds=getattr(config, "AI_RETRY_BACKOFF_SECONDS", 1.5),
                    api_keys=api_keys if api_keys else None,
                )
            else:
                # Use OpenAI-compatible client for others
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
        high_value_threshold = getattr(config, "HIGH_VALUE_CONFIDENCE_THRESHOLD", 0.75)
        deep_min_interval = getattr(config, "DEEP_ANALYSIS_MIN_INTERVAL", 25.0)

        engine = cls(
            True,
            client,
            getattr(config, "AI_SIGNAL_THRESHOLD", 0.0),
            asyncio.Semaphore(concurrency),
            provider_label=provider_label,
            deep_analysis_min_interval=deep_min_interval,
            high_value_threshold=high_value_threshold,
        )

        memory_bundle = create_memory_backend(config)
        engine._memory_bundle = memory_bundle

        deep_config = getattr(config, "get_deep_analysis_config", lambda: {})()
        deep_engine: DeepAnalysisEngine | None = None
        fallback_engine: DeepAnalysisEngine | None = None

        if deep_config.get("enabled"):
            provider_name = deep_config.get("provider", "claude")
            try:
                deep_engine = create_deep_analysis_engine(
                    provider=provider_name,
                    config=config,
                    parse_callback=engine._parse_response_text,
                    memory_bundle=memory_bundle,
                )
            except DeepAnalysisError as exc:
                logger.warning("深度分析引擎 %s 初始化失败: %s", provider_name, exc)

            fallback_name = deep_config.get("fallback_provider")
            if fallback_name and fallback_name != provider_name:
                try:
                    fallback_engine = create_deep_analysis_engine(
                        provider=fallback_name,
                        config=config,
                        parse_callback=engine._parse_response_text,
                        memory_bundle=memory_bundle,
                    )
                except DeepAnalysisError as exc:
                    logger.warning("备用深度分析引擎 %s 初始化失败: %s", fallback_name, exc)

        engine.attach_deep_analysis_engine(deep_engine, fallback=fallback_engine)
        return engine

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

        # Extract images for GeminiClient multimodal support
        images = None
        if isinstance(self._client, GeminiClient) and payload.media:
            images = [
                {"base64": img["base64"], "mime_type": img["mime_type"]}
                for img in payload.media
                if img.get("base64") and img.get("mime_type")
            ]
            if images:
                logger.debug("AI 分析包含 %d 张图片", len(images))

        # Step 1: Gemini fast analysis (90%)
        async with self._semaphore:
            try:
                if isinstance(self._client, GeminiClient):
                    response = await self._client.generate_signal(messages, images=images)
                else:
                    response = await self._client.generate_signal(messages)
            except AiServiceError as exc:
                is_temporary = getattr(exc, "temporary", False)
                logger.warning(
                    "AI 调用失败: %s",
                    exc,
                    exc_info=not is_temporary,
                )
                return SignalResult(status="error", error=str(exc))

        response_text = getattr(response, "text", "") or ""
        logger.debug("%s 返回长度: %d", self._provider_label, len(response_text))
        parts = getattr(response, "parts", None)
        self._log_ai_response_debug(self._provider_label, response_text, parts)
        gemini_result = self._parse_response(response)
        gemini_result = self._apply_extreme_event_overrides(payload, gemini_result)

        # Step 2: Determine whether to trigger deep analysis
        is_high_value = gemini_result.is_high_value_signal(
            confidence_threshold=self._high_value_threshold,
        )

        logger.debug(
            "🤖 %s 分析完成: action=%s confidence=%.2f event_type=%s asset=%s is_high_value=%s",
            self._provider_label,
            gemini_result.action,
            gemini_result.confidence,
            gemini_result.event_type,
            gemini_result.asset,
            is_high_value,
        )

        # 排除低价值事件类型（macro、other 触发过多且价值低，scam_alert 已经是风险警告）
        # airdrop: 空投类活动价值低、投机性强
        # Binance Alpha 相关的 listing 也倾向于低市值、高投机，通过 AI prompt 控制置信度
        excluded_event_types = {"macro", "other", "airdrop", "governance", "celebrity", "scam_alert"}
        should_skip_deep = gemini_result.event_type in excluded_event_types

        deep_engine = self._deep_engine
        fallback_engine = self._deep_fallback_engine
        deep_label = self._deep_provider_label or "deep"
        fallback_label = self._deep_fallback_label or "fallback"

        # 频率限制检查
        import time

        time_since_last_call = time.time() - self._last_deep_call_time
        rate_limited = time_since_last_call < self._deep_min_interval

        if should_skip_deep and is_high_value:
            logger.debug(
                "⏭️  跳过深度分析（低价值事件类型 %s）: confidence=%.2f asset=%s",
                gemini_result.event_type,
                gemini_result.confidence,
                gemini_result.asset,
            )
        elif rate_limited and is_high_value and self._deep_enabled:
            logger.debug(
                "⏭️  跳过深度分析（频率限制，距上次调用 %.1f 秒）: confidence=%.2f asset=%s",
                time_since_last_call,
                gemini_result.confidence,
                gemini_result.asset,
            )
        elif self._deep_enabled and deep_engine and is_high_value:
            logger.info(
                "🧠 触发 %s 深度分析: event_type=%s confidence=%.2f asset=%s (阈值: %.2f) source=%s",
                deep_label,
                gemini_result.event_type,
                gemini_result.confidence,
                gemini_result.asset,
                self._high_value_threshold,
                payload.source,
            )
            logger.debug(
                "深度分析引擎类型: %s, 有备用引擎: %s",
                type(deep_engine).__name__,
                "是" if fallback_engine else "否",
            )
            self._last_deep_call_time = time.time()
            try:
                logger.debug("正在调用 %s 引擎执行深度分析...", deep_label)
                deep_result = await deep_engine.analyse(payload, gemini_result)
                logger.info(
                    "✅ %s 深度分析完成: action=%s confidence=%.2f (%s 初判: %.2f) asset=%s summary=%s",
                    deep_label,
                    deep_result.action,
                    deep_result.confidence,
                    self._provider_label,
                    gemini_result.confidence,
                    deep_result.asset,
                    deep_result.summary[:100] if deep_result.summary else "",
                )
                return deep_result
            except DeepAnalysisError as exc:
                logger.warning(
                    "⚠️ %s 深度分析失败，将尝试备用或回退到主分析结果: %s",
                    deep_label,
                    exc,
                    exc_info=True,
                )
                if fallback_engine:
                    try:
                        logger.info("🔁 尝试备用深度引擎 %s (类型: %s)", fallback_label, type(fallback_engine).__name__)
                        fallback_result = await fallback_engine.analyse(payload, gemini_result)
                        logger.info(
                            "✅ 备用引擎 %s 深度分析完成: action=%s confidence=%.2f summary=%s",
                            fallback_label,
                            fallback_result.action,
                            fallback_result.confidence,
                            fallback_result.summary[:100] if fallback_result.summary else "",
                        )
                        return fallback_result
                    except DeepAnalysisError as fallback_exc:
                        logger.warning(
                            "⚠️ 备用深度引擎 %s 失败: %s",
                            fallback_label,
                            fallback_exc,
                            exc_info=True,
                        )
                else:
                    logger.debug("无备用深度引擎可用，将使用主引擎分析结果")

        return gemini_result

    @staticmethod
    def _log_ai_response_debug(label: str, text: str, parts: Sequence[Any] | None = None) -> None:
        """Log raw AI responses with truncation to avoid noisy logs."""
        if parts:
            try:
                part_types = [
                    getattr(part, "type", None)
                    or getattr(part, "type_", None)
                    or type(part).__name__
                    for part in parts
                ]
                logger.debug("%s 响应包含结构化分段: %s", label, part_types)
            except Exception:
                logger.debug("%s 结构化分段类型统计失败", label, exc_info=True)

        if not text:
            logger.debug("%s 原始响应为空字符串", label)
            return

        snippet = text.strip()
        max_length = 800
        if len(snippet) > max_length:
            snippet = f"{snippet[:max_length]}…(truncated)"
        logger.debug("%s 原始响应: %s", label, snippet)

    def _parse_response(self, response: OpenAIChatResponse) -> SignalResult:
        return self._parse_response_text(response.text)

    def _parse_response_text(self, text: str) -> SignalResult:
        raw_text = (text or "").strip()
        normalized_text = self._prepare_json_text(raw_text)

        asset = ""
        try:
            data = json.loads(normalized_text)
            # Parse confidence safely for debug log
            confidence_debug = data.get("confidence", 1.0)
            if isinstance(confidence_debug, str):
                confidence_map = {"high": 0.8, "medium": 0.5, "low": 0.3}
                confidence_debug = confidence_map.get(confidence_debug.lower(), 0.0)
            else:
                try:
                    confidence_debug = float(confidence_debug)
                except (ValueError, TypeError):
                    confidence_debug = 1.0

            logger.debug(
                "AI JSON 解析成功: action=%s confidence=%.2f",
                data.get("action"),
                confidence_debug,
            )
            summary = str(data.get("summary", "")).strip()
            event_type = str(data.get("event_type", "other")).lower()
            asset_field = data.get("asset", "")
            asset_name_field = (
                data.get("asset_name")
                or data.get("asset_names")
                or data.get("asset_display")
                or ""
            )
            action = str(data.get("action", "observe")).lower()
            direction = str(data.get("direction", "neutral")).lower()
            strength = str(data.get("strength", "low")).lower()
            timeframe = str(data.get("timeframe", "medium")).lower()

            # Handle confidence - should be float but AI sometimes returns string like "high"
            confidence_raw = data.get("confidence", 1.0)
            if isinstance(confidence_raw, str):
                # Map string values to numeric confidence
                confidence_map = {"high": 0.8, "medium": 0.5, "low": 0.3}
                confidence = confidence_map.get(confidence_raw.lower(), 0.0)
                logger.warning(
                    "AI 返回了字符串 confidence '%s'，已转换为数字 %.2f",
                    confidence_raw,
                    confidence,
                )
            else:
                try:
                    confidence = float(confidence_raw)
                except (ValueError, TypeError):
                    logger.warning(
                        "无法解析 confidence 值 '%s'，使用默认值 1.0",
                        confidence_raw,
                    )
                    confidence = 1.0
            risk_flags = data.get("risk_flags", []) or []
            if not isinstance(risk_flags, list):
                risk_flags = [str(risk_flags)]
            notes = str(data.get("notes", "")).strip()
            links_raw = data.get("links", [])
            if isinstance(links_raw, str):
                links = [links_raw]
            elif isinstance(links_raw, list):
                links = [str(item).strip() for item in links_raw if str(item).strip()]
            else:
                links = []
            if links:
                # 去重同时保持顺序，避免同一来源被重复渲染
                links = list(dict.fromkeys(links))
            if isinstance(asset_field, (list, tuple)):
                asset = ",".join(str(item).strip() for item in asset_field if str(item).strip())
            else:
                asset = str(asset_field).strip()
            if isinstance(asset_name_field, (list, tuple)):
                asset_names = "、".join(
                    str(item).strip() for item in asset_name_field if str(item).strip()
                )
            else:
                asset_names = str(asset_name_field).strip()
        except json.JSONDecodeError:
            logger.debug(
                "AI 返回无法解析为 JSON，使用纯文本摘要: %s",
                normalized_text[:120].replace("\n", " "),
            )
            summary = "AI 返回格式异常，已忽略原始内容"
            event_type = "other"
            asset = ""
            asset_names = ""
            action = "observe"
            direction = "neutral"
            strength = "low"
            timeframe = "medium"
            confidence = 1.0
            risk_flags = ["confidence_low"]
            notes = ""
            links = []

        event_type = event_type if event_type in ALLOWED_EVENT_TYPES else "other"
        action = action if action in ALLOWED_ACTIONS else "observe"
        direction = direction if direction in ALLOWED_DIRECTIONS else "neutral"
        if strength not in ALLOWED_STRENGTH:
            strength = "low"
        if timeframe not in ALLOWED_TIMEFRAMES:
            timeframe = "medium"

        asset = asset.upper().strip()
        asset_tokens = [token.strip() for token in asset.split(",") if token.strip()]
        normalized_assets = []
        for token in asset_tokens:
            if token in NO_ASSET_TOKENS:
                continue
            if not ASSET_CODE_REGEX.match(token):
                continue
            if any(token.startswith(prefix) for prefix in FORBIDDEN_ASSET_PREFIXES):
                continue
            if token in FORBIDDEN_ASSET_CODES:
                continue
            normalized_assets.append(token)
        if asset_names:
            canonical_name = asset_names.strip()
            upper_name = canonical_name.upper()
            if upper_name in {"NONE", "NA", "N/A"} or canonical_name in {"无", "暂无"}:
                asset_names = ""

        if not normalized_assets:
            asset = "NONE"
            asset_names = ""
        else:
            asset = ",".join(normalized_assets)
            if not asset_names:
                asset_names = ",".join(normalized_assets)
        confidence = max(0.0, min(1.0, round(confidence, 2)))
        filtered_flags: list[str] = []
        for flag in risk_flags:
            if not isinstance(flag, str):
                continue
            value = flag.strip()
            if value in ALLOWED_RISK_FLAGS:
                filtered_flags.append(value)
        if not filtered_flags and confidence < 0.3:
            filtered_flags.append("confidence_low")

        has_crypto_asset = asset != "NONE"
        noise_flags = {"speculative", "vague_timeline", "unverifiable"}
        has_noise_flag = any(flag in noise_flags for flag in filtered_flags)

        result = SignalResult(
            status="skip",
            summary=summary,
            event_type=event_type,
            asset=asset,
            asset_names=asset_names,
            action=action,
            direction=direction,
            confidence=confidence,
            strength=strength,
            timeframe=timeframe,
            risk_flags=filtered_flags,
            raw_response=raw_text,
            notes=notes,
            links=links,
        )
        self._finalize_signal_status(
            result,
            has_crypto_asset=has_crypto_asset,
            has_noise_flag=has_noise_flag,
        )
        return result


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

    def _apply_extreme_event_overrides(
        self,
        payload: EventPayload,
        result: SignalResult,
    ) -> SignalResult:
        """Adjust AI output for extreme depeg/liquidation scenarios."""
        if not payload.text and not payload.translated_text:
            return result

        analysis = analyze_event_intensity(
            payload.text or "",
            payload.translated_text or "",
        )
        has_extreme_move = analysis["has_high_impact"] and (
            analysis["has_percent_change"]
            or analysis["has_price_level_change"]
            or analysis["has_drop_keyword"]
        )

        asset_tokens = {
            token.strip().lower()
            for token in (result.asset or "").split(",")
            if token.strip()
        }
        mentions_critical_asset = analysis["mentions_critical_asset"] or bool(
            asset_tokens & {"usde", "wbeth", "wbtc", "wbsol", "stablecoin"}
        )

        modified = False

        if has_extreme_move:
            result.confidence = min(1.0, max(result.confidence + 0.2, 0.0))
            if not result.alert:
                result.alert = "extreme_market_move"
            if not result.severity:
                result.severity = "high"
            if "price_volatility" not in result.risk_flags:
                result.risk_flags.append("price_volatility")
            modified = True

        if has_extreme_move and mentions_critical_asset:
            if result.action != "sell":
                result.action = "sell"
                modified = True
            if result.direction != "short":
                result.direction = "short"
                modified = True
            if result.confidence < 0.8:
                result.confidence = min(1.0, max(result.confidence, 0.8))
                modified = True

        if result.alert or result.severity or modified:
            self._refresh_signal_status(result)

        return result

    def _finalize_signal_status(
        self,
        result: SignalResult,
        *,
        has_crypto_asset: bool,
        has_noise_flag: bool,
    ) -> None:
        """Evaluate signal eligibility after confidence/action adjustments."""
        effective_threshold = max(self._threshold, 0.4)

        if has_noise_flag and result.confidence < 0.7:
            result.status = "skip"
        elif result.confidence >= effective_threshold and has_crypto_asset:
            result.status = "success"
        else:
            result.status = "skip"

        if not has_crypto_asset and "data_incomplete" not in result.risk_flags:
            result.risk_flags.append("data_incomplete")

    def _refresh_signal_status(self, result: SignalResult) -> None:
        """Re-run status gating using current signal attributes."""
        has_crypto_asset = bool(result.asset and result.asset != "NONE")
        noise_flags = {"speculative", "vague_timeline", "unverifiable"}
        has_noise_flag = any(flag in noise_flags for flag in result.risk_flags)
        self._finalize_signal_status(
            result,
            has_crypto_asset=has_crypto_asset,
            has_noise_flag=has_noise_flag,
        )


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
        "is_priority_kol": payload.is_priority_kol,
        "priority_flags": ["priority_kol"] if payload.is_priority_kol else [],
    }

    context_json = json.dumps(context, ensure_ascii=False)

    system_prompt = (
        "你是加密交易台的资深分析师，需要从多语种快讯中快速提炼可交易信号。\n"
        "务必仅输出一个 JSON 对象，禁止生成多段 JSON、列表外层或 Markdown 代码块，输出前后不得附加 ```、#、说明文字或额外段落。\n"
        "JSON 字段固定为 summary、event_type、asset、asset_name、action、direction、confidence、strength、timeframe、risk_flags、notes。\n"
        "**summary 字段必须使用简体中文撰写，简明扼要（1-2 句话），直接说明核心事件和影响。**\n"
        "event_type 仅能取 listing、delisting、hack、regulation、funding、whale、liquidation、partnership、product_launch、governance、macro、celebrity、airdrop、scam_alert、other。\n"
        "action 为 buy、sell、observe；direction 为 long、short、neutral；strength 仅取 high、medium、low；timeframe 仅取 short、medium、long。\n"
        "如事件涉及多个币种，asset 可为数组（如 [\"BTC\",\"ETH\"]），asset_name 用简体中文名以顿号或逗号分隔；若无法确认币种则 asset=NONE、asset_name=无，并在 notes 解释原因。\n"
        "**黄金映射规则**：当消息涉及黄金（Gold/XAU/黄金期货）时，使用 asset=XAUT（Tether Gold 代币）来查询价格和分析。\n"
        "\n## 主流币市场指标重要性 ⚠️ 核心优先级\n"
        "**BTC（比特币）是整个加密市场的风向标和宏观指标**：\n"
        "1. **宏观关联性**：比特币价格与全球宏观环境（美联储政策、美元指数、地缘政治）高度相关，是加密市场风险偏好的核心指标。\n"
        "2. **市场联动性**：当比特币涨跌时，整个加密市场（ETH、SOL、山寨币）通常同向波动，BTC 跌意味着全币圈承压。\n"
        "3. **宏观传导机制**：\n"
        "   - 贸易战/地缘冲突 → 风险偏好下降 → BTC 下跌 → 全币圈下跌\n"
        "   - 美联储加息/美元走强 → 流动性收紧 → BTC 承压 → 加密市场整体回调\n"
        "   - 宏观利好（降息预期、机构入场） → BTC 上涨 → 带动整个加密市场\n"
        "4. **主流币优先级**：BTC > ETH > SOL，这三个币种的价格变化必须重点关注并提醒。\n"
        "5. **主流币信号增强规则**：\n"
        "   - 涉及 BTC/ETH/SOL 价格涨跌 ≥3% → confidence 自动 +0.15 到 +0.25\n"
        "   - 涉及 BTC 突破关键心理关口（如 $100K、$90K） → confidence ≥0.75, strength=high\n"
        "   - 涉及 BTC 与宏观事件（川普、贸易战、美联储、CPI） → 必须在 summary 中明确说明宏观影响和市场联动\n"
        "   - 主流币大涨（≥5%）或大跌（≤-5%） → action 必须明确（buy/sell），避免模糊的 observe\n"
        "\n## 置信度（confidence）\n"
        "confidence 衡量该信号是否值得执行：0.7-1.0 高可信、0.4-0.7 中等、0.0-0.4 仅提示风险或噪音；即使事件真实但不可执行，也应降低 confidence 至 ≤0.4。\n"
        "**可执行性判断标准**：\n"
        "- ✅ 可执行（confidence ≥0.6）：明确的买入/卖出标的 + 时间窗口 + 具体价格/数据支撑\n"
        "- ⚠️ 部分可执行（confidence 0.4-0.6）：有交易方向但缺少时间节点，或有数据但缺少明确标的\n"
        "- ❌ 不可执行（confidence ≤0.4）：纯统计数字、笼统趋势、情绪观察、无具体标的或时间\n"
        "**特别注意**：涉及主流币（BTC/ETH/SOL）价格变化的消息，默认具有更高执行价值，confidence 应适当提升（+0.15 到 +0.25）。\n"
        "\n## 时间范围（timeframe）\n"
        "timeframe 表示建议持仓时间或影响周期：\n"
        "- short（短期，<1周）：链上数据突变、巨鲸短期操作、短期事件催化（如空投、IDO）、技术面信号等需快速反应的机会\n"
        "- medium（中期，1周-1月）：产品上线、合作公告、季度财报、中短期叙事（如某赛道热点）\n"
        "- long（长期，>1月）：监管政策、宏观采用率提升、基础设施建设、长期叙事（如机构入场、ETF 净流入持续）\n"
        "根据事件性质判断影响持续时间，例如：机构采用率提升→long；交易所上线→medium；巨鲸 24h 内买入→short。\n"
        "\n## 风险标志（risk_flags）\n"
        "risk_flags 数组仅允许 price_volatility、liquidity_risk、regulation_risk、confidence_low、data_incomplete、vague_timeline、speculative、unverifiable。\n"
        "仅在实际触发时添加标志，避免堆砌；当 confidence <0.4 或缺少关键数据，可加入 confidence_low 或 data_incomplete。\n"
        "当稳定币或包裹资产（如 USDE、WBETH、WBTC、WBSOL 等）出现脱锚、暴跌、折价、清算或强制平仓风险时，必须返回 action=sell、direction=short，confidence ≥0.8，并在 notes 说明触发原因与核心数据。\n"
        "若文本包含“脱锚、depeg、暴跌、大幅下跌、跌至、低于、清算、强制平仓”等词汇且伴随百分比或价格变动，请视为极端行情，重点描述跌幅、价格区间，并相应提升 confidence。\n"
        "对于极端行情，请在 risk_flags 中至少加入 price_volatility；如数据来源或链上细节缺失，额外标记 data_incomplete。\n"
        "\n## 信号判断规则\n"
        "1. 时间模糊（\"近期\"、\"soon\" 等）→ 添加 vague_timeline，并降低 confidence。\n"
        "2. 内容笼统、缺乏指标或只是情绪表述 → 添加 speculative，并将 action 设为 observe 或 confidence ≤0.5。\n"
        "3. 来源无法验证或为传闻 → 添加 unverifiable，并将 action=observe。\n"
        "4. 仅当事件直接涉及加密资产（2-10 位大写/数字）才填写 asset；股票、指数、ETF 等非加密标的必须返回 NONE。\n"
        "5. 若提供链上数据、成交量、资金流等客观指标，可据此提高 confidence，并在 notes 概述关键数字。\n"
        "6. Meme 币爆料、营销文案或活动预告若缺少可执行细节，应输出 event_type=scam_alert 或 other，action=observe，confidence ≤0.4，并说明风险。\n"
        "7. 交易所/衍生品上线仅公告而无成交、资金费率、流动性指标时，action=observe、direction=neutral，confidence ≤0.5，必要时标记 speculative 或 data_incomplete。\n"
        "8. **宏观统计数据（event_type=macro）严格限制**：\n"
        '   - 仅统计数字（如"总供应量创新高"、"市值突破XX"、"整体增长XX%"）而无具体交易机会 → confidence ≤0.4，添加 data_incomplete 或 speculative\n'
        "   - 稳定币总供应量/总市值类消息，除非明确说明资金流入具体链（ETH/SOL）、协议（Aave/Curve）或配合链上数据（DEX交易量激增），否则 confidence ≤0.4\n"
        "   - 机构采用、DeFi能力、长期趋势等笼统观察，无时间节点和可执行标的 → action=observe，confidence ≤0.5，添加 vague_timeline\n"
        '   - event_type=macro + action=observe 组合时，必须有明确交易催化剂（如"X机构宣布本周买入Y亿美元BTC"）才能 confidence >0.6\n'
        "9. **低市值代币风险控制**：\n"
        "   - 市值 < 5000万美元的代币，默认视为高风险投机标的 → confidence 自动 -0.15 到 -0.25\n"
        "   - 市值 < 1000万美元的代币，极高风险 → confidence 自动 -0.25 到 -0.35，必须添加 liquidity_risk\n"
        "   - 未上线主流交易所（仅在 DEX 或小型 CEX）的代币 → confidence 降低 0.1-0.2，添加 liquidity_risk\n"
        "   - 低市值 + 无明确催化剂（如仅空投、仅上线小交易所） → confidence ≤0.4，action=observe\n"
        "10. **Binance Alpha 特殊处理**：\n"
        "   - Binance Alpha 平台上线的代币通常市值较小、投机性强 → 自动降低 confidence 0.2-0.3\n"
        "   - Binance Alpha 空投活动，除非有明确的交易机会和时间节点 → action=observe，confidence ≤0.5\n"
        "   - 对于 Binance Alpha 消息，必须在 summary 中明确标注 '市值较小' 或 '投机性强' 等风险提示\n"
        "11. **稳定币不可交易原则**：\n"
        "   - USDC、USDT、DAI、BUSD、TUSD、USDP、GUSD、FRAX、LUSD、USDD 等稳定币设计目标为保持 1 美元价格，不存在价格波动交易机会\n"
        "   - 涉及稳定币的基础设施、供应量、市值等消息 → asset=NONE，action=observe，confidence ≤0.4\n"
        "   - **示例**：\"Circle 获得美联储支付通道，USDC 市场地位提升\" → asset=NONE，notes 说明 \"USDC 是稳定币不可交易，若想受益应关注使用 USDC 的 DeFi 协议或支付类代币\"\n"
        "   - **例外情况**：仅当稳定币出现明确脱锚风险（价格偏离 >5%、depeg、暴跌等） → action=sell、direction=short，confidence ≥0.8\n"
        "   - 对于稳定币相关利好消息，应在 notes 中建议关注受益的 DeFi 协议（Aave、Curve、Uniswap 等）或其原生代币，而非稳定币本身\n"
        "\n## 历史参考\n"
        "historical_reference.entries 若非空，请对比相似案例并在 notes 简述结论（如“与 2024-08 BTC ETF 净流入类似”）；若为空可忽略。\n"
        "\n## 图片处理\n"
        "识别图片中的交易对、公告主体或链上指标；若图片与加密无关或无法读出，请 asset=NONE 并添加 data_incomplete，notes 说明“图片无法识别”或“与加密无关”。\n"
        "\n所有字段使用简体中文，禁止输出 Markdown、表格或多余解释，确保 JSON 可直接解析。"
    )

    if payload.is_priority_kol:
        system_prompt += (
            "\n\n## 白名单 KOL 优先指引\n"
            "该消息来自高度可信的优先 KOL，默认视为具备较高执行价值：\n"
            "1. 重点提炼最具交易价值的要点，优先给出可执行动作及方向，必要时提供关键数据或链上证据。\n"
            "2. 若信息仍然缺乏可执行性，请明确指出缺口，并阐明需要等待的补充要素，避免含糊其辞。\n"
            "3. 避免因为语气保守而将 confidence 人为压低，如无明显噪音或矛盾信息，confidence 可适度提升至 0.5-0.8 区间。\n"
            "4. 对于宏观或情绪类观点，需判断其对主流资产或赛道的可操作影响，并在 notes 中给出简洁的执行建议或观察重点。"
        )

    user_prompt = (
        "请结合以下事件上下文给出最具操作性的建议，若包含多条信息需综合判断：\n"
        f"```json\n{context_json}\n```\n"
        "返回仅包含上述字段的 JSON 字符串，禁止出现额外文本；多资产请使用 asset 数组，notes 简洁说明关键要点或风险。"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
