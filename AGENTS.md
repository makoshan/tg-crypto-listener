# Repository Guidelines

## Project Structure & Module Organization
- Core runtime lives in `src/`, with listeners (`src/listener.py`), AI helpers under `src/ai/`, pipelines in `src/pipeline/`, and persistence in `src/db/`.
- Integration tests mirror the runtime in `tests/`; add new fixtures or async helpers there rather than mixing them into production code.
- Operational assets stay outside the Python package: `docs/` holds architecture notes, `scripts/` includes utility entry points, and `examples/` demonstrates prompt and config patterns.
- Runtime state such as sessions, temporary memories, and logs should stay in the existing `session/`, `memories/`, and `logs/` directories to keep git diffs clean.

## Build, Test & Development Commands
- `uvx --with-requirements requirements.txt python -m src.listener` starts the Telegram listener with dependencies resolved into an ephemeral environment.
- `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` prepares a reusable local environment for iterative development.
- `pytest` runs the default async-aware suite (`pytest.ini` skips the `integration` marker by default).
- `pytest -m integration` executes the live-service scenarios; guard credentials with a dedicated `.env`.
- `npm run start` boots the PM2-managed process defined in `ecosystem.config.js`; pair with `npm run logs` when diagnosing long-running agents.

## Coding Style & Naming Conventions
- Stick to Python 3.10+ features already in use (pattern matching, `|` unions) and standard 4-space indentation.
- Keep modules async-friendly: prefer `async` functions and type hints (`src/listener.py` is a good reference).
- Loggers are created via `src/utils.setup_logger`; reuse it to keep colorized output consistent.
- Name new configs in `Config` using uppercase snake case and document them in README.md or related docs.

## Testing Guidelines
- Co-locate unit tests next to the feature area inside `tests/` (e.g., `tests/test_pipeline_*.py` for `src/pipeline`).
- Use descriptive `test_*` names that mention the behaviour under guard and `asyncio` fixtures when awaiting coroutines.
- When adding integration coverage, mark the test with `@pytest.mark.integration` so the default `pytest` run remains sandbox-friendly.

## Commit & Pull Request Guidelines
- Follow the existing conventional prefixes (`feat:`, `fix:`, `chore:`, `docs:`) seen in `git log -5 --oneline`; keep the subject under ~72 characters.
- Squash work-in-progress commits before opening a PR and include a concise summary, config changes, and any operational impacts.
- Link to issues or TODO docs in the PR body, attach screenshots or log excerpts when behaviour changes, and call out any new environment variables explicitly.

## Security & Configuration Tips
- Maintain secrets in `.env`; never commit `keywords.txt` or service keys—use the provided samples or environment overrides instead.
- Rotate AI and translation provider tokens regularly; `src/ai/gemini_key_rotator.py` offers a template for failover logic that new providers should follow.
- Verify third-party calls through the existing HTTP clients (`httpx` for async, `requests` for sync) to ensure retry semantics stay consistent.

## Codex 协作习惯
- 交流默认使用简体中文回复，保持上下文一致。
- Prompt/规则调整常见于 `src/ai/signal_engine.py` 与 `src/ai/deep_analysis/` 目录。
- 深度分析模型与开关配置集中在 `src/config.py`。
- 涉及流程或习惯更新时优先同步到 `AGENTS.md` 或相关 `docs/` 文档。
