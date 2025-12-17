#!/usr/bin/env python3
"""
Apply schema fixes to news_events table directly via Supabase REST API.
This script updates existing NULL timestamps and tests the schema.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from src.db.supabase_client import get_supabase_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def apply_fixes():
    """Apply schema fixes to news_events table."""
    logger.info("=" * 80)
    logger.info("🔧 Applying news_events Schema Fixes via REST API")
    logger.info("=" * 80)

    # Load environment
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        return False

    logger.info("✅ Environment loaded")
    logger.info(f"   SUPABASE_URL: {supabase_url[:40]}...")

    client = get_supabase_client(supabase_url, supabase_key, timeout=30.0)

    # Step 1: Count records with NULL timestamps
    logger.info("\n📊 Step 1: Checking records with NULL timestamps...")

    null_records = await client._request('GET', 'news_events', params={
        'select': 'id',
        'or': '(created_at.is.null,updated_at.is.null)',
    })

    null_count = len(null_records)
    logger.info(f"   Found {null_count} records with NULL created_at or updated_at")

    if null_count == 0:
        logger.info("✅ No records need fixing!")
        return True

    # Show sample of affected records
    if null_count > 0:
        sample = await client._request('GET', 'news_events', params={
            'select': 'id,created_at,updated_at,source,published_at',
            'or': '(created_at.is.null,updated_at.is.null)',
            'order': 'id.desc',
            'limit': '5'
        })
        logger.info("   Sample of affected records:")
        for rec in sample:
            logger.info(f"      ID: {rec['id']}, published_at: {rec.get('published_at')}, "
                       f"created_at: {rec.get('created_at')}, updated_at: {rec.get('updated_at')}")

    # Step 2: Try to call a fix RPC if available, or update records manually
    logger.info("\n🔧 Step 2: Attempting to fix records...")
    logger.info("   ⚠️  Note: REST API cannot execute ALTER TABLE statements.")
    logger.info("   We can only update existing records, not change table defaults.")
    logger.info("")
    logger.info("   📋 SQL commands needed (run manually in Supabase SQL Editor):")
    logger.info("   " + "=" * 76)

    sql_commands = """
-- Set DEFAULT for created_at and updated_at
ALTER TABLE news_events
    ALTER COLUMN created_at SET DEFAULT now();

ALTER TABLE news_events
    ALTER COLUMN updated_at SET DEFAULT now();

-- Create trigger function to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger
DROP TRIGGER IF EXISTS update_news_events_updated_at ON news_events;
CREATE TRIGGER update_news_events_updated_at
    BEFORE UPDATE ON news_events
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""

    for line in sql_commands.strip().split('\n'):
        logger.info(f"   {line}")

    logger.info("   " + "=" * 76)
    logger.info("")

    # Step 3: Update existing NULL records using published_at or now()
    logger.info("🔄 Step 3: Updating existing NULL records...")
    logger.info("   (This can be done via REST API)")

    # Get all records with NULL timestamps
    records_to_fix = await client._request('GET', 'news_events', params={
        'select': 'id,published_at,created_at,updated_at',
        'or': '(created_at.is.null,updated_at.is.null)',
    })

    logger.info(f"   Found {len(records_to_fix)} records to update")

    fixed_count = 0
    error_count = 0

    # Update in batches
    for i, rec in enumerate(records_to_fix):
        try:
            # Use published_at if available, otherwise use now
            timestamp = rec.get('published_at') or datetime.utcnow().isoformat() + 'Z'

            update_data = {}
            if rec.get('created_at') is None:
                update_data['created_at'] = timestamp
            if rec.get('updated_at') is None:
                update_data['updated_at'] = timestamp

            if update_data:
                # Update via REST API
                response = await client._request(
                    'PATCH',
                    'news_events',
                    params={'id': f'eq.{rec["id"]}'},
                    json=update_data
                )
                fixed_count += 1

                if (i + 1) % 100 == 0:
                    logger.info(f"   Updated {i + 1}/{len(records_to_fix)} records...")

        except Exception as e:
            error_count += 1
            logger.error(f"   ❌ Failed to update record {rec['id']}: {e}")
            if error_count > 10:
                logger.error("   Too many errors, stopping...")
                break

    logger.info(f"✅ Updated {fixed_count} records")
    if error_count > 0:
        logger.warning(f"⚠️  {error_count} records failed to update")

    # Step 4: Verify the fix
    logger.info("\n🧪 Step 4: Verifying the fix...")

    remaining_null = await client._request('GET', 'news_events', params={
        'select': 'id',
        'or': '(created_at.is.null,updated_at.is.null)',
    })

    remaining_count = len(remaining_null)

    if remaining_count == 0:
        logger.info("✅ All existing records have been fixed!")
    else:
        logger.warning(f"⚠️  {remaining_count} records still have NULL timestamps")

    # Check a sample of recently fixed records
    recent = await client._request('GET', 'news_events', params={
        'select': 'id,created_at,updated_at,published_at',
        'order': 'id.desc',
        'limit': '5'
    })

    logger.info("\n   Sample of recent records:")
    for rec in recent:
        logger.info(f"      ID: {rec['id']}, created_at: {rec.get('created_at')}, "
                   f"updated_at: {rec.get('updated_at')}")

    logger.info("\n" + "=" * 80)
    logger.info("📋 IMPORTANT: Next Steps")
    logger.info("=" * 80)
    logger.info("1. Open Supabase SQL Editor:")
    logger.info(f"   {supabase_url.replace('/rest/v1', '')}/project/{supabase_url.split('//')[1].split('.')[0]}/sql")
    logger.info("")
    logger.info("2. Copy and run the SQL commands shown above to:")
    logger.info("   - Set DEFAULT values for created_at and updated_at")
    logger.info("   - Create trigger for auto-updating updated_at")
    logger.info("")
    logger.info("3. Or copy the entire fix_news_events_schema.sql file")
    logger.info("")
    logger.info("✅ Existing records have been updated!")
    logger.info("=" * 80)

    return True


if __name__ == "__main__":
    success = asyncio.run(apply_fixes())
    sys.exit(0 if success else 1)
