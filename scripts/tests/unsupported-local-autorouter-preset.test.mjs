import assert from "node:assert/strict"
import { register } from "node:module"
import { dirname, join, resolve } from "node:path"
import test from "node:test"
import { fileURLToPath, pathToFileURL } from "node:url"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
register(
  new URL("./fixtures/export-fanout-autorouter-loader.mjs", import.meta.url),
  import.meta.url,
)

const corePath =
  process.env.TSCIRCUIT_CORE_PATH ??
  join(
    repoRoot,
    "toolchain",
    "node_modules",
    "@tscircuit",
    "core",
    "dist",
    "index.js",
  )

test("unknown local autorouter presets fail instead of silently selecting capacity routing", async (t) => {
  let getPresetAutoroutingConfig
  try {
    ;({ getPresetAutoroutingConfig } = await import(pathToFileURL(corePath).href))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  assert.deepEqual(getPresetAutoroutingConfig(undefined), {
    local: true,
    groupMode: "subcircuit",
  })
  assert.deepEqual(getPresetAutoroutingConfig("default"), {
    local: true,
    groupMode: "subcircuit",
  })
  assert.deepEqual(getPresetAutoroutingConfig("fanout"), {
    local: true,
    groupMode: "subcircuit",
    preset: "fanout",
  })

  for (const preset of ["freerouting", "krt", "made-up-router"]) {
    assert.throws(
      () => getPresetAutoroutingConfig(preset),
      new RegExp(
        `Unsupported autorouter preset "${preset}".*no local implementation`,
      ),
    )
  }

  const platformRouter = {
    createAutorouter: async () => ({ start() {} }),
  }
  const registered = getPresetAutoroutingConfig("freerouting", {
    autorouterMap: { freerouting: platformRouter },
  })
  assert.equal(registered.local, true)
  assert.equal(registered.groupMode, "subcircuit")
  assert.equal(typeof registered.algorithmFn, "function")
})
