import { defineConfig } from "@playwright/test";

// Builds a test copy pointed at a fake Supabase origin; every request to it is intercepted in the test.
export const FAKE_SUPABASE = "https://e2e-test.supabase.co";
const OUT = ".e2e-dist";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4174/field/",
    // Locally: the installed Chrome, no browser download. CI: the Chromium that `playwright install` provides.
    ...(process.env.CI ? {} : { channel: "chrome" }),
    serviceWorkers: "allow",
  },
  webServer: {
    command: `npx vite build --outDir ${OUT} --emptyOutDir && npx vite preview --outDir ${OUT} --port 4174 --strictPort --host 127.0.0.1`,
    url: "http://127.0.0.1:4174/field/",
    reuseExistingServer: false,
    timeout: 120_000,
    env: { VITE_SUPABASE_URL: FAKE_SUPABASE, VITE_SUPABASE_ANON_KEY: "e2e-anon-key" },
  },
});
