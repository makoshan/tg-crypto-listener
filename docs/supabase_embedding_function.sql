create or replace function search_memory_events(
    query_embedding vector(1536),
    match_threshold float default 0.85,
    match_count int default 5,
    asset_filter text[] default null,
    min_confidence float default 0.6,
    time_window_hours int default 72
)
returns table (
    ai_signal_id bigint,
    news_event_id bigint,
    created_at timestamptz,
    assets text[],
    action text,
    confidence double precision,
    summary text,
    similarity double precision
)
language plpgsql
as $$
begin
    return query
    select
        ais.id,
        ne.id,
        ne.created_at,
        regexp_split_to_array(
            regexp_replace(coalesce(nullif(ais.assets, ''), 'NONE'), '\s+', '', 'g'),
            ','
        )::text[],
        ais.action::text,
        ais.confidence::double precision,
        ais.summary_cn::text,
        (1 - (ne.embedding <=> query_embedding))::double precision as similarity
    from news_events ne
    join ai_signals ais on ais.news_event_id = ne.id
    where
        ne.embedding is not null
        and ais.confidence >= min_confidence
        and ne.created_at >= now() - (time_window_hours || ' hours')::interval
        and (
            asset_filter is null
            or regexp_split_to_array(
                regexp_replace(coalesce(nullif(ais.assets, ''), 'NONE'), '\s+', '', 'g'),
                ','
            ) && asset_filter
        )
        and (1 - (ne.embedding <=> query_embedding)) >= match_threshold
    order by similarity desc, ais.confidence desc, ne.created_at desc
    limit match_count;
end;
$$;
