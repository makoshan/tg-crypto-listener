-- Supabase Migration Script
-- Generated from docs/data_storage_schema.md
-- Enable required extensions first (do this in Supabase Dashboard → Database → Extensions)

-- ============================================
-- 1. news_events - 原始新闻表
-- ============================================
create table if not exists news_events (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  source text not null,
  source_message_id text not null,
  source_url text,
  language varchar(12) default 'unknown',
  published_at timestamptz not null,
  content_text text not null,
  summary text,
  translated_text text,
  media_refs jsonb default '[]'::jsonb,
  hash_raw char(64) not null,
  hash_canonical char(64),
  embedding vector(1536),
  keywords_hit jsonb default '[]'::jsonb,
  ingest_status varchar(32) default 'pending',
  metadata jsonb default '{}'::jsonb
);

-- 索引
create unique index if not exists idx_news_events_source_msg
  on news_events(source, source_message_id);
create unique index if not exists idx_news_events_hash_raw
  on news_events(hash_raw);
create index if not exists idx_news_events_keywords_hit
  on news_events using gin(keywords_hit);
create index if not exists idx_news_events_published_at
  on news_events(published_at desc);
create index if not exists idx_news_events_ingest_status
  on news_events(ingest_status);
create index if not exists idx_news_events_updated_at
  on news_events(updated_at desc);

-- 向量索引 (等 embedding 数据足够多后再创建)
-- create index if not exists idx_news_events_embedding
--   on news_events using ivfflat(embedding vector_cosine_ops) with (lists = 100);


-- ============================================
-- 2. ai_signals - AI 决策信号表
-- ============================================
create table if not exists ai_signals (
  id bigserial primary key,
  news_event_id bigint not null references news_events(id) on delete cascade,
  created_at timestamptz not null default now(),
  model_name text not null,
  summary_cn text not null,
  event_type varchar(32) not null,
  assets varchar(120) default 'NONE',
  asset_names text,
  action varchar(24) not null,
  direction varchar(24) default 'neutral',
  confidence float4 default 0,
  strength varchar(16) default 'low',
  risk_flags jsonb default '[]'::jsonb,
  notes text,
  links jsonb default '[]'::jsonb,
  execution_path varchar(16) default 'cold',
  should_alert boolean default false,
  latency_ms integer,
  raw_response jsonb
);

-- 索引
create index if not exists idx_ai_signals_news_event_id
  on ai_signals(news_event_id);
create index if not exists idx_ai_signals_created_at
  on ai_signals(created_at desc);
create index if not exists idx_ai_signals_should_alert
  on ai_signals(should_alert) where should_alert = true;
create index if not exists idx_ai_signals_event_type
  on ai_signals(event_type);
create index if not exists idx_ai_signals_execution_path
  on ai_signals(execution_path);


-- ============================================
-- 3. strategy_insights - 洞察/人工标注表（可选）
-- ============================================
create table if not exists strategy_insights (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  title text,
  summary text,
  narrative text,
  relation text,
  action text,
  confidence float4,
  source_urls jsonb default '[]'::jsonb,
  news_event_ids bigint[],
  ai_signal_ids bigint[],
  tags jsonb default '[]'::jsonb,
  url_hash text,
  content_hash text,
  embedding vector(1536)
);

-- 索引
create index if not exists idx_strategy_insights_created_at
  on strategy_insights(created_at desc);
create index if not exists idx_strategy_insights_tags
  on strategy_insights using gin(tags);
create unique index if not exists idx_strategy_insights_url_hash
  on strategy_insights(url_hash) where url_hash is not null;
create index if not exists idx_strategy_insights_content_hash
  on strategy_insights(content_hash) where content_hash is not null;


-- ============================================
-- 4. market_snapshots - 行情快照（可选）
-- ============================================
create table if not exists market_snapshots (
  id bigserial primary key,
  captured_at timestamptz not null,
  asset varchar(32) not null,
  price numeric(18,8) not null,
  volume_1h numeric(20,2),
  open_interest numeric(20,2),
  external_source text,
  metadata jsonb default '{}'::jsonb
);

-- 索引
create index if not exists idx_market_snapshots_captured_at
  on market_snapshots(captured_at desc);
create index if not exists idx_market_snapshots_asset
  on market_snapshots(asset);
create unique index if not exists idx_market_snapshots_asset_time
  on market_snapshots(asset, captured_at);


-- ============================================
-- 5. 视图 - v_signal_feed
-- ============================================
create or replace view v_signal_feed as
select
  s.id as signal_id,
  e.published_at,
  e.source,
  e.content_text,
  e.translated_text,
  s.summary_cn,
  s.event_type,
  s.assets,
  s.action,
  s.direction,
  s.confidence,
  s.risk_flags,
  s.links,
  s.execution_path,
  s.should_alert
from ai_signals s
join news_events e on e.id = s.news_event_id
order by e.published_at desc;


-- ============================================
-- 6. 触发器 - 自动更新 updated_at
-- ============================================
create or replace function trigger_set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger set_updated_at_strategy
  before update on strategy_insights
  for each row
  execute function trigger_set_updated_at();

create trigger set_updated_at_news
  before update on news_events
  for each row
  execute function trigger_set_updated_at();


-- ============================================
-- 完成说明
-- ============================================
-- 1. 在 Supabase Dashboard 启用 pgvector 扩展
-- 2. 在 SQL Editor 执行此脚本
-- 3. 等 embedding 数据积累后，创建向量索引以提升性能
-- 4. 根据需要调整索引类型 (ivfflat vs hnsw)
