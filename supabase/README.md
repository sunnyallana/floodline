# Supabase backend for Floodline Field

One table of reports, one private bucket, and row-level security that lets enrolled phones insert and nothing else. The migration in
`migrations/` is the whole schema; `app/tests/sql.test.ts` runs it against Postgres in CI and checks the rules.

## What each key can do

| Key | Where it lives | Can |
|---|---|---|
| anon (public) | built into the app | nothing on its own: sign a phone in anonymously, which gives it an identity that can redeem one invite |
| a phone's own session | on the phone, after `enroll(invite)` | insert reports for its own team; upload `<uuid>.jpg` to `field-photos` |
| service role | `SUPABASE_SERVICE_KEY` on the analysis machine only | everything; used by `floodline ingest` |

Phones cannot read any report or photo, their own included, and cannot read teams, invites or devices.

Each phone is enrolled with a single-use invite (`floodline invites`), so the number of identities anyone
can report under is the number of invites issued. The device id on a report is the phone's server-signed
identity, stamped by the database; `floodline devices --revoke` withdraws a phone and `floodline ingest`
then rejects its reports, past ones included.

## Set up a project

1. Create a project at supabase.com (free tier). Note the project URL and the anon and service-role keys
   (Project settings, API).
2. Run every file in `migrations/`, in name order, in the SQL editor. Under Authentication, enable anonymous
   sign-ins: they create an identity that can do nothing until it redeems an invite.
3. Add a team, then issue one invite per phone. Each line printed is a code and a link that opens the app with
   the code filled in; it is shown once (only its hash is stored), works on one phone, and expires:

   ```sql
   insert into public.teams (code, name) values ('<16+ random characters>', 'District team');
   ```

   ```bash
   floodline invites '<team code>' --count 10 --label "Rescue 1122 Larkana" --days 14
   floodline devices                      # who is enrolled, from which invite
   floodline devices --revoke <device id> # withdraw a phone
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

- An enrolled phone can upload a photo without sending its report; `ingest --prune-orphans` deletes photos
  with no report after 24 hours. Strangers can no longer upload at all.
- Anonymous sign-ins are rate-limited per IP by Supabase (30 an hour by default). They create empty
  identities, which the project can prune; none can write anything without an invite.
- Uploads happen while the app is open. Background Sync would send from a closed app on Chrome for Android,
  but not on iOS Safari; it is not used yet.
