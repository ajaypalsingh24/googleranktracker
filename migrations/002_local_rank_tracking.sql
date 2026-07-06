alter table projects add column if not exists project_type text not null default 'organic';
alter table projects add column if not exists search_location text not null default '';
alter table projects add column if not exists local_business_name text not null default '';

alter table rank_checks add column if not exists search_type text not null default 'organic';
alter table rank_checks add column if not exists matched_title text;

create index if not exists idx_projects_project_type on projects(project_type);
