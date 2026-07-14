// Ambient declaration for Vite's built-in "?raw" import suffix (Vitest runs
// on Vite, so this works with no new dependency and without pulling in
// @types/node -- which would add ambient globals like `fetch`/`Response`
// that conflict with @cloudflare/workers-types across the whole program,
// including src/index.ts.
declare module "*?raw" {
  const content: string;
  export default content;
}
