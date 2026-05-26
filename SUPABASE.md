# Supabase Integration Plan

## Architektur-Überblick

| Schicht | Vorher | Nachher |
|---|---|---|
| Raw Files | `data/raw/<module>/<file>` | Supabase Storage Bucket `raw-files` |
| Parsed Docs, Chunks | `data/processed/*.json`, `*.jsonl` | PostgreSQL Tabellen |
| Embeddings / Vector Search | ChromaDB (lokal, disk) | pgvector (PostgreSQL Extension) |
| Summaries, Roadmaps, Daily Plans | `data/processed/**/*.md` | Supabase Storage Bucket `processed` |
| Module-Profile, Settings | `data/modules/*.json`, `data/settings.json` | PostgreSQL Tabellen |
| Auth | keins (Single-User) | Supabase Auth (Email/Password) |
| Zugriffskontrolle | keins | Row Level Security (RLS) auf allen Tabellen |

---

## Storage Buckets

### `raw-files` (privat)
Originaldateien der Nutzer. Zugriff nur über signed URLs.

```
raw-files/
└── {user_id}/
    └── {module_slug}/
        └── {filename}          # z.B. "analysis_1.pdf"
```

### `processed` (privat)
Alle KI-generierten Outputs als Markdown/JSON.

```
processed/
└── {user_id}/
    └── {module_slug}/
        ├── summaries/
        │   └── {filename}.summary.md
        ├── roadmap.md
        ├── roadmap_history.md
        ├── exam_profile.md
        ├── daily_plan.md
        ├── task_history.json
        └── exams/
            └── exam_{n}.md
```

---

## Datenbank-Schema

### Aktivierung pgvector
```sql
create extension if not exists vector;
```

---

### Tabelle: `modules`
Ersetzt `data/modules/<slug>.json`.

```sql
create table modules (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users on delete cascade,
  name         text not null,
  slug         text not null,
  aliases      text[] default '{}',
  schwerpunkte text[] default '{}',
  pruefungsrelevant text[] default '{}',
  stil         text default 'mixed',
  prompt_hint  text default '',
  extra        text default '',
  created_at   timestamptz default now(),
  updated_at   timestamptz default now(),
  unique(user_id, slug)
);
```

---

### Tabelle: `files`
Registry aller hochgeladenen Rohdateien. Ersetzt das Scannen von `data/raw/`.

```sql
create table files (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users on delete cascade,
  module_id    uuid not null references modules on delete cascade,
  file_name    text not null,
  storage_path text not null,           -- Pfad in Bucket "raw-files"
  file_type    text not null,           -- 'pdf' | 'pptx' | 'txt' | 'md'
  file_size    bigint,
  content_hash text,                    -- SHA-256 für Dedup-Check
  is_exam      boolean default false,
  uploaded_at  timestamptz default now()
);
```

---

### Tabelle: `documents`
Parsed-Dokument-Metadaten. Ersetzt `data/processed/<name>_<ts>.json`.

```sql
create table documents (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users on delete cascade,
  file_id           uuid not null references files on delete cascade,
  module_id         uuid not null references modules on delete cascade,
  extracted_text    text,
  extraction_status text default 'success',   -- 'success' | 'failed'
  extraction_notes  text,
  metadata          jsonb default '{}',
  processed_at      timestamptz default now()
);
```

---

### Tabelle: `chunks`
Text-Chunks + Embeddings. Ersetzt `chunks_*.jsonl` UND ChromaDB komplett.

```sql
create table chunks (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users on delete cascade,
  document_id     uuid not null references documents on delete cascade,
  module_id       uuid not null references modules on delete cascade,
  chunk_text      text not null,
  chunk_index     integer not null,
  chunk_size      integer,
  embedding       vector(1536),     -- text-embedding-3-small Output-Dimension
  metadata        jsonb default '{}',
  created_at      timestamptz default now()
);

-- Vektor-Index für schnelle Similarity-Search
create index on chunks using hnsw (embedding vector_cosine_ops);

-- Filter-Index für module_id (wird bei RAG immer gefiltert)
create index on chunks (module_id);
```

> **Warum HNSW statt IVFFlat?** HNSW braucht kein `lists`-Tuning und ist bei < 1M Vektoren schneller beim Einfügen. Kann später zu IVFFlat gewechselt werden wenn die Datenmenge wächst.

---

### Tabelle: `summaries`
Index für Summary-Dateien in Storage. Ermöglicht `GET /lecture/summaries/{m}` ohne Storage-Listing.

```sql
create table summaries (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users on delete cascade,
  module_id    uuid not null references modules on delete cascade,
  file_id      uuid references files on delete set null,
  title        text,
  storage_path text not null,       -- Pfad in Bucket "processed"
  created_at   timestamptz default now()
);
```

---

### Tabelle: `exams`
Index für generierte Übungsklausuren in Storage.

```sql
create table exams (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users on delete cascade,
  module_id    uuid not null references modules on delete cascade,
  storage_path text not null,
  tasks_count  integer,
  total_points integer,
  created_at   timestamptz default now()
);
```

---

### Tabelle: `settings`
Ersetzt `data/settings.json`. Ein Eintrag pro User.

```sql
create table settings (
  user_id          uuid primary key references auth.users on delete cascade,
  favorite_module  uuid references modules on delete set null,
  preferences      jsonb default '{}'
);
```

---

## Row Level Security (RLS)

Jede Tabelle bekommt dieselbe Policy: User sieht nur seine eigenen Zeilen.

```sql
-- Beispiel für modules (gleiches Muster für alle Tabellen)
alter table modules enable row level security;

create policy "modules: eigene Daten" on modules
  for all using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
```

Tabellen die RLS brauchen: `modules`, `files`, `documents`, `chunks`, `summaries`, `exams`, `settings`

Storage Buckets: RLS via Storage Policies, Pfad beginnt immer mit `{auth.uid()}/`.

---

## Similarity Search (RAG)

Ersetzt `ChromaVectorStore.search()`. Mit optionalem Modul-Filter:

```sql
select
  chunk_text,
  metadata,
  1 - (embedding <=> $1::vector) as similarity
from chunks
where
  user_id = auth.uid()
  and module_id = $2          -- optional, wenn Modul-Filter aktiv
  and 1 - (embedding <=> $1::vector) > 0.7
order by embedding <=> $1::vector
limit $3;
```

---

## Migrations-Reihenfolge (Implementierung)

1. `enable vector extension`
2. `modules` Tabelle + RLS
3. `files` Tabelle + RLS + Storage Bucket `raw-files`
4. `documents` Tabelle + RLS
5. `chunks` Tabelle + HNSW Index + RLS
6. `summaries` Tabelle + RLS + Storage Bucket `processed`
7. `exams` Tabelle + RLS
8. `settings` Tabelle + RLS
9. Supabase Auth konfigurieren (Email/Password)

---

## Was dieses Schema löst

| Problem | Gelöst? |
|---|---|
| Kein persistentes Dateisystem auf Vercel | ✅ (Storage + DB) |
| ChromaDB lokal/disk-basiert | ✅ (pgvector in PostgreSQL) |
| State/Settings gehen verloren | ✅ (settings Tabelle) |
| Multi-User ohne Isolation | ✅ (RLS auf allen Tabellen) |
| Timeout-Limits auf Vercel | ❌ (bleibt offenes Problem → separater Backend-Host) |
