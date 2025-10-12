#!/usr/bin/env python3
"""启用 LangGraph 工具增强流程的便捷脚本。

执行后会在项目根目录的 `.env` 中写入/更新以下键：
    - DEEP_ANALYSIS_TOOLS_ENABLED=true
    - TOOL_SEARCH_ENABLED=true
    - DEEP_ANALYSIS_MAX_TOOL_CALLS=3 (可通过 --max-calls 自定义)
    - DEEP_ANALYSIS_TOOL_TIMEOUT=15 (秒，可通过 --timeout 自定义)

如果 `.env` 不存在，会自动创建。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, Iterable


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

DEFAULT_SETTINGS: Dict[str, str] = {
    "DEEP_ANALYSIS_TOOLS_ENABLED": "true",
    "TOOL_SEARCH_ENABLED": "true",
    "DEEP_ANALYSIS_MAX_TOOL_CALLS": "3",
    "DEEP_ANALYSIS_TOOL_TIMEOUT": "15",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启用 LangGraph 工具增强流程并配置阈值")
    parser.add_argument("--env", type=Path, default=ENV_PATH, help="指定要写入的 .env 文件路径")
    parser.add_argument("--max-calls", type=int, default=3, help="工具最大调用次数")
    parser.add_argument("--timeout", type=int, default=15, help="工具调用超时时间 (秒)")
    parser.add_argument(
        "--daily-limit",
        type=int,
        default=None,
        help="每日工具调用上限 (可选，将写入 DEEP_ANALYSIS_TOOL_DAILY_LIMIT)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示将要写入的键值，不实际修改文件",
    )
    return parser.parse_args()


def read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def upsert_env(
    lines: Iterable[str],
    updates: Dict[str, str],
) -> list[str]:
    pattern = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=")
    existing: Dict[str, int] = {}
    new_lines = list(lines)

    for idx, line in enumerate(new_lines):
        match = pattern.match(line)
        if match:
            existing[match.group(1)] = idx

    for key, value in updates.items():
        if key in existing:
            new_lines[existing[key]] = f"{key}={value}"
        else:
            new_lines.append(f"{key}={value}")

    return new_lines


def main() -> int:
    args = parse_args()

    updates = dict(DEFAULT_SETTINGS)
    updates["DEEP_ANALYSIS_MAX_TOOL_CALLS"] = str(max(1, args.max_calls))
    updates["DEEP_ANALYSIS_TOOL_TIMEOUT"] = str(max(1, args.timeout))
    if args.daily_limit is not None:
        updates["DEEP_ANALYSIS_TOOL_DAILY_LIMIT"] = str(max(1, args.daily_limit))

    target_env = args.env.resolve()
    print(f"🔧 准备更新环境文件: {target_env}")
    for key, value in updates.items():
        print(f"  - {key}={value}")

    if args.dry_run:
        print("📝 dry-run 模式已启用，不会写入文件")
        return 0

    lines = read_env_lines(target_env)
    new_lines = upsert_env(lines, updates)
    target_env.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print("✅ 环境变量写入完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
