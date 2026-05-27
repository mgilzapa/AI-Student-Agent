-- ============================================================
-- Migration 001: Initial Supabase schema
-- Run this once in the Supabase SQL editor.
-- ============================================================

-- 1) pgvector extension
create extension if not exists vector;

-- 2) Storage buckets
insert into storage.buckets (id, name, public)
values ('raw-files', 'raw-files', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('processed', 'processed', false)
on conflict (id) do nothing;

-- Storage policies: path always starts with {user_id}/
create policy "raw-files: owner read"
  on storage.objects for select
  using (bucket_id = 'raw-files' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "raw-files: owner insert"
  on storage.objects for insert
  with check (bucket_id = 'raw-files' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "raw-files: owner delete"
  on storage.objects for delete
  using (bucket_id = 'raw-files' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "processed: owner read"
  on storage.objects for select
  using (bucket_id = 'processed' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "processed: owner insert"
  on storage.objects for insert
  with check (bucket_id = 'processed' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "processed: owner delete"
  on storage.objects for delete
  using (bucket_id = 'processed' and (storage.foldername(name))[1] = auth.uid()::text);

-- ============================================================
-- 3) Tables
-- ============================================================

create table if not exists modules (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null,
  name                  text not null,
  slug                  text not null,
  aliases               text[]  default '{}',
  schwerpunkte          text[]  default '{}',
  pruefungsrelevant     text[]  default '{}',
  stil                  text    default 'mixed',
  prompt_hint           text    default '',
  extra                 text    default '',
  exam_profile_md       text    default '',
  history_md            text    default '',
  manual_exam_files     text[]  default '{}',
  manual_not_exam_files text[]  default '{}',
  file_types            jsonb   default '{}',
  created_at            timestamptz default now(),
  updated_at            timestamptz default now(),
  unique(user_id, slug)
);

create table if not exists files (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null,
  module_id    uuid not null references modules on delete cascade,
  file_name    text not null,
  storage_path text not null,
  file_type    text not null,
  file_size    bigint,
  content_hash text,
  is_exam      boolean default false,
  file_category text,
  relative_path text not null default '',
  uploaded_at  timestamptz default now()
);

create table if not exists documents (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null,
  file_id           uuid references files on delete cascade,
  module_id         uuid not null references modules on delete cascade,
  extracted_text    text,
  extraction_status text default 'success',
  extraction_notes  text,
  metadata          jsonb default '{}',
  processed_at      timestamptz default now()
);

create table if not exists chunks (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null,
  module_id       uuid not null references modules on delete cascade,
  chunk_text      text not null,
  chunk_index     integer not null,
  chunk_size      integer,
  embedding       vector(1536),
  metadata        jsonb default '{}',
  created_at      timestamptz default now()
);

create index if not exists chunks_embedding_idx  on chunks using hnsw (embedding vector_cosine_ops);
create index if not exists chunks_module_idx     on chunks (module_id);
create index if not exists chunks_user_idx       on chunks (user_id);

create table if not exists summaries (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null,
  module_id    uuid not null references modules on delete cascade,
  file_id      uuid references files on delete set null,
  title        text,
  storage_path text not null,
  created_at   timestamptz default now()
);

create table if not exists exams (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null,
  module_id    uuid not null references modules on delete cascade,
  storage_path text not null,
  exam_n       integer not null,
  tasks_count  integer,
  total_points integer,
  created_at   timestamptz default now(),
  unique(module_id, exam_n)
);

create table if not exists settings (
  user_id         uuid primary key,
  favorite_module uuid references modules on delete set null,
  preferences     jsonb default '{}'
);

-- ============================================================
-- 4) Row Level Security
-- ============================================================

alter table modules   enable row level security;
alter table files     enable row level security;
alter table documents enable row level security;
alter table chunks    enable row level security;
alter table summaries enable row level security;
alter table exams     enable row level security;
alter table settings  enable row level security;

create policy "modules: own data"   on modules   for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "files: own data"     on files     for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "documents: own data" on documents for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "chunks: own data"    on chunks    for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "summaries: own data" on summaries for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "exams: own data"     on exams     for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "settings: own data"  on settings  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ============================================================
-- 5) pgvector similarity search function
-- ============================================================

create or replace function match_chunks(
  query_embedding   vector(1536),
  match_threshold   float   default 0.0,
  match_count       int     default 5,
  filter_module_id  uuid    default null,
  filter_user_id    uuid    default null
)
returns table (
  id          uuid,
  chunk_text  text,
  metadata    jsonb,
  similarity  float,
  module_id   uuid
)
language sql stable
as $$
  select
    c.id,
    c.chunk_text,
    c.metadata,
    1 - (c.embedding <=> query_embedding) as similarity,
    c.module_id
  from chunks c
  where
    (filter_user_id  is null or c.user_id   = filter_user_id)
    and (filter_module_id is null or c.module_id = filter_module_id)
    and 1 - (c.embedding <=> query_embedding) > match_threshold
  order by c.embedding <=> query_embedding
  limit match_count;
$$;
