import type { Report } from "../src/types";

export function sampleReport(over: Partial<Report> = {}): Report {
  return {
    id: "0f8fad5b-d9cb-469f-a165-70867728950e",
    deviceId: "6b1e0b52-3f5a-4c55-9b0e-3a1d2c4e5f60",
    teamCode: "TEAM-2026-01",
    capturedAt: "2026-10-20T09:30:00.000Z",
    location: { lat: 50.0123, lon: 14.4011, accuracyM: 9, source: "device" },
    peopleVisible: 3,
    peopleInWater: 1,
    peopleElevated: 2,
    depthGuess: "waist",
    note: "boat needed",
    consent: true,
    appVersion: "0.1.0",
    ...over,
  };
}
