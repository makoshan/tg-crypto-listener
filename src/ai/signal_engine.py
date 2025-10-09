"""AI Signal Engine orchestrating OpenAI-compatible inference."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from ..utils import setup_logger
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
    risk_flags: list[str] = field(default_factory=list)
    raw_response: str = ""
    notes: str = ""
    error: Optional[str] = None
    links: list[str] = field(default_factory=list)

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
                client = GeminiClient(
                    api_key=str(api_key),
                    model_name=getattr(config, "AI_MODEL_NAME", "gemini-2.0-flash-exp"),
                    timeout=getattr(config, "AI_TIMEOUT_SECONDS", 8.0),
                    max_retries=getattr(config, "AI_RETRY_ATTEMPTS", 1),
                    retry_backoff_seconds=getattr(config, "AI_RETRY_BACKOFF_SECONDS", 1.5),
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

        logger.debug("%s 返回长度: %d", self._provider_label, len(response.text))
        self._log_ai_response_debug(self._provider_label, response.text)
        gemini_result = self._parse_response(response)

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
                "🧠 触发 %s 深度分析: event_type=%s confidence=%.2f asset=%s (阈值: %.2f)",
                deep_label,
                gemini_result.event_type,
                gemini_result.confidence,
                gemini_result.asset,
                self._high_value_threshold,
            )
            self._last_deep_call_time = time.time()
            try:
                deep_result = await deep_engine.analyse(payload, gemini_result)
                logger.info(
                    "✅ %s 深度分析完成: action=%s confidence=%.2f (%s 初判: %.2f) asset=%s",
                    deep_label,
                    deep_result.action,
                    deep_result.confidence,
                    self._provider_label,
                    gemini_result.confidence,
                    deep_result.asset,
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
                        logger.info("🔁 尝试备用深度引擎 %s", fallback_label)
                        fallback_result = await fallback_engine.analyse(payload, gemini_result)
                        logger.info(
                            "✅ 备用引擎 %s 深度分析完成: action=%s confidence=%.2f",
                            fallback_label,
                            fallback_result.action,
                            fallback_result.confidence,
                        )
                        return fallback_result
                    except DeepAnalysisError as fallback_exc:
                        logger.warning(
                            "⚠️ 备用深度引擎 %s 失败: %s",
                            fallback_label,
                            fallback_exc,
                            exc_info=True,
                        )

        return gemini_result

    @staticmethod
    def _log_ai_response_debug(label: str, text: str) -> None:
        """Log raw AI responses with truncation to avoid noisy logs."""
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
            logger.debug(
                "AI JSON 解析成功: action=%s confidence=%.2f",
                data.get("action"),
                float(data.get("confidence", 0.0)),
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
            confidence = float(data.get("confidence", 0.0))
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
            confidence = 0.0
            risk_flags = ["confidence_low"]
            notes = ""
            links = []

        event_type = event_type if event_type in ALLOWED_EVENT_TYPES else "other"
        action = action if action in ALLOWED_ACTIONS else "observe"
        direction = direction if direction in ALLOWED_DIRECTIONS else "neutral"
        if strength not in ALLOWED_STRENGTH:
            strength = "low"

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
        filtered_flags = []
        for flag in risk_flags:
            if not isinstance(flag, str):
                continue
            value = flag.strip()
            if value in ALLOWED_RISK_FLAGS:
                filtered_flags.append(value)
        if not filtered_flags and confidence < 0.3:
            filtered_flags.append("confidence_low")

        # 保证所有推送附带摘要，但置信度低于 0.4 会被上层过滤
        effective_threshold = max(self._threshold, 0.4)
        # 仅当模型识别到加密货币标的时才推送
        has_crypto_asset = asset != "NONE"

        # 检查是否包含噪音标志（speculative、vague_timeline、unverifiable）
        noise_flags = {"speculative", "vague_timeline", "unverifiable"}
        has_noise_flag = any(flag in noise_flags for flag in filtered_flags)

        # 如果包含噪音标志且置信度不足，自动降级为 skip
        # 注：即使有噪音标志，如果置信度 >= 0.7 仍可能是有价值的信号（如知名人士的模糊预告）
        if has_noise_flag and confidence < 0.7:
            status = "skip"
        elif confidence >= effective_threshold and has_crypto_asset:
            status = "success"
        else:
            status = "skip"

        if not has_crypto_asset and "data_incomplete" not in filtered_flags:
            filtered_flags.append("data_incomplete")

        return SignalResult(
            status=status,
            summary=summary,
            event_type=event_type,
            asset=asset,
            asset_names=asset_names,
            action=action,
            direction=direction,
            confidence=confidence,
            strength=strength,
            risk_flags=filtered_flags,
            raw_response=raw_text,
            notes=notes,
            links=links,
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
        "你是加密交易台的资深分析师。\n"
        "需从多语种快讯中快速提炼可交易信号，并严格使用 JSON 结构输出结果。\n"
        "输出字段固定为 summary、event_type、asset、asset_name、action、direction、confidence、strength、risk_flags、notes。\n"
        "event_type 仅能取 listing、delisting、hack、regulation、funding、whale、liquidation、partnership、product_launch、governance、macro、celebrity、airdrop、scam_alert、other。\n"
        "action 为 buy、sell 或 observe；direction 为 long、short 或 neutral。\n"
        "\n\n## 置信度（confidence）的语义定义\n"
        "confidence 表示**该信号作为交易建议的可靠性**，而非事件真实性：\n"
        "- 0.7-1.0：高质量买入/卖出信号，有充分数据支撑，可直接交易\n"
        "- 0.4-0.7：中等质量信号，需结合其他信息判断\n"
        "- 0.0-0.4：低质量信号或风险警告，不建议交易\n"
        "**关键**：即使事件真实性高（如确实有交易员暴富），但若这不是一个好的交易机会（如高风险投机、无法复制的个案），confidence 应设为低值（≤0.4）。\n\n"
        "risk_flags 为数组，枚举 price_volatility、liquidity_risk、regulation_risk、confidence_low、data_incomplete、vague_timeline、speculative、unverifiable。\n"
        "historical_reference.entries 提供近似历史案例，包含时间、资产、动作、置信度与相似度；若列表非空，务必结合这些案例比较当前事件并在 notes 中说明与历史是否一致，若为空可直接按照当前事实判断。\n"
        "仅当事件直接涉及可识别的加密货币或代币（通常为 2-10 位大写字母/数字的代码，如 BTC、ETH、SOL、BNB、XRP 等）时，输出准确的币种代码；若提及股票、股指、ETF（如特斯拉、S&P500、纳指、恒生指数等）或无法确定具体加密资产，请将 asset 设置为 NONE 并在 notes 中说明原因，禁止返回 GENERAL、CRYPTO、MARKET 等泛化词。\n"
        "\n\n## 信号质量评估准则（Signal vs Noise）\n"
        "严格区分可交易信号与市场噪音，优先考虑可验证性和具体性：\n"
        "1. **时间线明确性**：若事件时间模糊（如\"即将\"、\"近期\"、\"不久\"、\"soon\"），添加 vague_timeline 标志并降低 confidence（建议 ≤0.5）。\n"
        "2. **内容具体性**：若描述含糊（如\"大事件\"、\"重要更新\"、\"major announcement\"）而无具体细节，添加 speculative 标志并降低 confidence（建议 ≤0.4）。\n"
        "3. **可验证性**：若声明无法通过客观数据验证（仅为预期、猜测或未经证实的传言），添加 unverifiable 标志。\n"
        "4. **数据支撑**：优先考虑有链上数据、交易量、价格变化、官方公告等客观证据的信号；仅基于社交媒体发言（即使是创始人）但无实质内容的，应标记为 speculative。\n"
        "5. **置信度调整**：模糊表述应显著降低 confidence，即使发言者是知名人士，若缺乏具体细节，置信度不应超过 0.6。\n"
        "6. **高风险投机与传闻识别**：针对 Meme 币暴富、爆料、传闻等信号，请结合来源可信度和可验证指标判断：\n"
        "   - 若仅来源于不可靠渠道或缺乏链上/成交数据支撑，应输出 event_type=scam_alert 或 other，action=observe，confidence ≤0.4，并在 notes 中说明风险点（speculative、liquidity_risk 等）。\n"
        "   - 若消息来自公信力较高的机构/个人，且附带可验证数据（链上地址、成交/资金流、官方声明等），可根据数据质量调整 confidence，并明确列出支撑点；若仍无法得到执行依据，action 保持 observe。\n"
        "7. **交易所/衍生品上线公告**：若消息仅说明交易所上币、永续/杠杆合约上线、做市启动或开启认购，而缺乏成交量、资金费率、流动性深度、做市规模或链上交易指标等可执行数据，必须视为观察级噪音：强制设置 action=observe、direction=neutral、confidence ≤0.4，并在 risk_flags 中加入 speculative 或 data_incomplete，notes 中说明『仅为上线公告，缺乏交易数据』。只有当公告附带可验证的成交/资金费率/巨额资金流或与主流资产强相关的链上指标时，才可考虑提升置信度并输出可执行方向。\n"
        "\n\n## 图片分析指南\n"
        "当消息包含图片时，请仔细识别图片内容类型并提取关键信息：\n"
        "1. **交易所截图**（订单明细、持仓、成交记录等）：识别交易对、成交价格、成交数量、时间戳，提取资产代码（如 2Z/KRW 中的 2Z），分析交易行为（大额买入/卖出）。\n"
        "2. **价格图表**（K线图、走势图等）：识别币种、价格趋势（上涨/下跌）、关键价位、技术指标，分析市场情绪。\n"
        "3. **公告截图**（交易所公告、官方声明等）：识别发布方、核心内容（上币、下架、活动等），提取币种和事件类型。\n"
        "4. **社交媒体截图**（Twitter/X、Telegram 等）：识别发言者、内容主题，判断影响力和可信度。\n"
        "5. **其他金融数据**（资金费率、持仓量、清算数据等）：提取具体数值和币种，分析市场异常信号。\n"
        "若图片内容与加密货币无关（如表情包、风景照、无关文档等），请在 notes 中说明'图片内容与加密货币无关'并将 asset 设为 NONE。\n"
        "若图片模糊或无法识别关键信息，请在 risk_flags 中添加 data_incomplete，并在 notes 中说明。\n"
        "\n所有字符串输出使用简体中文，禁止返回 Markdown、额外文本或解释。"
    )

    user_prompt = (
        "请结合以下事件上下文给出最具操作性的建议，若包含多条信息需综合判断：\n"
        f"```json\n{context_json}\n```\n"
        "返回仅包含上述字段的 JSON 字符串，必要时在 notes 中说明原因或疑点；当事件与加密货币无关或标的不明确时请设置 asset 为 NONE、asset_name 为 无，并解释原因，严禁输出泛化标签或非币种名称。"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
