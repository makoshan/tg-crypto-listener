import os
import json
import argparse
import asyncio
from typing import List, Optional

from src.db.supabase_client import get_supabase_client
from src.db.repositories import MemoryRepository
from src.utils import setup_logger
from dotenv import load_dotenv


async def main() -> None:
    # Load .env from project root
    load_dotenv()
    parser = argparse.ArgumentParser(description="Test Supabase search_memory RPC")
    parser.add_argument("--keywords", nargs="*", default=None, help="keywords list, e.g. bitcoin etf")
    parser.add_argument("--embedding-file", type=str, default=None, help="path to JSON file containing 1536-dim embedding list")
    parser.add_argument("--assets", nargs="*", default=None, help="asset code filters, e.g. BTC ETH SOL")
    parser.add_argument("--match-threshold", type=float, default=float(os.getenv("MEMORY_MATCH_THRESHOLD", 0.85)))
    parser.add_argument("--min-confidence", type=float, default=float(os.getenv("MEMORY_MIN_CONFIDENCE", 0.6)))
    parser.add_argument("--time-window-hours", type=int, default=int(os.getenv("MEMORY_TIME_WINDOW_HOURS", 72)))
    parser.add_argument("--match-count", type=int, default=int(os.getenv("MEMORY_MATCH_COUNT", 5)))
    args = parser.parse_args()

    logger = setup_logger("search_memory_test")

    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not supabase_key:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in environment")
        return

    embedding: Optional[List[float]] = None
    if args.embedding_file:
        try:
            with open(args.embedding_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    embedding = [float(x) for x in data]
        except Exception as exc:
            logger.error("Failed to load embedding file: %s", exc)

    keywords = args.keywords if args.keywords else None
    asset_codes = args.assets if args.assets else None

    client = get_supabase_client(url=supabase_url, service_key=supabase_key)
    repo = MemoryRepository(client)

    result = await repo.search_memory(
        embedding_1536=embedding,
        keywords=keywords,
        asset_codes=asset_codes,
        match_threshold=args.match_threshold,
        min_confidence=args.min_confidence,
        time_window_hours=args.time_window_hours,
        match_count=args.match_count,
    )

    # Pretty print
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())


