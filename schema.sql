create extension if not exists pgcrypto;

create table if not exists projects (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  domain text not null,
  location text not null default 'India',
  gl text not null default 'in',
  hl text not null default 'en',
  device text not null default 'desktop',
  check_frequency text not null default 'manual',
  notes text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists keywords (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  phrase text not null,
  tags text[] not null default '{}',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique(project_id, phrase)
);

create table if not exists rank_checks (
  id uuid primary key default gen_random_uuid(),
  keyword_id uuid not null references keywords(id) on delete cascade,
  position integer,
  matched_url text,
  previous_position integer,
  change integer,
  result_count integer,
  checked_at timestamptz not null default now(),
  raw_response jsonb not null default '{}'::jsonb
);

create table if not exists serp_results (
  id uuid primary key default gen_random_uuid(),
  check_id uuid not null references rank_checks(id) on delete cascade,
  position integer not null,
  title text,
  link text,
  display_link text,
  snippet text
);

create table if not exists project_notes (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  note text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_keywords_project_id on keywords(project_id);
create index if not exists idx_rank_checks_keyword_id_checked_at on rank_checks(keyword_id, checked_at desc);
create index if not exists idx_serp_results_check_id_position on serp_results(check_id, position);
