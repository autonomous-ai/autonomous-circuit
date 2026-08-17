/**
 * The verify half of the edit loop: what the board looks like ~1s after a drag.
 *
 * A placement edit writes one number into `boards/<stem>.tsx`. The compiled
 * `circuit.json` on disk was made from the *old* number, and recompiling costs
 * ~20s for placement only and minutes for the full gauntlet — neither is an IDE
 * interaction. So this runs `python -m circuitpy.fastcheck`, which translates
 * the moved placement's geometry in memory and grades *that*.
 *
 * Three words, and this module may only ever produce the first two:
 *
 *   saved      — the literal is in the file. Immediate; what the write returns.
 *   legal      — every check that can run without a compile came back clean.
 *   orderable  — the full gauntlet ran. Not this. Never this.
 *
 * The verdict carries `notChecked` from the Python side rather than inventing
 * reassuring words here: nothing in the pipeline — including the 13.5s KiCad
 * DRC leg — can see a copper-pour defect, so "the pour is unchecked" is part of
 * the answer, not a caveat somebody may forget to print.
 *
 * Measured on `examples/terminal-keyboard` (137 parts, 5,463 elements): 1,150ms
 * untouched, 1,168ms with a 2.8mm move applied, and the moved run reproduces
 * all six of the blocking errors that the 20s placement-only compile produced
 * for the same edit — plus four `trace_left_its_pad` the compile does not
 * report at all.
 */

import fs from "node:fs";
import path from "node:path";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";

import { augmentedPathDirs } from "./driver.mjs";
import { claudeConfigDir } from "./projects.mjs";

/** Ceiling on one gate run. Ten times the measured worst case: a slow answer
 *  is still an answer, a hung one holds the edit bar open forever. */
export const FAST_CHECK_TIMEOUT_MS = 12_000;

const HERE = path.dirname(fileURLToPath(import.meta.url));
/** `<repo>/` — five levels above viewer/src/server/circuit. */
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..");

/**
 * The interpreter, resolved exactly as `app_prereq_check` resolves it
 * (`http.mjs:311`) so the gate never runs on a different Python than the one
 * the app told the user it found. 3.9 is on PATH on this machine and the
 * pipeline needs 3.10+, hence python3.12 before python3.
 */
export function resolvePython(env = process.env) {
  if (env.CIRCUIT_PYTHON && fs.existsSync(env.CIRCUIT_PYTHON)) return env.CIRCUIT_PYTHON;
  for (const name of ["python3.12", "python3"]) {
    for (const dir of augmentedPathDirs(env)) {
      const candidate = path.join(dir, name);
      try {
        if (fs.statSync(candidate).isFile()) return candidate;
      } catch {
        // keep looking
      }
    }
  }
  return null;
}

/**
 * Where `circuitpy` and `verifylib` live, in the order the skill runtime itself
 * resolves them (`verify_bridge.py:52-60`): the repo checkout in dev, the
 * vendored copy under the installed skill in prod. Both are returned when both
 * exist — the repo first, because a developer editing `checks.py` expects the
 * gate to run the edit, not last night's vendored copy.
 */
export function pythonPathDirs(env = process.env) {
  if (env.CIRCUIT_PYTHONPATH) return env.CIRCUIT_PYTHONPATH.split(path.delimiter).filter(Boolean);
  const dirs = [];
  for (const dir of [
    path.join(REPO_ROOT, "packages", "circuitpy", "src"),
    path.join(REPO_ROOT, "packages", "verify", "src"),
    path.join(claudeConfigDir(env), "skills", "circuitcode", "scripts", "packages"),
  ]) {
    if (fs.existsSync(dir)) dirs.push(dir);
  }
  return dirs;
}

/** `--move X,Y:DX,DY` per move, in the CLI's grammar. */
function moveArgs(moves) {
  const out = [];
  for (const move of moves || []) {
    const x = Number(move?.anchor?.x);
    const y = Number(move?.anchor?.y);
    const dx = Number(move?.dx ?? 0);
    const dy = Number(move?.dy ?? 0);
    if (![x, y, dx, dy].every(Number.isFinite)) continue;
    if (dx === 0 && dy === 0) continue; // a lock changes no geometry
    // `--move=…` and never `--move …`: a board coordinate is very often
    // negative, and argparse reads a bare `-45.8,-32:2.8,0` as an option name.
    out.push(`--move=${x},${y}:${dx},${dy}`);
  }
  return out;
}

/**
 * The shape returned when the gate could not run at all.
 *
 * It is a *result*, not a throw, and it says `unavailable` rather than
 * anything that could be mistaken for a pass. An absent check must be visible;
 * silence is not a pass, and that rule is older than this file
 * (`verify_bridge.py:15-17`).
 */
function unavailable(reason, startedAt) {
  return {
    ok: false,
    status: "unavailable",
    reason,
    warnings: [],
    counts: { error: 0, warning: 0, info: 0 },
    moves: [],
    geometry: "unknown",
    checked: [],
    notChecked: [],
    lastBuild: null,
    elapsedMs: Date.now() - startedAt,
  };
}

/**
 * Grade a board, with placement moves applied to the built geometry.
 *
 * @param {string} circuitJsonPath absolute path to `<stem>.circuit.json`
 * @param {object} options
 * @param {string} options.projectRoot the project workspace
 * @param {Array<{anchor:{x:number,y:number}, dx:number, dy:number}>} options.moves
 * @returns {Promise<object>} never rejects — a broken gate is a reported result
 */
export function runFastCheck(circuitJsonPath, { projectRoot, moves = [], env = process.env } = {}) {
  const startedAt = Date.now();
  return new Promise((resolve) => {
    const python = resolvePython(env);
    if (!python) {
      resolve(unavailable("python3.10+ was not found, so the board was not checked", startedAt));
      return;
    }
    if (!circuitJsonPath || !fs.existsSync(circuitJsonPath)) {
      resolve(unavailable("this board has not been built yet, so there is nothing to check against", startedAt));
      return;
    }
    const root = projectRoot || path.dirname(path.dirname(circuitJsonPath));
    const args = [
      "-m",
      "circuitpy.fastcheck",
      root,
      "--board",
      path.relative(root, circuitJsonPath),
      ...moveArgs(moves),
    ];
    const dirs = pythonPathDirs(env);
    execFile(
      python,
      args,
      {
        cwd: root,
        timeout: FAST_CHECK_TIMEOUT_MS,
        maxBuffer: 32 * 1024 * 1024, // a dense board's findings, with detail text
        env: {
          ...env,
          PYTHONPATH: [...dirs, env.PYTHONPATH].filter(Boolean).join(path.delimiter),
          // The gate must never reach the network: the parts engine's cold
          // queries are 47-90s and this budget is one second.
          CIRCUIT_PARTS_ENGINE: "off",
        },
      },
      (error, stdout, stderr) => {
        // One JSON line, last line wins — stray prints do not kill a verdict.
        const lines = String(stdout || "").trim().split("\n");
        let parsed = null;
        try {
          parsed = JSON.parse(lines[lines.length - 1]);
        } catch {
          parsed = null;
        }
        if (!parsed || typeof parsed !== "object") {
          const detail = String(stderr || error?.message || "no output").trim().slice(-400);
          resolve(unavailable(`the fast check did not run: ${detail}`, startedAt));
          return;
        }
        resolve({
          ok: parsed.ok !== false,
          // `legal` is the strongest word a compile-free answer may carry, and
          // it is scoped by `notChecked` below.
          status: parsed.ok === false ? "unavailable" : parsed.counts?.error ? "blocked" : "legal",
          reason: String(parsed.error || ""),
          geometry: String(parsed.geometry || "unknown"),
          warnings: Array.isArray(parsed.warnings) ? parsed.warnings : [],
          counts: parsed.counts || { error: 0, warning: 0, info: 0 },
          moves: Array.isArray(parsed.moves) ? parsed.moves : [],
          checked: Array.isArray(parsed.checked) ? parsed.checked : [],
          notChecked: Array.isArray(parsed.not_checked) ? parsed.not_checked : [],
          // What the last full build said, when there has been one. Without
          // it, `counts.error` reads as the whole truth — and on the board
          // that prompted this it said 1 where the real number was 3.
          lastBuild: parsed.last_build && typeof parsed.last_build === "object"
            ? {
                atEpochS: Number(parsed.last_build.at_epoch_s) || 0,
                blocking: Number(parsed.last_build.blocking) || 0,
                invisibleHere: Number(parsed.last_build.invisible_here) || 0,
                // The warning tier, which is where most of the KiCad findings
                // actually land: a board can hide 380 of them behind a chip
                // that says nothing at all.
                warnings: Number(parsed.last_build.warnings) || 0,
                invisibleWarningsHere: Number(parsed.last_build.invisible_warnings_here) || 0,
                invisibleKinds: Array.isArray(parsed.last_build.invisible_kinds)
                  ? parsed.last_build.invisible_kinds
                  : [],
                fabReady: parsed.last_build.fab_ready === true,
              }
            : null,
          elapsedMs: Date.now() - startedAt,
        });
      },
    );
  });
}
