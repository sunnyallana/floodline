import { describe, expect, it } from "vitest";
import { capturedWhen, savedMessage, statusLabel, teamCodeHint } from "../src/copy";
import { FLICK, SETTLE, Spring, project, rubberband, settled, stepSpring, velocityOf } from "../src/motion";
import { restingOffset, trackedOffset } from "../src/swipe";

describe("springs", () => {
  it("a critically damped spring reaches the target without overshoot", () => {
    let s = { value: 0, velocity: 0 };
    let max = 0;
    for (let i = 0; i < 120; i++) {
      s = stepSpring(s, 100, SETTLE, 1 / 60);
      max = Math.max(max, s.value);
    }
    expect(max).toBeLessThanOrEqual(100.0001);
    expect(settled(s, 100)).toBe(true);
  });

  it("an underdamped spring overshoots, which is kept for flicks", () => {
    let s = { value: 0, velocity: 0 };
    let max = 0;
    for (let i = 0; i < 120; i++) {
      s = stepSpring(s, 100, FLICK, 1 / 60);
      max = Math.max(max, s.value);
    }
    expect(max).toBeGreaterThan(100.5);
  });

  it("is frame-rate independent", () => {
    let a = { value: 0, velocity: 0 };
    let b = { value: 0, velocity: 0 };
    for (let i = 0; i < 30; i++) a = stepSpring(a, 50, SETTLE, 1 / 60);
    for (let i = 0; i < 60; i++) b = stepSpring(b, 50, SETTLE, 1 / 120);
    expect(a.value).toBeCloseTo(b.value, 1);
  });

  it("a retarget starts from the current value and keeps its velocity", () => {
    const frames: ((t: number) => void)[] = [];
    const seen: number[] = [];
    const s = new Spring(0, (v) => seen.push(v), (cb) => frames.push(cb), () => {});
    s.to(-88, SETTLE, -1500);
    let t = 0;
    for (let i = 0; i < 5; i++) frames.shift()!((t += 16));
    const mid = s.state;
    expect(mid.value).toBeLessThan(0);
    s.to(0, SETTLE); // grabbed and sent back mid-flight
    frames.shift()!((t += 16));
    expect(Math.abs(s.value - mid.value)).toBeLessThan(30); // no jump to the old target or to 0
    expect(seen.every(Number.isFinite)).toBe(true);
  });
});

describe("gesture math", () => {
  it("projects a flick like a scroll view", () => {
    expect(project(1000)).toBeCloseTo(499, 0);
    expect(project(-500)).toBeCloseTo(-249.5, 0);
    expect(project(0)).toBe(0);
  });

  it("rubber-bands past a boundary, with less follow the further it goes", () => {
    const a = rubberband(50, 320);
    const b = rubberband(200, 320) - rubberband(150, 320);
    expect(a).toBeGreaterThan(0);
    expect(a).toBeLessThan(50);
    expect(b).toBeLessThan(a);
    expect(rubberband(-50, 320)).toBeCloseTo(-a);
  });

  it("measures velocity over the recent window only", () => {
    const samples = [
      { t: 0, x: 0 },
      { t: 300, x: 0 },
      { t: 350, x: -40 },
      { t: 400, x: -80 },
    ];
    expect(velocityOf(samples)).toBeCloseTo(-800, 0);
    expect(velocityOf([{ t: 0, x: 0 }])).toBe(0);
  });

  it("a row tracks the finger inside the action and resists beyond it", () => {
    expect(trackedOffset(-40, 88, 320)).toBe(-40);
    expect(trackedOffset(-140, 88, 320)).toBeGreaterThan(-140);
    expect(trackedOffset(-140, 88, 320)).toBeLessThan(-88);
    expect(trackedOffset(30, 88, 320)).toBeLessThan(30);
  });

  it("opens on a flick even from a short drag, and closes on a slow short one", () => {
    expect(restingOffset(-20, -600, 88)).toBe(-88);
    expect(restingOffset(-30, 0, 88)).toBe(0);
    expect(restingOffset(-70, 0, 88)).toBe(-88);
    expect(restingOffset(-80, 900, 88)).toBe(0); // flicked back
  });
});

describe("copy", () => {
  it("names the real reason a report has not gone", () => {
    expect(statusLabel("queued", false, true)).toBe("Waiting for signal");
    expect(statusLabel("queued", true, true)).toBe("Waiting to send");
    expect(statusLabel("queued", true, false)).toBe("Saved on this phone");
    expect(statusLabel("failed", true, true)).toBe("Not accepted");
    expect(savedMessage(false, true)).toContain("when there is signal");
    expect(savedMessage(true, false)).toContain("not set up");
  });

  it("guides the team code while it is typed", () => {
    expect(teamCodeHint("")).toContain("coordinator");
    expect(teamCodeHint("TEAM-2")).toBe("2 more characters.");
    expect(teamCodeHint("TEAM-20")).toBe("1 more character.");
    expect(teamCodeHint("TEAM-2026")).toBe("Looks right.");
  });

  it("says when a report was captured in plain words", () => {
    const now = Date.parse("2026-10-20T12:00:00Z");
    expect(capturedWhen("2026-10-20T11:59:30Z", now)).toBe("Just now");
    expect(capturedWhen("2026-10-20T11:48:00Z", now)).toBe("12 min ago");
    expect(capturedWhen("2026-10-20T09:00:00Z", now)).toBe("3 h ago");
  });
});
