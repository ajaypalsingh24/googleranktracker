create table if not exists schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
);

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  name text not null,
  password_hash text not null,
  role text not null default 'viewer' check (role in ('admin', 'manager', 'viewer')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_login_at timestamptz
);

alter table projects add column if not exists country text not null default 'India';
alter table projects add column if not exists competitors text[] not null default '{}';

alter table keywords add column if not exists search_volume integer;

create index if not exists idx_users_email on users(lower(email));
create index if not exists idx_projects_domain on projects(lower(domain));
