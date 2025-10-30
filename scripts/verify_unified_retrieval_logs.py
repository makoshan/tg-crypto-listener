#!/usr/bin/env python3
"""验证统一检索方案的日志输出。

检查日志是否足够清晰，能追踪：
1. 使用的是 search_memory RPC（不是 search_memory_events）
2. 向量/关键词检索的统计信息
3. 降级逻辑的执行
"""

import os
import sys
import asyncio
import logging
from io import StringIO
from typing import List, Optional

from dotenv import load_dotenv

# 设置日志捕获
log_capture = StringIO()
handler = logging.StreamHandler(log_capture)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

# 获取所有相关 logger
loggers_to_check = [
    "src.memory.repository",
    "src.db.repositories",
    "src.memory.coordinator",
]

all_loggers = []
for logger_name in loggers_to_check:
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    all_loggers.append(logger)

# 同时添加到 root logger 确保捕获所有日志
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

from src.db.supabase_client import get_supabase_client
from src.memory.repository import SupabaseMemoryRepository, MemoryRepositoryConfig
from src.memory.coordinator import fetch_memory_evidence


async def test_and_verify_logs() -> bool:
    """运行测试并验证日志输出。"""
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not supabase_key:
        print("⚠️  缺少 SUPABASE_URL 或 SUPABASE_SERVICE_KEY，跳过实际测试")
        print("📋 将仅检查代码中的日志点...")
        return check_log_points_in_code()

    print("=" * 80)
    print("🧪 测试统一检索方案并验证日志输出")
    print("=" * 80)
    print()

    client = get_supabase_client(url=supabase_url, service_key=supabase_key)
    repo = SupabaseMemoryRepository(client, MemoryRepositoryConfig(max_notes=3))

    # 清空日志捕获
    log_capture.seek(0)
    log_capture.truncate(0)

    # 测试 1: SupabaseMemoryRepository.fetch_memories()
    print("📝 测试 1: SupabaseMemoryRepository.fetch_memories()")
    print("-" * 80)
    try:
        await repo.fetch_memories(embedding=None, keywords=["bitcoin", "etf"])
        print("✅ 调用完成")
    except Exception as exc:
        print(f"⚠️  调用失败: {exc}")

    # 检查日志
    logs = log_capture.getvalue()
    print("\n📋 捕获的日志:")
    print(logs)
    print()

    # 验证日志
    checks = {
        "search_memory_rpc": "search_memory" in logs and "search_memory_events" not in logs,
        "unified_retrieval_start": "统一检索" in logs or "统一检索 RPC" in logs,
        "stats_info": "vector=" in logs or "keyword=" in logs or "total=" in logs,
        "no_old_rpc": "search_memory_events" not in logs.lower(),
    }

    print("=" * 80)
    print("✅ 日志验证结果:")
    print("=" * 80)
    all_passed = True
    for check_name, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"{status} {check_name}: {passed}")
        if not passed:
            all_passed = False

    # 测试 2: fetch_memory_evidence 协调器
    print("\n" + "=" * 80)
    print("📝 测试 2: fetch_memory_evidence() 协调器")
    print("=" * 80)

    class TestConfig:
        SUPABASE_URL = supabase_url
        SUPABASE_SERVICE_KEY = supabase_key

    log_capture.seek(0)
    log_capture.truncate(0)

    try:
        result = await fetch_memory_evidence(
            config=TestConfig(),
            embedding_1536=None,
            keywords=["bitcoin"],
            asset_codes=None,
            match_count=3,
        )
        print("✅ 协调器调用完成")
        print(f"   结果: {list(result.keys())}")
    except Exception as exc:
        print(f"⚠️  调用失败: {exc}")

    logs2 = log_capture.getvalue()
    print("\n📋 协调器日志:")
    print(logs2)

    coordinator_checks = {
        "coordinator_start": "fetch_memory_evidence" in logs2,
        "unified_search": "统一检索" in logs2,
        "stats_displayed": "total=" in logs2 or "vector=" in logs2 or "keyword=" in logs2,
    }

    print("\n✅ 协调器日志验证:")
    for check_name, passed in coordinator_checks.items():
        status = "✅" if passed else "❌"
        print(f"{status} {check_name}: {passed}")
        if not passed:
            all_passed = False

    return all_passed


def check_log_points_in_code() -> bool:
    """检查代码中的日志点（不需要实际运行）。"""
    print("=" * 80)
    print("📋 检查代码中的日志点")
    print("=" * 80)

    import os
    import re

    log_files = [
        "src/memory/repository.py",
        "src/db/repositories.py",
        "src/memory/coordinator.py",
    ]

    checks = {}
    for file_path in log_files:
        if not os.path.exists(file_path):
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        file_checks = {
            "has_search_memory_log": "search_memory" in content and "RPC" in content,
            "no_search_memory_events": "search_memory_events" not in content.lower(),
            "has_unified_retrieval": "统一检索" in content,
            "has_stats_log": "vector=" in content or "keyword=" in content or "total=" in content,
        }

        checks[file_path] = file_checks

    all_good = True
    for file_path, file_checks in checks.items():
        print(f"\n📄 {file_path}:")
        for check_name, passed in file_checks.items():
            status = "✅" if passed else "❌"
            print(f"  {status} {check_name}: {passed}")
            if not passed:
                all_good = False

    return all_good


async def main():
    """主函数。"""
    try:
        passed = await test_and_verify_logs()
        print("\n" + "=" * 80)
        if passed:
            print("✅ 所有日志验证通过！统一检索方案日志输出正常。")
        else:
            print("⚠️  部分日志验证未通过，请检查日志输出。")
        print("=" * 80)
        sys.exit(0 if passed else 1)
    except Exception as exc:
        print(f"\n❌ 验证过程出错: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
