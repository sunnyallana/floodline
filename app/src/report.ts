import { NOTE_MAX, PEOPLE_MAX, type DepthClass, type QueueItem, type Report, type ResolvedLocation } from "./types";

export interface Draft {
  deviceId: string;
  teamCode: string;
  capturedAt: number;
  location: ResolvedLocation;
  peopleVisible: number;
  peopleInWater: number;
  peopleElevated: number;
  depthGuess: DepthClass;
  note: string;
}

export function clampCount(n: number): number {
  return Number.isFinite(n) ? Math.min(PEOPLE_MAX, Math.max(0, Math.round(n))) : 0;
}

/** Builds the report exactly as the database will validate it, so a constraint violation is caught on the
 * device, where the user can still fix it, not on the server where it would be a permanent failure. */
export function buildReport(draft: Draft, id: string, appVersion: string): Report {
  const teamCode = draft.teamCode.trim();
  if (teamCode.length < 8 || teamCode.length > 64) throw new Error("Team code must be 8 to 64 characters.");
  const peopleInWater = clampCount(draft.peopleInWater);
  const peopleElevated = clampCount(draft.peopleElevated);
  return {
    id,
    deviceId: draft.deviceId,
    teamCode,
    capturedAt: new Date(draft.capturedAt).toISOString(),
    location: draft.location,
    // Everyone in the water or on a roof is also visible; the total can never be lower than its parts.
    peopleVisible: clampCount(Math.max(draft.peopleVisible, peopleInWater + peopleElevated)),
    peopleInWater,
    peopleElevated,
    depthGuess: draft.depthGuess,
    note: draft.note.trim().slice(0, NOTE_MAX),
    consent: true,
    appVersion: appVersion.slice(0, 32),
  };
}

export function newItem(report: Report, photo: Blob, thumb: Blob, now: number): QueueItem {
  return {
    id: report.id,
    report,
    photo,
    photoBytes: photo.size,
    thumb,
    status: "queued",
    photoUploaded: false,
    attempts: 0,
    nextAttemptAt: now,
    lastError: null,
    sentAt: null,
  };
}
