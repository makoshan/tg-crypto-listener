"""Claude deep analysis engine implementation."""

from __future__ import annotations

import logging

from src.ai.anthropic_client import AnthropicClient, AnthropicResponse, AiServiceError

from .base import DeepAnalysisEngine, DeepAnalysisError, build_deep_analysis_messages

logger = logging.getLogger(__name__)


class ClaudeDeepAnalysisEngine(DeepAnalysisEngine):
    """Execute deep analysis through Anthropic Claude with Memory Tool support."""

    def __init__(
        self,
        *,
        client: AnthropicClient,
        parse_json_callback,
    ) -> None:
        super().__init__(provider_name="claude", parse_json_callback=parse_json_callback)
        self._client = client

    async def analyse(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        messages = build_deep_analysis_messages(payload, preliminary)
        try:
            response: AnthropicResponse = await self._client.generate_signal(messages)
        except AiServiceError as exc:
            logger.warning("Claude 深度分析失败: %s", exc)
            raise DeepAnalysisError(str(exc)) from exc

        logger.info(
            "✅ Claude 深度分析完成，token 使用: %s",
            getattr(response, "usage", None),
        )
        return self._parse_json(response.text)


from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import EventPayload, SignalResult
