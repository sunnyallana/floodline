import { type IDBPDatabase, openDB } from "idb";
import type { QueueItem } from "./types";

const DB_NAME = "floodline-field";
const STORE = "queue";

export interface Queue {
  put(item: QueueItem): Promise<void>;
  get(id: string): Promise<QueueItem | undefined>;
  all(): Promise<QueueItem[]>;
  remove(id: string): Promise<void>;
  patch(id: string, change: Partial<QueueItem>): Promise<QueueItem | undefined>;
}

/** The outbox. Photos and reports stay on the device until the server has both. */
export class IdbQueue implements Queue {
  private constructor(private readonly db: IDBPDatabase) {}

  static async open(): Promise<IdbQueue> {
    const db = await openDB(DB_NAME, 1, {
      upgrade(d) {
        const s = d.createObjectStore(STORE, { keyPath: "id" });
        s.createIndex("status", "status");
      },
    });
    return new IdbQueue(db);
  }

  close(): void {
    this.db.close();
  }

  async put(item: QueueItem): Promise<void> {
    await this.db.put(STORE, item);
  }

  async get(id: string): Promise<QueueItem | undefined> {
    return (await this.db.get(STORE, id)) as QueueItem | undefined;
  }

  async all(): Promise<QueueItem[]> {
    const items = (await this.db.getAll(STORE)) as QueueItem[];
    return items.sort((a, b) => b.report.capturedAt.localeCompare(a.report.capturedAt));
  }

  async remove(id: string): Promise<void> {
    await this.db.delete(STORE, id);
  }

  async patch(id: string, change: Partial<QueueItem>): Promise<QueueItem | undefined> {
    const tx = this.db.transaction(STORE, "readwrite");
    const current = (await tx.store.get(id)) as QueueItem | undefined;
    if (!current) {
      await tx.done;
      return undefined;
    }
    const next = { ...current, ...change };
    await tx.store.put(next);
    await tx.done;
    return next;
  }
}

/** Ask the browser not to evict the outbox under storage pressure (Safari otherwise may after 7 days). */
export async function requestPersistence(): Promise<boolean> {
  try {
    return (await navigator.storage?.persist?.()) ?? false;
  } catch {
    return false;
  }
}
