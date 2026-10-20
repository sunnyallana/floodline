-- Device enrolment: one single-use invite per phone, and an identity for each phone that the server issues.
--
-- Before this, the shared team code was the only gate and device_id came from the phone, so anyone holding the
-- code could pose as any number of devices. That also weakened the rule that two devices must agree before an
-- unmapped area's priority moves. Now:
--
-- 1. A coordinator issues invites (`floodline invites`), one per phone. Only a SHA-256 hash is stored; the code
--    is shown once, to the coordinator. Each invite has a label (who it was given to) and an expiry.
-- 2. The phone signs in anonymously with Supabase Auth, which gives it a signed identity (auth.uid()). It calls
--    enroll(invite) once, with signal; the invite is consumed and the identity is bound to the team.
-- 3. Only enrolled, active devices of an active team can insert a report or upload a photo. The report's
--    device_id is set from auth.uid() by the server; whatever the phone sends there is ignored.
--
-- So N devices need N invites, each traceable to the coordinator who issued it and to its label, and each
-- revocable (`floodline devices --revoke`), which `floodline ingest` applies retroactively. The anon key alone can
-- now do nothing at all, which also closes the hole that let anyone upload stray photos to the bucket.

create table public.invites (
    code_hash    text primary key check (code_hash ~ '^[0-9a-f]{64}$'),  -- hex SHA-256 of the normalised code
    team_code    text not null references public.teams (code),
    label        text not null default '' check (char_length(label) <= 120),
    created_at   timestamptz not null default now(),
    expires_at   timestamptz not null default now() + interval '30 days',
    redeemed_by  uuid unique,
    redeemed_at  timestamptz
);
alter table public.invites enable row level security;
revoke all on public.invites from anon, authenticated;

create table public.devices (
    id            uuid primary key,  -- the phone's auth.uid()
    team_code     text not null references public.teams (code),
    invite_label  text not null default '',
    enrolled_at   timestamptz not null default now(),
    active        boolean not null default true
);
alter table public.devices enable row level security;
revoke all on public.devices from anon, authenticated;

-- Codes are typed by people: compare them without case, spaces or dashes.
create function private.normalise_invite(code text) returns text
    language sql immutable set search_path = ''
as $$ select upper(regexp_replace(coalesce(code, ''), '[^0-9A-Za-z]', '', 'g')) $$;

create function public.enroll(invite text) returns json
    language plpgsql security definer set search_path = ''
as $$
declare
    uid  uuid := auth.uid();
    h    text := encode(sha256(convert_to(private.normalise_invite(invite), 'UTF8')), 'hex');
    inv  public.invites;
    t    public.teams;
begin
    if uid is null then
        raise exception 'sign in before enrolling' using errcode = 'PT401';
    end if;
    update public.invites i set redeemed_by = uid, redeemed_at = now()
        where i.code_hash = h and i.redeemed_by is null and i.expires_at > now()
        returning * into inv;
    if not found then
        -- A retry after a lost reply: this phone already redeemed this very invite.
        select * into inv from public.invites i where i.code_hash = h and i.redeemed_by = uid;
        if not found then
            raise exception 'this invite is not valid, has been used, or has expired' using errcode = 'PT403';
        end if;
    end if;
    select * into t from public.teams tm where tm.code = inv.team_code and tm.active;
    if not found then
        raise exception 'this team is no longer active' using errcode = 'PT403';
    end if;
    insert into public.devices (id, team_code, invite_label) values (uid, inv.team_code, inv.label)
        on conflict (id) do update
        set team_code = excluded.team_code, invite_label = excluded.invite_label, active = true, enrolled_at = now();
    return json_build_object('team_code', t.code, 'team_name', t.name, 'device_id', uid);
end
$$;
revoke all on function public.enroll(text) from public, anon;
grant execute on function public.enroll(text) to authenticated;

create function private.device_may_report(team text) returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1 from public.devices d join public.teams t on t.code = d.team_code
        where d.id = auth.uid() and d.active and t.active and d.team_code = team
    )
$$;
create function private.device_is_enrolled() returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1 from public.devices d join public.teams t on t.code = d.team_code
        where d.id = auth.uid() and d.active and t.active
    )
$$;
revoke all on function private.device_may_report(text), private.device_is_enrolled() from public;
grant execute on function private.device_may_report(text), private.device_is_enrolled() to authenticated;

-- Reports: enrolled devices only, for their own team.
drop policy "devices submit reports for an active team" on public.reports;
revoke all on public.reports from anon;
create policy "enrolled devices submit reports for their team" on public.reports
    for insert to authenticated
    with check (private.device_may_report(team_code));

-- The device id is the server's, not the phone's: it is what rate limits and corroboration count.
create or replace function private.check_report_limits() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    recent_device integer;
    recent_team   integer;
begin
    new.device_id := coalesce(auth.uid(), new.device_id);
    if new.captured_at > now() + interval '10 minutes' then
        raise exception 'captured_at is in the future' using errcode = 'PT422';
    end if;
    if new.captured_at < now() - interval '30 days' then
        raise exception 'the photo is more than 30 days old' using errcode = 'PT422';
    end if;
    select count(*) into recent_device from public.reports r
        where r.device_id = new.device_id and r.received_at > now() - interval '10 minutes';
    if recent_device >= 30 then
        raise exception 'too many reports from this device; try again in a few minutes' using errcode = 'PT429';
    end if;
    select count(*) into recent_team from public.reports r
        where r.team_code = new.team_code and r.received_at > now() - interval '1 minute';
    if recent_team >= 300 then
        raise exception 'too many reports for this team; try again shortly' using errcode = 'PT429';
    end if;
    new.received_at := now();  -- the server clock, never the client's
    return new;
end
$$;

-- Photos: enrolled devices only.
drop policy "devices upload report photos" on storage.objects;
create policy "enrolled devices upload report photos" on storage.objects
    for insert to authenticated
    with check (
        bucket_id = 'field-photos'
        and name ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg$'
        and private.device_is_enrolled()
    );
