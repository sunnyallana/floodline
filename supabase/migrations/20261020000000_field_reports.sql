-- Field reports from the Floodline capture app.
--
-- Trust model: the app ships the project's public anon key, so anyone can call these endpoints. Devices may
-- therefore only INSERT. No anon or authenticated role can read, change or delete a report or a photo, including
-- its own; only the service role (used by `floodline ingest` on the analysis machine) can. A report must carry an
-- active team code, so a stranger with the anon key cannot add rows without one. Team codes live in a table no
-- client role can read, and the membership check sits in a schema PostgREST does not expose, so codes cannot be
-- probed through the API.

create schema if not exists private;

create table public.teams (
    code        text primary key check (char_length(code) between 8 and 64),
    name        text not null,
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);
alter table public.teams enable row level security;  -- no policies: invisible to anon and authenticated

create table public.reports (
    id               uuid primary key,  -- generated on the device, so a retried upload cannot duplicate a row
    team_code        text not null references public.teams (code),
    captured_at      timestamptz not null,
    received_at      timestamptz not null default now(),
    lat              double precision check (lat between -90 and 90),
    lon              double precision check (lon between -180 and 180),
    accuracy_m       real check (accuracy_m >= 0),
    location_source  text not null check (location_source in ('device', 'exif', 'none')),
    people_visible   smallint not null default 0 check (people_visible between 0 and 999),
    people_in_water  smallint not null default 0 check (people_in_water between 0 and 999),
    people_elevated  smallint not null default 0 check (people_elevated between 0 and 999),
    depth_guess      text not null default 'unknown'
                     check (depth_guess in ('unknown', 'dry', 'ankle', 'knee', 'waist', 'chest', 'above_head')),
    note             text not null default '' check (char_length(note) <= 500),
    photo_path       text not null,
    photo_bytes      integer not null check (photo_bytes between 1 and 5000000),
    app_version      text not null check (char_length(app_version) <= 32),
    consent          boolean not null check (consent),
    check ((lat is null) = (lon is null)),
    check ((lat is null) = (location_source = 'none')),
    check (photo_path = id::text || '.jpg')
);
create index reports_received_at on public.reports (received_at);
alter table public.reports enable row level security;

create function private.team_is_active(team text) returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (select 1 from public.teams t where t.code = team and t.active)
$$;
revoke all on function private.team_is_active(text) from public;
grant usage on schema private to anon, authenticated;
grant execute on function private.team_is_active(text) to anon, authenticated;

revoke all on public.reports from anon, authenticated;
grant insert on public.reports to anon, authenticated;
create policy "devices submit reports for an active team" on public.reports
    for insert to anon, authenticated
    with check (private.team_is_active(team_code));

-- Photos: a private bucket that accepts JPEGs up to 5 MB named <uuid>.jpg. Uploads cannot carry a team code, so a
-- photo with no matching report is possible; `floodline ingest --prune-orphans` deletes those after a day.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('field-photos', 'field-photos', false, 5000000, array['image/jpeg'])
on conflict (id) do nothing;

create policy "devices upload report photos" on storage.objects
    for insert to anon, authenticated
    with check (
        bucket_id = 'field-photos'
        and name ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg$'
    );
