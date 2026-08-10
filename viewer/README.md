# Circuit viewer

The web app for Autonomous Circuit: a Vite + React chat surface plus a board
viewer, backed by a Node server driver.

- `src/client/` — the SPA: chat sidebar (plan/approve/build flow) and the
  artifact workspace that shows what the pipeline produced.
- `src/server/circuit/` — the backend: `POST /api/<command>` endpoints, an SSE
  event stream, workspace asset serving, and the Claude subprocess driver.
- Dev and prod run the same middlewares: `npm run dev` mounts them in Vite;
  `src/server/server.mjs` serves the built app in prod.

Run tests with `node scripts/run-tests.mjs src/server` (server) or `npm test` (client).
