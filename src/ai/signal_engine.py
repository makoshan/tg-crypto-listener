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
from .gemini_client import AiServiceError, GeminiClient, GeminiResponse
from .anthropic_client import AnthropicClient, AnthropicResponse

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
        confidence_threshold: float = 0.7,
        critical_event_types: set[str] | None = None,
        critical_keywords: set[str] | None = None,
        message_text: str = "",
    ) -> bool:
        """Determine if signal qualifies for Claude deep analysis.

        Args:
            confidence_threshold: Minimum confidence for high-value classification
            critical_event_types: Event types requiring deep analysis
            critical_keywords: Keywords triggering deep analysis
            message_text: Original message text for keyword matching

        Returns:
            True if signal meets high-value criteria
        """
        if self.status != "success":
            return False

        # Criterion 1: High confidence
        if self.confidence >= confidence_threshold:
            return True

        # Criterion 2: Critical event type
        critical_events = critical_event_types or {"hack", "regulation", "listing", "delisting"}
        if self.event_type in critical_events:
            return True

        # Criterion 3: Critical keyword match
        if critical_keywords and message_text:
            message_lower = message_text.lower()
            if any(keyword.lower() in message_lower for keyword in critical_keywords):
                return True

        return False


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
        claude_client: Optional[AnthropicClient] = None,
        claude_enabled: bool = False,
        high_value_threshold: float = 0.7,
        critical_keywords: set[str] | None = None,
    ) -> None:
        self.enabled = enabled and client is not None
        self._client = client
        self._threshold = threshold
        self._semaphore = semaphore
        self._claude_client = claude_client
        self._claude_enabled = claude_enabled and claude_client is not None
        self._high_value_threshold = high_value_threshold
        self._critical_keywords = critical_keywords or set()
        if not self.enabled:
            logger.debug("AiSignalEngine 未启用或缺少客户端，所有消息将跳过 AI 分析")
        if self._claude_enabled:
            logger.info("🤖 Claude 深度分析已启用 (10% 高价值信号路由)")

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

        # Initialize Claude client if enabled
        claude_client: Optional[AnthropicClient] = None
        claude_enabled = getattr(config, "CLAUDE_ENABLED", False)
        if claude_enabled:
            claude_api_key = getattr(config, "CLAUDE_API_KEY", "").strip()
            if claude_api_key:
                try:
                    from ..memory import MemoryToolHandler

                    memory_dir = getattr(config, "MEMORY_DIR", "./memories")
                    memory_handler = MemoryToolHandler(base_path=memory_dir)

                    claude_client = AnthropicClient(
                        api_key=claude_api_key,
                        model_name=getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
                        timeout=getattr(config, "CLAUDE_TIMEOUT_SECONDS", 30.0),
                        max_tool_turns=getattr(config, "CLAUDE_MAX_TOOL_TURNS", 10),
                        memory_handler=memory_handler,
                        context_trigger_tokens=getattr(config, "MEMORY_CONTEXT_TRIGGER_TOKENS", 10000),
                        context_keep_tools=getattr(config, "MEMORY_CONTEXT_KEEP_TOOLS", 2),
                        context_clear_at_least=getattr(config, "MEMORY_CONTEXT_CLEAR_AT_LEAST", 500),
                    )
                    logger.info("🧠 Claude 深度分析引擎已初始化")
                except Exception as exc:
                    logger.warning("Claude 初始化失败，将禁用深度分析: %s", exc)
                    claude_client = None
                    claude_enabled = False

        high_value_threshold = getattr(config, "HIGH_VALUE_CONFIDENCE_THRESHOLD", 0.7)
        critical_keywords = getattr(config, "CRITICAL_KEYWORDS", set())

        return cls(
            True,
            client,
            getattr(config, "AI_SIGNAL_THRESHOLD", 0.0),
            asyncio.Semaphore(concurrency),
            claude_client=claude_client,
            claude_enabled=claude_enabled,
            high_value_threshold=high_value_threshold,
            critical_keywords=critical_keywords,
        )

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

        logger.debug("Gemini 返回长度: %d", len(response.text))
        gemini_result = self._parse_response(response)

        # Step 2: Check if high-value signal qualifies for Claude (10%)
        if (
            self._claude_enabled
            and self._claude_client
            and gemini_result.is_high_value_signal(
                confidence_threshold=self._high_value_threshold,
                critical_keywords=self._critical_keywords,
                message_text=payload.text,
            )
        ):
            logger.info(
                "🧠 触发 Claude 深度分析: event_type=%s confidence=%.2f asset=%s",
                gemini_result.event_type,
                gemini_result.confidence,
                gemini_result.asset,
            )
            try:
                # Build Claude prompt based on Gemini's initial analysis
                claude_prompt = self._build_claude_deep_analysis_prompt(payload, gemini_result)
                claude_response = await self._claude_client.generate_signal(claude_prompt)

                # Parse Claude's response (expects same JSON structure)
                claude_result = self._parse_response(claude_response)
                logger.info(
                    "✅ Claude 深度分析完成: confidence=%.2f (Gemini: %.2f)",
                    claude_result.confidence,
                    gemini_result.confidence,
                )
                return claude_result
            except Exception as exc:
                logger.warning("Claude 深度分析失败，回退到 Gemini 结果: %s", exc)
                return gemini_result

        return gemini_result

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
        status = "success" if (confidence >= effective_threshold and has_crypto_asset) else "skip"
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

    def _build_claude_deep_analysis_prompt(
        self, payload: EventPayload, gemini_result: SignalResult
    ) -> str:
        """Build enriched prompt for Claude based on Gemini's initial analysis."""
        context = {
            "original_text": payload.text,
            "translated_text": payload.translated_text or payload.text,
            "source": payload.source,
            "timestamp": payload.timestamp.isoformat(),
            "language": payload.language,
            "keywords_hit": payload.keywords_hit,
            "historical_reference": payload.historical_reference,
            "gemini_analysis": {
                "summary": gemini_result.summary,
                "event_type": gemini_result.event_type,
                "asset": gemini_result.asset,
                "asset_names": gemini_result.asset_names,
                "action": gemini_result.action,
                "direction": gemini_result.direction,
                "confidence": gemini_result.confidence,
                "strength": gemini_result.strength,
                "risk_flags": gemini_result.risk_flags,
                "notes": gemini_result.notes,
            },
        }

        context_json = json.dumps(context, ensure_ascii=False, indent=2)

        system_prompt = (
            "你是加密交易台的资深分析师，擅长深度分析高价值信号。\n"
            "当前任务：基于 Gemini 的初步分析，进行深度验证和优化。\n\n"
            "## 分析要点\n"
            "1. **验证 Gemini 判断**：检查事件类型、资产识别、置信度是否合理\n"
            "2. **历史对比**：结合 historical_reference 中的相似案例，判断当前事件的独特性\n"
            "3. **风险评估**：补充 Gemini 可能遗漏的风险点（如流动性、监管、市场情绪）\n"
            "4. **置信度校准**：基于历史案例和当前市场环境，调整置信度\n"
            "5. **可操作性**：明确 action 的具体执行策略（入场点、止损、仓位建议）\n\n"
            "## 输出要求\n"
            "严格使用 JSON 格式，字段与 Gemini 一致：\n"
            "- summary: 深度分析后的精炼摘要（中文）\n"
            "- event_type: listing | delisting | hack | regulation | funding | whale | liquidation | partnership | product_launch | governance | macro | celebrity | airdrop | other\n"
            "- asset: 加密资产代码（如 BTC、ETH），非加密资产设为 NONE\n"
            "- asset_name: 资产名称\n"
            "- action: buy | sell | observe\n"
            "- direction: long | short | neutral\n"
            "- confidence: 0-1 (两位小数)\n"
            "- strength: low | medium | high\n"
            "- risk_flags: [price_volatility, liquidity_risk, regulation_risk, confidence_low, data_incomplete]\n"
            "- notes: 深度分析要点，包括历史对比结论、风险提示、操作建议\n"
            "- links: 相关链接数组\n\n"
            "若 Gemini 分析存在明显错误（如误判资产、置信度过高），请在 notes 中说明修正理由。"
        )

        user_prompt = (
            "请基于以下上下文进行深度分析：\n"
            f"```json\n{context_json}\n```\n\n"
            "返回优化后的 JSON 分析结果。"
        )

        # Claude expects messages format for generate_signal
        return json.dumps(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            ensure_ascii=False,
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
        "输出字段固定为 summary、event_type、asset、asset_name、action、direction、confidence、strength、risk_flags、notes。"
        "event_type 仅能取 listing、delisting、hack、regulation、funding、whale、liquidation、partnership、product_launch、governance、macro、celebrity、airdrop、other。"
        "action 为 buy、sell 或 observe；direction 为 long、short 或 neutral。"
        "confidence 范围 0-1，保留两位小数，并与 high/medium/low 的 strength 保持一致性。"
        "risk_flags 为数组，枚举 price_volatility、liquidity_risk、regulation_risk、confidence_low、data_incomplete。"
        "historical_reference.entries 提供近似历史案例，包含时间、资产、动作、置信度与相似度；若列表非空，务必结合这些案例比较当前事件并在 notes 中说明与历史是否一致，若为空可直接按照当前事实判断。"
        "仅当事件直接涉及可识别的加密货币或代币（通常为 2-10 位大写字母/数字的代码，如 BTC、ETH、SOL、BNB、XRP 等）时，输出准确的币种代码；若提及股票、股指、ETF（如特斯拉、S&P500、纳指、恒生指数等）或无法确定具体加密资产，请将 asset 设置为 NONE 并在 notes 中说明原因，禁止返回 GENERAL、CRYPTO、MARKET 等泛化词。"
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
