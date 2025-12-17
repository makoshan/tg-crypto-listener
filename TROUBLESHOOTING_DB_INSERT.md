# Troubleshooting: Database Insert Returns NULL ID

## Problem Summary

**Symptom**: The `news_events` table inserts are returning `NULL` for `id`, `created_at`, and `updated_at` fields, causing the application to fail to persist events.

**Log Evidence**:
```
⚠️ Supabase insert 返回 id=None - table=news_events
record_values={"id": null, "created_at": null, "updated_at": null, ...}
```

## Root Cause

The PostgreSQL `news_events` table is missing:

1. **Auto-increment configuration for `id` column**
   - Missing `DEFAULT nextval('news_events_id_seq')`
   - Or the column is not configured as `SERIAL`/`BIGSERIAL`

2. **Default value for `created_at` column**
   - Missing `DEFAULT now()`

3. **Default value and trigger for `updated_at` column**
   - Missing `DEFAULT now()`
   - Missing `BEFORE UPDATE` trigger to auto-update timestamp

## Diagnosis Steps

### 1. Verify the Problem

Run the test script to confirm the issue:

```bash
python3 test_news_event_insert.py
```

**Expected output if problem exists**:
```
❌ Supabase insert 返回 id=None (list[0])
record_values={"id": null, "created_at": null, "updated_at": null, ...}
```

### 2. Check Table Schema

In Supabase SQL Editor, run:

```sql
-- Check table structure
\d news_events;

-- Check column defaults
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
```

**Problem indicators**:
- `id` column has `NULL` or empty `column_default`
- `created_at` has `NULL` or empty `column_default`
- `updated_at` has `NULL` or empty `column_default`

## Solution

### Option 1: Quick Fix (Recommended)

Run the comprehensive SQL fix script in Supabase SQL Editor:

1. Go to: `https://supabase.com/dashboard/project/YOUR_PROJECT/sql`
2. Copy and paste the contents of `fix_news_events_schema.sql`
3. Click **Run**

The script will:
- ✅ Create/configure the `news_events_id_seq` sequence
- ✅ Set `DEFAULT nextval('news_events_id_seq')` for `id` column
- ✅ Set `DEFAULT now()` for `created_at` and `updated_at`
- ✅ Create trigger to auto-update `updated_at` on record changes
- ✅ Fix any existing records with NULL timestamps
- ✅ Run a test insert to verify the fix

### Option 2: Manual Step-by-Step Fix

If you prefer to apply fixes incrementally:

```sql
-- 1. Create sequence for id column
CREATE SEQUENCE IF NOT EXISTS news_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- 2. Link sequence to id column
ALTER SEQUENCE news_events_id_seq OWNED BY news_events.id;

-- 3. Set DEFAULT for id
ALTER TABLE news_events
    ALTER COLUMN id SET DEFAULT nextval('news_events_id_seq'::regclass);

-- 4. Set NOT NULL constraint for id
ALTER TABLE news_events
    ALTER COLUMN id SET NOT NULL;

-- 5. Sync sequence with existing data
SELECT setval('news_events_id_seq',
    COALESCE((SELECT MAX(id) FROM news_events WHERE id IS NOT NULL), 0) + 1,
    false);

-- 6. Set DEFAULT for created_at
ALTER TABLE news_events
    ALTER COLUMN created_at SET DEFAULT now();

-- 7. Set DEFAULT for updated_at
ALTER TABLE news_events
    ALTER COLUMN updated_at SET DEFAULT now();

-- 8. Create trigger function for auto-updating updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 9. Create trigger
DROP TRIGGER IF EXISTS update_news_events_updated_at ON news_events;
CREATE TRIGGER update_news_events_updated_at
    BEFORE UPDATE ON news_events
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 10. Fix existing NULL records
UPDATE news_events
SET
    created_at = COALESCE(created_at, now()),
    updated_at = COALESCE(updated_at, now())
WHERE created_at IS NULL OR updated_at IS NULL;
```

## Verification

### 1. Test Insert

Run this SQL in Supabase SQL Editor:

```sql
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
    'VERIFICATION_TEST',
    'verify_fix_001',
    'https://test.com/verify',
    now(),
    'Test message to verify schema fix',
    'en',
    'verify_hash_001',
    'verify_canonical_001',
    ARRAY['test'],
    'processed',
    '{"test": true}'::jsonb
) RETURNING id, created_at, updated_at, source, source_message_id;
```

**Expected output** ✅:
```
 id  |         created_at         |         updated_at         |      source       | source_message_id
-----+----------------------------+----------------------------+-------------------+-------------------
 123 | 2025-11-02 17:30:00.123... | 2025-11-02 17:30:00.123... | VERIFICATION_TEST | verify_fix_001
```

**Problem output** ❌:
```
 id  | created_at | updated_at |      source       | source_message_id
-----+------------+------------+-------------------+-------------------
     |            |            | VERIFICATION_TEST | verify_fix_001
```

### 2. Run Python Test Script

After applying the SQL fix, verify with the Python test:

```bash
python3 test_news_event_insert.py
```

**Expected output** ✅:
```
✅ Insert successful! Event ID: 123
✅ PASS - Basic Insert
✅ PASS - Minimal Insert
✅ PASS - Direct Client Insert

Total: 3/3 tests passed
```

### 3. Check Application Logs

Run the listener and check for successful inserts:

```bash
npm run logs -- --lines 100 | grep "insert_event 成功"
```

**Expected output** ✅:
```
✅ insert_event 成功 - source=BlockBeats, event_id=456
✅ Supabase insert 成功 - table=news_events, id=456
```

## Code Enhancements Applied

The following enhancements have been added to improve debugging:

### 1. Enhanced Logging in `src/db/supabase_client.py`

**Before**:
```python
if record_id is None:
    logger.warning("⚠️ Supabase insert 返回 id=None")
```

**After** (line 74-80):
```python
if record_id is None:
    logger.error(
        "❌ Supabase insert 返回 id=None (list[0]) - table=%s, record_keys=%s, payload_keys=%s, record_values=%s",
        table,
        list(record.keys()),
        list(payload.keys()),
        json.dumps({k: (str(v)[:100] if isinstance(v, str) else v) for k, v in record.items()}, ...),
    )
```

This now provides:
- Full list of returned record keys
- Comparison with payload keys
- Sample of returned values (truncated for readability)

### 2. Debug Logging for Request/Response

Added detailed request/response logging (lines 37-42, 61-66):

```python
logger.debug(
    "🔍 Supabase insert 请求 - table=%s, payload_keys=%s, payload_preview=%s",
    table,
    list(payload.keys()),
    json.dumps(payload_preview, ensure_ascii=False, default=str)[:500],
)
```

### 3. Test Scripts Created

- `test_news_event_insert.py` - Comprehensive test with 3 scenarios
- `diagnose_db_schema.py` - Schema diagnostic tool
- `fix_db_schema.py` - Python-based fix instructions

## Prevention

To prevent this issue in the future:

### 1. Use Supabase Table Editor Defaults

When creating tables in Supabase UI, ensure:
- `id` column: Select **"INT8"** or **"BIGINT"** with **"Auto-increment"** enabled
- `created_at` column: Set **"Default value"** to `now()`
- `updated_at` column: Set **"Default value"** to `now()`

### 2. Include in Migration Scripts

For any new tables, include these defaults in your SQL migrations:

```sql
CREATE TABLE my_new_table (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- ... other columns
);

-- Trigger for updated_at
CREATE TRIGGER update_my_new_table_updated_at
    BEFORE UPDATE ON my_new_table
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

### 3. Schema Validation Tests

Add schema validation to your test suite:

```python
async def test_table_defaults():
    """Verify news_events has proper defaults."""
    client = get_supabase_client(...)

    # Insert minimal record
    record = await client.insert("news_events", {...})

    # Assert non-NULL defaults
    assert record["id"] is not None, "id should auto-generate"
    assert record["created_at"] is not None, "created_at should default to now()"
    assert record["updated_at"] is not None, "updated_at should default to now()"
```

## Related Files

- `fix_news_events_schema.sql` - SQL fix script (comprehensive)
- `fix_db_schema.py` - Python fix instructions
- `test_news_event_insert.py` - Test script to reproduce/verify
- `diagnose_db_schema.py` - Schema diagnostic tool
- `src/db/supabase_client.py` - Enhanced logging (lines 30-107)
- `src/db/repositories.py` - Repository layer (lines 91-160)

## Summary

✅ **Problem**: Database table missing auto-increment and timestamp defaults
✅ **Solution**: Apply SQL fix script to configure defaults and triggers
✅ **Verification**: Test script confirms successful inserts with non-NULL IDs
✅ **Prevention**: Include defaults in all table creation scripts

If you continue to see issues after applying the fix, check:
1. Supabase service role key has sufficient permissions
2. PostgreSQL version supports `BIGSERIAL` and triggers
3. No row-level security (RLS) policies blocking inserts
