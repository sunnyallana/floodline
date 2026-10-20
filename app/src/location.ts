import type { ResolvedLocation } from "./types";

export interface Fix {
  lat: number;
  lon: number;
  accuracyM: number;
  at: number; // epoch ms
}

export interface PhotoMeta {
  lat: number | null;
  lon: number | null;
  accuracyM: number | null;
  takenAt: number | null; // epoch ms, from EXIF DateTimeOriginal or the file's lastModified
}

/** A device fix only describes a photo taken while the fix was current. A gallery photo taken an hour ago, or
 * in another street, must not inherit where the phone is now. */
export const DEVICE_FIX_MAX_AGE_MS = 10 * 60 * 1000;
/** Fixes worse than this place the photo in the wrong hexagon more often than the right one. */
export const DEVICE_FIX_MAX_ACCURACY_M = 1000;

const NONE: ResolvedLocation = { lat: null, lon: null, accuracyM: null, source: "none" };

function valid(lat: number | null, lon: number | null): lat is number {
  return (
    lat !== null && lon !== null && Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 && !(lat === 0 && lon === 0)
  );
}

/**
 * Where the photo was taken, with the source that says so.
 *
 * 1. GPS written into the photo by the camera is used first: it was recorded at the moment of capture.
 * 2. Otherwise the phone's own fix, but only if the photo is fresh (taken within DEVICE_FIX_MAX_AGE_MS of the
 *    fix) and the fix is usable.
 * 3. Otherwise no location. The report is still sent, but it never gets a point on the map.
 */
export function resolveLocation(photo: PhotoMeta, device: Fix | null, now: number): ResolvedLocation {
  if (valid(photo.lat, photo.lon)) {
    return { lat: photo.lat, lon: photo.lon as number, accuracyM: photo.accuracyM, source: "exif" };
  }
  if (!device || device.accuracyM > DEVICE_FIX_MAX_ACCURACY_M || !valid(device.lat, device.lon)) return NONE;
  const takenAt = photo.takenAt ?? now;
  if (Math.abs(takenAt - device.at) > DEVICE_FIX_MAX_AGE_MS) return NONE;
  return { lat: device.lat, lon: device.lon, accuracyM: Math.round(device.accuracyM), source: "device" };
}

/** Keeps the best fix seen while the capture screen is open; a fix gets better for a minute after GPS wakes up. */
export class FixTracker {
  private watchId: number | null = null;
  private current: Fix | null = null;

  constructor(
    private readonly geo: Geolocation | undefined,
    private readonly onChange: (fix: Fix | null, error: string | null) => void,
  ) {}

  start(): void {
    if (!this.geo) {
      this.onChange(null, "This browser has no location service.");
      return;
    }
    if (this.watchId !== null) return;
    this.watchId = this.geo.watchPosition(
      (p) => this.offer({ lat: p.coords.latitude, lon: p.coords.longitude, accuracyM: p.coords.accuracy, at: p.timestamp }),
      (e) => this.onChange(this.current, e.code === e.PERMISSION_DENIED ? "Location permission is off." : "No location fix yet."),
      { enableHighAccuracy: true, maximumAge: 30_000, timeout: 60_000 },
    );
  }

  stop(): void {
    if (this.watchId !== null && this.geo) this.geo.clearWatch(this.watchId);
    this.watchId = null;
  }

  best(): Fix | null {
    return this.current;
  }

  offer(fix: Fix): void {
    const stale = this.current !== null && fix.at - this.current.at > 2 * 60 * 1000;
    if (this.current === null || stale || fix.accuracyM <= this.current.accuracyM) {
      this.current = fix;
      this.onChange(fix, null);
    }
  }
}

export function describe(loc: ResolvedLocation): string {
  if (loc.source === "none") return "No location: this report will not appear on the map";
  const acc = loc.accuracyM === null ? "" : ` ±${Math.round(loc.accuracyM)} m`;
  return loc.source === "exif" ? `Location from the photo${acc}` : `Phone location${acc}`;
}
