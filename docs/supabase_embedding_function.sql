-- PostgreSQL function for vector similarity search
-- Run this in Supabase SQL Editor after creating tables

create or replace function find_similar_events(
  query_embedding vector(1536),
  similarity_threshold float default 0.92,
  time_window_hours int default 72,
  max_results int default 1
)
returns table (
  id bigint,
  content_text text,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    ne.id,
    ne.content_text,
    1 - (ne.embedding <=> query_embedding) as similarity
  from news_events ne
  where ne.embedding is not null
    and ne.created_at > now() - make_interval(hours => time_window_hours)
    and 1 - (ne.embedding <=> query_embedding) >= similarity_threshold
  order by ne.embedding <=> query_embedding
  limit max_results;
end;
$$;

-- Grant execute permission
grant execute on function find_similar_events to anon, authenticated, service_role;
