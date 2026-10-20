-- Team codes are the only secret a device needs, so the API roles get no privilege on the table at all.
--
-- Supabase grants every new table in public to anon and authenticated by default. Row-level security with no
-- policies already returned no rows, but that leaves one wrong policy between a stranger and every team code.
-- Without the grant, a read is refused outright (permission denied), whatever policies are added later.
-- private.team_is_active is security definer, so the insert check still sees the table.

revoke all on public.teams from anon, authenticated;
