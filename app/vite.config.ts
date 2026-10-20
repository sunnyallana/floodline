import { readFileSync } from "node:fs";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as { version: string };

// Served by GitHub Pages from docs/field/, next to the map at docs/.
export default defineConfig({
  base: "/field/",
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  build: { outDir: "../docs/field", emptyOutDir: true, target: "es2022", sourcemap: true },
  plugins: [
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: false,
      includeAssets: ["icon.svg"],
      manifest: {
        name: "Floodline Field",
        short_name: "Floodline",
        description: "Capture flood photos with location and send them to your coordination team when there is signal.",
        start_url: "/field/",
        scope: "/field/",
        display: "standalone",
        orientation: "portrait",
        background_color: "#0f1417",
        theme_color: "#0f1417",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest}"],
        navigateFallback: "/field/index.html",
        // Uploads go straight to Supabase; the service worker never caches or replays API traffic.
        runtimeCaching: [],
      },
    }),
  ],
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
