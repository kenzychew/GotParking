import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// GotParking PWA frontend (T6). Single config file drives both the Vite
// dev/build pipeline and the Vitest test runner (jsdom environment).
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/*.png"],
      manifest: {
        id: "/",
        name: "GotParking",
        short_name: "GotParking",
        description:
          "Carpark availability forecasts for 10 Singapore malls, 20 minutes ahead.",
        theme_color: "#0969da",
        background_color: "#ffffff",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "icons/pwa-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "icons/pwa-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "icons/maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        // App shell + hashed assets (JS/CSS/fonts/icons) precached at build
        // time. The forecast payload is handled separately below (runtime
        // cache, not precache -- it changes every poll cycle).
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff,woff2}"],
        runtimeCaching: [
          {
            // Cache the last-successful forecast payload so the offline
            // state can show "last-seen data" (design doc D10 staleness
            // caveat + offline copy) instead of nothing at all. NetworkFirst
            // means a reachable network always wins; the cached response is
            // only served when the network request fails or times out.
            urlPattern: ({ url }: { url: URL }) => url.pathname === "/api/forecast",
            handler: "NetworkFirst",
            options: {
              cacheName: "forecast-api-cache",
              networkTimeoutSeconds: 5,
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
