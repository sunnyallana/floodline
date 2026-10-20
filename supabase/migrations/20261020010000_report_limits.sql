-- Limits that hold even against someone who has a valid team code.
--
-- device_id: a random UUID the app creates once per install. It identifies nothing about the person; it lets
-- the pipeline count independent devices (two reports from one phone are not two witnesses) and lets these
-- limits apply per device.
--
-- Rejections use PostgREST's custom status convention (SQLSTATE 'PTxyz' -> HTTP xyz): 429 is retried later by
-- the app, 422 is shown to the user as a permanent refusal.

alter table public.reports add column device_id uuid not null;
create index reports_device_received on public.reports (device_id, received_at);
create index reports_team_received on public.reports (team_code, received_at);

create function private.check_report_limits() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    recent_device integer;
    recent_team   integer;
begin
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
revoke all on function private.check_report_limits() from public;

create trigger reports_limits before insert on public.reports
    for each row execute function private.check_report_limits();
