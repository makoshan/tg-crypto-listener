#!/usr/bin/env python3
"""
Codex CLI 工具：记忆检索
功能：调用 HybridMemoryRepository.fetch_memories() 并输出标准 JSON 格式供 Agent 解析

推荐用法：
    python scripts/codex_tools/fetch_memory.py \
        --query "USDC depeg risk" \
        --asset USDC \
        --limit 3

备用（缺少依赖时再使用，需网络下载）：
    uvx --with-requirements requirements.txt python scripts/codex_tools/fetch_memory.py \
        --query "USDC depeg risk" \
        --asset USDC \
        --limit 3
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config import Config
from src.memory.factory import create_memory_backend


async def main():
    parser = argparse.ArgumentParser(
        description="Fetch memory entries for Codex CLI Agent"
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Query text for memory retrieval",
    )
    parser.add_argument(
        "--asset",
        help="Asset code for filtering (e.g., USDC, BTC)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of memory entries to retrieve (default: 3)",
    )
    args = parser.parse_args()

    try:
        # Load configuration
        config = Config()

        # Create memory backend
        memory_bundle = create_memory_backend(config)

        if not memory_bundle.enabled or memory_bundle.repository is None:
            output = {
                "success": True,
                "entries": [],
                "similarity_floor": None,
                "message": "Memory system is disabled or not configured",
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return

        # Parse keywords from query
        keywords = args.query.split()

        # Parse asset codes
        asset_codes = [args.asset] if args.asset else []

        # Execute memory retrieval
        repo = memory_bundle.repository

        # Check if repository has fetch_memories method (Hybrid/Supabase)
        if hasattr(repo, "fetch_memories"):
            context = await repo.fetch_memories(
                embedding=None,  # Codex CLI doesn't generate embeddings, use keyword fallback
                asset_codes=asset_codes,
                keywords=keywords,
            )
            entries = list(context.entries) if hasattr(context, "entries") else []
        # Fallback to local store's load_entries method
        elif hasattr(repo, "load_entries"):
            entries = repo.load_entries(
                keywords=keywords,
                limit=args.limit,
                min_confidence=getattr(config, "MEMORY_MIN_CONFIDENCE", 0.6),
            )
        else:
            raise AttributeError("Memory repository has no fetch_memories or load_entries method")

        # Limit results
        entries = entries[:args.limit]

        # Convert MemoryEntry objects to dicts
        entries_data = []
        similarities = []

        for entry in entries:
            entry_dict = {
                "id": entry.id,
                "summary": entry.summary,
                "action": entry.action,
                "confidence": entry.confidence,
                "similarity": entry.similarity,
                "assets": entry.assets,
                "evidence": getattr(entry, "evidence", ""),
                "timestamp": entry.created_at.isoformat() if hasattr(entry, "created_at") else "",
            }
            entries_data.append(entry_dict)
            similarities.append(entry.similarity)

        # Calculate similarity floor
        similarity_floor = min(similarities) if similarities else None

        output = {
            "success": True,
            "entries": entries_data,
            "similarity_floor": similarity_floor,
            "message": f"Retrieved {len(entries_data)} memory entries",
        }

        # Print JSON to stdout (for Codex Agent parsing)
        print(json.dumps(output, ensure_ascii=False, indent=2))

    except Exception as exc:
        # Print error as JSON with success=false
        error_output = {
            "success": False,
            "entries": [],
            "similarity_floor": None,
            "message": str(exc),
        }
        print(json.dumps(error_output, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
