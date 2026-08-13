-- Harden future Data API exposure in public schema.
-- This migration only changes default privileges for newly created objects.
-- It does not alter grants on existing tables.

alter default privileges for role postgres in schema public
  revoke select, insert, update, delete, truncate, references, trigger on tables
  from anon, authenticated, service_role;

alter default privileges for role postgres in schema public
  revoke usage, select, update on sequences
  from anon, authenticated, service_role;

alter default privileges for role postgres in schema public
  revoke execute on functions
  from anon, authenticated, service_role, public;

alter default privileges for role supabase_admin in schema public
  revoke select, insert, update, delete, truncate, references, trigger on tables
  from anon, authenticated, service_role;

alter default privileges for role supabase_admin in schema public
  revoke usage, select, update on sequences
  from anon, authenticated, service_role;

alter default privileges for role supabase_admin in schema public
  revoke execute on functions
  from anon, authenticated, service_role, public;
