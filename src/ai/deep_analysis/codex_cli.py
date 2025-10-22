"""Codex CLI deep analysis engine implementation."""

from __future__ import annotations

import asyncio
import logging
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
    ) -> None:
        super().__init__(provider_name="codex_cli", parse_json_callback=parse_json_callback)
        self._cli_path = cli_path or "codex"
        self._timeout = max(1.0, float(timeout))
        self._context_refs = tuple(ref for ref in (context_refs or ()) if ref)
        self._extra_args = tuple(str(arg) for arg in (extra_cli_args or ()))
        self._max_retries = max(0, int(max_retries))
        self._working_directory = working_directory

    async def analyse(  # pragma: no cover - exercised via dedicated tests
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> "SignalResult":
        prompt = self._build_cli_prompt(payload, preliminary)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                raw_output = await self._invoke_cli(prompt)
                json_payload = self._extract_json(raw_output)
                result = self._parse_json(json_payload)
                result.raw_response = raw_output
                logger.info(
                    "✅ Codex CLI 深度分析完成 (attempt %s/%s)",
                    attempt + 1,
                    self._max_retries + 1,
                )
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
                    break

        message = str(last_error) if last_error else "Codex CLI 未返回结果"
        raise DeepAnalysisError(message)

    def _build_cli_prompt(
        self,
        payload: "EventPayload",
        preliminary: "SignalResult",
    ) -> str:
        """Flatten chat-style prompts into a single CLI-friendly prompt."""
        messages = build_deep_analysis_messages(payload, preliminary)
        sections: list[str] = []

        for item in messages:
            role = item.get("role", "user")
            header = "系统指令" if role == "system" else "分析任务"
            content = item.get("content", "").strip()
            if not content:
                continue
            sections.append(f"{header}:\n{content}")

        if self._context_refs:
            joined_refs = "\n".join(self._context_refs)
            sections.append(f"参考资料:\n{joined_refs}")

        sections.append(
            "请严格按照要求，仅输出一个 JSON 对象，禁止输出 Markdown 代码块、额外说明或多段 JSON。"
        )
        return "\n\n".join(sections)

    async def _invoke_cli(self, prompt: str) -> str:
        """Execute Codex CLI and return stdout."""
        command = [self._cli_path, "exec"]
        command.extend(self._extra_args)
        command.append(prompt)

        logger.debug("Codex CLI 命令: %s", command[:-1] + ["<prompt>"])

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_directory,
            )
        except FileNotFoundError as exc:
            logger.error("Codex CLI 未找到: %s", self._cli_path)
            raise DeepAnalysisError(
                f"Codex CLI 未找到，请检查 CODEX_CLI_PATH 设置: {exc}"
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("Codex CLI 进程启动失败: %s", exc, exc_info=True)
            raise DeepAnalysisError(f"Codex CLI 启动失败: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            logger.error("Codex CLI 超时 (%.1fs)", self._timeout)
            raise DeepAnalysisError(f"Codex CLI 超时 {self._timeout:.1f}s") from exc

        if process.returncode != 0:
            stderr_text = (stderr.decode("utf-8", errors="replace") or "").strip()
            logger.error("Codex CLI 退出码 %s: %s", process.returncode, stderr_text[:600])
            raise DeepAnalysisError(
                f"Codex CLI 失败 (exit={process.returncode}): {stderr_text or 'no stderr'}"
            )

        output_text = stdout.decode("utf-8", errors="replace")
        logger.debug("Codex CLI 输出预览: %s", output_text[:400])
        return output_text.strip()

    @staticmethod
    def _extract_json(text: str) -> str:
        """Best-effort extraction of JSON payload from CLI output."""
        candidate = (text or "").strip()
        if not candidate:
            return candidate

        # Remove Markdown code fences if present
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                candidate = "\n".join(lines[1:-1]).strip()

        if candidate.lower().startswith("json"):
            candidate = candidate[4:].lstrip(" :\n")

        if candidate.lower().startswith("python"):
            candidate = candidate[6:].lstrip(" :\n")

        return candidate


from typing import TYPE_CHECKING  # isort: skip

if TYPE_CHECKING:  # pragma: no cover
    from src.ai.signal_engine import EventPayload, SignalResult
