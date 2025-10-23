"""Claude CLI deep analysis engine implementation."""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from .base import DeepAnalysisEngine, DeepAnalysisError, build_deep_analysis_messages

logger = logging.getLogger(__name__)


class ClaudeCliDeepAnalysisEngine(DeepAnalysisEngine):
    """Execute deep analysis through Claude CLI process.

    Key differences from Codex CLI:
    - Prompt must be sent via stdin (not command line argument)
    - Uses --print for non-interactive mode
    - Uses --dangerously-skip-permissions instead of --full-auto
    - Requires explicit --allowedTools specification
    """

    def __init__(
        self,
        *,
        cli_path: str,
        timeout: float,
        parse_json_callback,
        context_refs: Sequence[str] | None = None,
        extra_cli_args: Sequence[str] | None = None,
        max_retries: int = 1,
        working_directory: str | None = None,
        allowed_tools: Sequence[str] | None = None,
    ) -> None:
        super().__init__(provider_name="claude_cli", parse_json_callback=parse_json_callback)
        self._cli_path = cli_path or "claude"
        self._timeout = max(1.0, float(timeout))
        self._context_refs = tuple(ref for ref in (context_refs or ()) if ref)
        self._extra_args = tuple(str(arg) for arg in (extra_cli_args or ()))
        self._max_retries = max(0, int(max_retries))
        self._working_directory = working_directory
        # Default allowed tools for deep analysis
        self._allowed_tools = list(allowed_tools) if allowed_tools else ["Bash", "Read"]

    async def analyse(  # pragma: no cover - exercised via dedicated tests
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        logger.info(
            "🤖 开始 Claude CLI 深度分析: source=%s event_type=%s asset=%s confidence=%.2f",
            payload.source,
            preliminary.event_type,
            preliminary.asset,
            preliminary.confidence,
        )

        prompt = self._build_cli_prompt(payload, preliminary)
        logger.debug("Claude CLI prompt 长度: %d 字符", len(prompt))

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                logger.debug(
                    "Claude CLI 调用开始 (attempt %s/%s): cli_path=%s timeout=%.1fs",
                    attempt + 1,
                    self._max_retries + 1,
                    self._cli_path,
                    self._timeout,
                )

                raw_output = await self._invoke_cli(prompt)
                logger.debug("Claude CLI 原始输出长度: %d 字符", len(raw_output))

                json_payload = self._extract_json(raw_output)
                logger.debug("Claude CLI JSON 提取完成，长度: %d 字符", len(json_payload))

                result = self._parse_json(json_payload)
                result.raw_response = raw_output
                logger.info(
                    "✅ Claude CLI 深度分析完成 (attempt %s/%s): action=%s confidence=%.2f asset=%s",
                    attempt + 1,
                    self._max_retries + 1,
                    result.action,
                    result.confidence,
                    result.asset,
                )
                return result
            except (DeepAnalysisError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    backoff = min(1.0 + attempt, 3.0)
                    logger.warning(
                        "⚠️ Claude CLI 调用失败 (attempt %s/%s): %s，%.1fs 后重试",
                        attempt + 1,
                        self._max_retries + 1,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "❌ Claude CLI 所有重试均失败 (%s/%s): %s",
                        self._max_retries + 1,
                        self._max_retries + 1,
                        exc,
                    )
                    break

        message = str(last_error) if last_error else "Claude CLI 未返回结果"
        raise DeepAnalysisError(message)

    def _build_cli_prompt(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> str:
        """Flatten chat-style prompts into a single CLI-friendly prompt.

        Uses the same prompt building logic as Codex CLI for consistency.
        """
        logger.debug("构建 Claude CLI prompt: source=%s", payload.source)

        messages = build_deep_analysis_messages(payload, preliminary)
        logger.debug("基础消息数量: %d 条", len(messages))

        sections: list[str] = []

        for item in messages:
            role = item.get("role", "user")
            header = "系统指令" if role == "system" else "分析任务"
            content = item.get("content", "").strip()
            if not content:
                continue
            sections.append(f"{header}:\n{content}")

        if self._context_refs:
            logger.debug("添加 %d 条上下文引用", len(self._context_refs))
            joined_refs = "\n".join(self._context_refs)
            sections.append(f"参考资料:\n{joined_refs}")

        # Add tool usage guidelines (same as Codex CLI)
        tool_guidelines = """工具使用守则（必读）:

你可以通过 bash 命令调用以下工具来验证消息真实性和获取市场数据：

1. **新闻搜索工具** (search_news.py)
   - 用途：验证事件真实性、获取多源确认、发现关键细节
   - 命令格式：
     uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \\
         --query "关键词" --max-results 6
   - 输出：JSON 格式，包含 success、data、confidence、triggered、error 字段
   - 何时使用：需要验证消息真伪、获取官方确认、查找事件细节
   - 示例：
     uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \\
         --query "Binance ABC token listing official announcement" --max-results 6

2. **价格数据工具** (fetch_price.py)
   - 用途：获取资产实时价格、涨跌幅、市值、交易量数据
   - 命令格式：
     uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \\
         --assets 资产1 资产2 资产3
   - 输出：JSON 格式，包含 success、count、assets 字段（每个资产包含 price、price_change_24h、price_change_1h、price_change_7d、market_cap、volume_24h）
   - 何时使用：需要验证价格异常、评估市场反应、量化涨跌幅
   - 示例（单个资产）：
     uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \\
         --assets BTC
   - 示例（多个资产）：
     uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \\
         --assets BTC ETH SOL

3. **历史记忆检索工具** (fetch_memory.py)
   - 用途：查找历史相似事件、参考过去案例的处理方式
   - 命令格式：
     uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \\
         --query "主题描述" --asset 资产代码 --limit 3
   - 输出：JSON 格式，包含 success、entries、similarity_floor 字段
   - 何时使用：需要历史案例参考、判断事件独特性、评估风险
   - 示例：
     uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \\
         --query "USDC depeg risk" --asset USDC --limit 3

**工具调用规则**：
- ✅ 必须：将执行的命令、关键数据、证据来源写入 notes 字段
- ✅ 必须：使用 JSON 输出中的数据来支持你的分析（引用 source、confidence、links 等）
- ✅ 建议：优先使用搜索工具验证高优先级事件（hack、regulation、partnership、listing）
- ✅ 建议：如果消息是传闻或缺乏来源，使用搜索工具验证
- ✅ 建议：如果涉及多个资产（如 BTC、ETH、SOL），使用价格工具**批量获取**所有资产价格
- ✅ 建议：如果消息声称价格异常或涨跌，使用价格工具验证实际数据
- ⚠️ 禁止：不要直接调用 Tavily HTTP API 或其他外部 API
- ⚠️ 禁止：不要伪造数据或在没有执行命令的情况下声称已验证
- ⚠️ 注意：如果工具返回 success=false，说明失败原因，必要时调整查询后重试

**失败处理**：
- 如果脚本返回 success=false，检查 error 字段了解失败原因
- 可以尝试调整查询关键词后重试（例如：简化关键词、使用英文、添加官方来源标识）
- 如果重试后仍然失败，在 notes 中说明"工具调用失败，依据初步分析"
- 工具失败不应阻止你完成分析，但应降低置信度并标注 data_incomplete

**证据引用示例**（在 notes 中）：
- "通过搜索工具验证：找到 5 条来源，多源确认=true，官方确认=true，confidence=0.85"
- "价格数据：BTC $107,817 (-0.68% 24h), ETH $3,245 (+1.2% 24h), SOL $185 (+0.5% 24h)"
- "价格命令：uvx ... fetch_price.py --assets BTC ETH SOL"
- "历史记忆检索到 2 条相似案例（similarity > 0.8），过去处理方式为 observe"
- "搜索命令：uvx ... search_news.py --query 'Binance ABC listing official'"
- "链接：[source1_url, source2_url]（来自搜索结果）"
"""
        sections.append(tool_guidelines)

        sections.append(
            "请严格按照要求，仅输出一个 JSON 对象，禁止输出 Markdown 代码块、额外说明或多段 JSON。"
        )

        prompt = "\n\n".join(sections)
        logger.debug("Claude CLI prompt 构建完成: %d 个 section, 总长度 %d", len(sections), len(prompt))

        return prompt

    async def _invoke_cli(self, prompt: str) -> str:
        """Execute Claude CLI and return stdout.

        CRITICAL: Claude CLI requires prompt via stdin, not command line argument.
        This is the key difference from Codex CLI.
        """
        # Build command with Claude-specific flags
        command = [self._cli_path, "--print"]

        # Add dangerously-skip-permissions for auto execution
        command.append("--dangerously-skip-permissions")

        # Add output format
        command.extend(["--output-format", "text"])

        # Add allowed tools
        if self._allowed_tools:
            command.append("--allowedTools")
            command.append(",".join(self._allowed_tools))

        # Add any extra args
        command.extend(self._extra_args)

        logger.info(
            "🚀 执行 Claude CLI: path=%s allowed_tools=%s cwd=%s timeout=%.1fs",
            self._cli_path,
            self._allowed_tools,
            self._working_directory or ".",
            self._timeout,
        )
        logger.debug("Claude CLI 完整命令: %s", command)

        try:
            logger.debug("创建 Claude CLI 子进程...")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,  # CRITICAL: Must have stdin
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_directory,
            )
            logger.debug("Claude CLI 子进程已创建，PID: %s", process.pid)
        except FileNotFoundError as exc:
            logger.error("❌ Claude CLI 可执行文件未找到: %s", self._cli_path)
            raise DeepAnalysisError(
                f"Claude CLI 未找到，请检查 CLAUDE_CLI_PATH 设置: {exc}"
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("❌ Claude CLI 进程启动失败: %s", exc, exc_info=True)
            raise DeepAnalysisError(f"Claude CLI 启动失败: {exc}") from exc

        try:
            logger.debug("通过 stdin 发送 prompt (长度: %d)...", len(prompt))
            # CRITICAL: Send prompt via stdin
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()

            logger.debug("等待 Claude CLI 进程完成 (timeout=%.1fs)...", self._timeout)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
            logger.debug("Claude CLI 进程已完成")
        except asyncio.TimeoutError as exc:
            logger.error("⏰ Claude CLI 超时 (%.1fs)，正在终止进程...", self._timeout)
            process.kill()
            await process.wait()
            logger.error("❌ Claude CLI 进程已被强制终止")
            raise DeepAnalysisError(f"Claude CLI 超时 {self._timeout:.1f}s") from exc

        if process.returncode != 0:
            stderr_text = (stderr.decode("utf-8", errors="replace") or "").strip()
            logger.error(
                "❌ Claude CLI 退出码异常: %s, stderr 预览: %s",
                process.returncode,
                stderr_text[:600],
            )
            raise DeepAnalysisError(
                f"Claude CLI 失败 (exit={process.returncode}): {stderr_text or 'no stderr'}"
            )

        output_text = stdout.decode("utf-8", errors="replace")
        stderr_text = (stderr.decode("utf-8", errors="replace") or "").strip()

        if stderr_text:
            logger.debug("Claude CLI stderr 输出: %s", stderr_text[:400])

        logger.info("✅ Claude CLI 执行成功，输出长度: %d 字符", len(output_text))
        logger.debug("Claude CLI stdout 预览: %s", output_text[:400])

        return output_text.strip()

    @staticmethod
    def _extract_json(text: str) -> str:
        """Best-effort extraction of JSON payload from CLI output.

        Claude CLI often returns explanatory text before the JSON block.
        We need to find and extract the JSON portion.
        """
        logger.debug("提取 JSON，原始文本长度: %d", len(text or ""))

        candidate = (text or "").strip()
        if not candidate:
            logger.warning("⚠️ Claude CLI 输出为空")
            return candidate

        # Strategy 1: Look for markdown code fence with json
        if "```json" in candidate:
            logger.debug("检测到 ```json 代码块...")
            # Find the start and end of the JSON block
            start_marker = "```json"
            end_marker = "```"

            start_idx = candidate.find(start_marker)
            if start_idx >= 0:
                # Find content after ```json
                content_start = start_idx + len(start_marker)
                # Find the closing ```
                end_idx = candidate.find(end_marker, content_start)
                if end_idx >= 0:
                    json_content = candidate[content_start:end_idx].strip()
                    logger.debug("从 ```json 块中提取 JSON，长度: %d", len(json_content))
                    return json_content

        # Strategy 2: Look for general markdown code fence
        if candidate.startswith("```"):
            logger.debug("检测到 Markdown 代码块，正在去除...")
            lines = candidate.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                candidate = "\n".join(lines[1:-1]).strip()
                logger.debug("Markdown 代码块已去除，新长度: %d", len(candidate))

        # Strategy 3: Look for { } JSON object in the text
        if "{" in candidate and "}" in candidate:
            logger.debug("检测到 JSON 对象标记，尝试提取...")
            start_idx = candidate.find("{")
            # Find the matching closing brace
            brace_count = 0
            end_idx = -1
            for i in range(start_idx, len(candidate)):
                if candidate[i] == "{":
                    brace_count += 1
                elif candidate[i] == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break

            if end_idx > start_idx:
                json_content = candidate[start_idx:end_idx]
                logger.debug("从文本中提取 JSON 对象，长度: %d", len(json_content))
                return json_content

        # Fallback: Clean up common prefixes
        if candidate.lower().startswith("json"):
            logger.debug("检测到 'json' 前缀，正在去除...")
            candidate = candidate[4:].lstrip(" :\n")

        if candidate.lower().startswith("python"):
            logger.debug("检测到 'python' 前缀，正在去除...")
            candidate = candidate[6:].lstrip(" :\n")

        logger.debug("JSON 提取完成，最终长度: %d", len(candidate))
        return candidate


from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import EventPayload, SignalResult
