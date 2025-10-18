"""
Codex CLI-based planner using subprocess execution.

Uses Claude Code CLI or compatible tools as an external planning engine,
providing advanced reasoning capabilities via CLI interface.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List

from .base import BasePlanner, ToolPlan

logger = logging.getLogger(__name__)


class CodexCliPlanner(BasePlanner):
    """
    Planner using Codex CLI (Claude Code or compatible).

    Features:
    - Claude's advanced reasoning and planning capabilities
    - CLI-based execution (subprocess)
    - Context file support via @docs/... syntax
    - Robust JSON parsing (handles markdown-wrapped output)

    Configuration (from config):
    - CODEX_CLI_PATH: Path to CLI executable (default: "codex")
    - CODEX_CLI_TIMEOUT: Command timeout in seconds (default: 60)
    - CODEX_CLI_MAX_TOKENS: Max tokens for response (default: 4000)
    - CODEX_CLI_CONTEXT: Context file reference (default: "@docs/codex_cli_integration_plan.md")
    """

    def __init__(self, engine: Any, config: Any):
        super().__init__(engine, config)
        self.cli_path = getattr(config, "CODEX_CLI_PATH", "codex")
        self.timeout = getattr(config, "CODEX_CLI_TIMEOUT", 60.0)
        self.max_tokens = getattr(config, "CODEX_CLI_MAX_TOKENS", 4000)
        self.context_file = getattr(
            config,
            "CODEX_CLI_CONTEXT",
            "@docs/codex_cli_integration_plan.md"
        )

    async def plan(
        self,
        state: Dict[str, Any],
        available_tools: List[str]
    ) -> ToolPlan:
        """Use CLI for tool planning decision."""
        # Build prompt with context
        prompt = self._build_planner_prompt(state, available_tools)

        # Execute CLI
        cli_output = await self._exec_codex(prompt)

        # Parse JSON response
        try:
            json_text = self._extract_json(cli_output)
            data = json.loads(json_text)

            tools_list = data.get("tools", [])
            keywords = data.get("search_keywords", "")
            macro_indicators = data.get("macro_indicators", []) or []
            onchain_assets = data.get("onchain_assets", []) or []
            protocol_slugs = data.get("protocol_slugs", []) or []
            reason = data.get("reason", "")

            logger.info(
                "🤖 Codex CLI Planner 决策: tools=%s, keywords='%s', macro=%s, onchain=%s, protocol=%s, 理由: %s",
                tools_list,
                keywords,
                macro_indicators,
                onchain_assets,
                protocol_slugs,
                reason,
            )

            return ToolPlan(
                tools=tools_list,
                search_keywords=keywords,
                macro_indicators=macro_indicators,
                onchain_assets=onchain_assets,
                protocol_slugs=protocol_slugs,
                reason=reason,
            )

        except json.JSONDecodeError as exc:
            logger.error("Codex CLI 返回无效 JSON: %s", exc)
            logger.error("CLI 输出 (前500字符): %s", cli_output[:500])
            raise RuntimeError(f"Invalid JSON from Codex CLI: {exc}") from exc

    async def synthesize(self, state: Dict[str, Any]) -> str:
        """Synthesize evidence into final signal JSON using CLI."""
        prompt = self._build_synthesis_prompt(state)

        try:
            cli_output = await self._exec_codex(prompt)
            json_text = self._extract_json(cli_output)

            # Validate JSON
            try:
                parsed = json.loads(json_text)
                final_conf = parsed.get("confidence", 0.0)
                prelim_conf = state["preliminary"].confidence
                logger.info(
                    "📊 Codex CLI Synthesis: 最终置信度 %.2f (初步 %.2f)",
                    final_conf,
                    prelim_conf
                )
            except json.JSONDecodeError as exc:
                logger.error("📊 Codex CLI Synthesis: JSON 解析失败 - %s", exc)
                logger.error("📊 原始响应 (前500字符): %s", cli_output[:500])
                raise RuntimeError(f"Invalid JSON from Codex CLI: {exc}") from exc

            return cli_output

        except Exception as exc:
            logger.error("Codex CLI Synthesis 失败: %s", exc)
            raise RuntimeError(f"Codex CLI synthesis failed: {exc}") from exc

    async def _exec_codex(self, prompt: str) -> str:
        """
        Execute Codex CLI command.

        Args:
            prompt: Full prompt text (may include @context references)

        Returns:
            CLI stdout output

        Raises:
            TimeoutError: If CLI execution exceeds timeout
            RuntimeError: If CLI exits with non-zero code or fails to execute
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path,
                "exec",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                logger.error("Codex CLI 超时 (%.1fs)", self.timeout)
                raise TimeoutError(f"Codex CLI timeout after {self.timeout}s") from exc

            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace")
                logger.error("Codex CLI 退出码 %d: %s", proc.returncode, stderr_text[:500])
                raise RuntimeError(
                    f"Codex CLI exited with code {proc.returncode}: {stderr_text[:200]}"
                )

            output = stdout.decode("utf-8", errors="replace")
            logger.debug("Codex CLI 输出 (前200字符): %s", output[:200])
            return output

        except FileNotFoundError as exc:
            logger.error("Codex CLI 未找到: %s", self.cli_path)
            raise RuntimeError(
                f"Codex CLI not found at '{self.cli_path}'. "
                f"Set CODEX_CLI_PATH environment variable."
            ) from exc
        except Exception as exc:
            logger.error("Codex CLI 执行失败: %s", exc)
            raise RuntimeError(f"Codex CLI execution failed: {exc}") from exc

    def _build_planner_prompt(self, state: Dict[str, Any], available_tools: List[str]) -> str:
        """Build prompt for tool planning."""
        payload = state["payload"]
        preliminary = state["preliminary"]
        evidence_summary = self._summarize_evidence(state)

        prompt = f"""你是加密交易分析专家，决定需要调用哪些工具来验证事件真实性。

【事件消息】
{payload.text}

【初步分析】
类型: {preliminary.event_type}
资产: {preliminary.asset}
操作: {preliminary.action}
置信度: {preliminary.confidence}

【已有证据】
{evidence_summary}

【可用工具】
{', '.join(available_tools)}

【输出要求】
返回 JSON 格式：
{{
  "tools": ["search", "price"],
  "search_keywords": "搜索关键词",
  "macro_indicators": ["CPI", "VIX"],
  "onchain_assets": ["USDC", "USDT"],
  "protocol_slugs": ["aave", "curve-dex"],
  "reason": "决策理由"
}}

只返回 JSON，不要包含 markdown 标记。

参考方案文档：
{self.context_file}
"""
        return prompt

    def _build_synthesis_prompt(self, state: Dict[str, Any]) -> str:
        """Build prompt for evidence synthesis."""
        payload = state["payload"]
        preliminary = state["preliminary"]
        evidence_summary = self._summarize_evidence(state)

        prompt = f"""你是加密交易分析师，综合所有证据生成最终分析报告。

【原始消息】
{payload.text}

【初步分析】
类型: {preliminary.event_type}
资产: {preliminary.asset}
操作: {preliminary.action}
置信度: {preliminary.confidence}
摘要: {preliminary.summary}

【所有证据】
{evidence_summary}

【输出要求】
返回 JSON 格式：
{{
  "summary": "中文摘要",
  "event_type": "{preliminary.event_type}",
  "asset": "{preliminary.asset}",
  "action": "buy|sell|observe",
  "confidence": 0.0-1.0,
  "notes": "推理依据",
  "links": [],
  "risk_flags": []
}}

只返回 JSON，不要包含 markdown 标记。

参考方案文档：
{self.context_file}
"""
        return prompt

    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from CLI output, handling markdown wrapping.

        Tries multiple patterns:
        1. ```json ... ```
        2. ``` ... ```
        3. Raw JSON (fallback)
        """
        # Try markdown json block
        if "```json" in text:
            match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
            if match:
                return match.group(1)

        # Try generic code block
        if "```" in text:
            match = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
            if match:
                return match.group(1)

        # Try to find JSON object
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json_match.group(0)

        # Fallback: return as-is
        return text.strip()
