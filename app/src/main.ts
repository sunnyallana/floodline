import "./styles.css";
import { registerSW } from "virtual:pwa-register";
import { APP_VERSION, uploadConfig, uploadsConfigured } from "./config";
import { type Fix, FixTracker, describe, resolveLocation } from "./location";
import { preparePhoto, type PreparedPhoto } from "./photo";
import { buildReport, clampCount, newItem } from "./report";
import { IdbQueue, requestPersistence } from "./store";
import { Syncer } from "./sync";
import { DEPTH_CLASSES, DEPTH_LABELS, NOTE_MAX, type DepthClass, type QueueItem } from "./types";

const SETTINGS_KEY = "floodline.field.settings";
const RETRY_INTERVAL_MS = 60_000;

interface Settings {
  teamCode: string;
  consentAt: string;
}

const $ = <T extends HTMLElement>(sel: string, root: ParentNode = document): T => {
  const el = root.querySelector<T>(sel);
  if (!el) throw new Error(`missing ${sel}`);
  return el;
};

const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);

function loadSettings(): Settings | null {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    const s = raw ? (JSON.parse(raw) as Partial<Settings>) : null;
    return s && typeof s.teamCode === "string" && typeof s.consentAt === "string" ? (s as Settings) : null;
  } catch {
    return null;
  }
}

function saveSettings(s: Settings): void {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
}

let toastTimer: number | undefined;
function toast(message: string): void {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (el.hidden = true), 4000);
}

const thumbUrls = new Map<string, string>();
function thumbUrl(item: QueueItem): string {
  let url = thumbUrls.get(item.id);
  if (!url) {
    url = URL.createObjectURL(item.thumb);
    thumbUrls.set(item.id, url);
  }
  return url;
}

async function main(): Promise<void> {
  registerSW({ immediate: true });
  void requestPersistence();
  const queue = await IdbQueue.open();
  const view = $("#view");

  const refreshBar = async (): Promise<void> => {
    const net = $("#net");
    net.textContent = navigator.onLine ? "Online" : "Offline";
    net.className = `chip ${navigator.onLine ? "online" : "offline"}`;
    const pending = (await queue.all()).filter((i) => i.status !== "sent").length;
    $("#pending").textContent = String(pending);
  };

  const syncer = new Syncer(queue, uploadConfig, undefined, Date.now, () => {
    void refreshBar();
    if (location.hash === "#outbox") void renderOutbox();
  });
  const sync = async (force = false): Promise<void> => {
    if (!uploadsConfigured() || !navigator.onLine) return;
    const r = await syncer.run(force);
    if (r.sent) toast(r.sent === 1 ? "1 report sent." : `${r.sent} reports sent.`);
  };

  // ---------------------------------------------------------------- setup
  const renderSetup = (): void => {
    const s = loadSettings();
    view.innerHTML = `
      <h1>Set up this phone</h1>
      <p class="muted">Your coordinator gives you a team code. Reports go only to that team.</p>
      <form id="setup" class="stack" novalidate>
        <label class="field">Team code
          <input id="team" type="text" autocomplete="off" autocapitalize="none" spellcheck="false" minlength="8" maxlength="64"
            required value="${esc(s?.teamCode ?? "")}" />
        </label>
        <div class="card stack">
          <strong>What is shared</strong>
          <span class="muted">The photo, the time, where it was taken, and what you tag. Only your coordination
          team sees photos. The public map shows counts per area, never a photo or an exact point.</span>
          <span class="muted">Avoid faces and number plates when you can. Never put yourself at risk for a photo.</span>
          <label class="check"><input id="consent" type="checkbox" ${s ? "checked" : ""} required />
            <span>I understand, and the people in my photos are not identifiable without need.</span></label>
        </div>
        <p id="setup-error" class="muted" role="alert"></p>
        <button class="primary big" type="submit">Save</button>
      </form>
      ${uploadsConfigured() ? "" : `<p class="card notice muted">Uploads are not configured in this build. Reports are kept on the phone.</p>`}
      <p class="muted">Version ${esc(APP_VERSION)}</p>`;
    $("#setup").addEventListener("submit", (e) => {
      e.preventDefault();
      const code = $<HTMLInputElement>("#team").value.trim();
      const err = $("#setup-error");
      if (code.length < 8) return void (err.textContent = "The team code has at least 8 characters.");
      if (!$<HTMLInputElement>("#consent").checked) return void (err.textContent = "Please confirm before continuing.");
      saveSettings({ teamCode: code, consentAt: new Date().toISOString() });
      location.hash = "#capture";
    });
  };

  // ---------------------------------------------------------------- capture
  let tracker: FixTracker | null = null;
  const renderCapture = (): void => {
    const settings = loadSettings();
    if (!settings) return void (location.hash = "#setup");
    let prepared: PreparedPhoto | null = null;
    let previewUrl: string | null = null;
    let fix: Fix | null = null;
    let fixError: string | null = null;
    const counts = { peopleVisible: 0, peopleInWater: 0, peopleElevated: 0 };

    view.innerHTML = `
      <section id="pick" class="stack">
        <h1>New report</h1>
        <p class="muted">Take the photo where you stand. Include something of known height in the water if you
        can: a person, a car, a door.</p>
        <label class="button primary big" for="camera">Take photo</label>
        <input id="camera" class="visually-hidden" type="file" accept="image/*" capture="environment" />
        <label class="button big" for="gallery">Choose from gallery</label>
        <input id="gallery" class="visually-hidden" type="file" accept="image/jpeg,image/png,image/heic,image/heif,image/webp" />
        <div class="loc"><span id="fix-dot" class="dot"></span><span id="fix-text" class="muted">Finding location…</span></div>
      </section>
      <form id="details" class="stack" hidden>
        <img id="preview" class="preview" alt="The photo you just took" />
        <div class="loc"><span id="loc-dot" class="dot"></span><span id="loc-text"></span></div>
        <h2>People</h2>
        <div class="card">
          ${stepper("peopleVisible", "Visible in the photo")}
          ${stepper("peopleInWater", "In the water")}
          ${stepper("peopleElevated", "On roofs or trapped up high")}
        </div>
        <h2>Water depth</h2>
        <fieldset class="depths" style="border:0;padding:0;margin:0">
          <legend class="visually-hidden">Water depth</legend>
          ${DEPTH_CLASSES.map(
            (d) => `<label><input type="radio" name="depth" value="${d}" ${d === "unknown" ? "checked" : ""} />
              <span><b>${DEPTH_LABELS[d].label}</b><small>${DEPTH_LABELS[d].hint}</small></span></label>`,
          ).join("")}
        </fieldset>
        <label class="field">Note <span class="muted" style="font-weight:400">(optional)</span>
          <textarea id="note" maxlength="${NOTE_MAX}" placeholder="Road blocked, current strong, boat needed…"></textarea>
        </label>
        <div class="row">
          <button id="discard" type="button">Discard</button>
          <button class="primary" type="submit">Save report</button>
        </div>
      </form>`;

    const showFix = (): void => {
      const dot = $("#fix-dot");
      const text = $("#fix-text");
      if (fix) {
        dot.className = `dot ${fix.accuracyM <= 50 ? "good" : "fair"}`;
        text.textContent = `Location ready, ±${Math.round(fix.accuracyM)} m`;
      } else {
        dot.className = `dot ${fixError ? "none" : ""}`;
        text.textContent = fixError ?? "Finding location…";
      }
    };
    tracker?.stop();
    tracker = new FixTracker(navigator.geolocation, (f, e) => {
      fix = f;
      fixError = e;
      if (document.contains($("#fix-text"))) showFix();
    });
    tracker.start();

    const onFile = async (input: HTMLInputElement): Promise<void> => {
      const file = input.files?.[0];
      input.value = "";
      if (!file) return;
      try {
        prepared = await preparePhoto(file);
      } catch {
        toast("That file could not be read as a photo.");
        return;
      }
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(prepared.photo);
      $<HTMLImageElement>("#preview").src = previewUrl;
      const loc = resolveLocation(prepared.meta, tracker?.best() ?? null, Date.now());
      $("#loc-dot").className = `dot ${loc.source === "none" ? "none" : (loc.accuracyM ?? 0) <= 50 ? "good" : "fair"}`;
      $("#loc-text").textContent = describe(loc);
      $("#pick").hidden = true;
      $("#details").hidden = false;
      $("#details").scrollIntoView({ block: "start" });
    };
    $<HTMLInputElement>("#camera").addEventListener("change", (e) => void onFile(e.target as HTMLInputElement));
    $<HTMLInputElement>("#gallery").addEventListener("change", (e) => void onFile(e.target as HTMLInputElement));

    view.querySelectorAll<HTMLButtonElement>("[data-step]").forEach((b) =>
      b.addEventListener("click", () => {
        const key = b.dataset.key as keyof typeof counts;
        counts[key] = clampCount(counts[key] + Number(b.dataset.step));
        // Everyone in the water or up high is also visible; keep the total in step so what is shown is what is sent.
        const floor = counts.peopleInWater + counts.peopleElevated;
        counts.peopleVisible = Math.max(counts.peopleVisible, floor);
        for (const k of Object.keys(counts) as (keyof typeof counts)[]) $(`#out-${k}`).textContent = String(counts[k]);
      }),
    );
    $("#discard").addEventListener("click", () => renderCapture());
    $("#details").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!prepared) return;
      const depth = (view.querySelector<HTMLInputElement>('input[name="depth"]:checked')?.value ?? "unknown") as DepthClass;
      const now = Date.now();
      const report = buildReport(
        {
          teamCode: settings.teamCode,
          capturedAt: prepared.meta.takenAt ?? now,
          location: resolveLocation(prepared.meta, tracker?.best() ?? null, now),
          ...counts,
          depthGuess: depth,
          note: $<HTMLTextAreaElement>("#note").value,
        },
        crypto.randomUUID(),
        APP_VERSION,
      );
      await queue.put(newItem(report, prepared.photo, prepared.thumb, now));
      await refreshBar();
      toast(navigator.onLine && uploadsConfigured() ? "Saved. Sending now." : "Saved. It will send when there is signal.");
      renderCapture();
      void sync();
    });
  };

  // ---------------------------------------------------------------- outbox
  const renderOutbox = async (): Promise<void> => {
    const items = await queue.all();
    const label: Record<QueueItem["status"], string> = {
      queued: "Waiting for signal",
      uploading: "Sending…",
      sent: "Sent",
      failed: "Not accepted",
    };
    view.innerHTML = `
      <h1>Outbox</h1>
      <div class="row" style="margin-bottom:12px">
        <button id="send-now" class="primary" ${uploadsConfigured() ? "" : "disabled"}>Send now</button>
        <a class="button" href="#setup">Settings</a>
      </div>
      ${items.length === 0 ? `<p class="muted">No reports yet.</p>` : ""}
      ${items
        .map(
          (i) => `<div class="card item" data-id="${esc(i.id)}">
            <img src="${thumbUrl(i)}" alt="" />
            <div>
              <div class="status ${i.status}">${label[i.status]}</div>
              <div class="muted">${esc(new Date(i.report.capturedAt).toLocaleString())} · ${esc(describe(i.report.location))}</div>
              <div class="muted">${DEPTH_LABELS[i.report.depthGuess].label} · ${i.report.peopleVisible === 1 ? "1 person" : `${i.report.peopleVisible} people`}${
                i.report.note ? ` · ${esc(i.report.note.slice(0, 80))}` : ""
              }</div>
              ${i.lastError && i.status !== "sent" ? `<div class="muted">${esc(i.lastError)}</div>` : ""}
              <div class="actions">
                ${i.status === "failed" ? `<button data-act="retry">Try again</button>` : ""}
                ${i.status !== "uploading" ? `<button data-act="delete">${i.status === "sent" ? "Remove" : "Delete"}</button>` : ""}
              </div>
            </div>
          </div>`,
        )
        .join("")}`;
    $("#send-now").addEventListener("click", () => void sync(true));
    view.querySelectorAll<HTMLButtonElement>("[data-act]").forEach((b) =>
      b.addEventListener("click", async () => {
        const id = b.closest<HTMLElement>("[data-id]")?.dataset.id;
        if (!id) return;
        if (b.dataset.act === "retry") {
          await queue.patch(id, { status: "queued", nextAttemptAt: 0, lastError: null });
          void sync(true);
        } else {
          const item = await queue.get(id);
          if (item && item.status !== "sent" && !confirm("Delete this report? It has not been sent.")) return;
          await queue.remove(id);
          const url = thumbUrls.get(id);
          if (url) URL.revokeObjectURL(url);
          thumbUrls.delete(id);
        }
        await refreshBar();
        await renderOutbox();
      }),
    );
  };

  const route = (): void => {
    if (location.hash !== "#capture") tracker?.stop();
    const target = location.hash || "#capture";
    if (target === "#setup") renderSetup();
    else if (target === "#outbox") void renderOutbox();
    else renderCapture();
    view.focus({ preventScroll: true });
  };

  window.addEventListener("hashchange", route);
  window.addEventListener("online", () => void refreshBar().then(() => sync()));
  window.addEventListener("offline", () => void refreshBar());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void sync();
  });
  window.setInterval(() => {
    if (document.visibilityState === "visible") void sync();
  }, RETRY_INTERVAL_MS);

  if (!loadSettings()) location.hash = "#setup";
  route();
  await refreshBar();
  void sync();
}

function stepper(key: string, label: string): string {
  return `<div class="stepper"><span>${label}</span>
    <button type="button" data-key="${key}" data-step="-1" aria-label="${label}: one fewer">−</button>
    <output id="out-${key}" aria-live="polite">0</output>
    <button type="button" data-key="${key}" data-step="1" aria-label="${label}: one more">+</button></div>`;
}

void main();
