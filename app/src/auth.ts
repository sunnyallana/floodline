import type { UploadConfig } from "./config";
import { UploadError } from "./upload";

/** The phone's own Supabase identity. The server signs it, so the device id that rate limits and corroboration
 * count is not something the phone can invent (see supabase/migrations/*_device_enrolment.sql). */
export interface Session {
  accessToken: string;
  refreshToken: string;
  expiresAt: number; // epoch ms
  userId: string;
}

export interface Enrolment {
  teamCode: string;
  teamName: string;
  deviceId: string;
}

export interface SessionStore {
  load(): Session | null;
  save(s: Session | null): void;
}

const SESSION_KEY = "floodline.field.session";
/** Refresh this long before expiry, so a token never expires between the check and the upload. */
export const REFRESH_MARGIN_MS = 90_000;

export const localSessionStore: SessionStore = {
  load() {
    try {
      const s = JSON.parse(localStorage.getItem(SESSION_KEY) ?? "null") as Session | null;
      return s && typeof s.refreshToken === "string" && typeof s.userId === "string" ? s : null;
    } catch {
      return null;
    }
  },
  save(s) {
    if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else localStorage.removeItem(SESSION_KEY);
  },
};

/** Invite codes are typed by people: compare without case, spaces or dashes (as private.normalise_invite). */
export function normaliseInvite(code: string): string {
  return code.replace(/[^0-9A-Za-z]/g, "").toUpperCase();
}

/** XXXX-XXXX-XXXX-XXXX, for display while typing. */
export function formatInvite(code: string): string {
  return (normaliseInvite(code).match(/.{1,4}/g) ?? []).join("-");
}

type Fetch = typeof fetch;

interface TokenReply {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: { id: string };
}

export class DeviceAuth {
  private pending: Promise<Session> | null = null;

  constructor(
    private readonly cfg: UploadConfig,
    private readonly store: SessionStore = localSessionStore,
    private readonly fetchImpl: Fetch = (...a) => fetch(...a),
    private readonly clock: () => number = Date.now,
  ) {}

  /** A valid session: the stored one, refreshed if it is about to expire, or a new anonymous sign-in. */
  session(): Promise<Session> {
    // One request at a time: two uploads starting together must not sign in twice.
    this.pending ??= this.fresh().finally(() => (this.pending = null));
    return this.pending;
  }

  async accessToken(): Promise<string> {
    return (await this.session()).accessToken;
  }

  /** Drop the stored session so the next call refreshes it (after a 401 that says the token expired). */
  expire(): void {
    const s = this.store.load();
    if (s) this.store.save({ ...s, expiresAt: 0 });
  }

  /** Redeem a coordinator's invite. Needs signal once; afterwards the phone works offline. */
  async enroll(invite: string): Promise<Enrolment> {
    const token = await this.accessToken();
    const res = await this.call("/rest/v1/rpc/enroll", { invite: normaliseInvite(invite) }, token);
    if (res.status === 403 || res.status === 400) {
      throw new UploadError("This invite is not valid, has already been used, or has expired.", false, res.status);
    }
    if (!res.ok) throw new UploadError(`Enrolment failed (${res.status})`, res.status >= 500 || res.status === 429, res.status);
    const r = (await res.json()) as { team_code: string; team_name: string; device_id: string };
    return { teamCode: r.team_code, teamName: r.team_name, deviceId: r.device_id };
  }

  private async fresh(): Promise<Session> {
    const s = this.store.load();
    if (s && s.expiresAt - REFRESH_MARGIN_MS > this.clock()) return s;
    if (s) {
      const res = await this.call("/auth/v1/token?grant_type=refresh_token", { refresh_token: s.refreshToken });
      if (res.ok) return this.keep((await res.json()) as TokenReply);
      if (res.status >= 500 || res.status === 429) throw new UploadError(`Sign-in refresh failed (${res.status})`, true, res.status);
      // The refresh token was revoked (the coordinator withdrew this phone) or is unknown: the identity is gone.
      this.store.save(null);
      throw new UploadError("This phone's enrolment has ended. Ask your coordinator for a new invite.", false, res.status);
    }
    const res = await this.call("/auth/v1/signup", {});
    if (!res.ok) throw new UploadError(`Sign-in failed (${res.status})`, res.status >= 500 || res.status === 429, res.status);
    return this.keep((await res.json()) as TokenReply);
  }

  private keep(r: TokenReply): Session {
    const s = { accessToken: r.access_token, refreshToken: r.refresh_token, expiresAt: this.clock() + r.expires_in * 1000, userId: r.user.id };
    this.store.save(s);
    return s;
  }

  private async call(path: string, body: unknown, token?: string): Promise<Response> {
    try {
      return await this.fetchImpl(`${this.cfg.supabaseUrl}${path}`, {
        method: "POST",
        headers: { apikey: this.cfg.anonKey, Authorization: `Bearer ${token ?? this.cfg.anonKey}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      throw new UploadError(`Network error: ${(e as Error).message}`, true);
    }
  }
}
