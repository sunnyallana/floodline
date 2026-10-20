import type { UploadConfig } from "./config";
import type { Report } from "./types";

export const PHOTO_BUCKET = "field-photos";

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

function headers(cfg: UploadConfig, extra: Record<string, string>): Record<string, string> {
  return { apikey: cfg.anonKey, Authorization: `Bearer ${cfg.anonKey}`, ...extra };
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
    headers: headers(cfg, { "Content-Type": "image/jpeg", "x-upsert": "false", "cache-control": "no-store" }),
    body: photo,
  });
  if (res.ok) return;
  const body = await res.clone().text().catch(() => "");
  if (isDuplicate(res.status, body)) return;
  throw await failure(res, "Photo upload");
}

export async function insertReport(cfg: UploadConfig, report: Report, photoBytes: number, fetchImpl: Fetch = fetch): Promise<void> {
  const res = await send(fetchImpl, `${cfg.supabaseUrl}/rest/v1/reports`, {
    method: "POST",
    // return=minimal: devices have INSERT but no SELECT, so asking for the row back would be refused.
    headers: headers(cfg, { "Content-Type": "application/json", Prefer: "return=minimal" }),
    body: JSON.stringify(toRow(report, photoBytes)),
  });
  if (res.ok || res.status === 409) return; // 409: primary key exists, i.e. an earlier attempt succeeded
  const err = await failure(res, "Report upload");
  if (res.status === 401 || res.status === 403) {
    throw new UploadError("The team code was not accepted. Check it under Settings.", false, res.status);
  }
  throw err;
}
