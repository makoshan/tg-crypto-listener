-- ============================================================================
-- Fix news_events table schema to auto-generate id, created_at, updated_at
-- ============================================================================
--
-- Problem: Supabase is inserting records with NULL values for:
--   - id (should auto-increment)
--   - created_at (should default to now())
--   - updated_at (should default to now() and auto-update on changes)
--
-- Root cause: Missing DEFAULT values and triggers in the table definition
--
-- Run this script in Supabase SQL Editor to fix the issue.
-- ============================================================================

-- Step 1: Check current table structure
\echo '=== Current table structure ==='
\d news_events;

\echo ''
\echo '=== Current column defaults ==='
SELECT
    column_name,
    column_default,
    is_nullable,
    data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'news_events'
  AND column_name IN ('id', 'created_at', 'updated_at')
ORDER BY ordinal_position;

-- Step 2: Create or verify sequence for id column
\echo ''
\echo '=== Creating/verifying sequence for id column ==='
CREATE SEQUENCE IF NOT EXISTS news_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- Set the sequence ownership to the id column
ALTER SEQUENCE news_events_id_seq OWNED BY news_events.id;

-- Step 3: Set DEFAULT for id column
\echo ''
\echo '=== Setting DEFAULT for id column ==='
ALTER TABLE news_events
    ALTER COLUMN id SET DEFAULT nextval('news_events_id_seq'::regclass);

-- Step 4: Set NOT NULL constraint for id (if not already set)
ALTER TABLE news_events
    ALTER COLUMN id SET NOT NULL;

-- Step 5: Ensure id is the primary key
\echo ''
\echo '=== Ensuring id is primary key ==='
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_name = 'news_events'
          AND constraint_type = 'PRIMARY KEY'
          AND constraint_name = 'news_events_pkey'
    ) THEN
        ALTER TABLE news_events ADD PRIMARY KEY (id);
        RAISE NOTICE 'Primary key added to news_events.id';
    ELSE
        RAISE NOTICE 'Primary key already exists on news_events.id';
    END IF;
END $$;

-- Step 6: Sync sequence with existing data (if any)
\echo ''
\echo '=== Syncing sequence with existing data ==='
SELECT setval('news_events_id_seq', COALESCE((SELECT MAX(id) FROM news_events WHERE id IS NOT NULL), 0) + 1, false);

-- Step 7: Set DEFAULT for created_at column
\echo ''
\echo '=== Setting DEFAULT for created_at column ==='
ALTER TABLE news_events
    ALTER COLUMN created_at SET DEFAULT now();

-- Step 8: Set DEFAULT for updated_at column
\echo ''
\echo '=== Setting DEFAULT for updated_at column ==='
ALTER TABLE news_events
    ALTER COLUMN updated_at SET DEFAULT now();

-- Step 9: Create function to auto-update updated_at
\echo ''
\echo '=== Creating trigger function for updated_at ==='
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Step 10: Create trigger to call the function
\echo ''
\echo '=== Creating trigger for updated_at ==='
DROP TRIGGER IF EXISTS update_news_events_updated_at ON news_events;

CREATE TRIGGER update_news_events_updated_at
    BEFORE UPDATE ON news_events
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Step 11: Fix any existing NULL records (set timestamps for existing records)
\echo ''
\echo '=== Fixing existing NULL timestamps ==='
UPDATE news_events
SET
    created_at = COALESCE(created_at, now()),
    updated_at = COALESCE(updated_at, now())
WHERE created_at IS NULL OR updated_at IS NULL;

-- Step 12: Verify the fixes
\echo ''
\echo '=== Verification: Column defaults after fix ==='
SELECT
    column_name,
    column_default,
    is_nullable,
    data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'news_events'
  AND column_name IN ('id', 'created_at', 'updated_at')
ORDER BY ordinal_position;

\echo ''
\echo '=== Verification: Triggers on news_events ==='
SELECT
    trigger_name,
    event_manipulation,
    action_statement
FROM information_schema.triggers
WHERE event_object_table = 'news_events'
  AND event_object_schema = 'public';

-- Step 13: Test insert
\echo ''
\echo '=== Test insert ==='
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
    'test_schema_fix_001',
    'https://test.com/schema_fix',
    now(),
    'Test message to verify schema fix',
    'en',
    'test_hash_schema_fix_001',
    'test_canonical_schema_fix_001',
    ARRAY['test'],
    'processed',
    '{"test": true}'::jsonb
) RETURNING id, created_at, updated_at, source, source_message_id;

\echo ''
\echo '=== ✅ Schema fix complete! ==='
\echo 'If the test insert above shows id, created_at, and updated_at with non-NULL values,'
\echo 'the fix was successful. You can now delete the test record if needed:'
\echo ''
\echo 'DELETE FROM news_events WHERE source_message_id = ''test_schema_fix_001'';'
