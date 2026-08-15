/**
 * A seeded Math.random for the tscircuit CLI, injected with `--import`.
 *
 * WHY THIS IS A PRELOAD AND NOT A PATCHED PACKAGE
 *
 * `toolchain/node_modules/@tscircuit/capacity-autorouter` looks like the
 * router but is not the code that runs. `tscircuit-cli` executes
 * `@tscircuit/cli/dist/cli/main.js`, a 25.9 MB single-file bundle with the
 * router, `@tscircuit/core`, `circuit-json` and three separate copies of the
 * random-id generator compiled into it. It imports nothing from node_modules
 * (`grep -c '^import.*from"' main.js` -> 0), so editing the standalone
 * autorouter package changes nothing about a build. Verified 2026-08-15 on
 * @tscircuit/cli 0.1.1876.
 *
 * That leaves two honest options: fork a minified 25.9 MB bundle and re-patch
 * it on every version bump, or replace the one global the bundle's randomness
 * flows through. This is the second. It is smaller, it survives a version
 * bump, and it cannot silently half-apply.
 *
 * WHAT IT CHANGES
 *
 * `Math.random` only. Every id the bundle mints has the shape
 * `${prefix}_${10 chars picked with Math.floor(Math.random()*62)}`
 * (circuit-json's `getZodPrefixedIdWithDefault`; three copies at byte offsets
 * 6468871 / 9721664 / 9828839 of the bundle). Seeding the generator makes
 * every such id a pure function of the board's source hash.
 *
 * WHAT IT DELIBERATELY DOES NOT CHANGE
 *
 * The clock. `Date.now()` and `performance.now()` are read 3 times in
 * @tscircuit/core and 56 times in the router, and every one of them is
 * telemetry: `timeToSolve`, `timeSpentOnPhase`, an iterations-per-second
 * number for a progress event. The solver loop is
 * `while (!solved && !failed) step()` with `MAX_ITERATIONS = 100e6 * effort`
 * (BaseSolver.solve). Freezing the clock would buy no determinism and would
 * break real timeouts, so the clock is left alone.
 *
 * SAFETY
 *
 * The only other consumers of `Math.random` in the bundle that touch the
 * filesystem are `atomically`'s temp-file names, which are built from the
 * destination path plus `Date.now()` plus the random suffix. Two processes
 * with the same seed still write under different work directories
 * (`.circuit/build/<stem>-<pid>/`), so the full temp path stays unique.
 * Retry jitter, spinner choice and telemetry ids also become deterministic;
 * none of them is load-bearing.
 *
 * IT ANNOUNCES ITSELF, AND THAT IS THE POINT
 *
 * `NODE_OPTIONS` is a Node mechanism. `node_modules/.bin/tscircuit-cli` picks
 * its runner at startup — `commandExists("bun") ? "bun" : "tsx"` — and bun
 * does not read `NODE_OPTIONS`. bun is not installed on this machine
 * (verified 2026-08-16), so the preload applies today; the day someone
 * installs bun it would stop applying, silently, and every build would go
 * back to random ids while the pipeline still claimed to be seeding them.
 *
 * So seeding writes one line to stderr, always. `circuitpy.generation` greps
 * the captured build output for it and records `determinism.seeded` from what
 * it *saw*, never from what it asked for. A flag that reports intent instead
 * of effect is how a fix goes quietly missing.
 *
 * USE
 *
 *   NODE_OPTIONS="--import file:///…/deterministic-run.mjs" \
 *   CIRCUIT_DETERMINISTIC_SEED=<hex> tscircuit-cli build boards/main.tsx
 *
 * With no seed set, this file does nothing at all — importing it is never a
 * behaviour change on its own.
 */

/** The string `circuitpy.generation` greps for. Changing it is a contract change. */
export const DETERMINISM_MARKER = "[determinism] Math.random seeded"

const seed = process.env.CIRCUIT_DETERMINISTIC_SEED

if (seed) {
  // FNV-1a over the seed string, into four 32-bit words. Any seed string
  // works; the board pipeline passes the board's source fingerprint, so the
  // same source always gets the same stream and two different boards
  // effectively never do.
  const words = new Uint32Array(4)
  for (let w = 0; w < 4; w++) {
    let h = 0x811c9dc5 ^ (w * 0x9e3779b1)
    const salted = `${w}:${seed}`
    for (let i = 0; i < salted.length; i++) {
      h ^= salted.charCodeAt(i)
      h = Math.imul(h, 0x01000193) >>> 0
    }
    words[w] = h >>> 0
  }

  // sfc32 — small, fast, 128 bits of state, passes PractRand. The exact
  // generator does not matter here (nothing depends on statistical quality);
  // what matters is that it is pure and has no global state but its own.
  let a = words[0] || 1
  let b = words[1] || 2
  let c = words[2] || 3
  let d = words[3] || 4

  Math.random = function seededRandom() {
    a >>>= 0
    b >>>= 0
    c >>>= 0
    d >>>= 0
    let t = (a + b) | 0
    a = b ^ (b >>> 9)
    b = (c + (c << 3)) | 0
    c = (c << 21) | (c >>> 11)
    d = (d + 1) | 0
    t = (t + d) | 0
    c = (c + t) | 0
    return (t >>> 0) / 4294967296
  }

  // Always, not behind a debug flag: this line is the only evidence the
  // preload reached the process that did the work.
  process.stderr.write(
    `${DETERMINISM_MARKER} from ${seed.slice(0, 12)} (pid ${process.pid})\n`,
  )
}
