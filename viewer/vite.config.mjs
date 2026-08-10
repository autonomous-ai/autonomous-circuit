import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import { transform as esbuildTransform } from "esbuild";
import react from "@vitejs/plugin-react";
import { DEFAULT_VIEWER_PORT } from "./src/server/circuit/static.mjs";

function normalizeViewerPort(raw, fallback) {
  const parsed = Number.parseInt(String(raw ?? ""), 10);
  return Number.isInteger(parsed) && parsed > 0 && parsed < 65536 ? parsed : fallback;
}
import { createCircuitServices } from "./src/server/circuit/http.mjs";
import {
  normalizeServerLifetimeMs,
  scheduleProcessShutdown,
} from "./src/server/serverLifetime.mjs";

const viewerPort = normalizeViewerPort(process.env.VIEWER_PORT, DEFAULT_VIEWER_PORT);
const viewerAppRoot = path.dirname(fileURLToPath(import.meta.url));
const viewerClientRoot = path.join(viewerAppRoot, "src", "client");
const viewerNodeModulesRoot = path.join(viewerAppRoot, "node_modules");
const viewerAllowedHosts = normalizeViewerAllowedHosts(process.env.VIEWER_ALLOWED_HOSTS ?? "");
const viewerServerLifetimeMs = normalizeServerLifetimeMs(process.env.VIEWER_SERVER_LIFETIME_MS);

function normalizeViewerAllowedHosts(value) {
  return String(value || "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
}

// Circuit replaces the donor's cadCatalogPlugin: the API + SSE + asset routes
// come from one middleware factory (src/server/circuit/http.mjs). Catalog
// watching lives inside the Circuit services (fs.watch + 150 ms debounce →
// SSE `catalog_changed`), not in Vite's watcher/HMR — the same code path
// serves the standalone prod server.
function circuitApiPlugin() {
  return {
    name: "circuit-api",
    configureServer(server) {
      const circuit = createCircuitServices();
      server.middlewares.use(circuit.apiMiddleware);
      server.middlewares.use(circuit.assetMiddleware);
      server.httpServer?.once("close", () => circuit.close());
    },
  };
}

// Panda owns a handful of .ts/.tsx sources (transport + zustand stores).
// The viewer's existing esbuild config has `loader: "jsx"` over all .[jt]sx?
// files, which does not strip TypeScript type syntax. This tiny plugin
// runs esbuild with the proper TS loader for those files so they make it
// through rollup. Track C's Tauri command bindings live in TypeScript too.
function typescriptSourcePlugin() {
  return {
    name: "panda-typescript-source",
    enforce: "pre",
    async transform(code, id) {
      const queryless = id.split("?", 1)[0];
      if (!/\.(ts|tsx)$/.test(queryless)) {
        return null;
      }
      const result = await esbuildTransform(code, {
        loader: queryless.endsWith("x") ? "tsx" : "ts",
        sourcefile: id,
        sourcemap: true,
        target: "es2020",
        jsx: "automatic",
      });
      return {
        code: result.code,
        map: result.map || null,
      };
    },
  };
}

function serverLifetimePlugin() {
  return {
    name: "cad-viewer-server-lifetime",
    configureServer(server) {
      if (viewerServerLifetimeMs === null) {
        return;
      }
      let shutdownTimer = null;
      const scheduleShutdown = () => {
        shutdownTimer = scheduleProcessShutdown({
          lifetimeMs: viewerServerLifetimeMs,
          label: "CAD Viewer dev server",
          close: () => server.close(),
        });
      };
      if (server.httpServer?.listening) {
        scheduleShutdown();
      } else {
        server.httpServer?.once("listening", scheduleShutdown);
      }
      server.httpServer?.once("close", () => {
        if (shutdownTimer) {
          clearTimeout(shutdownTimer);
        }
      });
    },
  };
}

export default defineConfig(({ command }) => ({
  root: viewerAppRoot,
  envPrefix: "VIEWER_",
  plugins: [
    typescriptSourcePlugin(),
    react(),
    ...(command === "serve" ? [circuitApiPlugin()] : []),
    serverLifetimePlugin(),
  ],
  resolve: {
    alias: {
      "@": viewerClientRoot,
      "clsx": path.join(viewerNodeModulesRoot, "clsx"),
      "gifenc": path.join(viewerNodeModulesRoot, "gifenc", "dist", "gifenc.esm.js"),
      "tailwind-merge": path.join(viewerNodeModulesRoot, "tailwind-merge"),
      "three": path.join(viewerNodeModulesRoot, "three"),
      "three/examples": path.join(viewerNodeModulesRoot, "three", "examples"),
    },
  },
  esbuild: {
    loader: "jsx",
    include: /\.jsx?$/,
    exclude: [],
  },
  optimizeDeps: {
    esbuildOptions: {
      loader: {
        ".js": "jsx",
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          if (id.includes("/three/")) {
            return "vendor-three";
          }
          if (id.includes("/react/") || id.includes("/react-dom/")) {
            return "vendor-react";
          }
          if (id.includes("/radix-ui/") || id.includes("/@radix-ui/")) {
            return "vendor-ui";
          }
          if (id.includes("/lucide-react/")) {
            return "vendor-icons";
          }
          return undefined;
        },
      },
    },
  },
  worker: {
    format: "es",
  },
  server: {
    host: "127.0.0.1",
    port: viewerPort,
    strictPort: true,
    allowedHosts: viewerAllowedHosts,
    watch: {
      // Never watch Rust build output. The viewer dev server watches the
      // workspace root for CAD-artifact changes (see serverLifetimePlugin's
      // `watcher.add`); bundling skills/ as a Tauri resource makes the build
      // copy them — including cad-viewer's vendored web viewer (index.html +
      // cadjs) — into desktop/src-tauri/target/, and watching that triggers
      // spurious full-page reloads in the Tauri webview.
      ignored: ["**/target/**"],
    },
  },
  preview: {
    host: "127.0.0.1",
    port: viewerPort,
    strictPort: true,
    allowedHosts: viewerAllowedHosts,
  },
}));
