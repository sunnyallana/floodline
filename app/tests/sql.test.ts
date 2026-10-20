import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { PGlite } from "@electric-sql/pglite";
import { beforeAll, describe as group, expect, it } from "vitest";
import { toRow } from "../src/upload";
import { sampleReport } from "./fixtures";

/** Runs the real migration against Postgres (PGlite) with the roles and storage tables Supabase provides, then
 * checks what a device holding the public anon key can and cannot do. */

const MIGRATIONS = fileURLToPath(new URL("../../supabase/migrations", import.meta.url));

const SUPABASE_STUB = `
  create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
  grant usage on schema public to anon, authenticated, service_role;
  -- As on a real Supabase project: every new table and function in public is granted to the API roles, so a
  -- table is private only if a migration revokes it or row-level security hides it.
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
  create schema storage;
  grant usage on schema storage to anon, authenticated, service_role;
  create table storage.buckets (id text primary key, name text, public boolean, file_size_limit bigint, allowed_mime_types text[]);
  create table storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text references storage.buckets, name text);
  alter table storage.objects enable row level security;
  grant insert, select on storage.objects to anon, authenticated;
  grant all on all tables in schema storage to service_role;
`;

let db: PGlite;

async function as<T>(role: string, fn: () => Promise<T>): Promise<T> {
  await db.exec(`set role ${role}`);
  try {
    return await fn();
  } finally {
    await db.exec("reset role");
  }
}

/** A row as the app sends it, captured now (the server refuses photos older than 30 days). */
function fresh(over: Parameters<typeof sampleReport>[0] = {}, bytes = 10): Record<string, unknown> {
  return toRow(sampleReport({ capturedAt: new Date().toISOString(), ...over }), bytes);
}

function insertSql(row: Record<string, unknown>): { sql: string; params: unknown[] } {
  const cols = Object.keys(row);
  return {
    sql: `insert into public.reports (${cols.join(", ")}) values (${cols.map((_, i) => `$${i + 1}`).join(", ")})`,
    params: Object.values(row),
  };
}

beforeAll(async () => {
  db = new PGlite();
  await db.exec(SUPABASE_STUB);
  for (const f of readdirSync(MIGRATIONS).filter((n) => n.endsWith(".sql")).sort()) {
    await db.exec(readFileSync(join(MIGRATIONS, f), "utf-8"));
  }
  await db.exec(`grant all on all tables in schema public to service_role;
    insert into public.teams (code, name) values ('TEAM-2026-01', 'Test team'), ('OLD-TEAM-2025', 'Retired');
    update public.teams set active = false where code = 'OLD-TEAM-2025';`);
}, 60_000);

group("reports table, as the anon role", () => {
  it("accepts a valid report for an active team", async () => {
    const { sql, params } = insertSql(fresh({}, 1234));
    await as("anon", () => db.query(sql, params));
    const n = await db.query<{ n: number }>("select count(*)::int as n from public.reports");
    expect(n.rows[0]?.n).toBe(1);
  });

  it("cannot read, change or delete any report", async () => {
    await expect(as("anon", () => db.query("select * from public.reports"))).rejects.toThrow(/permission denied/);
    await expect(as("anon", () => db.query("update public.reports set note = 'x'"))).rejects.toThrow(/permission denied/);
    await expect(as("anon", () => db.query("delete from public.reports"))).rejects.toThrow(/permission denied/);
  });

  it("cannot read team codes", async () => {
    await expect(as("anon", () => db.query("select * from public.teams"))).rejects.toThrow(/permission denied/);
  });

  it("rejects unknown or retired team codes", async () => {
    for (const code of ["NOT-A-TEAM-XX", "OLD-TEAM-2025"]) {
      const { sql, params } = insertSql(fresh({ id: crypto.randomUUID(), teamCode: code }));
      await expect(as("anon", () => db.query(sql, params))).rejects.toThrow(/row-level security|foreign key/);
    }
  });

  it("enforces the contract: photo named after the id, location consistent, consent given", async () => {
    const id = crypto.randomUUID();
    const base = fresh({ id });
    const bad: Record<string, unknown>[] = [
      { ...base, photo_path: "someone-else.jpg" },
      { ...base, lat: null }, // lon without lat
      { ...base, location_source: "none" }, // a point that claims to have no source
      { ...base, consent: false },
      { ...base, depth_guess: "neck" },
      { ...base, note: "x".repeat(501) },
    ];
    for (const row of bad) {
      const { sql, params } = insertSql(row);
      await expect(as("anon", () => db.query(sql, params))).rejects.toThrow();
    }
    const none = fresh({ id, location: { lat: null, lon: null, accuracyM: null, source: "none" } });
    const { sql, params } = insertSql(none);
    await as("anon", () => db.query(sql, params)); // a report without a location is valid
  });

  it("rejects a second insert with the same id, which the app treats as already sent", async () => {
    const { sql, params } = insertSql(fresh({}, 1234));
    await expect(as("anon", () => db.query(sql, params))).rejects.toThrow(/duplicate key/);
  });

  it("lets the service role read everything (that is what `floodline ingest` uses)", async () => {
    const rows = await as("service_role", () => db.query("select id from public.reports"));
    expect(rows.rows.length).toBe(2);
  });
});

group("report limits", () => {
  const insertAs = (row: Record<string, unknown>) => {
    const { sql, params } = insertSql(row);
    return as("anon", () => db.query(sql, params));
  };

  it("refuses capture times in the future or more than 30 days back, with HTTP-mapped codes", async () => {
    const future = new Date(Date.now() + 60 * 60_000).toISOString();
    const old = new Date(Date.now() - 31 * 24 * 3600_000).toISOString();
    for (const capturedAt of [future, old]) {
      const err = await insertAs(fresh({ id: crypto.randomUUID(), capturedAt })).catch((e: { code?: string }) => e);
      expect((err as { code?: string }).code).toBe("PT422"); // PostgREST answers 422: the app shows it, no retry
    }
  });

  it("stamps received_at with the server clock, whatever the client sends", async () => {
    const id = crypto.randomUUID();
    await insertAs({ ...fresh({ id }), received_at: "2000-01-01T00:00:00Z" });
    const r = await db.query<{ ok: boolean }>("select received_at > now() - interval '1 minute' as ok from public.reports where id = $1", [id]);
    expect(r.rows[0]?.ok).toBe(true);
  });

  it("rate-limits one device to 30 reports in 10 minutes", async () => {
    const deviceId = crypto.randomUUID();
    for (let i = 0; i < 30; i++) await insertAs(fresh({ id: crypto.randomUUID(), deviceId }));
    const err = await insertAs(fresh({ id: crypto.randomUUID(), deviceId })).catch((e: { code?: string }) => e);
    expect((err as { code?: string }).code).toBe("PT429"); // the app retries a 429 later
    await insertAs(fresh({ id: crypto.randomUUID(), deviceId: crypto.randomUUID() })); // other devices unaffected
  });

  it("requires a device id", async () => {
    const row = { ...fresh({ id: crypto.randomUUID() }) };
    delete row.device_id;
    await expect(insertAs(row)).rejects.toThrow(/device_id/);
  });
});

group("photo bucket", () => {
  it("is private, JPEG-only and capped at 5 MB", async () => {
    const b = await db.query<{ public: boolean; file_size_limit: number; allowed_mime_types: string[] }>(
      "select public, file_size_limit::int, allowed_mime_types from storage.buckets where id = 'field-photos'",
    );
    expect(b.rows[0]).toEqual({ public: false, file_size_limit: 5_000_000, allowed_mime_types: ["image/jpeg"] });
  });

  it("accepts <uuid>.jpg uploads and nothing else", async () => {
    const ok = `${crypto.randomUUID()}.jpg`;
    await as("anon", () => db.query("insert into storage.objects (bucket_id, name) values ('field-photos', $1)", [ok]));
    for (const name of ["../escape.jpg", "a.jpg", `${crypto.randomUUID()}.png`, `dir/${crypto.randomUUID()}.jpg`]) {
      await expect(
        as("anon", () => db.query("insert into storage.objects (bucket_id, name) values ('field-photos', $1)", [name])),
      ).rejects.toThrow(/row-level security/);
    }
  });

  it("cannot be listed or read by devices", async () => {
    const rows = await as("anon", () => db.query("select * from storage.objects"));
    expect(rows.rows).toHaveLength(0); // RLS with no select policy hides every object
  });
});
