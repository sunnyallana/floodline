import { FLICK, SETTLE, Spring, project, rubberband, velocityOf } from "./motion";

/** Pointer travel before a drag commits to an axis. Below it, a press is still a tap or a scroll. */
export const HYSTERESIS_PX = 10;

/** Resting position after a release: open when the flick would carry the row past half the action. */
export function restingOffset(x: number, velocity: number, reveal: number): number {
  return x + project(velocity) < -reveal / 2 ? -reveal : 0;
}

/** The row's offset for a finger at ``raw`` px: 1:1 inside [-reveal, 0], rubber-banded beyond. */
export function trackedOffset(raw: number, reveal: number, width: number): number {
  if (raw > 0) return rubberband(raw, width);
  if (raw < -reveal) return -reveal + rubberband(raw + reveal, width);
  return raw;
}

let openRow: { close: () => void } | null = null;

/** Swipe left on ``content`` to reveal the action behind it (iOS list behaviour). The row tracks the finger
 * from where it was grabbed, can be caught mid-animation, and settles with the release velocity. Vertical
 * drags are left to the browser so the list still scrolls. */
export function attachSwipe(row: HTMLElement, content: HTMLElement, reveal: number): () => void {
  const spring = new Spring(0, (x) => {
    content.style.transform = x ? `translate3d(${x}px,0,0)` : "";
    row.classList.toggle("open", x < -1);
  });
  const close = (): void => spring.to(0, SETTLE);
  let id: number | null = null;
  let startX = 0;
  let startY = 0;
  let from = 0;
  let axis: "x" | "y" | null = null;
  let samples: { t: number; x: number }[] = [];

  const down = (e: PointerEvent): void => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button, a, input, textarea")) return;
    if (openRow && openRow.close !== close) openRow.close();
    id = e.pointerId;
    startX = e.clientX;
    startY = e.clientY;
    from = spring.value; // catch it where it is, mid-flight included
    spring.stop();
    axis = null;
    samples = [{ t: e.timeStamp, x: from }];
  };
  const move = (e: PointerEvent): void => {
    if (e.pointerId !== id) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (!axis) {
      if (Math.hypot(dx, dy) < HYSTERESIS_PX) return;
      axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      if (axis === "x") content.setPointerCapture(e.pointerId);
    }
    if (axis !== "x") return;
    const x = trackedOffset(from + dx, reveal, content.offsetWidth || 320);
    samples.push({ t: e.timeStamp, x });
    if (samples.length > 8) samples.shift();
    spring.set(x);
  };
  const up = (e: PointerEvent): void => {
    if (e.pointerId !== id) return;
    id = null;
    if (axis !== "x") {
      if (!axis && spring.value < -1) close(); // a tap on an open row closes it
      return;
    }
    const v = velocityOf(samples);
    const target = restingOffset(spring.value, v, reveal);
    spring.to(target, Math.abs(v) > 300 ? FLICK : SETTLE, v);
    openRow = target ? { close } : openRow?.close === close ? null : openRow;
  };

  content.addEventListener("pointerdown", down);
  content.addEventListener("pointermove", move);
  content.addEventListener("pointerup", up);
  content.addEventListener("pointercancel", up);
  return close;
}
