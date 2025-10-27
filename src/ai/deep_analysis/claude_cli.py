"""Claude CLI deep analysis engine implementation.

NOTE: This implementation does NOT use the Anthropic Memory Tool API
(memory_20250818) because Claude CLI is a command-line tool that does
not support the API's tool-calling mechanism.

Instead, it uses a pre-retrieval strategy where historical memories are
injected into the prompt during construction. This is a reasonable
alternative for CLI environments where multi-turn tool calling is not
available.

For API-based implementations using the standard Memory Tool API, see
src/ai/anthropic_client.py and src/ai/deep_analysis/claude.py instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Sequence

from src.memory.claude_deep_memory_handler import ClaudeDeepAnalysisMemoryHandler

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
        memory_handler: Optional[ClaudeDeepAnalysisMemoryHandler] = None,
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
        # Memory handler for deep analysis
        self._memory_handler = memory_handler

        if self._memory_handler:
            logger.info("Claude CLI 深度分析记忆系统已启用")

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

                # Fix unescaped quotes before parsing
                fixed_json = self._fix_unescaped_quotes(json_payload)

                result = self._parse_json(fixed_json)
                result.raw_response = raw_output
                logger.info(
                    "✅ Claude CLI 深度分析完成 (attempt %s/%s): action=%s confidence=%.2f asset=%s",
                    attempt + 1,
                    self._max_retries + 1,
                    result.action,
                    result.confidence,
                    result.asset,
                )

                # Store analysis result to memory system
                self._store_analysis_result(payload, preliminary, result)

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

        capabilities = {
            "provider": "claude_cli",
            "tool_enabled": True,
            "search_enabled": True,
            "price_enabled": True,
            "macro_enabled": True,
            "onchain_enabled": True,
            "protocol_enabled": True,
            "notes": "Claude CLI 模式，可通过 bash 命令调用本地验证工具",
        }

        messages = build_deep_analysis_messages(
            payload,
            preliminary,
            additional_context={"analysis_capabilities": capabilities},
        )
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

        # Add memory context if available
        if self._memory_handler:
            memory_context = self._retrieve_memory_context(
                asset=preliminary.asset,
                event_type=preliminary.event_type
            )
            if memory_context:
                sections.append(memory_context)

        # Add tool usage guidelines (same as Codex CLI)
        tool_guidelines = """工具使用守则（必读）:

你可以通过 bash 命令调用以下工具来验证消息真实性和获取市场数据：

1. **新闻搜索工具** (search_news.py)
   - 用途：验证事件真实性、获取多源确认、发现关键细节
   - 优先命令：
     python3 scripts/codex_tools/search_news.py \\
        --query "关键词" --max-results 6
   - 备用命令（仅当本地 Python 缺少依赖时再使用，会触发网络下载）：
     uvx --with-requirements requirements.txt python3 scripts/codex_tools/search_news.py \\
         --query "关键词" --max-results 6
   - 输出：JSON 格式，包含 success、data、confidence、triggered、error 字段
   - 何时使用：需要验证消息真伪、获取官方确认、查找事件细节
   - 示例：
     uvx --with-requirements requirements.txt python3 scripts/codex_tools/search_news.py \\
         --query "Binance ABC token listing official announcement" --max-results 6

2. **价格数据工具** (fetch_price.py)
   - 用途：获取资产实时价格、涨跌幅、市值、交易量数据
   - 优先命令：
     python3 scripts/codex_tools/fetch_price.py \\
        --assets 资产1 资产2 资产3
   - 备用命令（仅当本地 Python 缺少依赖时再使用，会触发网络下载）：
     uvx --with-requirements requirements.txt python3 scripts/codex_tools/fetch_price.py \\
         --assets 资产1 资产2 资产3
   - 输出：JSON 格式，包含 success、count、assets 字段（每个资产包含 price、price_change_24h、price_change_1h、price_change_7d、market_cap、volume_24h）
   - 何时使用：需要验证价格异常、评估市场反应、量化涨跌幅
   - 示例（单个资产）：
     python3 scripts/codex_tools/fetch_price.py \\
         --assets BTC
   - 示例（多个资产）：
     python3 scripts/codex_tools/fetch_price.py \\
         --assets BTC ETH SOL

3. **历史记忆检索工具** (fetch_memory.py)
   - 用途：查找历史相似事件、参考过去案例的处理方式
   - 优先命令：
     python3 scripts/codex_tools/fetch_memory.py \\
        --query "主题描述" --asset 资产代码 --limit 3
   - 备用命令（仅当本地 Python 缺少依赖时再使用，会触发网络下载）：
     uvx --with-requirements requirements.txt python3 scripts/codex_tools/fetch_memory.py \\
         --query "主题描述" --asset 资产代码 --limit 3
   - 输出：JSON 格式，包含 success、entries、similarity_floor 字段
   - 何时使用：需要历史案例参考、判断事件独特性、评估风险
   - 示例：
     python3 scripts/codex_tools/fetch_memory.py \\
         --query "USDC depeg risk" --asset USDC --limit 3

**工具调用规则**：
- ✅ 必须：将关键数据、证据来源、分析逻辑写入 notes 字段
- ✅ 必须：使用 JSON 输出中的数据来支持你的分析（引用 source、confidence、links 等）
- ✅ 建议：优先使用搜索工具验证高优先级事件（hack、regulation、partnership、listing）
- ✅ 建议：如果消息是传闻或缺乏来源，使用搜索工具验证
- ✅ 建议：如果涉及多个资产（如 BTC、ETH、SOL），使用价格工具**批量获取**所有资产价格
- ✅ 建议：如果消息声称价格异常或涨跌，使用价格工具验证实际数据
- ⚠️ 禁止：不要直接调用 Tavily HTTP API 或其他外部 API
- ⚠️ 禁止：不要伪造数据或在没有执行命令的情况下声称已验证
- ⚠️ 禁止：不要在 notes 中包含完整的命令行指令（如 python3 scripts/...），仅说明验证方法和结果
- ⚠️ 注意：如果工具返回 success=false，说明失败原因，必要时调整查询后重试

**失败处理**：
- 如果脚本返回 success=false，检查 error 字段了解失败原因
- 可以尝试调整查询关键词后重试（例如：简化关键词、使用英文、添加官方来源标识）
- 如果重试后仍然失败，在 notes 中说明"工具调用失败，依据初步分析"
- 工具失败不应阻止你完成分析，但应降低置信度并标注 data_incomplete

**证据引用示例**（在 notes 中）：
- "通过搜索工具验证：找到 5 条来源，多源确认=true，官方确认=true，confidence=0.85"
- "价格数据验证：BTC $107,817 (-0.68% 24h), ETH $3,245 (+1.2% 24h), SOL $185 (+0.5% 24h)"
- "历史记忆检索到 2 条相似案例（similarity > 0.8），过去处理方式为 observe"
- "新闻搜索发现多篇报道验证事件真实性，包括官方公告"
- "参考链接：[source1_url, source2_url]"
"""
        sections.append(tool_guidelines)

        sections.append(
            """请严格按照要求，仅输出一个 JSON 对象，禁止输出 Markdown 代码块、额外说明或多段 JSON。

**JSON 格式要求（非常重要）**：
1. 所有字符串值中的英文双引号（"）必须用反斜杠转义：\\"
2. 如果需要表达引用或强调，请使用【】或「」等中文符号，避免使用英文引号
3. 示例错误：\\"summary\\": \\"巨鲸被标记为\\"多头战神\\"和\\"波段之王\\"\\"  ❌
4. 示例正确：\\"summary\\": \\"巨鲸被标记为【多头战神】和【波段之王】\\"  ✅
5. 或者转义：\\"summary\\": \\"巨鲸被标记为\\\\\\"多头战神\\\\\\"和\\\\\\"波段之王\\\\\\"\\"  ✅
"""
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

    @staticmethod
    def _fix_unescaped_quotes(json_str: str) -> str:
        """Fix unescaped quotes in JSON string values.

        Claude CLI sometimes returns JSON with unescaped quotes in string values,
        which causes JSON parsing to fail. This function provides a best-effort fix.

        Strategy:
        1. Try parsing as-is first
        2. If that fails, replace common quote patterns that break JSON
        3. Try parsing again

        Args:
            json_str: Potentially malformed JSON string

        Returns:
            Fixed JSON string, or original if already valid
        """
        import json

        if not json_str or not isinstance(json_str, str):
            return json_str

        # First, try parsing as-is
        try:
            json.loads(json_str)
            # Success! No need to fix
            return json_str
        except json.JSONDecodeError as e:
            logger.debug("JSON解析失败: %s at line %d col %d", e.msg, e.lineno, e.colno)

        # Simple approach: Try using json5 library which is more lenient
        try:
            import json5  # type: ignore
            parsed = json5.loads(json_str)
            # If json5 can parse it, convert back to standard JSON
            result = json.dumps(parsed, ensure_ascii=False)
            logger.info("✅ 使用json5成功修复JSON")
            return result
        except Exception:
            # json5 not available or also failed
            pass

        # Fallback: Simple heuristic fixes
        # This is not perfect but handles common cases
        result = json_str

        # Log the error location for debugging
        try:
            json.loads(result)
        except json.JSONDecodeError as e:
            # Get a snippet around the error location
            lines = result.splitlines()
            if e.lineno <= len(lines):
                error_line = lines[e.lineno - 1]
                start = max(0, e.colno - 40)
                end = min(len(error_line), e.colno + 40)
                snippet = error_line[start:end]
                logger.warning(
                    "JSON解析错误位置 (line %d, col %d): ...%s...",
                    e.lineno,
                    e.colno,
                    snippet
                )

        # Return original - let the calling code handle the parse error
        logger.info("⚠️ 无法自动修复JSON，将返回原始内容并由parse_json处理")
        return result

    def _retrieve_memory_context(self, asset: str, event_type: str) -> str:
        """
        检索相关的记忆上下文

        Args:
            asset: 资产代码
            event_type: 事件类型

        Returns:
            格式化的记忆上下文字符串
        """
        if not self._memory_handler:
            return ""

        try:
            memories = self._memory_handler.retrieve_similar_analyses(
                asset=asset,
                event_type=event_type,
                limit=3
            )

            if not memories:
                logger.debug("未找到相关历史记忆")
                return ""

            # 格式化记忆上下文
            context_parts = ["历史记忆参考（供深度分析参考）:\n"]

            for i, memory in enumerate(memories, 1):
                memory_type = memory.get("type", "unknown")
                content = memory.get("content", "")
                source = memory.get("source", "")

                # 限制每条记忆的长度
                if len(content) > 1000:
                    content = content[:1000] + "\n...[内容已截断]"

                context_parts.append(f"{i}. **{memory_type}** (来源: {source})")
                context_parts.append(f"```\n{content}\n```\n")

            logger.info("✅ 检索到 %d 条历史记忆，已添加到 prompt", len(memories))
            return "\n".join(context_parts)

        except Exception as exc:
            logger.error("检索记忆上下文失败: %s", exc, exc_info=True)
            return ""

    def _store_analysis_result(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
        final_result: "SignalResult",
    ):
        """
        存储分析结果到记忆系统

        Args:
            payload: 事件载荷
            preliminary: 初步分析结果
            final_result: 最终分析结果
        """
        if not self._memory_handler:
            return

        try:
            analysis_data = {
                "timestamp": payload.timestamp.isoformat(),
                "event_summary": preliminary.summary[:100],  # 截断摘要
                "preliminary_confidence": preliminary.confidence,
                "preliminary_action": preliminary.action,
                "final_confidence": final_result.confidence,
                "adjustment_reason": f"confidence {preliminary.confidence:.2f} → {final_result.confidence:.2f}",
                "verification_summary": "工具验证已完成" if final_result.notes else "无工具验证",
                "key_insights": final_result.notes[:200] if final_result.notes else "无洞察",
                "improvement_suggestions": "继续改进分析流程",
            }

            self._memory_handler.store_analysis_memory(
                asset=final_result.asset,
                event_type=final_result.event_type,
                analysis_data=analysis_data
            )

            logger.info("✅ 分析结果已存储到记忆系统")

        except Exception as exc:
            logger.error("存储分析结果到记忆失败: %s", exc, exc_info=True)


from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import EventPayload, SignalResult
