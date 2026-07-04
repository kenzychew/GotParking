import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Runs after every test: unmount any rendered tree and reset localStorage so
// shortcuts/theme/cache state never leaks between test cases.
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});
