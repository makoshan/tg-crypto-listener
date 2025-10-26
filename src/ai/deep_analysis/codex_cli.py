"""Codex CLI deep analysis engine implementation."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Sequence

from .base import DeepAnalysisEngine, DeepAnalysisError, build_deep_analysis_messages

logger = logging.getLogger(__name__)


class CodexCliDeepAnalysisEngine(DeepAnalysisEngine):
    """Execute deep analysis through an external Codex CLI process."""

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
        disable_after_failures: int = 2,
        failure_cooldown: float = 300.0,
    ) -> None:
        super().__init__(provider_name="codex_cli", parse_json_callback=parse_json_callback)
        self._cli_path = cli_path or "codex"
        self._timeout = max(1.0, float(timeout))
        self._context_refs = tuple(ref for ref in (context_refs or ()) if ref)
        self._extra_args = tuple(str(arg) for arg in (extra_cli_args or ()))
        self._max_retries = max(0, int(max_retries))
        self._working_directory = working_directory
        self._disable_after_failures = max(1, int(disable_after_failures))
        self._failure_cooldown = max(0.0, float(failure_cooldown))
        self._consecutive_failures: int = 0
        self._cooldown_until: float = 0.0

    async def analyse(  # pragma: no cover - exercised via dedicated tests
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        if self._is_in_cooldown():
            remaining = max(0.0, self._cooldown_until - time.time())
            logger.warning(
                "⏭️  Codex CLI 深度分析处于冷却期 (剩余 %.1fs)，跳过调用",
                remaining,
            )
            raise DeepAnalysisError(
                f"Codex CLI 暂停中，冷却剩余 {remaining:.0f}s"
            )

        logger.info(
            "🤖 开始 Codex CLI 深度分析: source=%s event_type=%s asset=%s confidence=%.2f",
            payload.source,
            preliminary.event_type,
            preliminary.asset,
            preliminary.confidence,
        )

        prompt = self._build_cli_prompt(payload, preliminary)
        logger.debug("Codex CLI prompt 长度: %d 字符", len(prompt))

        # 记录 prompt 的关键部分（历史记忆上下文）
        if payload.historical_reference and payload.historical_reference.get("entries"):
            entries = payload.historical_reference.get("entries", [])
            logger.info(
                "📚 Claude CLI 接收历史记忆上下文: %d 条记录",
                len(entries)
            )
            for i, entry in enumerate(entries[:5], 1):  # 最多显示前5条
                logger.info(
                    "  记忆[%d]: asset=%s action=%s conf=%.2f sim=%.2f summary=%s",
                    i,
                    getattr(entry, 'assets', 'N/A'),
                    getattr(entry, 'action', 'N/A'),
                    getattr(entry, 'confidence', 0.0),
                    getattr(entry, 'similarity', 0.0),
                    getattr(entry, 'summary', '')[:80]
                )
        else:
            logger.info("📚 Claude CLI 无历史记忆上下文")

        # 在 DEBUG 级别记录完整的 prompt（仅用于深度调试）
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("=" * 80)
            logger.debug("完整 Codex CLI Prompt:")
            logger.debug("-" * 80)
            logger.debug(prompt)
            logger.debug("=" * 80)

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                logger.debug(
                    "Codex CLI 调用开始 (attempt %s/%s): cli_path=%s timeout=%.1fs",
                    attempt + 1,
                    self._max_retries + 1,
                    self._cli_path,
                    self._timeout,
                )

                raw_output = await self._invoke_cli(prompt)
                logger.debug("Codex CLI 原始输出长度: %d 字符", len(raw_output))

                json_payload = self._extract_json(raw_output)
                logger.debug("Codex CLI JSON 提取完成，长度: %d 字符", len(json_payload))

                result = self._parse_json(json_payload)
                result.raw_response = raw_output
                logger.info(
                    "✅ Codex CLI 深度分析完成 (attempt %s/%s): action=%s confidence=%.2f asset=%s",
                    attempt + 1,
                    self._max_retries + 1,
                    result.action,
                    result.confidence,
                    result.asset,
                )

                # 显示 Claude 的推理过程（notes 字段）
                if result.notes:
                    logger.info("🧠 Claude 推理细节:")
                    # 将 notes 按行分割，每行单独记录
                    notes_lines = result.notes.strip().split('\n')
                    for line in notes_lines[:10]:  # 最多显示前10行
                        if line.strip():
                            logger.info("   %s", line.strip())
                    if len(notes_lines) > 10:
                        logger.info("   ... (共 %d 行，已省略 %d 行)", len(notes_lines), len(notes_lines) - 10)

                # 显示风险标记和链接
                if result.risk_flags:
                    logger.info("⚠️  风险标记: %s", ", ".join(result.risk_flags))
                if result.links:
                    logger.info("🔗 验证链接: %d 个", len(result.links))
                self._reset_failure_state()
                return result
            except (DeepAnalysisError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    backoff = min(1.0 + attempt, 3.0)
                    logger.warning(
                        "⚠️ Codex CLI 调用失败 (attempt %s/%s): %s，%.1fs 后重试",
                        attempt + 1,
                        self._max_retries + 1,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "❌ Codex CLI 所有重试均失败 (%s/%s): %s",
                        self._max_retries + 1,
                        self._max_retries + 1,
                        exc,
                    )
                    break

        self._register_failure(last_error)
        message = str(last_error) if last_error else "Codex CLI 未返回结果"
        raise DeepAnalysisError(message)

    def _build_cli_prompt(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> str:
        """Flatten chat-style prompts into a single CLI-friendly prompt."""
        logger.debug("构建 Codex CLI prompt: source=%s", payload.source)

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

        # Add tool usage guidelines for Codex CLI Agent
        tool_guidelines = """工具使用守则（必读）:

你可以通过 bash 命令调用以下工具来验证消息真实性和获取市场数据：

1. **新闻搜索工具** (search_news.py)
   - 用途：验证事件真实性、获取多源确认、发现关键细节
   - 优先命令：
     python scripts/codex_tools/search_news.py \\
         --query "关键词" --max-results 6
   - 备用命令（仅当本地 Python 缺少依赖时再使用，会触发网络下载）：
     uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \\
         --query "关键词" --max-results 6
   - 输出：JSON 格式，包含 success、data、confidence、triggered、error 字段
   - 何时使用：需要验证消息真伪、获取官方确认、查找事件细节
   - 示例：
     uvx --with-requirements requirements.txt python scripts/codex_tools/search_news.py \\
         --query "Binance ABC token listing official announcement" --max-results 6

2. **价格数据工具** (fetch_price.py)
   - 用途：获取资产实时价格、涨跌幅、市值、交易量数据
   - 优先命令：
     python scripts/codex_tools/fetch_price.py \\
        --assets 资产1 资产2 资产3
   - 备用命令（仅当本地 Python 缺少依赖时再使用，会触发网络下载）：
     uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_price.py \\
         --assets 资产1 资产2 资产3
   - 输出：JSON 格式，包含 success、count、assets 字段（每个资产包含 price、price_change_24h、price_change_1h、price_change_7d、market_cap、volume_24h）
   - 何时使用：需要验证价格异常、评估市场反应、量化涨跌幅
   - 示例（单个资产）：
     python scripts/codex_tools/fetch_price.py \\
        --assets BTC
   - 示例（多个资产）：
     python scripts/codex_tools/fetch_price.py \\
        --assets BTC ETH SOL

3. **历史记忆检索工具** (fetch_memory.py)
   - 用途：查找历史相似事件、参考过去案例的处理方式
   - 优先命令：
     python scripts/codex_tools/fetch_memory.py \\
        --query "主题描述" --asset 资产代码 --limit 3
   - 备用命令（仅当本地 Python 缺少依赖时再使用，会触发网络下载）：
     uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \\
         --query "主题描述" --asset 资产代码 --limit 3
   - 输出：JSON 格式，包含 success、entries、similarity_floor 字段
   - 何时使用：需要历史案例参考、判断事件独特性、评估风险
   - 示例：
     python scripts/codex_tools/fetch_memory.py \\
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
- "价格命令：python scripts/codex_tools/fetch_price.py --assets BTC ETH SOL"
- "历史记忆检索到 2 条相似案例（similarity > 0.8），过去处理方式为 observe"
- "搜索命令：python scripts/codex_tools/search_news.py --query 'Binance ABC listing official'"
- "链接：[source1_url, source2_url]（来自搜索结果）"
"""
        sections.append(tool_guidelines)

        sections.append(
            "请严格按照要求，仅输出一个 JSON 对象，禁止输出 Markdown 代码块、额外说明或多段 JSON。"
        )

        prompt = "\n\n".join(sections)
        logger.debug("Codex CLI prompt 构建完成: %d 个 section, 总长度 %d", len(sections), len(prompt))

        return prompt

    def _is_in_cooldown(self) -> bool:
        if self._cooldown_until <= 0.0:
            return False
        if time.time() >= self._cooldown_until:
            self._cooldown_until = 0.0
            self._consecutive_failures = 0
            return False
        return True

    def _reset_failure_state(self) -> None:
        self._consecutive_failures = 0
        self._cooldown_until = 0.0

    def _register_failure(self, error: Exception | None) -> None:
        self._consecutive_failures += 1
        logger.debug(
            "Codex CLI 失败计数: %d/%d",
            self._consecutive_failures,
            self._disable_after_failures,
        )
        if (
            self._failure_cooldown > 0.0
            and self._consecutive_failures >= self._disable_after_failures
        ):
            self._cooldown_until = time.time() + self._failure_cooldown
            logger.warning(
                "🚫 Codex CLI 连续失败达到上限 (%d/%d)，暂停 %.0f 秒: %s",
                self._consecutive_failures,
                self._disable_after_failures,
                self._failure_cooldown,
                error,
            )

    async def _invoke_cli(self, prompt: str) -> str:
        """Execute Codex CLI and return stdout."""
        command = [self._cli_path, "exec"]
        command.extend(self._extra_args)
        command.append(prompt)

        logger.info(
            "🚀 执行 Codex CLI: path=%s args=%s cwd=%s timeout=%.1fs",
            self._cli_path,
            self._extra_args or [],
            self._working_directory or ".",
            self._timeout,
        )
        logger.debug("Codex CLI 完整命令: %s", command[:-1] + ["<prompt>"])

        try:
            logger.debug("创建 Codex CLI 子进程...")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_directory,
            )
            logger.debug("Codex CLI 子进程已创建，PID: %s", process.pid)
        except FileNotFoundError as exc:
            logger.error("❌ Codex CLI 可执行文件未找到: %s", self._cli_path)
            raise DeepAnalysisError(
                f"Codex CLI 未找到，请检查 CODEX_CLI_PATH 设置: {exc}"
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("❌ Codex CLI 进程启动失败: %s", exc, exc_info=True)
            raise DeepAnalysisError(f"Codex CLI 启动失败: {exc}") from exc

        try:
            logger.debug("等待 Codex CLI 进程完成 (timeout=%.1fs)...", self._timeout)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
            logger.debug("Codex CLI 进程已完成")
        except asyncio.TimeoutError as exc:
            logger.error("⏰ Codex CLI 超时 (%.1fs)，正在终止进程...", self._timeout)
            process.kill()
            await process.wait()
            logger.error("❌ Codex CLI 进程已被强制终止")
            raise DeepAnalysisError(f"Codex CLI 超时 {self._timeout:.1f}s") from exc

        if process.returncode != 0:
            stderr_text = (stderr.decode("utf-8", errors="replace") or "").strip()
            logger.error(
                "❌ Codex CLI 退出码异常: %s, stderr 预览: %s",
                process.returncode,
                stderr_text[:600],
            )
            raise DeepAnalysisError(
                f"Codex CLI 失败 (exit={process.returncode}): {stderr_text or 'no stderr'}"
            )

        output_text = stdout.decode("utf-8", errors="replace")
        stderr_text = (stderr.decode("utf-8", errors="replace") or "").strip()

        if stderr_text:
            logger.debug("Codex CLI stderr 输出: %s", stderr_text[:400])

        logger.info("✅ Codex CLI 执行成功，输出长度: %d 字符", len(output_text))
        logger.debug("Codex CLI stdout 预览: %s", output_text[:400])

        return output_text.strip()

    @staticmethod
    def _extract_json(text: str) -> str:
        """Best-effort extraction of JSON payload from CLI output."""
        logger.debug("提取 JSON，原始文本长度: %d", len(text or ""))

        candidate = (text or "").strip()
        if not candidate:
            logger.warning("⚠️ Codex CLI 输出为空")
            return candidate

        # Remove Markdown code fences if present
        if candidate.startswith("```"):
            logger.debug("检测到 Markdown 代码块，正在去除...")
            lines = candidate.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                candidate = "\n".join(lines[1:-1]).strip()
                logger.debug("Markdown 代码块已去除，新长度: %d", len(candidate))

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
