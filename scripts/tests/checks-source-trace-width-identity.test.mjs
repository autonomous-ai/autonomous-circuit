import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import test from "node:test"

import {
  TOOLCHAIN_PATCHES,
  applyToolchainPatch,
} from "../build/apply-toolchain-patches.mjs"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
const toolchainDir =
  process.env.TSCIRCUIT_TOOLCHAIN_DIR ?? join(repoRoot, "toolchain")
const digest = (value) => createHash("sha256").update(value).digest("hex")

const fixture = () => [
  {
    type: "source_group",
    source_group_id: "source_group_0",
    is_subcircuit: true,
    subcircuit_id: "subcircuit_0",
  },
  ...["a", "b", "c"].flatMap((id) => [
    {
      type: "source_component",
      source_component_id: `source_component_${id}`,
      name: id.toUpperCase(),
      ftype: "simple_chip",
      subcircuit_id: "subcircuit_0",
    },
    {
      type: "source_port",
      source_port_id: `source_port_${id}`,
      source_component_id: `source_component_${id}`,
      name: "pin1",
      pin_number: "1",
      subcircuit_id: "subcircuit_0",
    },
  ]),
  ...["a", "b", "c"].map((id, index) => ({
    type: "pcb_port",
    pcb_port_id: `pcb_port_${id}`,
    source_port_id: `source_port_${id}`,
    pcb_component_id: `pcb_component_${id}`,
    x: index,
    y: 0,
    layers: ["top"],
  })),
  {
    type: "source_trace",
    source_trace_id: "source_trace_neck",
    name: "TR_NECK",
    connected_source_port_ids: ["source_port_a", "source_port_b"],
    connected_source_net_ids: [],
    min_trace_thickness: 0.2,
    subcircuit_id: "subcircuit_0",
    subcircuit_connectivity_map_key: "mixed_width_tree",
  },
  {
    type: "source_trace",
    source_trace_id: "source_trace_trunk",
    name: "TR_TRUNK",
    connected_source_port_ids: ["source_port_b", "source_port_c"],
    connected_source_net_ids: [],
    min_trace_thickness: 0.8,
    subcircuit_id: "subcircuit_0",
    subcircuit_connectivity_map_key: "mixed_width_tree",
  },
  {
    type: "pcb_trace",
    pcb_trace_id: "pcb_trace_neck",
    source_trace_id: "source_trace_neck",
    route: [
      {
        route_type: "wire",
        x: 0,
        y: 0,
        width: 0.2,
        layer: "top",
        start_pcb_port_id: "pcb_port_a",
      },
      {
        route_type: "wire",
        x: 1,
        y: 0,
        width: 0.2,
        layer: "top",
        end_pcb_port_id: "pcb_port_b",
      },
    ],
  },
  {
    type: "pcb_trace",
    pcb_trace_id: "pcb_trace_trunk",
    source_trace_id: "source_trace_trunk",
    route: [
      {
        route_type: "wire",
        x: 1,
        y: 0,
        width: 0.8,
        layer: "top",
        start_pcb_port_id: "pcb_port_b",
      },
      {
        route_type: "wire",
        x: 2,
        y: 0,
        width: 0.8,
        layer: "top",
        end_pcb_port_id: "pcb_port_c",
      },
    ],
  },
]

const warningsForTrunk = (checks, circuit) =>
  checks.checkSourceTracesMatchPcbTraceThickness(circuit).filter(
    (warning) => warning.source_trace_id === "source_trace_trunk",
  )

test("mixed-width authored trees use exact source_trace_id and retain fail-closed fallback", async (t) => {
  const patch = TOOLCHAIN_PATCHES.find(
    (candidate) =>
      candidate.packageName === "@tscircuit/checks" &&
      candidate.replacements.some((replacement) =>
        replacement.label.includes("exact authored route identity"),
      ),
  )
  assert.ok(patch, "checks identity patch must be present in the manifest")

  const root = await mkdtemp(join(tmpdir(), "checks-width-identity-test-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  const sourceNodeModules = join(toolchainDir, "node_modules")
  const nodeModules = join(root, "node_modules")
  const packageDir = join(nodeModules, "@tscircuit", "checks")
  await mkdir(join(nodeModules, "@tscircuit"), { recursive: true })
  await cp(join(sourceNodeModules, "@tscircuit", "checks"), packageDir, {
    recursive: true,
  })

  for (const packageName of ["math-utils", "circuit-json-util"]) {
    await symlink(
      join(sourceNodeModules, "@tscircuit", packageName),
      join(nodeModules, "@tscircuit", packageName),
      "dir",
    )
  }
  for (const packageName of [
    "@flatten-js",
    "circuit-json",
    "circuit-json-to-connectivity-map",
    "transformation-matrix",
  ]) {
    await symlink(
      join(sourceNodeModules, packageName),
      join(nodeModules, packageName),
      "dir",
    )
  }

  const distPath = join(packageDir, patch.file)
  let pristine = await readFile(distPath, "utf8")
  if (digest(pristine) === patch.patchedSha256) {
    for (const replacement of [...patch.replacements].reverse()) {
      pristine = pristine.split(replacement.after).join(replacement.before)
    }
  }
  assert.equal(digest(pristine), patch.pristineSha256)
  await writeFile(distPath, pristine)

  const before = await import(`${pathToFileURL(distPath).href}?before=${Date.now()}`)
  assert.equal(
    warningsForTrunk(before, fixture()).length,
    1,
    "upstream incorrectly attributes the 0.2mm neck to the exact 0.8mm trunk",
  )

  assert.deepEqual(await applyToolchainPatch(nodeModules, patch, false), {
    packageName: "@tscircuit/checks",
    status: "patched",
  })
  assert.equal(digest(await readFile(distPath, "utf8")), patch.patchedSha256)
  assert.deepEqual(await applyToolchainPatch(nodeModules, patch, false), {
    packageName: "@tscircuit/checks",
    status: "already-patched",
  })

  const after = await import(`${pathToFileURL(distPath).href}?after=${Date.now()}`)
  assert.deepEqual(warningsForTrunk(after, fixture()), [])

  const identityAbsent = fixture()
  delete identityAbsent.find(
    (element) => element.pcb_trace_id === "pcb_trace_trunk",
  ).source_trace_id
  const fallbackWarnings = warningsForTrunk(after, identityAbsent)
  assert.equal(fallbackWarnings.length, 1)
  assert.match(fallbackWarnings[0].message, /actual: 0\.2mm/)

  const duplicate = fixture()
  duplicate.push({
    type: "pcb_trace",
    pcb_trace_id: "pcb_trace_trunk_duplicate",
    source_trace_id: "source_trace_trunk",
    route: [
      { route_type: "wire", x: 1, y: 0, width: 0.6, layer: "top" },
      { route_type: "wire", x: 1.5, y: 0, width: 0.6, layer: "top" },
    ],
  })
  const duplicateWarnings = warningsForTrunk(after, duplicate)
  assert.equal(duplicateWarnings.length, 1)
  assert.match(duplicateWarnings[0].message, /actual: 0\.6mm/)
})
