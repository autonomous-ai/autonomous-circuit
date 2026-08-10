// Video settings — ~/.autonomous-video/settings.json {hasOnboarded, autoBuild, model}.
//
// Contract §2: the settings file carries exactly these three fields. The
// `app_settings_read` reply is padded with harmless legacy defaults so the
// donor's `AppSettings` TS shape (kept verbatim in transport.ts) stays
// satisfied for not-yet-excised client code.

import fs from "node:fs";
import path from "node:path";

import { videoHome } from "./projects.mjs";

export function settingsFilePath(env = process.env) {
  return path.join(videoHome(env), "settings.json");
}

const DEFAULTS = Object.freeze({
  hasOnboarded: false,
  autoBuild: true,
  model: undefined,
});

function normalize(raw) {
  const obj = raw && typeof raw === "object" ? raw : {};
  const out = {
    hasOnboarded: typeof obj.hasOnboarded === "boolean" ? obj.hasOnboarded : DEFAULTS.hasOnboarded,
    // Missing → autopilot on (matches the donor's serde default).
    autoBuild: typeof obj.autoBuild === "boolean" ? obj.autoBuild : DEFAULTS.autoBuild,
  };
  const model = typeof obj.model === "string" ? obj.model.trim() : "";
  if (model) {
    out.model = model;
  }
  // Render provider (contract §1 set); absent → dramapy's default (mock).
  const provider = typeof obj.renderProvider === "string" ? obj.renderProvider.trim() : "";
  if (["mock", "fal", "dashscope", "minimax"].includes(provider)) {
    out.renderProvider = provider;
  }
  return out;
}

export function createSettingsStore({ filePath = settingsFilePath() } = {}) {
  function read() {
    try {
      return normalize(JSON.parse(fs.readFileSync(filePath, "utf8")));
    } catch {
      return normalize(null);
    }
  }

  /** Merge-write: only the Video trio is persisted; unknown keys are dropped. */
  function write(partial) {
    const current = read();
    const merged = { ...current, ...(partial && typeof partial === "object" ? partial : {}) };
    const next = normalize(merged);
    // An explicit `model: ""` / null clears the stored model.
    if (partial && Object.prototype.hasOwnProperty.call(partial, "model")) {
      const model = typeof partial.model === "string" ? partial.model.trim() : "";
      if (model) {
        next.model = model;
      } else {
        delete next.model;
      }
    }
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, `${JSON.stringify(next, null, 2)}\n`);
    return next;
  }

  /** The full AppSettings wire shape (video trio + legacy-compatible pads). */
  function readWire() {
    const s = read();
    return {
      defaultFilament: "PLA",
      slicerBinaryPath: "",
      hasOnboarded: s.hasOnboarded,
      autoUpdate: false,
      autoBuild: s.autoBuild,
      ...(s.model ? { model: s.model } : {}),
      ...(s.renderProvider ? { renderProvider: s.renderProvider } : {}),
    };
  }

  return { filePath, read, write, readWire };
}
