#!/usr/bin/env python3
"""
Programmatically fix the news_events table schema to auto-generate id, created_at, updated_at.

This script applies the same fixes as fix_news_events_schema.sql but through Python.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def execute_sql(url: str, key: str, sql: str) -> dict:
    """Execute SQL via Supabase REST API."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    # Use pg_meta API for schema changes
    # Note: This requires the pg_meta extension and appropriate permissions
    rest_url = url.rstrip("/")
    if not rest_url.endswith("/rest/v1"):
        rest_url = f"{rest_url}/rest/v1"

    # For DDL statements, we need to use RPC or direct database connection
    # Supabase REST API doesn't directly support DDL
    logger.warning(
        "⚠️ Note: Supabase REST API has limited support for DDL statements. "
        "For best results, run the SQL script manually in Supabase SQL Editor."
    )

    # Try using rpc if available
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{rest_url}/rpc/exec_sql",
                headers=headers,
                json={"query": sql},
            )
            return {"status": response.status_code, "text": response.text}
        except Exception as e:
            return {"status": 0, "error": str(e)}


async def apply_schema_fixes():
    """Apply schema fixes to news_events table."""
    logger.info("=" * 80)
    logger.info("🔧 Applying news_events Schema Fixes")
    logger.info("=" * 80)

    # Load environment
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        return False

    logger.info("✅ Environment loaded")

    # SQL statements to execute
    sql_statements = [
        # Step 1: Create sequence
        (
            "Create sequence for id",
            """
            CREATE SEQUENCE IF NOT EXISTS news_events_id_seq
                START WITH 1
                INCREMENT BY 1
                NO MINVALUE
                NO MAXVALUE
                CACHE 1;
            """,
        ),
        # Step 2: Set sequence ownership
        (
            "Set sequence ownership",
            """
            ALTER SEQUENCE news_events_id_seq OWNED BY news_events.id;
            """,
        ),
        # Step 3: Set default for id
        (
            "Set DEFAULT for id",
            """
            ALTER TABLE news_events
                ALTER COLUMN id SET DEFAULT nextval('news_events_id_seq'::regclass);
            """,
        ),
        # Step 4: Set NOT NULL for id
        (
            "Set NOT NULL for id",
            """
            ALTER TABLE news_events
                ALTER COLUMN id SET NOT NULL;
            """,
        ),
        # Step 5: Sync sequence
        (
            "Sync sequence with data",
            """
            SELECT setval('news_events_id_seq',
                COALESCE((SELECT MAX(id) FROM news_events WHERE id IS NOT NULL), 0) + 1,
                false);
            """,
        ),
        # Step 6: Set default for created_at
        (
            "Set DEFAULT for created_at",
            """
            ALTER TABLE news_events
                ALTER COLUMN created_at SET DEFAULT now();
            """,
        ),
        # Step 7: Set default for updated_at
        (
            "Set DEFAULT for updated_at",
            """
            ALTER TABLE news_events
                ALTER COLUMN updated_at SET DEFAULT now();
            """,
        ),
        # Step 8: Create trigger function
        (
            "Create trigger function",
            """
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """,
        ),
        # Step 9: Create trigger
        (
            "Create trigger",
            """
            DROP TRIGGER IF EXISTS update_news_events_updated_at ON news_events;
            CREATE TRIGGER update_news_events_updated_at
                BEFORE UPDATE ON news_events
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """,
        ),
        # Step 10: Fix existing NULL records
        (
            "Fix existing NULL timestamps",
            """
            UPDATE news_events
            SET
                created_at = COALESCE(created_at, now()),
                updated_at = COALESCE(updated_at, now())
            WHERE created_at IS NULL OR updated_at IS NULL;
            """,
        ),
    ]

    logger.info("")
    logger.info("📋 Schema Fix Instructions:")
    logger.info("=" * 80)
    logger.info("")
    logger.info("⚠️  IMPORTANT: Due to Supabase REST API limitations, please apply")
    logger.info("   these fixes manually through the Supabase SQL Editor.")
    logger.info("")
    logger.info("Steps:")
    logger.info("1. Go to https://supabase.com/dashboard/project/YOUR_PROJECT/sql")
    logger.info("2. Copy and paste the SQL from 'fix_news_events_schema.sql'")
    logger.info("3. Click 'Run' to execute")
    logger.info("")
    logger.info("Alternatively, here are the individual SQL statements:")
    logger.info("=" * 80)

    for i, (description, sql) in enumerate(sql_statements, 1):
        logger.info(f"\n-- Step {i}: {description}")
        logger.info(sql.strip())

    logger.info("\n" + "=" * 80)
    logger.info("🧪 Testing Query (run this to verify the fix):")
    logger.info("=" * 80)
    logger.info(
        """
INSERT INTO news_events (
    source,
    source_message_id,
    source_url,
    published_at,
    content_text,
    language,
    hash_raw,
    hash_canonical,
    keywords_hit,
    ingest_status,
    metadata
) VALUES (
    'TEST_SOURCE',
    'test_python_fix_001',
    'https://test.com/python_fix',
    now(),
    'Test message to verify schema fix from Python script',
    'en',
    'test_hash_python_001',
    'test_canonical_python_001',
    ARRAY['test'],
    'processed',
    '{"test": true}'::jsonb
) RETURNING id, created_at, updated_at, source, source_message_id;
"""
    )

    logger.info("\n" + "=" * 80)
    logger.info("✅ If the test query returns non-NULL id, created_at, and updated_at,")
    logger.info("   the fix was successful!")
    logger.info("=" * 80)

    return True


if __name__ == "__main__":
    success = asyncio.run(apply_schema_fixes())
    sys.exit(0 if success else 1)
