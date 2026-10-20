import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { type Page, type Request, expect, test } from "@playwright/test";
import { FAKE_SUPABASE } from "../playwright.config";

const PHOTO = fileURLToPath(new URL("./fixtures/flood.jpg", import.meta.url));
const TEAM = "TEAM-2026-01";

test.use({
  geolocation: { latitude: 50.0123, longitude: 14.4011, accuracy: 8 },
  permissions: ["geolocation"],
});

interface Fake {
  photos: Request[];
  rows: Record<string, unknown>[];
  rowStatus: number;
}

async function fakeSupabase(page: Page): Promise<Fake> {
  const fake: Fake = { photos: [], rows: [], rowStatus: 201 };
  await page.context().route(`${FAKE_SUPABASE}/**`, async (route) => {
    const req = route.request();
    if (req.url().includes("/storage/v1/object/field-photos/")) {
      fake.photos.push(req);
      return route.fulfill({ status: 200, contentType: "application/json", body: '{"Key":"ok"}' });
    }
    if (req.url().endsWith("/rest/v1/reports")) {
      if (fake.rowStatus < 300) fake.rows.push(JSON.parse(req.postData() ?? "{}") as Record<string, unknown>);
      return route.fulfill({ status: fake.rowStatus, body: fake.rowStatus < 300 ? "" : '{"message":"rls"}' });
    }
    return route.fulfill({ status: 404 });
  });
  return fake;
}

async function setUp(page: Page): Promise<void> {
  await page.goto("./");
  await expect(page).toHaveURL(/#setup$/);
  await page.getByLabel("Team code").fill(TEAM);
  await page.getByLabel(/I understand/).check();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "New report" })).toBeVisible();
  await page.evaluate(() => navigator.serviceWorker.ready);
}

async function capture(page: Page): Promise<void> {
  await expect(page.locator("#fix-text")).toContainText("Location ready");
  await expect(page.locator("#details")).toBeHidden(); // no form until there is a photo
  // A camera capture is a file created now; the fixture on disk is older than the 10-minute freshness rule.
  await page.locator("#camera").setInputFiles({ name: "capture.jpg", mimeType: "image/jpeg", buffer: readFileSync(PHOTO) });
  await expect(page.locator("#details")).toBeVisible();
  await expect(page.locator("#pick")).toBeHidden();
  await expect(page.locator("#loc-text")).toHaveText("Phone location ±8 m");
  await page.getByRole("button", { name: "In the water: one more" }).click();
  await page.getByRole("button", { name: "In the water: one more" }).click();
  await page.getByText("Waist", { exact: true }).click();
  await page.getByLabel(/Note/).fill("two people on a car roof");
}

test("a report taken offline is kept, survives a reload, and is sent when signal returns", async ({ page, context }) => {
  const fake = await fakeSupabase(page);
  await setUp(page);
  await capture(page);

  await context.setOffline(true);
  await page.getByRole("button", { name: "Save report" }).click();
  await expect(page.locator("#toast")).toContainText("when there is signal");
  await expect(page.locator("#pending")).toHaveText("1");

  await page.reload(); // served by the service worker: there is no network
  await page.getByRole("link", { name: /Outbox/ }).click();
  await expect(page.locator(".status")).toHaveText("Waiting for signal");
  expect(fake.rows).toHaveLength(0);

  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.locator(".status")).toHaveText("Sent");
  await expect(page.locator("#pending")).toHaveText("0");

  expect(fake.photos).toHaveLength(1);
  const row = fake.rows[0]!;
  expect(fake.photos[0]!.url()).toContain(`/field-photos/${row.id as string}.jpg`);
  expect(fake.photos[0]!.headers()["content-type"]).toBe("image/jpeg");
  expect(row).toMatchObject({
    team_code: TEAM,
    location_source: "device",
    accuracy_m: 8,
    people_in_water: 2,
    people_visible: 2,
    depth_guess: "waist",
    note: "two people on a car roof",
    consent: true,
    photo_path: `${row.id as string}.jpg`,
  });
  expect(row.lat as number).toBeCloseTo(50.0123, 4);
  expect(row.device_id as string).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

test("a refused team code stops retrying and says why", async ({ page }) => {
  const fake = await fakeSupabase(page);
  fake.rowStatus = 403;
  await setUp(page);
  await capture(page);
  await page.getByRole("button", { name: "Save report" }).click();
  await page.getByRole("link", { name: /Outbox/ }).click();
  await expect(page.locator(".status")).toHaveText("Not accepted");
  await expect(page.getByText(/team code was not accepted/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});
