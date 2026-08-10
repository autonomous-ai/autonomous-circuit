#!/usr/bin/env node
// Video standalone production server: the same Video services Vite mounts in
// dev (POST /api/<cmd>, GET /api/events SSE, /projects/<id>/… assets) plus
// the built viewer bundle from dist/.
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { createVideoServices } from "./video/http.mjs";
import { DEFAULT_VIEWER_PORT, serveDistAsset } from "./video/static.mjs";

function normalizeViewerPort(raw, fallback) {
  const parsed = Number.parseInt(String(raw ?? ""), 10);
  return Number.isInteger(parsed) && parsed > 0 && parsed < 65536
    ? parsed
    : fallback;
}
import {
  closeHttpServer,
  normalizeServerLifetimeMs,
  scheduleProcessShutdown,
} from "./serverLifetime.mjs";

const serverModuleDir = path.dirname(fileURLToPath(import.meta.url));
const viewerAppRoot = path.basename(path.dirname(serverModuleDir)) === "src"
  ? path.resolve(serverModuleDir, "..", "..")
  : path.resolve(serverModuleDir, "..");
const port = normalizeViewerPort(process.env.VIEWER_PORT, DEFAULT_VIEWER_PORT);
const host = process.env.VIEWER_HOST || "127.0.0.1";
const serverLifetimeMs = normalizeServerLifetimeMs(process.env.VIEWER_SERVER_LIFETIME_MS);
const distRoot = path.resolve(viewerAppRoot, "dist");

const video = createVideoServices();

const middlewares = [
  video.apiMiddleware,
  video.assetMiddleware,
  serveDistAsset({ distRoot }),
];

function runMiddleware(index, req, res) {
  const middleware = middlewares[index];
  if (!middleware) {
    res.statusCode = 404;
    res.end("Not found");
    return;
  }
  middleware(req, res, () => runMiddleware(index + 1, req, res));
}

const server = http.createServer((req, res) => runMiddleware(0, req, res));

server.on("close", () => video.close());

server.listen(port, host, () => {
  console.log(`[video:server] listening on http://${host}:${port}/ (projects: ${video.projectsRoot})`);
  if (serverLifetimeMs !== null) {
    scheduleProcessShutdown({
      lifetimeMs: serverLifetimeMs,
      label: "Video backend",
      close: () => closeHttpServer(server),
    });
  }
});
