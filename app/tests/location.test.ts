import { describe as group, expect, it } from "vitest";
import { DEVICE_FIX_MAX_AGE_MS, type Fix, FixTracker, resolveLocation } from "../src/location";

const NOW = Date.UTC(2026, 9, 20, 12, 0, 0);
const fix = (over: Partial<Fix> = {}): Fix => ({ lat: 50.0, lon: 14.4, accuracyM: 12, at: NOW, ...over });
const noExif = { lat: null, lon: null, accuracyM: null, takenAt: NOW };

group("resolveLocation", () => {
  it("prefers GPS written into the photo", () => {
    const loc = resolveLocation({ lat: 49.9, lon: 14.3, accuracyM: 5, takenAt: NOW }, fix(), NOW);
    expect(loc).toEqual({ lat: 49.9, lon: 14.3, accuracyM: 5, source: "exif" });
  });

  it("uses the phone fix for a photo taken now", () => {
    expect(resolveLocation(noExif, fix({ accuracyM: 12.4 }), NOW)).toEqual({ lat: 50, lon: 14.4, accuracyM: 12, source: "device" });
  });

  it("never gives an old gallery photo the phone's current position", () => {
    const old = { ...noExif, takenAt: NOW - DEVICE_FIX_MAX_AGE_MS - 1 };
    expect(resolveLocation(old, fix(), NOW).source).toBe("none");
  });

  it("drops fixes that are too coarse or nonsensical", () => {
    expect(resolveLocation(noExif, fix({ accuracyM: 5000 }), NOW).source).toBe("none");
    expect(resolveLocation(noExif, fix({ lat: 0, lon: 0 }), NOW).source).toBe("none");
    expect(resolveLocation({ ...noExif, lat: 95, lon: 10 }, null, NOW).source).toBe("none");
  });

  it("returns no location when there is nothing to go on", () => {
    expect(resolveLocation(noExif, null, NOW)).toEqual({ lat: null, lon: null, accuracyM: null, source: "none" });
  });
});

group("FixTracker", () => {
  it("keeps the most accurate fix and replaces it once it is stale", () => {
    const seen: (number | undefined)[] = [];
    const t = new FixTracker(undefined, (f) => seen.push(f?.accuracyM));
    t.offer(fix({ accuracyM: 80 }));
    t.offer(fix({ accuracyM: 20, at: NOW + 1000 }));
    t.offer(fix({ accuracyM: 60, at: NOW + 2000 })); // worse and recent: ignored
    expect(t.best()?.accuracyM).toBe(20);
    t.offer(fix({ accuracyM: 60, at: NOW + 3 * 60_000 })); // worse but the old one is stale
    expect(t.best()?.accuracyM).toBe(60);
    expect(seen).toEqual([80, 20, 60]);
  });

  it("reports a missing location service", () => {
    let error: string | null = null;
    new FixTracker(undefined, (_f, e) => (error = e)).start();
    expect(error).toMatch(/no location service/);
  });
});
