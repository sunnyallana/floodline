import { describe, expect, it } from "vitest";
import { DeviceAuth, REFRESH_MARGIN_MS, type Session, type SessionStore, formatInvite, normaliseInvite } from "../src/auth";
import { inviteHint } from "../src/copy";
import { UploadError } from "../src/upload";

const CFG = { supabaseUrl: "https://x.supabase.co", anonKey: "anon" };

function memory(initial: Session | null = null): SessionStore & { value: Session | null } {
  const m = {
    value: initial,
    load: () => m.value,
    save: (s: Session | null) => {
      m.value = s;
    },
  };
  return m;
}

/** A fake GoTrue + PostgREST: records calls and answers by path. */
function server(routes: Record<string, (body: Record<string, unknown>, auth: string) => [number, unknown]>) {
  const calls: { path: string; body: Record<string, unknown>; auth: string }[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    const path = url.replace(CFG.supabaseUrl, "");
    const body = JSON.parse(String(init.body ?? "{}")) as Record<string, unknown>;
    const auth = (init.headers as Record<string, string>).Authorization ?? "";
    calls.push({ path, body, auth });
    const route = routes[path];
    const [status, payload] = route ? route(body, auth) : [404, {}];
    return new Response(JSON.stringify(payload), { status });
  }) as unknown as typeof fetch;
  return { calls, fetchImpl };
}

const token = (n: number, id = "dev-1") => ({ access_token: `at${n}`, refresh_token: `rt${n}`, expires_in: 3600, user: { id } });

describe("device identity", () => {
  it("signs in anonymously once and reuses the session while it is fresh", async () => {
    let n = 0;
    const { calls, fetchImpl } = server({ "/auth/v1/signup": () => [200, token(++n)] });
    const store = memory();
    const auth = new DeviceAuth(CFG, store, fetchImpl, () => 1_000);
    const [a, b] = await Promise.all([auth.accessToken(), auth.accessToken()]); // concurrent uploads, one sign-in
    expect([a, b]).toEqual(["at1", "at1"]);
    expect(await auth.accessToken()).toBe("at1");
    expect(calls.filter((c) => c.path === "/auth/v1/signup")).toHaveLength(1);
    expect(store.value?.userId).toBe("dev-1");
  });

  it("refreshes shortly before expiry and keeps the same identity", async () => {
    let now = 0;
    const { calls, fetchImpl } = server({
      "/auth/v1/token?grant_type=refresh_token": (b) => [200, b.refresh_token === "rt1" ? token(2) : {}],
    });
    const store = memory({ accessToken: "at1", refreshToken: "rt1", expiresAt: 3_600_000, userId: "dev-1" });
    const auth = new DeviceAuth(CFG, store, fetchImpl, () => now);
    expect(await auth.accessToken()).toBe("at1");
    now = 3_600_000 - REFRESH_MARGIN_MS + 1;
    expect(await auth.accessToken()).toBe("at2");
    expect(calls).toHaveLength(1);
    expect(store.value).toMatchObject({ refreshToken: "rt2", userId: "dev-1" });
  });

  it("forgets an identity whose refresh is refused (the coordinator withdrew it) and says so", async () => {
    const { fetchImpl } = server({ "/auth/v1/token?grant_type=refresh_token": () => [400, { error: "invalid_grant" }] });
    const store = memory({ accessToken: "at1", refreshToken: "rt1", expiresAt: 0, userId: "dev-1" });
    const err = await new DeviceAuth(CFG, store, fetchImpl, () => 1).accessToken().catch((e: UploadError) => e);
    expect(err).toBeInstanceOf(UploadError);
    expect((err as UploadError).retryable).toBe(false);
    expect((err as UploadError).message).toMatch(/new invite/);
    expect(store.value).toBeNull();
  });

  it("treats a server or network failure during refresh as temporary", async () => {
    const { fetchImpl } = server({ "/auth/v1/token?grant_type=refresh_token": () => [503, {}] });
    const store = memory({ accessToken: "at1", refreshToken: "rt1", expiresAt: 0, userId: "dev-1" });
    const err = await new DeviceAuth(CFG, store, fetchImpl, () => 1).accessToken().catch((e: UploadError) => e);
    expect((err as UploadError).retryable).toBe(true);
    expect(store.value).not.toBeNull();
  });

  it("enrols with a normalised invite, sent with the phone's own token", async () => {
    const { calls, fetchImpl } = server({
      "/auth/v1/signup": () => [200, token(1)],
      "/rest/v1/rpc/enroll": (b) =>
        b.invite === "ABCD2345EFGH6789" ? [200, { team_code: "T1", team_name: "Larkana boats", device_id: "dev-1" }] : [403, {}],
    });
    const auth = new DeviceAuth(CFG, memory(), fetchImpl, () => 1);
    expect(await auth.enroll(" abcd-2345 efgh-6789 ")).toEqual({ teamCode: "T1", teamName: "Larkana boats", deviceId: "dev-1" });
    expect(calls.at(-1)?.auth).toBe("Bearer at1");
    const err = await auth.enroll("ZZZZ-ZZZZ-ZZZZ-ZZZZ").catch((e: UploadError) => e);
    expect((err as UploadError).message).toMatch(/not valid|already been used|expired/);
    expect((err as UploadError).retryable).toBe(false);
  });

  it("formats and checks invites as they are typed", () => {
    expect(normaliseInvite("ab-cd ef")).toBe("ABCDEF");
    expect(formatInvite("abcd2345efgh6789")).toBe("ABCD-2345-EFGH-6789");
    expect(inviteHint("")).toMatch(/One invite per phone/);
    expect(inviteHint("ABCD-2345")).toBe("8 more characters.");
    expect(inviteHint("ABCD-2345-EFGH-6789")).toBe("Looks right.");
    expect(inviteHint("ABCD-2345-EFGH-67890")).toMatch(/too many/);
  });
});
