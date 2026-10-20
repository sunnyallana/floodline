import type { UploadConfig } from "./config";
import type { Report } from "./types";

export const PHOTO_BUCKET = "field-photos";
export const NOT_ENROLLED = "This phone is not enrolled with the team, or its enrolment was withdrawn. Enrol it again under Settings.";

/** `retryable` decides between "try again later" and "this will never succeed; tell the user". */
export class UploadError extends Error {
  constructor(
    message: string,
    readonly retryable: boolean,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "UploadError";
  }
}

type Fetch = typeof fetch;

export function photoPath(reportId: string): string {
  return `${reportId}.jpg`;
}

/** The database row, column for column with supabase/migrations/*_field_reports.sql. */
export function toRow(report: Report, photoBytes: number): Record<string, unknown> {
  return {
    id: report.id,
    device_id: report.deviceId,
    team_code: report.teamCode,
    captured_at: report.capturedAt,
    lat: report.location.lat,
    lon: report.location.lon,
    accuracy_m: report.location.accuracyM,
    location_source: report.location.source,
    people_visible: report.peopleVisible,
    people_in_water: report.peopleInWater,
    people_elevated: report.peopleElevated,
    depth_guess: report.depthGuess,
    note: report.note,
    photo_path: photoPath(report.id),
    photo_bytes: photoBytes,
    app_version: report.appVersion,
    consent: report.consent,
  };
}

async function headers(cfg: UploadConfig, extra: Record<string, string>): Promise<Record<string, string>> {
  const token = cfg.accessToken ? await cfg.accessToken() : cfg.anonKey;
  return { apikey: cfg.anonKey, Authorization: `Bearer ${token}`, ...extra };
}

/** A 401 for an expired token is worth retrying once the token is refreshed; any other 401/403 is final. */
function tokenExpired(cfg: UploadConfig, status: number, body: string): boolean {
  if (status !== 401 || !/jwt expired|token is expired|invalid jwt/i.test(body)) return false;
  cfg.expireToken?.();
  return true;
}

async function send(fetchImpl: Fetch, url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetchImpl(url, init);
  } catch (e) {
    // fetch rejects only for network failure (offline, DNS, captive portal): always worth retrying.
    throw new UploadError(`Network error: ${(e as Error).message}`, true);
  }
}

async function failure(res: Response, what: string): Promise<UploadError> {
  const body = await res.text().catch(() => "");
  const retryable = res.status >= 500 || res.status === 408 || res.status === 429;
  const detail = body.slice(0, 200).replace(/\s+/g, " ").trim();
  return new UploadError(`${what} failed (${res.status})${detail ? `: ${detail}` : ""}`, retryable, res.status);
}

/** True when Storage says the object already exists: a previous attempt uploaded it and the reply was lost. */
function isDuplicate(status: number, body: string): boolean {
  return status === 409 || (status === 400 && /duplicate|already exists/i.test(body));
}

export async function uploadPhoto(cfg: UploadConfig, reportId: string, photo: Blob, fetchImpl: Fetch = fetch): Promise<void> {
  const url = `${cfg.supabaseUrl}/storage/v1/object/${PHOTO_BUCKET}/${photoPath(reportId)}`;
  const res = await send(fetchImpl, url, {
    method: "POST",
    headers: await headers(cfg, { "Content-Type": "image/jpeg", "x-upsert": "false", "cache-control": "no-store" }),
    body: photo,
  });
  if (res.ok) return;
  const body = await res.clone().text().catch(() => "");
  if (isDuplicate(res.status, body)) return;
  if (tokenExpired(cfg, res.status, body)) throw new UploadError("Sign-in expired; retrying", true, res.status);
  if (res.status === 403 || (res.status === 400 && /row-level security|unauthorized/i.test(body))) {
    throw new UploadError(NOT_ENROLLED, false, res.status);
  }
  throw await failure(res, "Photo upload");
}

export async function insertReport(cfg: UploadConfig, report: Report, photoBytes: number, fetchImpl: Fetch = fetch): Promise<void> {
  const res = await send(fetchImpl, `${cfg.supabaseUrl}/rest/v1/reports`, {
    method: "POST",
    // return=minimal: devices have INSERT but no SELECT, so asking for the row back would be refused.
    headers: await headers(cfg, { "Content-Type": "application/json", Prefer: "return=minimal" }),
    body: JSON.stringify(toRow(report, photoBytes)),
  });
  if (res.ok || res.status === 409) return; // 409: primary key exists, i.e. an earlier attempt succeeded
  const body = await res.clone().text().catch(() => "");
  if (tokenExpired(cfg, res.status, body)) throw new UploadError("Sign-in expired; retrying", true, res.status);
  const err = await failure(res, "Report upload");
  if (res.status === 401 || res.status === 403) throw new UploadError(NOT_ENROLLED, false, res.status);
  throw err;
}
