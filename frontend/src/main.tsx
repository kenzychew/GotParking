import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { registerSW } from "virtual:pwa-register";

// Self-hosted Public Sans (design doc AI-slop finding: a system-font stack
// as the PRIMARY typeface is disallowed). No external font CDN.
import "@fontsource/public-sans/400.css";
import "@fontsource/public-sans/500.css";
import "@fontsource/public-sans/600.css";
import "@fontsource/public-sans/700.css";
import "./index.css";
import { App } from "./App";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("#root element not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

if (import.meta.env.PROD) {
  // registerType: "autoUpdate" (vite.config.ts) means a new service worker
  // activates silently on the next load -- no "update available" prompt
  // needed for this MVP's scope.
  registerSW({ immediate: true });
}
