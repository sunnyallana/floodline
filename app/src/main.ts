import "./styles.css";
import { registerSW } from "virtual:pwa-register";
import { APP_VERSION, uploadConfig, uploadsConfigured } from "./config";
import { DeviceAuth, formatInvite, normaliseInvite } from "./auth";
import { capturedWhen, INVITE_LENGTH, inviteHint, peopleSummary, savedMessage, statusLabel, TEAM_CODE_MIN, teamCodeHint } from "./copy";
import { depthPictogram, icon, type IconName } from "./icons";
import { type Fix, FixTracker, describe, resolveLocation } from "./location";
import { haptic, prefersReducedMotion } from "./motion";
import { preparePhoto, type PreparedPhoto } from "./photo";
import { buildReport, clampCount, newItem } from "./report";
import { IdbQueue, requestPersistence } from "./store";
import { attachSwipe } from "./swipe";
import { Syncer } from "./sync";
import { UploadError } from "./upload";
import { DEPTH_CLASSES, DEPTH_LABELS, NOTE_MAX, type DepthClass, type QueueItem } from "./types";

const SETTINGS_KEY = "floodline.field.settings";
const DEVICE_KEY = "floodline.field.device";
const RETRY_INTERVAL_MS = 60_000;
const SWIPE_REVEAL_PX = 88;

interface Settings {
  teamCode: string;
  consentAt: string;
  /** Set once the phone has redeemed an invite; absent in builds without uploads. */
  teamName?: string;
  deviceId?: string;
}

type Tab = "capture" | "outbox" | "setup";

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

/** A random id created once per install. It is not tied to the person or the phone's hardware. */
function deviceId(): string {
  let id = localStorage.getItem(DEVICE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(DEVICE_KEY, id);
  }
  return id;
}

function saveSettings(s: Settings): void {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
}

// ---------------------------------------------------------------- feedback
let toastTimer: number | undefined;
function toast(message: string, kind: "success" | "error" | "info" = "info"): void {
  const el = $("#toast");
  const glyph: Record<typeof kind, IconName | null> = { success: "check", error: "alert", info: null };
  const g = glyph[kind];
  el.innerHTML = `${g ? icon(g) : ""}<span></span>`;
  $("span", el).textContent = message;
  el.dataset.kind = kind;
  el.hidden = false;
  // Visual, sound-free haptic and the announcement land on the same frame.
  requestAnimationFrame(() => {
    el.classList.add("in");
    if (kind !== "info") haptic(kind);
  });
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    el.classList.remove("in");
    // Leaves along the path it came in on; hidden only if no newer toast arrived meanwhile.
    window.setTimeout(() => {
      if (!el.classList.contains("in")) el.hidden = true;
    }, prefersReducedMotion() ? 0 : 260);
  }, 4000);
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

/** The compact title in the bar appears once the large title has scrolled under it, as on iOS. */
let titleObserver: IntersectionObserver | null = null;
function largeTitle(view: HTMLElement): void {
  titleObserver?.disconnect();
  const bar = $("#bar");
  const h1 = [...view.querySelectorAll<HTMLElement>("h1")].find((h) => !h.closest("[hidden]"));
  $("#bar-title").textContent = h1?.textContent ?? "";
  bar.classList.remove("titled");
  if (!h1 || typeof IntersectionObserver === "undefined") return;
  titleObserver = new IntersectionObserver(([e]) => bar.classList.toggle("titled", !e!.isIntersecting), {
    rootMargin: `-${bar.offsetHeight}px 0px 0px 0px`,
  });
  titleObserver.observe(h1);
}

async function main(): Promise<void> {
  registerSW({ immediate: true });
  void requestPersistence();
  // iOS only applies :active on elements when a touch listener exists; press feedback must start on touch-down.
  document.addEventListener("touchstart", () => {}, { passive: true });
  for (const [id, name] of [["tab-capture", "camera"], ["tab-outbox", "tray"], ["tab-setup", "gear"]] as const)
    $(`#${id}`).innerHTML = icon(name);
  window.addEventListener("scroll", () => $("#bar").classList.toggle("scrolled", window.scrollY > 2), { passive: true });

  const queue = await IdbQueue.open();
  const view = $("#view");

  const refreshBar = async (): Promise<void> => {
    const net = $("#net");
    net.innerHTML = navigator.onLine ? `<i class="dot good"></i>Online` : `${icon("wifiOff")}Offline`;
    net.className = `net ${navigator.onLine ? "online" : "offline"}`;
    const pending = (await queue.all()).filter((i) => i.status !== "sent").length;
    const badge = $("#pending");
    if (badge.textContent !== String(pending)) {
      badge.textContent = String(pending);
      badge.classList.remove("bump");
      void badge.offsetWidth;
      badge.classList.add("bump");
    }
    badge.dataset.zero = String(pending === 0);
  };

  // Every upload carries this phone's own signed identity; the anon key alone can write nothing.
  const auth = uploadsConfigured() ? new DeviceAuth(uploadConfig) : null;
  const cfg = auth ? { ...uploadConfig, accessToken: () => auth.accessToken(), expireToken: () => auth.expire() } : uploadConfig;
  let pendingInvite = "";
  const syncer = new Syncer(queue, cfg, undefined, Date.now, () => {
    void refreshBar();
    if (location.hash === "#outbox") void renderOutbox();
  });
  const sync = async (force = false): Promise<void> => {
    if (!uploadsConfigured() || !navigator.onLine) return;
    const r = await syncer.run(force);
    if (r.sent) toast(r.sent === 1 ? "1 report sent." : `${r.sent} reports sent.`, "success");
  };

  const setTab = (tab: Tab): void => {
    document.querySelectorAll<HTMLAnchorElement>("#tabs a").forEach((a) => {
      if (a.dataset.tab === tab) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  };

  /** New content fades in without blocking input; tabs are peers, so there is no spatial slide. */
  const show = (html: string): void => {
    view.innerHTML = html;
    view.classList.remove("enter");
    void view.offsetWidth;
    view.classList.add("enter");
  };

  // ---------------------------------------------------------------- setup
  const renderSetup = (): void => {
    const s = loadSettings();
    const enrolled = Boolean(s?.teamName);
    document.body.classList.remove("composing");
    setTab("setup");
    // Builds without uploads keep reports on the phone; there is nothing to enrol with, so a local code is enough.
    const local = !auth;
    const invite = pendingInvite;
    pendingInvite = "";
    show(`
      <h1>${s ? "Settings" : "Set up this phone"}</h1>
      <p class="lede">${
        local
          ? "Uploads are not set up in this build, so reports stay on this phone."
          : "Your coordinator gives each phone its own invite. It works once, on one phone, and needs signal once."
      }</p>
      ${
        enrolled
          ? `<div class="group enrolled">${icon("shield")}<div><b>Enrolled with ${esc(s!.teamName!)}</b>
              <span class="muted">This phone ${esc((s!.deviceId ?? "").slice(0, 8))} · since ${esc(new Date(s!.consentAt).toLocaleDateString())}</span></div></div>`
          : ""
      }
      <form id="setup" novalidate>
        ${enrolled ? `<h2 class="section">Enrol again with a new invite</h2>` : ""}
        <div class="group">
          <label class="row-field">
            <span>${local ? "Team code" : "Invite code"}</span>
            <input id="team" type="text" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false"
              maxlength="64" ${enrolled ? "" : "required"} value="${esc(local ? (s?.teamCode ?? "") : invite ? formatInvite(invite) : "")}"
              aria-describedby="team-hint" placeholder="${enrolled ? "Optional" : local ? "Required" : "XXXX-XXXX-XXXX-XXXX"}" />
          </label>
        </div>
        <p id="team-hint" class="hint"></p>

        <h2 class="section">What is shared</h2>
        <ul class="group list">
          <li>${icon("photo")}<span>The photo, the time, where it was taken and what you tag.</span></li>
          <li>${icon("shield")}<span>Only your coordination team sees photos. The public map shows counts per area,
            never a photo or an exact point.</span></li>
          <li>${icon("eye")}<span>Avoid faces and number plates when you can. Never put yourself at risk for a photo.</span></li>
        </ul>
        <label class="group consent"><input id="consent" type="checkbox" ${s ? "checked" : ""} required />
          <span>I understand, and the people in my photos are not identifiable without need.</span></label>
        <p id="setup-error" class="error" role="alert"></p>
        <button id="setup-save" class="primary big" type="submit">${local || enrolled ? "Save" : "Enrol this phone"}</button>
      </form>
      <p class="footnote">Floodline Field ${esc(APP_VERSION)} · works offline</p>`);
    const team = $<HTMLInputElement>("#team");
    const hint = $("#team-hint");
    const err = $("#setup-error");
    const update = (): void => {
      hint.textContent = local ? teamCodeHint(team.value) : inviteHint(team.value, enrolled);
      hint.classList.toggle("good", hint.textContent === "Looks right.");
    };
    team.addEventListener("input", () => {
      update();
      err.textContent = "";
    });
    update();
    const fail = (message: string): void => {
      err.textContent = message;
      haptic("error");
    };
    $("#setup").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!$<HTMLInputElement>("#consent").checked) return fail("Please confirm before continuing.");
      const consentAt = new Date().toISOString();
      if (local) {
        const code = team.value.trim();
        if (code.length < TEAM_CODE_MIN) return fail(`The team code has at least ${TEAM_CODE_MIN} characters.`);
        saveSettings({ teamCode: code, consentAt });
        if (s) toast("Settings saved.", "success");
        location.hash = "#capture";
        return;
      }
      const code = normaliseInvite(team.value);
      if (!code && enrolled) {
        saveSettings({ ...s!, consentAt });
        toast("Settings saved.", "success");
        location.hash = "#capture";
        return;
      }
      if (code.length !== INVITE_LENGTH) return fail(`An invite has ${INVITE_LENGTH} letters and digits.`);
      if (!navigator.onLine) return fail("Enrolling needs signal once. Try again where there is signal.");
      const button = $<HTMLButtonElement>("#setup-save");
      button.disabled = true;
      button.textContent = "Enrolling…";
      try {
        const r = await auth!.enroll(code);
        saveSettings({ teamCode: r.teamCode, teamName: r.teamName, deviceId: r.deviceId, consentAt });
        toast(`Enrolled with ${r.teamName}.`, "success");
        location.hash = "#capture";
        void sync(true); // anything queued before (re-)enrolment can go now
      } catch (x) {
        button.disabled = false;
        button.textContent = enrolled ? "Save" : "Enrol this phone";
        fail(x instanceof UploadError ? x.message : "Enrolment failed. Try again.");
      }
    });
    largeTitle(view);
  };

  // ---------------------------------------------------------------- capture
  let tracker: FixTracker | null = null;
  const renderCapture = (): void => {
    const settings = loadSettings();
    if (!settings) return void (location.hash = "#setup");
    document.body.classList.remove("composing");
    setTab("capture");
    let prepared: PreparedPhoto | null = null;
    let previewUrl: string | null = null;
    let fix: Fix | null = null;
    let fixError: string | null = null;
    const counts = { peopleVisible: 0, peopleInWater: 0, peopleElevated: 0 };

    show(`
      <section id="pick">
        <h1>New report</h1>
        <p class="lede">Take the photo where you stand.</p>
        <label class="shutter" for="camera">${icon("camera", "ico big")}<span>Take photo</span></label>
        <input id="camera" class="visually-hidden" type="file" accept="image/*" capture="environment" />
        <label class="button secondary big" for="gallery">${icon("photo")}Choose from gallery</label>
        <input id="gallery" class="visually-hidden" type="file" accept="image/jpeg,image/png,image/heic,image/heif,image/webp" />
        <p class="locpill"><span id="fix-dot" class="dot pulse"></span><span id="fix-text">Finding location…</span></p>
        <h2 class="section">A good report</h2>
        <ul class="group list">
          <li>${icon("ruler")}<span>Show something of known height in the water: a person, a car, a door.</span></li>
          <li>${icon("shield")}<span>Stay out of the water. No photo is worth the risk.</span></li>
          <li>${icon("wifiOff")}<span>No signal? The report waits on this phone and sends itself later.</span></li>
        </ul>
      </section>
      <form id="details" hidden>
        <h1>Report details</h1>
        <figure class="photo">
          <img id="preview" alt="The photo you just took" />
          <figcaption class="locchip"><span id="loc-dot" class="dot"></span><span id="loc-text"></span></figcaption>
        </figure>
        <h2 class="section">People</h2>
        <div class="group">
          ${stepper("peopleVisible", "Visible in the photo")}
          ${stepper("peopleInWater", "In the water")}
          ${stepper("peopleElevated", "On roofs or trapped up high")}
        </div>
        <h2 class="section" id="depth-h">Water depth</h2>
        <p class="section-note">Where would the water reach on a standing adult?</p>
        <fieldset class="depths" aria-labelledby="depth-h">
          ${DEPTH_CLASSES.map(
            (d) => `<label class="tile"><input type="radio" name="depth" value="${d}" ${d === "unknown" ? "checked" : ""} />
              <span class="tile-body">${depthPictogram(d)}<span class="tile-text"><b>${DEPTH_LABELS[d].label}</b><small>${DEPTH_LABELS[d].hint}</small></span></span></label>`,
          ).join("")}
        </fieldset>
        <label class="note"><span class="section">Note <span class="opt">optional</span></span>
          <span class="group"><textarea id="note" maxlength="${NOTE_MAX}" placeholder="Road blocked, current strong, boat needed…"></textarea></span>
        </label>
        <div class="actionbar">
          <button id="discard" type="button">Discard</button>
          <button class="primary" type="submit">Save report</button>
        </div>
      </form>`);

    const showFix = (): void => {
      const dot = $("#fix-dot");
      const text = $("#fix-text");
      if (fix) {
        dot.className = `dot ${fix.accuracyM <= 50 ? "good" : "fair"}`;
        text.textContent = `Location ready, ±${Math.round(fix.accuracyM)} m`;
      } else {
        dot.className = `dot ${fixError ? "none" : "pulse"}`;
        text.textContent = fixError ?? "Finding location…";
      }
    };
    tracker?.stop();
    tracker = new FixTracker(navigator.geolocation, (f, e) => {
      fix = f;
      fixError = e;
      if (document.contains(document.querySelector("#fix-text"))) showFix();
    });
    tracker.start();

    const onFile = async (input: HTMLInputElement): Promise<void> => {
      const file = input.files?.[0];
      input.value = "";
      if (!file) return;
      try {
        prepared = await preparePhoto(file);
      } catch {
        toast("That file could not be read as a photo.", "error");
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
      // Composing is a focused task: the tab bar steps aside and the actions take its place at the bottom.
      document.body.classList.add("composing");
      window.scrollTo({ top: 0 });
      largeTitle(view);
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
        for (const k of Object.keys(counts) as (keyof typeof counts)[]) {
          const out = $(`#out-${k}`);
          if (out.textContent !== String(counts[k])) {
            out.textContent = String(counts[k]);
            out.classList.remove("tick");
            void out.offsetWidth;
            out.classList.add("tick");
          }
          view.querySelector<HTMLButtonElement>(`[data-key="${k}"][data-step="-1"]`)!.disabled =
            counts[k] === 0 || (k === "peopleVisible" && counts[k] <= floor);
        }
      }),
    );
    $("#discard").addEventListener("click", () => {
      window.scrollTo({ top: 0 });
      renderCapture();
    });
    $("#details").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!prepared) return;
      const depth = (view.querySelector<HTMLInputElement>('input[name="depth"]:checked')?.value ?? "unknown") as DepthClass;
      const now = Date.now();
      const report = buildReport(
        {
          deviceId: deviceId(),
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
      toast(savedMessage(navigator.onLine, uploadsConfigured()), "success");
      window.scrollTo({ top: 0 });
      renderCapture();
      void sync();
    });
    largeTitle(view);
  };

  // ---------------------------------------------------------------- outbox
  const renderOutbox = async (): Promise<void> => {
    document.body.classList.remove("composing");
    setTab("outbox");
    const items = await queue.all();
    const configured = uploadsConfigured();
    const waiting = items.filter((i) => i.status !== "sent").length;
    const sent = items.length - waiting;
    const now = Date.now();
    const glyph: Record<QueueItem["status"], IconName> = { queued: "clock", uploading: "up", sent: "check", failed: "alert" };
    show(`
      <h1>Outbox</h1>
      <div class="summary group">
        <div class="counts"><span><b>${waiting}</b> waiting</span><span><b>${sent}</b> sent</span></div>
        <button id="send-now" class="primary" ${configured && waiting ? "" : "disabled"}>${icon("up")}Send now</button>
      </div>
      ${configured ? "" : `<p class="notice">${icon("alert")}<span>Uploads are not set up in this build. Reports stay on this phone.</span></p>`}
      ${
        items.length === 0
          ? `<div class="empty">${icon("tray", "ico big")}<p>No reports yet.</p><a class="button primary" href="#capture">${icon("camera")}New report</a></div>`
          : `<p class="section-note">Swipe a report left to delete it.</p><ul class="group rows">${items
              .map(
                (i) => `<li class="swipe" data-id="${esc(i.id)}">
            <button class="swipe-action" data-act="delete" tabindex="-1" aria-hidden="true">Delete</button>
            <div class="swipe-content item">
              <img src="${thumbUrl(i)}" alt="" />
              <div class="item-body">
                <div class="item-top"><span class="pill ${i.status}">${icon(glyph[i.status])}<span class="status ${i.status}">${statusLabel(i.status, navigator.onLine, configured)}</span></span>
                  <time datetime="${esc(i.report.capturedAt)}">${esc(capturedWhen(i.report.capturedAt, now))}</time></div>
                <div class="item-line">${DEPTH_LABELS[i.report.depthGuess].label} · ${peopleSummary(i.report.peopleVisible)}</div>
                <div class="item-line muted">${icon("pin", "ico sm")}${esc(describe(i.report.location))}</div>
                ${i.report.note ? `<div class="item-line muted">${esc(i.report.note.slice(0, 120))}</div>` : ""}
                ${i.lastError && i.status !== "sent" ? `<div class="item-line error">${esc(i.lastError)}</div>` : ""}
                <div class="actions">
                  ${i.status === "failed" ? `<button data-act="retry" class="small">Try again</button>` : ""}
                  ${i.status !== "uploading" ? `<button data-act="delete" class="small quiet">${i.status === "sent" ? "Remove" : "Delete"}</button>` : ""}
                </div>
              </div>
            </div></li>`,
              )
              .join("")}</ul>`
      }`);
    $("#send-now").addEventListener("click", () => void sync(true));
    view.querySelectorAll<HTMLElement>(".swipe").forEach((row) => attachSwipe(row, $(".swipe-content", row), SWIPE_REVEAL_PX));
    view.querySelectorAll<HTMLButtonElement>("[data-act]").forEach((b) =>
      b.addEventListener("click", async () => {
        const id = b.closest<HTMLElement>("[data-id]")?.dataset.id;
        if (!id) return;
        if (b.dataset.act === "retry") {
          await queue.patch(id, { status: "queued", nextAttemptAt: 0, lastError: null });
          void sync(true);
        } else {
          const item = await queue.get(id);
          // Irreversible and unsent: the one place a confirmation earns its interruption.
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
    largeTitle(view);
  };

  const route = (): void => {
    // An invite link (.../field/#invite=XXXX-...) opens enrolment with the code filled in.
    const link = /^#invite=([0-9A-Za-z-]+)$/.exec(location.hash);
    if (link) {
      pendingInvite = link[1]!;
      history.replaceState(null, "", "#setup"); // the code leaves the address bar and the history
    }
    if (location.hash !== "#capture") tracker?.stop();
    const target = location.hash || "#capture";
    if (target === "#setup") renderSetup();
    else if (target === "#outbox") void renderOutbox();
    else renderCapture();
    view.focus({ preventScroll: true });
  };

  window.addEventListener("hashchange", () => {
    window.scrollTo({ top: 0 });
    route();
  });
  window.addEventListener("online", () => void refreshBar().then(() => sync()));
  window.addEventListener("offline", () => void refreshBar());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void sync();
  });
  window.setInterval(() => {
    if (document.visibilityState === "visible") void sync();
  }, RETRY_INTERVAL_MS);

  // A first run opened from an invite link keeps the link: route() reads the code out of it.
  if (!loadSettings() && !location.hash.startsWith("#invite=")) location.hash = "#setup";
  route();
  await refreshBar();
  void sync();
}

function stepper(key: string, label: string): string {
  return `<div class="stepper"><span>${label}</span>
    <span class="stepper-ctl">
      <button type="button" data-key="${key}" data-step="-1" aria-label="${label}: one fewer" disabled>−</button>
      <output id="out-${key}" aria-live="polite">0</output>
      <button type="button" data-key="${key}" data-step="1" aria-label="${label}: one more">+</button>
    </span></div>`;
}

void main();
