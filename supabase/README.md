# Supabase backend for Floodline Field

One table, one private bucket, and row-level security that lets phones insert and nothing else. The migration in
`migrations/` is the whole schema; `app/tests/sql.test.ts` runs it against Postgres in CI and checks the rules.

## What each key can do

| Key | Where it lives | Can |
|---|---|---|
| anon (public) | built into the app | insert a report for an active team code; upload `<uuid>.jpg` to `field-photos` |
| service role | `SUPABASE_SERVICE_KEY` on the analysis machine only | everything; used by `floodline ingest` |

Phones cannot read any report or photo, their own included, and cannot read team codes.

## Set up a project

1. Create a project at supabase.com (free tier). Note the project URL and the anon and service-role keys
   (Project settings, API).
2. Run `migrations/20261020000000_field_reports.sql` in the SQL editor.
3. Add a team. The code is what responders type into the app. Make it long and random, and rotate it by
   setting `active = false` and adding a new one:

   ```sql
   insert into public.teams (code, name) values ('<16+ random characters>', 'District team');
   ```

4. Build the app against the project and publish it:

   ```bash
   cd app
   VITE_SUPABASE_URL=https://<ref>.supabase.co VITE_SUPABASE_ANON_KEY=<anon key> npm run build
   ```

5. On the analysis machine:

   ```bash
   export SUPABASE_URL=https://<ref>.supabase.co SUPABASE_SERVICE_KEY=<service-role key>
   floodline ingest --prune-orphans
   floodline fuse results/satellite_<city>.csv ... --reports data/field/reports_for_fuse.csv
   ```

## Known limits

- A photo upload cannot carry the team code, so anyone with the anon key can upload a JPEG of up to 5 MB.
  Such photos have no report; `ingest --prune-orphans` deletes them after 24 hours. Supabase's per-project rate
  limits apply.
- Uploads happen while the app is open. Background Sync would send from a closed app on Chrome for Android,
  but not on iOS Safari; it is not used yet.
