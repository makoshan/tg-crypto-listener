"""Synthesis node for generating final signal."""
import json
import logging
from typing import Any, Dict

from .base import BaseNode
from ..helpers.prompts import build_synthesis_prompt

logger = logging.getLogger(__name__)


class SynthesisNode(BaseNode):
    """Node for synthesizing all evidence into final signal."""

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesize evidence and generate final signal."""
        logger.info("📊 Synthesis: 生成最终分析")

        prompt = build_synthesis_prompt(state, self.engine)
        final_json = await self._invoke_text_model(prompt)

        try:
            parsed = json.loads(final_json)
            final_conf = parsed.get("confidence", 0.0)
            prelim_conf = state["preliminary"].confidence
            logger.info("📊 Synthesis: 最终置信度 %.2f (初步 %.2f)", final_conf, prelim_conf)
        except Exception:
            logger.warning("📊 Synthesis: 无法解析最终 JSON")

        return {"final_response": final_json}

    async def _invoke_text_model(self, prompt: str) -> str:
        """Invoke Gemini for text generation."""
        messages = [{"role": "user", "content": prompt}]
        response = await self.engine._client.generate_content_with_tools(messages, tools=None)

        if not response or not response.text:
            raise Exception("Gemini 返回空响应")

        return response.text.strip()
