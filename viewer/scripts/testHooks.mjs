// Two things Vite gives the app and `node --test` does not: a JSX transform
// and the `@` alias. Without them a test can only read a component's source as
// a string, which is how `PcbCanvas.jsx` shipped a canvas that never imported
// `canvasPointer.js` and a `snapDelta` that was never defined (8fb33cd).
//
// Loaded once, ahead of every test file, from scripts/run-tests.mjs via
// `--import`. It touches nothing but `.jsx`/`.tsx` files and `@/…`
// specifiers, so the plain-logic suite runs exactly as it did before.
import fs from "node:fs";
import path from "node:path";
import { registerHooks } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { transformSync } from "esbuild";

const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Mirrors `resolve.alias["@"]` in vite.config.mjs. If that moves, this moves.
const CLIENT_ROOT = path.join(PACKAGE_ROOT, "src", "client");
const ALIAS_PREFIX = "@/";

// Vite resolves an extensionless import by probing; Node never does. The order
// matters: `@/ui/utils` must find utils.js, not a same-named directory.
const PROBE_SUFFIXES = [
  "",
  ".js",
  ".jsx",
  ".mjs",
  ".ts",
  ".tsx",
  "/index.js",
  "/index.jsx",
  "/index.ts",
  "/index.tsx",
];

function probe(base) {
  for (const suffix of PROBE_SUFFIXES) {
    const candidate = `${base}${suffix}`;
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return "";
}

function resolveAliased(specifier) {
  const found = probe(path.join(CLIENT_ROOT, specifier.slice(ALIAS_PREFIX.length)));
  if (found) return found;
  throw new Error(`Cannot resolve "${specifier}" under ${CLIENT_ROOT}`);
}

// `.js` is deliberately absent even though the Vite config runs the jsx loader
// over it. No `.js` in src/client holds JSX today, and re-emitting all 900
// tests' worth of plain modules through esbuild to cover a file that does not
// exist would slow every run and change code no test asked to change.
const LOADER_BY_EXT = { ".jsx": "jsx", ".tsx": "tsx" };

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(ALIAS_PREFIX)) {
      return { url: pathToFileURL(resolveAliased(specifier)).href, shortCircuit: true };
    }
    // A relative import with no extension. Vite probes for one; Node refuses,
    // so `import ChatCodeBlock from "./ChatCodeBlock"` (Markdown.jsx) is an
    // ERR_MODULE_NOT_FOUND here and nothing downstream of it can be mounted.
    // Tried only after Node has failed, so every specifier Node can resolve on
    // its own still resolves exactly as it did.
    try {
      return nextResolve(specifier, context);
    } catch (error) {
      if (error?.code !== "ERR_MODULE_NOT_FOUND") throw error;
      if (!specifier.startsWith("./") && !specifier.startsWith("../")) throw error;
      if (!context.parentURL?.startsWith("file:")) throw error;
      const found = probe(path.resolve(path.dirname(fileURLToPath(context.parentURL)), specifier));
      if (!found) throw error;
      return { url: pathToFileURL(found).href, shortCircuit: true };
    }
  },

  load(url, context, nextLoad) {
    if (!url.startsWith("file:")) return nextLoad(url, context);
    const filename = fileURLToPath(url);
    const extension = path.extname(filename);
    // Vite turns `import "./prose.css"` into a style injection; Node throws
    // ERR_UNKNOWN_FILE_EXTENSION. An empty module is the honest stand-in — a
    // headless DOM has no layout for the rules to affect, and `dom.js` hands
    // out boxes instead.
    if (extension === ".css") return { format: "module", source: "", shortCircuit: true };
    const loader = LOADER_BY_EXT[extension];
    if (!loader) return nextLoad(url, context);
    // `jsx: "automatic"` is what @vitejs/plugin-react does, so the component a
    // test mounts compiles to the same react/jsx-runtime calls the app runs.
    const { code } = transformSync(fs.readFileSync(filename, "utf8"), {
      loader,
      jsx: "automatic",
      format: "esm",
      target: "es2022",
      sourcefile: filename,
      sourcemap: "inline",
    });
    return { format: "module", source: code, shortCircuit: true };
  },
});
