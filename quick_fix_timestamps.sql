-- Quick fix: Update existing NULL timestamps using published_at or now()
-- Run this in Supabase SQL Editor

-- Step 1: Update NULL timestamps
UPDATE news_events
SET
    created_at = COALESCE(created_at, published_at, now()),
    updated_at = COALESCE(updated_at, published_at, now())
WHERE created_at IS NULL OR updated_at IS NULL;

-- Step 2: Set DEFAULT for future inserts
ALTER TABLE news_events
    ALTER COLUMN created_at SET DEFAULT now();

ALTER TABLE news_events
    ALTER COLUMN updated_at SET DEFAULT now();

-- Step 3: Create trigger function to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Step 4: Create trigger
DROP TRIGGER IF EXISTS update_news_events_updated_at ON news_events;
CREATE TRIGGER update_news_events_updated_at
    BEFORE UPDATE ON news_events
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Step 5: Verify the fix
SELECT COUNT(*) as total_records FROM news_events;
SELECT COUNT(*) as records_with_timestamps
FROM news_events
WHERE created_at IS NOT NULL AND updated_at IS NOT NULL;
SELECT COUNT(*) as remaining_null_count
FROM news_events
WHERE created_at IS NULL OR updated_at IS NULL;
