-- Odds time-series storage for football / esports research.
-- Project: bjmqwerbslpdbfrkedqj (run in Supabase SQL Editor or via CLI).

create extension if not exists "pgcrypto";

create table if not exists public.matches (
  id uuid primary key default gen_random_uuid(),
  external_id text not null unique,
  sport_key text not null,
  sport_title text,
  home_team text not null,
  away_team text not null,
  commence_time timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists matches_commence_time_idx on public.matches (commence_time);
create index if not exists matches_sport_key_idx on public.matches (sport_key);

create table if not exists public.odds_ticks (
  id bigserial primary key,
  match_id uuid not null references public.matches (id) on delete cascade,
  captured_at timestamptz not null,
  source text not null default 'the_odds_api',
  bookmaker_key text not null,
  bookmaker_title text,
  market_key text not null default 'h2h',
  outcome_name text not null,
  price numeric(12, 6) not null,
  point numeric(12, 4),
  implied_prob numeric(12, 8),
  last_update timestamptz,
  raw jsonb
);

create index if not exists odds_ticks_match_captured_idx
  on public.odds_ticks (match_id, captured_at desc);

create index if not exists odds_ticks_captured_idx
  on public.odds_ticks (captured_at desc);

create index if not exists odds_ticks_bookmaker_idx
  on public.odds_ticks (bookmaker_key, captured_at desc);

create table if not exists public.collection_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  source text not null,
  sport_keys text[] not null default '{}',
  regions text[] not null default '{}',
  events_seen integer not null default 0,
  ticks_inserted integer not null default 0,
  requests_used integer,
  requests_remaining integer,
  status text not null default 'running',
  error_message text
);

-- Row Level Security: service role bypasses; anon read-only optional.
alter table public.matches enable row level security;
alter table public.odds_ticks enable row level security;
alter table public.collection_runs enable row level security;

create policy "service_role_all_matches"
  on public.matches
  for all
  to service_role
  using (true)
  with check (true);

create policy "service_role_all_odds_ticks"
  on public.odds_ticks
  for all
  to service_role
  using (true)
  with check (true);

create policy "service_role_all_collection_runs"
  on public.collection_runs
  for all
  to service_role
  using (true)
  with check (true);

create policy "anon_read_matches"
  on public.matches
  for select
  to anon
  using (true);

create policy "anon_read_odds_ticks"
  on public.odds_ticks
  for select
  to anon
  using (true);

create policy "anon_read_collection_runs"
  on public.collection_runs
  for select
  to anon
  using (true);
