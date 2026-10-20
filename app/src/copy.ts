import type { QueueStatus } from "./types";

/** What the outbox says about a report. It names the real reason a report has not gone: no signal, a retry
 * pending, or a build with uploads switched off. */
export function statusLabel(status: QueueStatus, online: boolean, configured: boolean): string {
  if (status === "sent") return "Sent";
  if (status === "uploading") return "Sending…";
  if (status === "failed") return "Not accepted";
  if (!configured) return "Saved on this phone";
  return online ? "Waiting to send" : "Waiting for signal";
}

export function savedMessage(online: boolean, configured: boolean): string {
  if (!configured) return "Saved on this phone. Uploads are not set up in this build.";
  return online ? "Saved. Sending now." : "Saved. It will send when there is signal.";
}

export const TEAM_CODE_MIN = 8;

/** Inline guidance while the team code is typed, so the form never fails only on submit. */
export function teamCodeHint(code: string): string {
  const n = code.trim().length;
  if (n === 0) return "Your coordinator gives you this code.";
  if (n < TEAM_CODE_MIN) return `${TEAM_CODE_MIN - n} more character${TEAM_CODE_MIN - n === 1 ? "" : "s"}.`;
  return "Looks right.";
}

export const INVITE_LENGTH = 16;

/** Inline guidance while an invite is typed (case, spaces and dashes do not count). */
export function inviteHint(code: string, optional = false): string {
  const n = code.replace(/[^0-9A-Za-z]/g, "").length;
  if (n === 0) return optional ? "Only needed to move this phone to another team." : "One invite per phone, from your coordinator.";
  if (n < INVITE_LENGTH) return `${INVITE_LENGTH - n} more character${INVITE_LENGTH - n === 1 ? "" : "s"}.`;
  if (n > INVITE_LENGTH) return `That is ${n - INVITE_LENGTH} too many characters.`;
  return "Looks right.";
}

/** "Just now", "12 min ago", "3 h ago", then the date and time. */
export function capturedWhen(iso: string, now: number, locale?: string): string {
  const t = Date.parse(iso);
  const s = Math.round((now - t) / 1000);
  if (!Number.isFinite(s)) return "";
  if (s < 60) return "Just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 6 * 3600) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t).toLocaleString(locale, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function peopleSummary(n: number): string {
  return n === 0 ? "No people seen" : n === 1 ? "1 person" : `${n} people`;
}
