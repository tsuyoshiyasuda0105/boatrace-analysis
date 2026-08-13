WITH public_tables AS (
  SELECT
    c.oid,
    c.relname AS table_name,
    c.relrowsecurity AS rls_enabled,
    c.relforcerowsecurity AS rls_forced
  FROM pg_class c
  JOIN pg_namespace n
    ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND c.relkind = 'r'
),
policy_counts AS (
  SELECT
    tablename AS table_name,
    COUNT(*) AS policy_count,
    STRING_AGG(policyname || ':' || cmd, ', ' ORDER BY policyname, cmd) AS policies
  FROM pg_policies
  WHERE schemaname = 'public'
  GROUP BY tablename
),
grants AS (
  SELECT
    table_name,
    grantee,
    STRING_AGG(privilege_type, ', ' ORDER BY privilege_type) AS privileges
  FROM information_schema.role_table_grants
  WHERE table_schema = 'public'
    AND grantee IN ('anon', 'authenticated', 'service_role', 'PUBLIC')
  GROUP BY table_name, grantee
)
SELECT
  pt.table_name,
  pt.rls_enabled,
  pt.rls_forced,
  COALESCE(pc.policy_count, 0) AS policy_count,
  COALESCE(pc.policies, '') AS policies,
  COALESCE(MAX(CASE WHEN g.grantee = 'anon' THEN g.privileges END), '') AS anon_privileges,
  COALESCE(MAX(CASE WHEN g.grantee = 'authenticated' THEN g.privileges END), '') AS authenticated_privileges,
  COALESCE(MAX(CASE WHEN g.grantee = 'service_role' THEN g.privileges END), '') AS service_role_privileges,
  COALESCE(MAX(CASE WHEN g.grantee = 'PUBLIC' THEN g.privileges END), '') AS public_privileges
FROM public_tables pt
LEFT JOIN policy_counts pc
  ON pc.table_name = pt.table_name
LEFT JOIN grants g
  ON g.table_name = pt.table_name
GROUP BY pt.table_name, pt.rls_enabled, pt.rls_forced, pc.policy_count, pc.policies
ORDER BY pt.table_name;
