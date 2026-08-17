/**
 * How wide a net is routed, and how wide the placement will let it be.
 *
 * The EE review's finding 4 asked for the power rails to be 0.5–1.0mm instead
 * of 0.2mm. The board file can say so and the router obeys — and on the board
 * they were reviewing, one of those rails **cannot** be 0.5mm at any effort,
 * because a track leaving a QFN-56 pad at 0.400mm pitch can be
 * `2 × (0.400 − 0.100 − 0.100) = 0.400mm` wide and no wider.
 *
 * So the number an engineer needs before they type anything is the *ceiling*,
 * measured against the placement rather than against this route:
 *
 *     V3_3   ceiling 0.4000mm, held by U3.IOVDD6   routed 0.2mm today
 *     V5     ceiling 1.1mm,    held by U1.VBUS     routed 0.2mm today
 *
 * One of those is free and the other is impossible, and until now the app
 * could not tell them apart. `circuitpy.netwidth` does the measurement; this
 * runs it the way `fastCheck.mjs` runs the gate, with the same interpreter
 * resolution and the same never-reject contract: a measurement that could not
 * run comes back as a result saying so, because an absent number must be
 * visible and silence is not a pass.
 */

import fs from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";

import { pythonPathDirs, resolvePython } from "./fastCheck.mjs";

/**
 * Ceiling on one measurement. The escape fans 180 bearings out of every pad on
 * the net, so a rail with 25 pads on a dense board is a couple of seconds;
 * ground on a big board is the worst case and is why this is not run for every
 * net at once.
 */
export const NET_WIDTH_TIMEOUT_MS = 30_000;

function unavailable(reason, startedAt) {
  return { ok: false, reason, nets: [], elapsedMs: Date.now() - startedAt };
}

/**
 * Measure the named nets, or every rail when `nets` is empty.
 *
 * @param {string} circuitJsonPath absolute path to `<stem>.circuit.json`
 * @param {{projectRoot?: string, nets?: string[], rails?: boolean, env?: object}} options
 * @returns {Promise<object>} never rejects
 */
export function runNetWidths(circuitJsonPath, { projectRoot, nets = [], rails = false, env = process.env } = {}) {
  const startedAt = Date.now();
  return new Promise((resolve) => {
    const python = resolvePython(env);
    if (!python) {
      resolve(unavailable("python3.10+ was not found, so the width was not measured", startedAt));
      return;
    }
    if (!circuitJsonPath || !fs.existsSync(circuitJsonPath)) {
      resolve(unavailable("this board has not been built yet, so there is nothing to measure", startedAt));
      return;
    }
    const wanted = (Array.isArray(nets) ? nets : [])
      .map((one) => String(one || "").trim())
      .filter(Boolean)
      .slice(0, 8); // a person is looking at one net, not forty
    if (!wanted.length && !rails) {
      resolve(unavailable("no net named", startedAt));
      return;
    }
    const root = projectRoot || path.dirname(path.dirname(circuitJsonPath));
    const args = [
      "-m",
      "circuitpy.netwidth",
      root,
      "--board",
      path.relative(root, circuitJsonPath),
      ...wanted.flatMap((one) => ["--net", one]),
      ...(rails ? ["--rails"] : []),
    ];
    execFile(
      python,
      args,
      {
        cwd: root,
        timeout: NET_WIDTH_TIMEOUT_MS,
        maxBuffer: 8 * 1024 * 1024,
        env: {
          ...env,
          PYTHONPATH: [...pythonPathDirs(env), env.PYTHONPATH].filter(Boolean).join(path.delimiter),
          CIRCUIT_PARTS_ENGINE: "off",
        },
      },
      (error, stdout, stderr) => {
        const lines = String(stdout || "").trim().split("\n");
        let parsed = null;
        try {
          parsed = JSON.parse(lines[lines.length - 1]);
        } catch {
          parsed = null;
        }
        if (!parsed || typeof parsed !== "object") {
          const detail = String(stderr || error?.message || "no output").trim().slice(-300);
          resolve(unavailable(`the width measurement did not run: ${detail}`, startedAt));
          return;
        }
        resolve({
          ok: parsed.ok !== false,
          reason: String(parsed.error || ""),
          nets: Array.isArray(parsed.nets) ? parsed.nets : [],
          powerFloorMm: Number(parsed.power_floor_mm) || null,
          minTraceMm: Number(parsed.min_trace_mm) || null,
          elapsedMs: Date.now() - startedAt,
        });
      },
    );
  });
}
