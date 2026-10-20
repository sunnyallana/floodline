import { describe as group, expect, it } from "vitest";
import { buildReport, clampCount, type Draft } from "../src/report";
import { fitWithin } from "../src/photo";

const draft = (over: Partial<Draft> = {}): Draft => ({
  deviceId: "dev-1",
  teamCode: "  TEAM-2026-01 ",
  capturedAt: Date.UTC(2026, 9, 20, 9, 30),
  location: { lat: 50, lon: 14.4, accuracyM: 9, source: "device" },
  peopleVisible: 1,
  peopleInWater: 2,
  peopleElevated: 1,
  depthGuess: "knee",
  note: "  x ".repeat(300),
  ...over,
});

group("buildReport", () => {
  it("trims, clamps and fixes counts so the database constraints always pass", () => {
    const r = buildReport(draft(), "id-1", "0.1.0");
    expect(r.teamCode).toBe("TEAM-2026-01");
    expect(r.capturedAt).toBe("2026-10-20T09:30:00.000Z");
    expect(r.peopleVisible).toBe(3); // at least in-water + elevated
    expect(r.note.length).toBeLessThanOrEqual(500);
    expect(r.consent).toBe(true);
  });

  it("refuses a team code the database would reject", () => {
    expect(() => buildReport(draft({ teamCode: "short" }), "id", "0.1.0")).toThrow(/8 to 64/);
  });

  it("clamps counts to what the column allows", () => {
    expect([clampCount(-3), clampCount(2.6), clampCount(5000), clampCount(Number.NaN)]).toEqual([0, 3, 999, 0]);
  });
});

group("fitWithin", () => {
  it("scales the long edge down and never up", () => {
    expect(fitWithin(4000, 3000, 1600)).toEqual({ width: 1600, height: 1200 });
    expect(fitWithin(3000, 4000, 1600)).toEqual({ width: 1200, height: 1600 });
    expect(fitWithin(800, 600, 1600)).toEqual({ width: 800, height: 600 });
  });
});
