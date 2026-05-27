-- Migration 002: Add unique constraint to files table so upsert works
-- Run this in the Supabase SQL editor.

alter table files
  add constraint files_module_rel_unique unique (module_id, relative_path);
