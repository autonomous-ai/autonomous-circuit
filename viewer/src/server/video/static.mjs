// Static/SPA serving for the standalone prod server, plus the app's network
// identity constants. Replaces the donor's httpHandlers.serveDistAsset and
// cadjs viewerServerInfo imports — self-contained so the donor modules can go.

import fs from "node:fs";
import path from "node:path";

export const DEFAULT_VIEWER_HOST = "127.0.0.1";
export const DEFAULT_VIEWER_PORT = 4178;

const CONTENT_TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".mjs", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".ico", "image/x-icon"],
  [".mp4", "video/mp4"],
  [".srt", "text/plain; charset=utf-8"],
  [".woff2", "font/woff2"],
  [".woff", "font/woff"],
  [".txt", "text/plain; charset=utf-8"],
]);

export function contentTypeForStaticAsset(filePath) {
  return CONTENT_TYPES.get(path.extname(filePath).toLowerCase()) || null;
}

function serveStaticFile(filePath, res) {
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    res.statusCode = 404;
    res.setHeader("content-type", "text/plain; charset=utf-8");
    res.end("Not found");
    return;
  }
  res.statusCode = 200;
  const type = contentTypeForStaticAsset(filePath);
  if (type) res.setHeader("content-type", type);
  res.setHeader("content-length", String(stat.size));
  fs.createReadStream(filePath)
    .on("error", () => {
      if (!res.headersSent) res.statusCode = 500;
      res.end();
    })
    .pipe(res);
}

/** SPA fallback middleware over the built `dist/`: real file when present,
 * 404 for asset-shaped misses, index.html for everything else. */
export function serveDistAsset({
  distRoot,
  indexHtmlPath = path.join(distRoot, "index.html"),
} = {}) {
  const root = path.resolve(distRoot);
  return function distAssetMiddleware(req, res) {
    const requestUrl = new URL(req.url || "/", "http://localhost");
    const requestPath =
      requestUrl.pathname === "/" ? "/index.html" : requestUrl.pathname;
    let filePath = "";
    try {
      filePath = path.resolve(
        root,
        decodeURIComponent(requestPath).replace(/^\/+/, ""),
      );
    } catch {
      res.statusCode = 400;
      res.end("Bad request");
      return;
    }
    if (!(filePath === root || filePath.startsWith(`${root}${path.sep}`))) {
      res.statusCode = 403;
      res.end("Forbidden");
      return;
    }
    const fileExists = fs.existsSync(filePath);
    const isStaticAssetRequest =
      requestPath.startsWith("/assets/") || Boolean(path.extname(requestPath));
    if (!fileExists && isStaticAssetRequest) {
      res.statusCode = 404;
      res.setHeader("content-type", "text/plain; charset=utf-8");
      res.setHeader("cache-control", "no-store");
      res.end("Not found");
      return;
    }
    serveStaticFile(fileExists ? filePath : indexHtmlPath, res);
  };
}
