-- =====================================================
-- Multi-Source Memory System - FDW Migration Script
-- =====================================================
-- This script:
-- 1. Renames existing search_memory_events to search_memory_events_v1
-- 2. Creates new search_memory_events with FDW support for docs table
-- =====================================================

-- Step 1: Rename existing function to v1
-- =====================================================
DROP FUNCTION IF EXISTS search_memory_events_v1(vector, float, int, text[], float, int);

CREATE OR REPLACE FUNCTION search_memory_events_v1(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.85,
    match_count int DEFAULT 5,
    asset_filter text[] DEFAULT NULL,
    min_confidence float DEFAULT 0.6,
    time_window_hours int DEFAULT 72
)
RETURNS TABLE (
    ai_signal_id bigint,
    news_event_id bigint,
    created_at timestamptz,
    assets text[],
    action text,
    confidence double precision,
    summary text,
    similarity double precision
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ais.id,
        ne.id,
        ne.created_at,
        regexp_split_to_array(
            regexp_replace(COALESCE(NULLIF(ais.assets, ''), 'NONE'), '\s+', '', 'g'),
            ','
        )::text[],
        ais.action::text,
        ais.confidence::double precision,
        ais.summary_cn::text,
        (1 - (ne.embedding <=> query_embedding))::double precision AS similarity
    FROM news_events ne
    JOIN ai_signals ais ON ais.news_event_id = ne.id
    WHERE
        ne.embedding IS NOT NULL
        AND ais.confidence >= min_confidence
        AND ne.created_at >= now() - (time_window_hours || ' hours')::interval
        AND (
            asset_filter IS NULL
            OR regexp_split_to_array(
                regexp_replace(COALESCE(NULLIF(ais.assets, ''), 'NONE'), '\s+', '', 'g'),
                ','
            ) && asset_filter
        )
        AND (1 - (ne.embedding <=> query_embedding)) >= match_threshold
    ORDER BY similarity DESC, ais.confidence DESC, ne.created_at DESC
    LIMIT match_count;
END;
$$;

-- Step 2: Create new search_memory_events with FDW support
-- =====================================================
-- NOTE: Before running this, ensure:
-- 1. postgres_fdw extension is enabled
-- 2. Foreign server 'docs_server' is created
-- 3. Foreign table 'docs_remote' is created
-- See memory_multi_source_fdw.md for setup instructions
-- =====================================================

DROP FUNCTION IF EXISTS search_memory_events(vector, float, int, float, int, text[], bool, int, float);

CREATE OR REPLACE FUNCTION search_memory_events(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.55,
    match_count int DEFAULT 3,
    min_confidence float DEFAULT 0.6,
    time_window_hours int DEFAULT 168,
    asset_filter text[] DEFAULT NULL,
    -- New parameters for docs (副库) control
    include_docs bool DEFAULT true,
    docs_max_count int DEFAULT 2,
    docs_threshold float DEFAULT 0.50
)
RETURNS TABLE (
    id text,
    created_at timestamptz,
    assets text[],
    action text,
    confidence float,
    summary text,
    similarity float,
    -- New fields
    source text,
    content_text text,
    metadata jsonb
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    -- CTE 1: Main database query (news_events + ai_signals)
    WITH main_results AS (
        SELECT
            'event-' || ne.id::text AS id,
            ne.created_at,
            regexp_split_to_array(
                regexp_replace(COALESCE(NULLIF(ais.assets, ''), 'NONE'), '\s+', '', 'g'),
                ','
            )::text[] AS assets,
            ais.action::text,
            ais.confidence,
            ais.summary_cn AS summary,
            (1 - (ne.embedding <=> query_embedding)) AS similarity,
            'news_events'::text AS source,
            ne.content_text,
            jsonb_build_object(
                'event_type', ais.event_type,
                'risk_flags', ais.risk_flags,
                'ai_signal_id', ais.id,
                'news_event_id', ne.id
            ) AS metadata
        FROM news_events ne
        INNER JOIN ai_signals ais ON ais.news_event_id = ne.id
        WHERE ne.embedding IS NOT NULL
          AND ais.confidence >= min_confidence
          AND (1 - (ne.embedding <=> query_embedding)) >= match_threshold
          AND ne.created_at >= now() - (time_window_hours || ' hours')::interval
          AND (asset_filter IS NULL OR regexp_split_to_array(
                regexp_replace(COALESCE(NULLIF(ais.assets, ''), 'NONE'), '\s+', '', 'g'),
                ','
            ) && asset_filter)
        ORDER BY similarity DESC, ais.confidence DESC
        LIMIT match_count
    ),
    -- CTE 2: Docs database query (via FDW)
    docs_results AS (
        SELECT
            'doc-' || d.id::text AS id,
            d.created_at,
            -- Extract asset codes from tags.entities
            COALESCE(
                ARRAY(SELECT jsonb_array_elements_text(d.tags->'entities')),
                ARRAY[]::text[]
            ) AS assets,
            'observe'::text AS action,
            1.0 AS confidence,
            COALESCE(d.ai_summary_cn, LEFT(d.content_text, 200)) AS summary,
            (1 - (d.embedding <=> query_embedding)) AS similarity,
            'docs'::text AS source,
            d.content_text,
            jsonb_build_object(
                'source', d.source,
                'author', d.source_author,
                'url', d.canonical_url,
                'tags', d.tags
            ) AS metadata
        FROM docs_remote d  -- Using foreign table
        WHERE d.embedding IS NOT NULL
          AND (1 - (d.embedding <=> query_embedding)) >= docs_threshold
          AND (
              asset_filter IS NULL
              OR d.tags->'entities' ?| asset_filter
          )
          AND include_docs = true  -- Can be disabled via parameter
        ORDER BY similarity DESC
        LIMIT docs_max_count
    )
    -- Merge main and docs results
    SELECT * FROM (
        SELECT * FROM main_results
        UNION ALL
        SELECT * FROM docs_results
    ) combined
    ORDER BY similarity DESC, confidence DESC
    LIMIT (match_count + docs_max_count);
END;
$$;

-- Step 3: Grant necessary permissions
-- =====================================================
GRANT EXECUTE ON FUNCTION search_memory_events_v1(vector, float, int, text[], float, int) TO postgres;
GRANT EXECUTE ON FUNCTION search_memory_events_v1(vector, float, int, text[], float, int) TO authenticator;

GRANT EXECUTE ON FUNCTION search_memory_events(vector, float, int, float, int, text[], bool, int, float) TO postgres;
GRANT EXECUTE ON FUNCTION search_memory_events(vector, float, int, float, int, text[], bool, int, float) TO authenticator;

-- Step 4: Verification queries
-- =====================================================
-- Test v1 function (old behavior)
-- SELECT * FROM search_memory_events_v1(
--     query_embedding := (SELECT embedding FROM news_events WHERE embedding IS NOT NULL LIMIT 1),
--     match_threshold := 0.55,
--     match_count := 3
-- );

-- Test new function with docs disabled (should behave like v1 but with new schema)
-- SELECT * FROM search_memory_events(
--     query_embedding := (SELECT embedding FROM news_events WHERE embedding IS NOT NULL LIMIT 1),
--     match_threshold := 0.55,
--     match_count := 3,
--     include_docs := false
-- );

-- Test new function with docs enabled (full multi-source)
-- SELECT id, source, summary, similarity, assets
-- FROM search_memory_events(
--     query_embedding := (SELECT embedding FROM news_events WHERE embedding IS NOT NULL LIMIT 1),
--     match_threshold := 0.55,
--     match_count := 3,
--     include_docs := true,
--     docs_max_count := 2,
--     docs_threshold := 0.50
-- );

-- =====================================================
-- IMPORTANT NOTES:
-- =====================================================
-- 1. Before running this script, ensure FDW infrastructure is set up:
--    - CREATE EXTENSION postgres_fdw;
--    - CREATE SERVER docs_server ...;
--    - CREATE USER MAPPING ...;
--    - CREATE FOREIGN TABLE docs_remote ...;
--
-- 2. If docs_remote table doesn't exist or FDW is not configured,
--    the function will fail when include_docs = true
--
-- 3. To temporarily disable docs while keeping the function:
--    - Set include_docs := false in queries, OR
--    - Change the default: include_docs bool DEFAULT false
--
-- 4. The new function returns a different schema than v1:
--    - Old: (ai_signal_id, news_event_id, ...)
--    - New: (id text, source, content_text, metadata jsonb, ...)
--    - Python code needs to handle the new schema
--
-- 5. For gradual migration:
--    - Keep v1 function for backward compatibility
--    - Update Python code to use new function signature
--    - Test thoroughly before removing v1
-- =====================================================
