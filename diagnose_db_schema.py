#!/usr/bin/env python3
"""
Diagnostic script to check news_events table schema and configuration.
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

from src.db.supabase_client import get_supabase_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def check_table_schema():
    """Check the news_events table schema."""
    logger.info("=" * 80)
    logger.info("Database Schema Diagnostic")
    logger.info("=" * 80)

    # Load environment
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        return

    client = get_supabase_client(supabase_url, supabase_key)

    # Query table schema from information_schema
    logger.info("\n📋 Querying news_events table columns...")
    try:
        response = await client.rpc(
            "sql",
            {
                "query": """
                    SELECT
                        column_name,
                        data_type,
                        column_default,
                        is_nullable,
                        character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'news_events'
                    ORDER BY ordinal_position;
                """
            },
        )
        logger.info(f"Schema query response: {response}")
    except Exception as e:
        logger.warning(f"⚠️ Could not query via RPC sql: {e}")
        logger.info("Trying alternative method...")

    # Try to get a sample record to see the structure
    logger.info("\n📊 Fetching a sample record...")
    try:
        # Use select with limit 1
        sample = await client.select_one(
            "news_events",
            filters={},
            columns="id,created_at,updated_at,source,source_message_id",
        )
        if sample:
            logger.info("✅ Sample record found:")
            for key, value in sample.items():
                logger.info(f"   {key}: {value} (type: {type(value).__name__})")
        else:
            logger.warning("⚠️ No records found in news_events table")
    except Exception as e:
        logger.error(f"❌ Error fetching sample: {e}")

    # Check if there are any records in the table
    logger.info("\n🔍 Checking table record count...")
    try:
        response = await client._request(
            "HEAD",
            "news_events",
            params={"select": "*", "limit": "1"},
            headers={"Prefer": "count=exact"},
        )
        logger.info(f"HEAD request response: {response}")
    except Exception as e:
        logger.warning(f"⚠️ Could not get record count: {e}")

    logger.info("\n" + "=" * 80)
    logger.info("🔍 Diagnosis:")
    logger.info("=" * 80)
    logger.info("""
The issue is that Supabase is returning records with:
- id: null
- created_at: null
- updated_at: null

This indicates that the PostgreSQL table is missing:

1. DEFAULT nextval('news_events_id_seq') for the id column
   OR the id column is not configured as a SERIAL/BIGSERIAL type

2. DEFAULT now() for the created_at column

3. A trigger to update updated_at on modifications

Recommended fixes in Supabase SQL Editor:

```sql
-- 1. Check current table definition
\\d news_events;

-- 2. Fix id column (if not already BIGSERIAL)
ALTER TABLE news_events
  ALTER COLUMN id SET DEFAULT nextval('news_events_id_seq');

-- Or if sequence doesn't exist:
CREATE SEQUENCE IF NOT EXISTS news_events_id_seq;
ALTER TABLE news_events
  ALTER COLUMN id SET DEFAULT nextval('news_events_id_seq');

-- 3. Fix created_at default
ALTER TABLE news_events
  ALTER COLUMN created_at SET DEFAULT now();

-- 4. Fix updated_at with trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_news_events_updated_at
  BEFORE UPDATE ON news_events
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

-- 5. Verify fixes
SELECT column_name, column_default
FROM information_schema.columns
WHERE table_name = 'news_events'
  AND column_name IN ('id', 'created_at', 'updated_at');
```
""")


if __name__ == "__main__":
    asyncio.run(check_table_schema())
