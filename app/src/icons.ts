import type { DepthClass } from "./types";

/** Line icons on a 24 px grid, drawn in currentColor. Decorative: every one sits next to a text label. */
const ICON = {
  camera:
    '<path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.6l1.4-2h5l1.4 2h1.6A2.5 2.5 0 0 1 20 8.5v8a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5z"/><circle cx="12" cy="12.5" r="3.6"/>',
  photo:
    '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l4.5-4.5 3.5 3.5 3-3 5 5"/>',
  tray: '<path d="M4 13.5V17a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3.5"/><path d="M4 13.5l2.2-7A2 2 0 0 1 8.1 5h7.8a2 2 0 0 1 1.9 1.5l2.2 7H15a3 3 0 0 1-6 0z"/>',
  // Cog after Lucide's "settings" (ISC licence).
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.5v.2"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
  up: '<path d="M12 19V6M6.5 11.5 12 6l5.5 5.5"/>',
  pin: '<path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
  shield: '<path d="M12 3.5l7 3v5.3c0 4.3-3 7.6-7 8.7-4-1.1-7-4.4-7-8.7V6.5z"/>',
  ruler: '<path d="M4 16.5 16.5 4 20 7.5 7.5 20z"/><path d="M8 12.5l1.8 1.8M10.5 10l1.8 1.8M13 7.5l1.8 1.8"/>',
  wifiOff: '<path d="M3 3l18 18M8.5 16a5 5 0 0 1 6.2-.6M5 12.5a10 10 0 0 1 4-2.3M19 12.5a10 10 0 0 0-5.3-2.6M12 19.5v.2"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
} as const;

export type IconName = keyof typeof ICON;

export function icon(name: IconName, cls = "ico"): string {
  return `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON[name]}</svg>`;
}

/** Top of the water on a 51-unit-tall standing adult (ground at y = 54), at the middle of each class's range in
 * DEPTH_LABELS. ``null`` draws no water. */
const WATER_Y: Record<DepthClass, number | null> = {
  unknown: null,
  dry: null,
  ankle: 51,
  knee: 44,
  waist: 32,
  chest: 16,
  above_head: 0,
};

const PERSON =
  '<circle cx="20" cy="8" r="4.6"/><path d="M14.5 15.5h11a2 2 0 0 1 2 2V31h-3v21h-3.6V36h-1.8v16h-3.6V31h-3V17.5a2 2 0 0 1 2-2z"/>';

/** A person with the water at the class's level, so a depth reads at a glance without reading the label. */
export function depthPictogram(d: DepthClass): string {
  const y = WATER_Y[d];
  const water =
    y === null
      ? ""
      : `<path class="water" d="M0 ${y + 1.5} q5 -3 10 0 t10 0 t10 0 t10 0 V56 H0z"/>`;
  const mark = d === "unknown" ? '<text x="31" y="15" class="q">?</text>' : "";
  return `<svg class="pict" viewBox="0 0 40 56" aria-hidden="true" focusable="false">
    <g class="person${d === "unknown" ? " faint" : ""}">${PERSON}</g>${water}<path class="ground" d="M2 54.5h36"/>${mark}</svg>`;
}
