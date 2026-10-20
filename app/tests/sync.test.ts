import "fake-indexeddb/auto";
import { beforeEach, describe as group, expect, it, vi } from "vitest";
import { newItem } from "../src/report";
import { IdbQueue } from "../src/store";
import { BASE_DELAY_MS, MAX_DELAY_MS, Syncer, backoff, type Uploader } from "../src/sync";
import { UploadError } from "../src/upload";
import { sampleReport } from "./fixtures";

const cfg = { supabaseUrl: "https://abc.supabase.co", anonKey: "k" };
const T0 = Date.UTC(2026, 9, 20, 12);

async function freshQueue(): Promise<IdbQueue> {
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase("floodline-field");
    req.onsuccess = req.onerror = req.onblocked = () => resolve();
  });
  return IdbQueue.open();
}

function uploader(over: Partial<Uploader> = {}): Uploader & { photo: ReturnType<typeof vi.fn>; row: ReturnType<typeof vi.fn> } {
  const photo = vi.fn(async () => {});
  const row = vi.fn(async () => {});
  return { uploadPhoto: photo, insertReport: row, photo, row, ...over } as never;
}

let queue: IdbQueue | undefined;
beforeEach(async () => {
  queue?.close();
  queue = await freshQueue();
});

group("Syncer", () => {
  it("sends photo then row, marks the item sent and frees the photo", async () => {
    const report = sampleReport();
    await queue!.put(newItem(report, new Blob(["jpeg"]), new Blob(["t"]), T0));
    const up = uploader();
    const result = await new Syncer(queue!, cfg, up, () => T0).run();
    expect(result).toEqual({ sent: 1, retrying: 0, failed: 0 });
    expect(up.photo).toHaveBeenCalledTimes(1);
    expect(up.row).toHaveBeenCalledWith(cfg, report, 4);
    const item = await queue!.get(report.id);
    expect(item).toMatchObject({ status: "sent", photo: null, photoUploaded: true, lastError: null });
  });

  it("does not upload the photo again when only the row failed", async () => {
    const report = sampleReport();
    await queue!.put(newItem(report, new Blob(["jpeg"]), new Blob(["t"]), T0));
    let clock = T0;
    const up = uploader();
    up.row.mockRejectedValueOnce(new UploadError("Network error", true));
    const s = new Syncer(queue!, cfg, up, () => clock);
    expect(await s.run()).toEqual({ sent: 0, retrying: 1, failed: 0 });
    const waiting = await queue!.get(report.id);
    expect(waiting).toMatchObject({ status: "queued", photoUploaded: true, attempts: 1 });
    expect(waiting!.nextAttemptAt).toBeGreaterThan(T0);

    expect(await s.run()).toEqual({ sent: 0, retrying: 0, failed: 0 }); // not due yet
    clock = waiting!.nextAttemptAt;
    expect(await s.run()).toEqual({ sent: 1, retrying: 0, failed: 0 });
    expect(up.photo).toHaveBeenCalledTimes(1);
    expect(up.row).toHaveBeenCalledTimes(2);
  });

  it("stops retrying a permanent failure and keeps the reason for the user", async () => {
    const report = sampleReport();
    await queue!.put(newItem(report, new Blob(["jpeg"]), new Blob(["t"]), T0));
    const up = uploader();
    up.row.mockRejectedValue(new UploadError("The team code was not accepted.", false, 403));
    const s = new Syncer(queue!, cfg, up, () => T0 + 10 * MAX_DELAY_MS);
    expect(await s.run()).toEqual({ sent: 0, retrying: 0, failed: 1 });
    expect(await queue!.get(report.id)).toMatchObject({ status: "failed", lastError: "The team code was not accepted." });
    expect(await s.run(true)).toEqual({ sent: 0, retrying: 0, failed: 0 }); // failed items wait for "Try again"
  });

  it("shares one run between overlapping triggers", async () => {
    await queue!.put(newItem(sampleReport(), new Blob(["jpeg"]), new Blob(["t"]), T0));
    const up = uploader();
    const s = new Syncer(queue!, cfg, up, () => T0);
    const [a, b] = await Promise.all([s.run(), s.run()]);
    expect(a).toBe(b);
    expect(up.row).toHaveBeenCalledTimes(1);
  });

  it("sends the oldest report first", async () => {
    const older = sampleReport({ id: "00000000-0000-4000-8000-000000000001", capturedAt: "2026-10-20T08:00:00.000Z" });
    const newer = sampleReport({ id: "00000000-0000-4000-8000-000000000002", capturedAt: "2026-10-20T09:00:00.000Z" });
    await queue!.put(newItem(newer, new Blob(["a"]), new Blob(["t"]), T0));
    await queue!.put(newItem(older, new Blob(["a"]), new Blob(["t"]), T0));
    const up = uploader();
    await new Syncer(queue!, cfg, up, () => T0).run();
    expect(up.row.mock.calls.map((c) => (c[1] as { id: string }).id)).toEqual([older.id, newer.id]);
  });
});

group("backoff", () => {
  it("doubles from 15 s, stays within [ceiling/2, ceiling], and caps at 30 min", () => {
    expect(backoff(1, () => 1)).toBe(BASE_DELAY_MS);
    expect(backoff(1, () => 0)).toBe(BASE_DELAY_MS / 2);
    expect(backoff(3, () => 1)).toBe(4 * BASE_DELAY_MS);
    expect(backoff(40, () => 1)).toBe(MAX_DELAY_MS);
  });
});
