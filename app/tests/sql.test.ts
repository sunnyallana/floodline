import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { PGlite } from "@electric-sql/pglite";
import { beforeAll, describe as group, expect, it } from "vitest";
import { normaliseInvite } from "../src/auth";
import { toRow } from "../src/upload";
import { sampleReport } from "./fixtures";

/** Runs the real migrations against Postgres (PGlite) with the roles, grants, auth.uid() and storage tables
 * Supabase provides, then checks what the public anon key, a signed-in but unenrolled phone, and an enrolled
 * phone can and cannot do. */

const MIGRATIONS = fileURLToPath(new URL("../../supabase/migrations", import.meta.url));

const SUPABASE_STUB = `
  create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
  grant usage on schema public to anon, authenticated, service_role;
  -- As on a real Supabase project: every new table and function in public is granted to the API roles, so a
  -- table is private only if a migration revokes it or row-level security hides it.
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
  create schema auth;
  grant usage on schema auth to anon, authenticated, service_role;
  -- auth.uid() reads the subject of the caller's JWT, which PostgREST exposes as a setting.
  create function auth.uid() returns uuid language sql stable
    as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant execute on function auth.uid() to anon, authenticated, service_role;
  create schema storage;
  grant usage on schema storage to anon, authenticated, service_role;
  create table storage.buckets (id text primary key, name text, public boolean, file_size_limit bigint, allowed_mime_types text[]);
  create table storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text references storage.buckets, name text);
  alter table storage.objects enable row level security;
  grant insert, select on storage.objects to anon, authenticated;
  grant all on all tables in schema storage to service_role;
`;

let db: PGlite;

async function as<T>(role: string, fn: () => Promise<T>, uid: string | null = null): Promise<T> {
  await db.query("select set_config('request.jwt.claim.sub', $1, false)", [uid ?? ""]);
  await db.exec(`set role ${role}`);
  try {
    return await fn();
  } finally {
    await db.exec("reset role");
    await db.query("select set_config('request.jwt.claim.sub', '', false)");
  }
}

/** A phone signed in with its own Supabase identity (enrolled or not). */
const asPhone = <T>(uid: string, fn: () => Promise<T>) => as("authenticated", fn, uid);

const hash = (code: string) => createHash("sha256").update(normaliseInvite(code), "utf8").digest("hex");

async function invite(code: string, team = "TEAM-2026-01", label = "", expires = "now() + interval '30 days'") {
  await db.query(
    `insert into public.invites (code_hash, team_code, label, expires_at) values ($1, $2, $3, ${expires})`,
    [hash(code), team, label],
  );
}

const enroll = (uid: string, code: string) =>
  asPhone(uid, () => db.query<{ r: { team_code: string; team_name: string; device_id: string } }>("select public.enroll($1) as r", [code]));

/** A row as the app sends it, captured now (the server refuses photos older than 30 days). */
function fresh(over: Parameters<typeof sampleReport>[0] = {}, bytes = 10): Record<string, unknown> {
  return toRow(sampleReport({ id: crypto.randomUUID(), capturedAt: new Date().toISOString(), ...over }), bytes);
}

function insertSql(row: Record<string, unknown>): { sql: string; params: unknown[] } {
  const cols = Object.keys(row);
  return {
    sql: `insert into public.reports (${cols.join(", ")}) values (${cols.map((_, i) => `$${i + 1}`).join(", ")})`,
    params: Object.values(row),
  };
}

const insertAs = (uid: string, row: Record<string, unknown>) => {
  const { sql, params } = insertSql(row);
  return asPhone(uid, () => db.query(sql, params));
};

const PHONE = crypto.randomUUID(); // enrolled with TEAM-2026-01
const PHONE2 = crypto.randomUUID(); // enrolled with TEAM-2026-01
const STRANGER = crypto.randomUUID(); // signed in, never enrolled

beforeAll(async () => {
  db = new PGlite();
  await db.exec(SUPABASE_STUB);
  for (const f of readdirSync(MIGRATIONS).filter((n) => n.endsWith(".sql")).sort()) {
    await db.exec(readFileSync(join(MIGRATIONS, f), "utf-8"));
  }
  await db.exec(`grant all on all tables in schema public to service_role;
    insert into public.teams (code, name) values
      ('TEAM-2026-01', 'Test team'), ('OLD-TEAM-2025', 'Retired'), ('OTHER-TEAM-01', 'Another district');
    update public.teams set active = false where code = 'OLD-TEAM-2025';`);
  await invite("AAAA-BBBB-CCCC-DDDD", "TEAM-2026-01", "Ali, boat 3");
  await invite("EEEE-FFFF-GGGG-HHHH");
  await enroll(PHONE, "AAAA-BBBB-CCCC-DDDD");
  await enroll(PHONE2, "EEEE-FFFF-GGGG-HHHH");
}, 60_000);

group("enrolment", () => {
  it("the anon key alone can do nothing: no report, no photo, no enrolment", async () => {
    const { sql, params } = insertSql(fresh());
    await expect(as("anon", () => db.query(sql, params))).rejects.toThrow(/permission denied/);
    await expect(as("anon", () => db.query("select public.enroll('AAAA-BBBB-CCCC-DDDD')"))).rejects.toThrow(/permission denied/);
    await expect(
      as("anon", () => db.query("insert into storage.objects (bucket_id, name) values ('field-photos', $1)", [`${crypto.randomUUID()}.jpg`])),
    ).rejects.toThrow(/row-level security/);
  });

  it("a signed-in phone that has not redeemed an invite cannot report", async () => {
    await expect(insertAs(STRANGER, fresh())).rejects.toThrow(/row-level security/);
  });

  it("binds the phone to the invite's team and records the invite's label", async () => {
    const r = await as("service_role", () =>
      db.query<{ team_code: string; invite_label: string; active: boolean }>("select team_code, invite_label, active from public.devices where id = $1", [PHONE]),
    );
    expect(r.rows[0]).toEqual({ team_code: "TEAM-2026-01", invite_label: "Ali, boat 3", active: true });
  });

  it("an invite works once: a second phone is refused, the same phone retrying is not", async () => {
    const err = await enroll(STRANGER, "AAAA-BBBB-CCCC-DDDD").catch((e: { code?: string }) => e);
    expect((err as { code?: string }).code).toBe("PT403");
    const again = await enroll(PHONE, "AAAA-BBBB-CCCC-DDDD"); // the first reply was lost on a bad connection
    expect(again.rows[0]?.r.team_code).toBe("TEAM-2026-01");
  });

  it("compares codes without case, spaces or dashes", async () => {
    await invite("JJJJ-KKKK-LLLL-MMMM");
    const uid = crypto.randomUUID();
    const r = await enroll(uid, " jjjj kkkk-llll mmmm ");
    expect(r.rows[0]?.r).toMatchObject({ team_code: "TEAM-2026-01", team_name: "Test team", device_id: uid });
  });

  it("refuses expired invites, invites for a retired team, and made-up codes", async () => {
    await invite("NNNN-PPPP-QQQQ-RRRR", "TEAM-2026-01", "", "now() - interval '1 day'");
    await invite("SSSS-TTTT-UUUU-VVVV", "OLD-TEAM-2025");
    for (const code of ["NNNN-PPPP-QQQQ-RRRR", "SSSS-TTTT-UUUU-VVVV", "WXYZ-WXYZ-WXYZ-WXYZ"]) {
      const err = await enroll(crypto.randomUUID(), code).catch((e: { code?: string }) => e);
      expect((err as { code?: string }).code).toBe("PT403");
    }
  });

  it("keeps invites, devices and team codes away from every phone", async () => {
    for (const table of ["invites", "devices", "teams"]) {
      await expect(asPhone(PHONE, () => db.query(`select * from public.${table}`))).rejects.toThrow(/permission denied/);
      await expect(as("anon", () => db.query(`select * from public.${table}`))).rejects.toThrow(/permission denied/);
    }
  });
});

group("reports, as an enrolled phone", () => {
  it("accepts a valid report for its own team", async () => {
    const row = fresh({}, 1234);
    await insertAs(PHONE, row);
    const n = await db.query<{ n: number }>("select count(*)::int as n from public.reports where id = $1", [row.id]);
    expect(n.rows[0]?.n).toBe(1);
  });

  it("cannot report for another team, even an active one", async () => {
    await expect(insertAs(PHONE, fresh({ teamCode: "OTHER-TEAM-01" }))).rejects.toThrow(/row-level security/);
  });

  it("cannot read, change or delete any report", async () => {
    await expect(asPhone(PHONE, () => db.query("select * from public.reports"))).rejects.toThrow(/permission denied/);
    await expect(asPhone(PHONE, () => db.query("update public.reports set note = 'x'"))).rejects.toThrow(/permission denied/);
    await expect(asPhone(PHONE, () => db.query("delete from public.reports"))).rejects.toThrow(/permission denied/);
  });

  it("stamps the server's device id, whatever the phone sends", async () => {
    const row = fresh({ deviceId: crypto.randomUUID() });
    await insertAs(PHONE, row);
    const r = await db.query<{ device_id: string }>("select device_id from public.reports where id = $1", [row.id]);
    expect(r.rows[0]?.device_id).toBe(PHONE);
  });

  it("enforces the contract: photo named after the id, location consistent, consent given", async () => {
    const base = fresh();
    const bad: Record<string, unknown>[] = [
      { ...base, photo_path: "someone-else.jpg" },
      { ...base, lat: null }, // lon without lat
      { ...base, location_source: "none" }, // a point that claims to have no source
      { ...base, consent: false },
      { ...base, depth_guess: "neck" },
      { ...base, note: "x".repeat(501) },
    ];
    for (const row of bad) await expect(insertAs(PHONE, row)).rejects.toThrow();
    const none = fresh({ id: base.id as string, location: { lat: null, lon: null, accuracyM: null, source: "none" } });
    await insertAs(PHONE, none); // a report without a location is valid
  });

  it("rejects a second insert with the same id, which the app treats as already sent", async () => {
    const row = fresh();
    await insertAs(PHONE, row);
    await expect(insertAs(PHONE, row)).rejects.toThrow(/duplicate key/);
  });

  it("a revoked phone can no longer report", async () => {
    const uid = crypto.randomUUID();
    await invite("XXXX-YYYY-ZZZZ-2345");
    await enroll(uid, "XXXX-YYYY-ZZZZ-2345");
    await insertAs(uid, fresh());
    await as("service_role", () => db.query("update public.devices set active = false where id = $1", [uid]));
    await expect(insertAs(uid, fresh())).rejects.toThrow(/row-level security/);
  });

  it("lets the service role read everything (that is what `floodline ingest` uses)", async () => {
    const rows = await as("service_role", () => db.query("select id from public.reports"));
    expect(rows.rows.length).toBeGreaterThan(3);
  });
});

group("report limits", () => {
  it("refuses capture times in the future or more than 30 days back, with HTTP-mapped codes", async () => {
    const future = new Date(Date.now() + 60 * 60_000).toISOString();
    const old = new Date(Date.now() - 31 * 24 * 3600_000).toISOString();
    for (const capturedAt of [future, old]) {
      const err = await insertAs(PHONE2, fresh({ capturedAt })).catch((e: { code?: string }) => e);
      expect((err as { code?: string }).code).toBe("PT422"); // PostgREST answers 422: the app shows it, no retry
    }
  });

  it("stamps received_at with the server clock, whatever the client sends", async () => {
    const row: Record<string, unknown> = { ...fresh(), received_at: "2000-01-01T00:00:00Z" };
    await insertAs(PHONE2, row);
    const r = await db.query<{ ok: boolean }>("select received_at > now() - interval '1 minute' as ok from public.reports where id = $1", [row.id]);
    expect(r.rows[0]?.ok).toBe(true);
  });

  it("limits a phone to 30 reports in 10 minutes, and inventing device ids does not get round it", async () => {
    const uid = crypto.randomUUID();
    await invite("2345-6789-ABCD-EFGH");
    await enroll(uid, "2345-6789-ABCD-EFGH");
    for (let i = 0; i < 30; i++) await insertAs(uid, fresh({ deviceId: crypto.randomUUID() }));
    const err = await insertAs(uid, fresh({ deviceId: crypto.randomUUID() })).catch((e: { code?: string }) => e);
    expect((err as { code?: string }).code).toBe("PT429"); // the app retries a 429 later
    await insertAs(PHONE2, fresh()); // other phones are unaffected
  });
});

group("photo bucket", () => {
  const upload = (role: string, uid: string | null, name: string) =>
    as(role, () => db.query("insert into storage.objects (bucket_id, name) values ('field-photos', $1)", [name]), uid);

  it("is private, JPEG-only and capped at 5 MB", async () => {
    const b = await db.query<{ public: boolean; file_size_limit: number; allowed_mime_types: string[] }>(
      "select public, file_size_limit::int, allowed_mime_types from storage.buckets where id = 'field-photos'",
    );
    expect(b.rows[0]).toEqual({ public: false, file_size_limit: 5_000_000, allowed_mime_types: ["image/jpeg"] });
  });

  it("accepts <uuid>.jpg from an enrolled phone and nothing else", async () => {
    await upload("authenticated", PHONE, `${crypto.randomUUID()}.jpg`);
    for (const name of ["../escape.jpg", "a.jpg", `${crypto.randomUUID()}.png`, `dir/${crypto.randomUUID()}.jpg`]) {
      await expect(upload("authenticated", PHONE, name)).rejects.toThrow(/row-level security/);
    }
    await expect(upload("authenticated", STRANGER, `${crypto.randomUUID()}.jpg`)).rejects.toThrow(/row-level security/);
  });

  it("cannot be listed or read by phones", async () => {
    const rows = await asPhone(PHONE, () => db.query("select * from storage.objects"));
    expect(rows.rows).toHaveLength(0); // RLS with no select policy hides every object
  });
});
