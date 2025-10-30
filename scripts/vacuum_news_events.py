"""
执行 VACUUM FULL 以回收 `news_events` 表删除空间的维护脚本。

要求：本机可用 `psql` 客户端，并能通过环境变量连接到数据库（推荐使用 Supabase 提供的 Postgres 连接参数或标准 Postgres 环境变量）。支持从项目根目录的 `.env` 自动加载（DATABASE_URL 或 PG* 变量）。

优先读取以下连接方式之一（从上到下）：
- 命令行参数 --dsn（Postgres 连接串，例如：postgres://user:pass@host:port/db）
- 环境变量 DATABASE_URL（同上）
- 标准 Postgres 环境变量（PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD），由 psql 自动读取

用法示例：
  uvx --with-requirements requirements.txt python scripts/vacuum_news_events.py
  uvx --with-requirements requirements.txt python scripts/vacuum_news_events.py --table news_events
  uvx --with-requirements requirements.txt python scripts/vacuum_news_events.py --dsn "$DATABASE_URL"

注意：VACUUM FULL 会锁表，执行期间会阻塞对该表的写入/部分读操作，建议业务低峰期执行。
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

from pathlib import Path

from dotenv import load_dotenv  # .env 支持

# 复用项目统一日志
sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
try:  # 尝试通过 importlib 加载项目内日志工具，避免静态分析误报
    import importlib
    _utils = importlib.import_module("utils")  # type: ignore[import-not-found]
    setup_logger = getattr(_utils, "setup_logger")  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - 兜底日志，避免静态分析告警
    import logging

    def setup_logger(name: str, level: str | None = None) -> logging.Logger:
        logger = logging.getLogger(name)
        if level is None:
            level = os.getenv("LOG_LEVEL", "INFO")
        if not logger.handlers:
            logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))
        return logger


def build_psql_command(sql: str, *, dsn: str | None) -> list[str]:
    """构建 psql 执行命令，不破坏现有环境变量连接行为。

    如果提供 dsn，则显式传递 --dbname；否则依赖 psql 从环境变量读取连接信息。
    """
    base_cmd: list[str] = ["psql", "--no-align", "--tuples-only", "-v", "ON_ERROR_STOP=1", "-c", sql]
    if dsn:
        return ["psql", "--no-align", "--tuples-only", "-v", "ON_ERROR_STOP=1", "--dbname", dsn, "-c", sql]
    return base_cmd


def main() -> int:
    # 先加载 .env，便于读取 DATABASE_URL / PG* 配置
    load_dotenv()
    logger = setup_logger("vacuum")

    parser = argparse.ArgumentParser(description="VACUUM FULL 指定表以回收空间")
    parser.add_argument(
        "--table",
        default=os.getenv("VACUUM_TABLE", "news_events"),
        help="要执行 VACUUM 的表名，默认: news_events",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", "").strip() or None,
        help="Postgres 连接串（可选）。若不提供，则使用环境变量 PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="执行 FULL 的同时进行 ANALYZE（默认已启用，设置该标志仅用于显式声明）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要执行的 psql 命令，不实际执行",
    )

    args = parser.parse_args()

    table = (args.table or "news_events").strip()
    if not table.isidentifier():
        logger.error("非法表名: %s", table)
        return 2

    # 生成 SQL：使用 VERBOSE 便于输出实际回收情况
    sql = f"VACUUM (FULL, VERBOSE, ANALYZE) {table};"

    cmd = build_psql_command(sql, dsn=args.dsn)
    logger.info("准备执行 VACUUM：%s", sql)
    logger.info("连接方式：%s", ("--dbname <DSN>" if args.dsn else "环境变量 (PG*/DATABASE_URL)"))

    if args.dry_run:
        logger.info("Dry Run 模式，命令如下：\n$ %s", " ".join(shlex.quote(part) for part in cmd))
        return 0

    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output = (proc.stdout or "").strip()
        if output:
            # psql VERBOSE 会输出行，直接透传
            print(output)
        logger.info("VACUUM 完成：%s", table)
        return 0
    except FileNotFoundError:
        logger.warning("找不到 psql，尝试使用 psycopg 直接连接执行 VACUUM …")
        # 回退：使用 psycopg 执行
        try:
            # 延迟导入，避免在未安装时影响其他路径
            import psycopg  # type: ignore

            conninfo = (args.dsn or os.getenv("DATABASE_URL", "").strip())
            if not conninfo:
                # 从标准 PG* 环境变量构建连接串
                host = os.getenv("PGHOST", "")
                port = os.getenv("PGPORT", "")
                db = os.getenv("PGDATABASE", "")
                user = os.getenv("PGUSER", "")
                password = os.getenv("PGPASSWORD", "")
                parts: list[str] = []
                if host:
                    parts.append(f"host={host}")
                if port:
                    parts.append(f"port={port}")
                if db:
                    parts.append(f"dbname={db}")
                if user:
                    parts.append(f"user={user}")
                if password:
                    parts.append(f"password={password}")
                conninfo = " ".join(parts)

            if not conninfo:
                logger.error("缺少数据库连接信息，请提供 --dsn 或设置 DATABASE_URL/PG* 环境变量。")
                return 2

            with psycopg.connect(conninfo, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    # psycopg 不会像 psql 那样输出 VERBOSE 明细，这里简单提示完成
                    logger.info("VACUUM 完成（psycopg）：%s", table)
            return 0
        except ModuleNotFoundError:
            logger.error(
                "未安装 psycopg。可使用 uvx 临时安装并运行：\n"
                "uvx --with psycopg[binary] --with-requirements requirements.txt python scripts/vacuum_news_events.py"
            )
            return 127
        except Exception as exc:
            logger.exception("使用 psycopg 执行失败: %s", exc)
            return 1
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "").strip()
        if output:
            print(output)
        logger.error("VACUUM 执行失败（退出码 %s）", exc.returncode)
        return exc.returncode or 1
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.exception("执行异常: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


