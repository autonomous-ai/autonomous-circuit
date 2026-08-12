import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { register } from "node:module"
import { dirname, join, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath, pathToFileURL } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
register(
  new URL("./fixtures/export-fanout-autorouter-loader.mjs", import.meta.url),
  import.meta.url,
)

const coreUrl = pathToFileURL(
  process.env.TSCIRCUIT_CORE_PATH ??
    join(
      repoRoot,
      "toolchain",
      "node_modules",
      "@tscircuit",
      "core",
      "dist",
      "index.js",
    ),
)
const fixturePath = join(
  repoRoot,
  "scripts",
  "tests",
  "fixtures",
  "fanout-direction-retry.simple-route.json",
)

test("failed inferred fanout directions get a bounded automatic retry", async (t) => {
  let FanoutAutorouter
  try {
    ;({ FanoutAutorouter } = await import(coreUrl.href))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }
  const input = JSON.parse(await readFile(fixturePath, "utf8"))
  assert.equal(input.connections.length, 14)
  assert.ok(
    (input.buses ?? []).every(
      (bus) => bus.direction === undefined && bus.preferredExit === undefined,
    ),
    "fixture must exercise inference, not an authored direction map",
  )

  const solve = (busFanoutDirections) => {
    const boundsOptions = {
      mode: "fanout",
      fanoutBoundaryPadding: 1,
      componentNamesById: new Map(),
      ...(busFanoutDirections ? { busFanoutDirections } : {}),
    }
    const fanoutBounds = FanoutAutorouter.resolveFanoutBounds(
      input,
      boundsOptions,
    )
    const autorouter = new FanoutAutorouter(input, {
      ...boundsOptions,
      fanoutBounds,
    })
    return autorouter.solveSync()
  }

  // The pinned one-shot inference routes only 13/14. The guarded wrapper
  // retries the failed inferred bus and finds the complete 14-trace answer.
  assert.equal(solve().length, 14)

  // Authored intent remains authoritative: a deliberately wrong direction
  // is not silently changed by the automatic fallback.
  assert.throws(
    () => solve({ TR_J1_gnd1: "center_right" }),
    /only 13 of 14 connections/,
  )
  assert.equal(solve({ TR_J1_gnd1: "center_left" }).length, 14)
})
