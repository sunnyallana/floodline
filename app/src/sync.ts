import type { UploadConfig } from "./config";
import type { Queue } from "./store";
import type { QueueItem, Report } from "./types";
import { UploadError, insertReport, uploadPhoto } from "./upload";

export interface Uploader {
  uploadPhoto(cfg: UploadConfig, reportId: string, photo: Blob): Promise<void>;
  insertReport(cfg: UploadConfig, report: Report, photoBytes: number): Promise<void>;
}

export const defaultUploader: Uploader = {
  uploadPhoto: (cfg, id, photo) => uploadPhoto(cfg, id, photo),
  insertReport: (cfg, report, bytes) => insertReport(cfg, report, bytes),
};

export const BASE_DELAY_MS = 15_000;
export const MAX_DELAY_MS = 30 * 60_000;

/** Exponential backoff with full jitter: 15 s, 30 s, 1 min, ... capped at 30 min. */
export function backoff(attempts: number, random: () => number = Math.random): number {
  const ceiling = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** Math.max(0, attempts - 1));
  return Math.round(ceiling / 2 + random() * (ceiling / 2));
}

export interface SyncResult {
  sent: number;
  retrying: number;
  failed: number;
}

export function due(item: QueueItem, now: number): boolean {
  return (item.status === "queued" || item.status === "uploading") && item.nextAttemptAt <= now;
}

/**
 * Sends every due item: photo first, then the report row that references it. Each step is idempotent on the
 * server (the photo name and the row id are the report id), so a reply lost on a bad connection is safe to
 * retry. One run at a time per tab; overlapping triggers (online event, timer, manual) share the run.
 */
export class Syncer {
  private running: Promise<SyncResult> | null = null;

  constructor(
    private readonly queue: Queue,
    private readonly cfg: UploadConfig,
    private readonly uploader: Uploader = defaultUploader,
    private readonly clock: () => number = Date.now,
    private readonly onItem: (item: QueueItem) => void = () => {},
  ) {}

  run(force = false): Promise<SyncResult> {
    if (!this.running) {
      this.running = this.drain(force).finally(() => {
        this.running = null;
      });
    }
    return this.running;
  }

  private async drain(force: boolean): Promise<SyncResult> {
    const result: SyncResult = { sent: 0, retrying: 0, failed: 0 };
    const now = this.clock();
    const items = (await this.queue.all()).filter((i) => (force ? i.status !== "sent" && i.status !== "failed" : due(i, now)));
    for (const item of items.reverse()) {
      // oldest first: a coordinator wants the backlog in order
      const outcome = await this.sendOne(item);
      result[outcome] += 1;
    }
    return result;
  }

  private async sendOne(item: QueueItem): Promise<keyof SyncResult> {
    let current = (await this.queue.patch(item.id, { status: "uploading" })) ?? item;
    this.onItem(current);
    try {
      if (!current.photoUploaded) {
        if (!current.photo) throw new UploadError("The photo is missing from this device.", false);
        await this.uploader.uploadPhoto(this.cfg, current.id, current.photo);
        current = (await this.queue.patch(current.id, { photoUploaded: true })) ?? current;
      }
      await this.uploader.insertReport(this.cfg, current.report, current.photoBytes);
      current =
        (await this.queue.patch(current.id, {
          status: "sent",
          photo: null,
          sentAt: new Date(this.clock()).toISOString(),
          lastError: null,
        })) ?? current;
      this.onItem(current);
      return "sent";
    } catch (e) {
      const err = e instanceof UploadError ? e : new UploadError(String(e), true);
      const attempts = current.attempts + 1;
      const change: Partial<QueueItem> = err.retryable
        ? { status: "queued", attempts, nextAttemptAt: this.clock() + backoff(attempts), lastError: err.message }
        : { status: "failed", attempts, lastError: err.message };
      current = (await this.queue.patch(current.id, change)) ?? current;
      this.onItem(current);
      return err.retryable ? "retrying" : "failed";
    }
  }
}
