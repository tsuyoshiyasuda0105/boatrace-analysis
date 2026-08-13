# Supabase RLS and TOP Perf Audit

Date: 2026-08-08
Project: `boatrace-db` (`npjxlqkbytgdxrvebnjr`)

## Supabase RLS inventory

- `public` tables: `46`
- `RLS OFF`: `14`
- `RLS ON but no policy`: `32`
- `anon` grants on tables: `0`
- `authenticated` grants on tables: `0`
- `service_role` grants on tables: `46`

### Confirmed facts

- Current production tables are not readable via Data API by `anon` or `authenticated` because explicit table grants are absent.
- A large part of `public` still has `RLS OFF`, including internal/server-side tables such as `page_html_cache`, `race_tides`, `derived_start_stats`, `x_post_queue`, and several accident-related tables.
- Many `public` tables have `RLS ON` but `policy_count = 0`. This is deny-by-default today because `anon/authenticated` grants are absent.
- `public` default privileges still include automatic grants for objects created by `supabase_admin`:
  - tables: `anon/authenticated/service_role`
  - sequences: `anon/authenticated/service_role`
  - functions: `anon/authenticated/service_role`
- `postgres` default privileges were partly hardened for tables, but functions and sequences are still auto-granted.

### Risk assessment

- Immediate external exposure risk today is low because current tables do not have `anon/authenticated` table grants.
- Future migration risk is high because new tables/functions created under `supabase_admin` in `public` can become Data API reachable unless grants are explicitly revoked.
- `public` remains a mixed schema containing browser-facing auth tables and server-only operational tables, which makes future permission drift more likely.

### Proposed next changes

- P1. Revoke `public` default privileges for `supabase_admin` on tables, sequences, and functions.
- P1. Revoke `public` default privileges for `postgres` on sequences and functions.
- P1. Keep server-only tables private by default and enable RLS only where browser/Data API exposure is intentional.
- P2. Split future browser-facing objects into a small explicit API surface, or at minimum maintain an allowlist of tables that may receive `anon/authenticated` grants.
- P2. Audit `public` functions separately before any revoke on `EXECUTE`, because some auth-related helpers may be intentionally callable.

### Prepared artifacts

- hardening migration draft:
  `supabase/migrations/202608080002_harden_public_default_privileges.sql`
- dry-run printer:
  `scripts/plan_supabase_rls_hardening.py`

## TOP page perf scope

### Already implemented

- `loadOdds123Timeline()` is not executed on the top race list path.
- Venue tile countdown is hidden on TOP.
- Refresh interval is `60s`.

### Next measurement targets

- `/races?date=...`
- `/api/market-signals?date=...`
- `/api/odds-123-timeline?date=...`

### What to measure

- end-to-end response time
- DB query count
- cumulative DB query time
- response payload size

### Why

- This lets us distinguish template/render cost from DB cost.
- It also tells us whether a speed issue is dominated by SQL round-trips, response bloat, or cache misses.

### Applied follow-up

- `/api/market-signals` now has short server-side caching:
  `@cached(ttl=8, past_ttl=3600)`
- This reduces repeated member-page polling and duplicate requests during the same short window without changing the cache-only design.
