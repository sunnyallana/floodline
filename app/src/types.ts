/** The report contract shared with `supabase/migrations` and `floodline ingest`. Field names are camelCase here
 * and snake_case in the database; `toRow` in upload.ts is the only place that maps between them. */

export const DEPTH_CLASSES = ["unknown", "dry", "ankle", "knee", "waist", "chest", "above_head"] as const;
export type DepthClass = (typeof DEPTH_CLASSES)[number];

/** Same boundaries as `DEPTH_BINS_CM` in src/floodline/depth.py. */
export const DEPTH_LABELS: Record<DepthClass, { label: string; hint: string }> = {
  unknown: { label: "Not sure", hint: "nothing to judge depth against" },
  dry: { label: "Dry", hint: "under 2 cm" },
  ankle: { label: "Ankle", hint: "2–20 cm" },
  knee: { label: "Knee", hint: "20–50 cm" },
  waist: { label: "Waist", hint: "50–100 cm" },
  chest: { label: "Chest", hint: "100–160 cm" },
  above_head: { label: "Above head", hint: "160 cm or more, ground floor under" },
};

export type LocationSource = "device" | "exif" | "none";

export interface ResolvedLocation {
  lat: number | null;
  lon: number | null;
  accuracyM: number | null;
  source: LocationSource;
}

export interface Report {
  id: string;
  /** Random per install; lets the pipeline count independent devices and the server rate-limit. */
  deviceId: string;
  teamCode: string;
  capturedAt: string; // ISO 8601
  location: ResolvedLocation;
  peopleVisible: number;
  peopleInWater: number;
  peopleElevated: number;
  depthGuess: DepthClass;
  note: string;
  consent: true;
  appVersion: string;
}

export type QueueStatus = "queued" | "uploading" | "sent" | "failed";

export interface QueueItem {
  id: string;
  report: Report;
  /** Full photo; dropped after a successful upload to free device storage. */
  photo: Blob | null;
  photoBytes: number;
  thumb: Blob;
  status: QueueStatus;
  photoUploaded: boolean;
  attempts: number;
  nextAttemptAt: number; // epoch ms
  lastError: string | null;
  sentAt: string | null;
}

export const NOTE_MAX = 500;
export const PEOPLE_MAX = 999;
