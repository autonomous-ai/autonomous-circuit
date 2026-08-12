import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"
import test from "node:test"

import {
  TOOLCHAIN_PATCHES,
  applyAllToolchainPatches,
  applyToolchainPatch,
} from "../build/apply-toolchain-patches.mjs"

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
const toolchainDir =
  process.env.TSCIRCUIT_TOOLCHAIN_DIR ?? join(repoRoot, "toolchain")
const digest = (value) =>
  createHash("sha256").update(value).digest("hex")

const replaceExact = (source, before, after, expectedMatches, label) => {
  const actualMatches = source.split(before).length - 1
  assert.equal(
    actualMatches,
    expectedMatches,
    `${label}: expected ${expectedMatches} exact byte match(es), found ${actualMatches}`,
  )
  return source.split(before).join(after)
}

const reverseReplacement = (source, replacement, packageName) => {
  const expectedMatches = replacement.expectedMatches ?? 1
  const label = `${packageName}: reverse ${replacement.label}`
  if (
    replacement.scopeStart !== undefined ||
    replacement.scopeEnd !== undefined
  ) {
    assert.equal(typeof replacement.scopeStart, "string", `${label}: scopeStart`)
    assert.equal(typeof replacement.scopeEnd, "string", `${label}: scopeEnd`)
    const startMatches = source.split(replacement.scopeStart).length - 1
    const endMatches = source.split(replacement.scopeEnd).length - 1
    assert.equal(startMatches, 1, `${label}: scopeStart must be unique`)
    assert.equal(endMatches, 1, `${label}: scopeEnd must be unique`)
    const scopeStart = source.indexOf(replacement.scopeStart)
    const scopeEnd = source.indexOf(replacement.scopeEnd, scopeStart)
    assert.ok(scopeEnd >= scopeStart, `${label}: scope end precedes start`)
    const scoped = source.slice(scopeStart, scopeEnd)
    return (
      source.slice(0, scopeStart) +
      replaceExact(
        scoped,
        replacement.after,
        replacement.before,
        expectedMatches,
        label,
      ) +
      source.slice(scopeEnd)
    )
  }
  return replaceExact(
    source,
    replacement.after,
    replacement.before,
    expectedMatches,
    label,
  )
}

const reversePatch = (source, patch) => {
  let pristine = source
  for (const replacement of [...patch.replacements].reverse()) {
    pristine = reverseReplacement(pristine, replacement, patch.packageName)
  }
  assert.equal(
    digest(pristine),
    patch.pristineSha256,
    `${patch.packageName}: reverse reconstruction missed the guarded predecessor`,
  )
  return pristine
}

test("checked patching is exact, atomic, guarded, and idempotent", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "toolchain-patch-test-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  const packageDir = join(root, "node_modules", "@example", "router")
  await mkdir(join(packageDir, "dist"), { recursive: true })
  await writeFile(
    join(packageDir, "package.json"),
    JSON.stringify({ name: "@example/router", version: "1.2.3" }),
  )
  await writeFile(join(packageDir, "dist", "index.js"), "before\nbefore\n")
  await writeFile(
    join(packageDir, "dist", "index.js.map"),
    JSON.stringify({ sources: ["../src/a.ts"], sourcesContent: ["audited source"] }),
  )

  const patch = {
    packageName: "@example/router",
    version: "1.2.3",
    file: "dist/index.js",
    pristineSha256: digest("before\nbefore\n"),
    patchedSha256: digest("after\nafter\n"),
    sourceMap: "dist/index.js.map",
    sourceGuards: [{ source: "../src/a.ts", contains: "audited source" }],
    replacements: [
      {
        label: "fixture edit",
        before: "before",
        after: "after",
        expectedMatches: 2,
      },
    ],
  }

  assert.deepEqual(await applyToolchainPatch(join(root, "node_modules"), patch, false), {
    packageName: "@example/router",
    status: "patched",
  })
  assert.equal(
    await readFile(join(packageDir, "dist", "index.js"), "utf8"),
    "after\nafter\n",
  )
  assert.deepEqual(await applyToolchainPatch(join(root, "node_modules"), patch, false), {
    packageName: "@example/router",
    status: "already-patched",
  })
  assert.deepEqual(await applyToolchainPatch(join(root, "node_modules"), patch, true), {
    packageName: "@example/router",
    status: "already-patched",
  })

  await writeFile(join(packageDir, "dist", "index.js"), "tampered\n")
  await assert.rejects(
    applyToolchainPatch(join(root, "node_modules"), patch, false),
    /SHA-256/,
  )
})

test("compiled replacements can be restricted to one audited class scope", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "toolchain-scoped-patch-test-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  const packageDir = join(root, "node_modules", "@example", "router")
  await mkdir(join(packageDir, "dist"), { recursive: true })
  await writeFile(
    join(packageDir, "package.json"),
    JSON.stringify({ name: "@example/router", version: "1.2.3" }),
  )
  const pristine = "class-A{before}\nclass-B{before}\n"
  const patched = "class-A{after}\nclass-B{before}\n"
  await writeFile(join(packageDir, "dist", "index.js"), pristine)

  const patch = {
    packageName: "@example/router",
    version: "1.2.3",
    file: "dist/index.js",
    pristineSha256: digest(pristine),
    patchedSha256: digest(patched),
    replacements: [
      {
        label: "only class A changes",
        scopeStart: "class-A{",
        scopeEnd: "}\nclass-B",
        before: "before",
        after: "after",
      },
    ],
  }

  await applyToolchainPatch(join(root, "node_modules"), patch, false)
  assert.equal(await readFile(join(packageDir, "dist", "index.js"), "utf8"), patched)
})

test("the complete capacity chain replays from pristine bytes after a restart", async (t) => {
  const capacityPatches = TOOLCHAIN_PATCHES.filter(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.file === "dist/index.js",
  )
  assert.ok(capacityPatches.length > 1)
  for (let index = 1; index < capacityPatches.length; index += 1) {
    assert.equal(
      capacityPatches[index - 1].patchedSha256,
      capacityPatches[index].pristineSha256,
      `capacity stages ${index - 1}/${index} do not form one exact chain`,
    )
  }

  const installedPackageDir = join(
    toolchainDir,
    "node_modules",
    "@tscircuit",
    "capacity-autorouter",
  )
  let installedSource
  let packageJson
  let sourceMap
  try {
    ;[installedSource, packageJson, sourceMap] = await Promise.all([
      readFile(join(installedPackageDir, "dist", "index.js"), "utf8"),
      readFile(join(installedPackageDir, "package.json"), "utf8"),
      readFile(join(installedPackageDir, "dist", "index.js.map"), "utf8"),
    ])
  } catch (error) {
    if (String(error).includes("ENOENT")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  // Tests normally see the final installed endpoint. A developer may also
  // invoke this immediately after npm ci (pristine) or after an interrupted
  // patch run (an intermediate endpoint). Reconstruct the exact npm bytes by
  // reversing only the stages already present, then replay the whole chain in
  // a private package. This makes a one-byte manifest-payload edit fail here,
  // even when the shared install still carries yesterday's valid endpoint.
  let pristineSource = installedSource
  const installedSha256 = digest(installedSource)
  if (installedSha256 !== capacityPatches[0].pristineSha256) {
    const installedStage = capacityPatches.findIndex(
      (patch) => patch.patchedSha256 === installedSha256,
    )
    assert.notEqual(
      installedStage,
      -1,
      `installed capacity bundle has unknown SHA-256 ${installedSha256}`,
    )
    for (let index = installedStage; index >= 0; index -= 1) {
      pristineSource = reversePatch(pristineSource, capacityPatches[index])
    }
  }
  assert.equal(digest(pristineSource), capacityPatches[0].pristineSha256)

  const root = await mkdtemp(join(tmpdir(), "capacity-chain-restart-"))
  t.after(() => rm(root, { recursive: true, force: true }))
  const packageDir = join(
    root,
    "node_modules",
    "@tscircuit",
    "capacity-autorouter",
  )
  await mkdir(join(packageDir, "dist"), { recursive: true })
  await Promise.all([
    writeFile(join(packageDir, "package.json"), packageJson),
    writeFile(join(packageDir, "dist", "index.js"), pristineSource),
    writeFile(join(packageDir, "dist", "index.js.map"), sourceMap),
  ])

  for (const patch of capacityPatches) {
    const result = await applyToolchainPatch(
      join(root, "node_modules"),
      patch,
      false,
    )
    assert.equal(result.status, "patched")
    assert.equal(
      digest(await readFile(join(packageDir, "dist", "index.js"), "utf8")),
      patch.patchedSha256,
      `${patch.packageName}: ${patch.replacements[0]?.label ?? "stage"}`,
    )
  }

  for (const patch of capacityPatches) {
    assert.equal(
      (await applyToolchainPatch(join(root, "node_modules"), patch, true)).status,
      "already-patched",
    )
  }
})

test("the installed pinned toolchain carries every required patch", async (t) => {
  try {
    const results = await applyAllToolchainPatches({
      toolchainDir,
      checkOnly: true,
    })
    assert.equal(results.length, TOOLCHAIN_PATCHES.length)
    assert.ok(results.every((result) => result.status === "already-patched"))
  } catch (error) {
    if (String(error).includes("ENOENT")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }
})

test("patch manifest pins the audited routing inputs and fail-closed gates", () => {
  const propsRuntime = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/props" &&
      patch.file === "dist/index.js" &&
      patch.replacements.some((item) =>
        item.after.includes("authoredNetTreeBoundary"),
      ),
  )
  const propsTypes = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/props" &&
      patch.file === "dist/index.d.ts" &&
      patch.replacements.some((item) => item.expectedMatches === 3),
  )
  const capacity = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) => item.after.includes("failOnUnresolvedDrc")),
  )
  const capacityDynamicConnectivity = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.label.includes("connectivity identities"),
      ),
  )
  const capacityPreloadedTraceExactDrc = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.after.includes('startsWith("trace_obstacle_")'),
      ),
  )
  const capacityThroughObstacleDrc = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.label.includes("through-obstacle copper"),
      ),
  )
  const capacityAuthoredTreeTopology = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.after.includes("__preserveConnectionTopology"),
      ),
  )
  const capacityDifferentialPair = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.after.includes("acValidateDifferentialPairPostProcessing"),
      ),
  )
  const capacityViaInSmdPad = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.label.includes("exact DRC rejects vias inside physical SMD"),
      ),
  )
  const capacityDifferentialPairZeroLengthEdge = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.label.includes("directionless planar edges"),
      ),
  )
  const capacityExplicitTraceWidth = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.label.includes("single hard-minimum target"),
      ),
  )
  const capacityLayerReversalRetry = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/capacity-autorouter" &&
      patch.replacements.some((item) =>
        item.after.includes("p7-layer-reversal-v1:"),
      ),
  )
  const core = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) => item.after.includes("routingConfig")),
  )
  const coreFanout = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) => item.after.includes("retryCandidateCount")),
  )
  const coreRegion = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes(
          "plan.routingBounds = phaseProps?.reroute ? void 0 : phaseProps?.region",
        ),
      ),
  )
  const coreUnknownPreset = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("Unsupported autorouter preset"),
      ),
  )
  const coreManualTrace = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("currentSubcircuitPreservedSourceTraceIds"),
      ),
  )
  const corePlaneTerminatedNet = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("planeTerminatedNetNames"),
      ),
  )
  const coreSameLayerPlaneTermination = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("is_inside_copper_pour = true"),
      ),
  )
  const coreManualPcbPathViaRules = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.label.includes("manual pcbPath vias inherit"),
      ),
  )
  const coreAuthoredTree = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("Group_applyAuthoredNetTreeContracts"),
      ),
  )
  const coreDecouplingMaxLength = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.label.includes("cap-to-device branches"),
      ),
  )
  const coreDifferentialPair = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("throwDifferentialPairSourceContractError"),
      ),
  )
  const coreRoutedTraceViaStyle = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("resolveRoutedViaDimensions"),
      ),
  )
  const coreAggregateRouteIdentity = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("db.source_net.get(trace.connection_name)"),
      ),
  )
  const coreDifferentialPairTraceEndpoints = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.label.includes("trace selectors validate their own physical endpoints"),
      ),
  )
  const coreViaInSmdPadOutputGate = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("Group_assertNoIllegalViaInSmdPad"),
      ),
  )
  const coreDifferentialPairPhasedTraceSelection = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes("traceSourceTraceId: positiveTraceSelection.sourceTraceId"),
      ),
  )
  const coreLayerReversalRetryCacheIdentity = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/core" &&
      patch.replacements.some((item) =>
        item.after.includes(
          'capacityLayerReversalRetry: "p7-layer-reversal-v1"',
        ),
      ),
  )
  const checksSourceTraceWidthIdentity = TOOLCHAIN_PATCHES.find(
    (patch) =>
      patch.packageName === "@tscircuit/checks" &&
      patch.replacements.some((item) =>
        item.label.includes("exact authored route identity"),
      ),
  )
  assert.ok(propsRuntime)
  assert.ok(propsTypes)
  assert.ok(capacity)
  assert.ok(capacityDynamicConnectivity)
  assert.ok(capacityPreloadedTraceExactDrc)
  assert.ok(capacityThroughObstacleDrc)
  assert.ok(capacityAuthoredTreeTopology)
  assert.ok(capacityDifferentialPair)
  assert.ok(capacityViaInSmdPad)
  assert.ok(capacityDifferentialPairZeroLengthEdge)
  assert.ok(capacityExplicitTraceWidth)
  assert.ok(capacityLayerReversalRetry)
  assert.ok(core)
  assert.ok(coreFanout)
  assert.ok(coreRegion)
  assert.ok(coreUnknownPreset)
  assert.ok(coreManualTrace)
  assert.ok(corePlaneTerminatedNet)
  assert.ok(coreSameLayerPlaneTermination)
  assert.ok(coreManualPcbPathViaRules)
  assert.ok(coreAuthoredTree)
  assert.ok(coreDecouplingMaxLength)
  assert.ok(coreDifferentialPair)
  assert.ok(coreRoutedTraceViaStyle)
  assert.ok(coreAggregateRouteIdentity)
  assert.ok(coreDifferentialPairTraceEndpoints)
  assert.ok(coreViaInSmdPadOutputGate)
  assert.ok(coreDifferentialPairPhasedTraceSelection)
  assert.ok(coreLayerReversalRetryCacheIdentity)
  assert.ok(checksSourceTraceWidthIdentity)
  assert.equal(checksSourceTraceWidthIdentity.version, "0.0.152")
  assert.equal(
    checksSourceTraceWidthIdentity.sourceGuards[0].source,
    "../lib/check-source-traces-match-pcb-trace-thickness.ts",
  )
  assert.match(
    checksSourceTraceWidthIdentity.replacements[0].after,
    /source_trace_id === sourceTrace\.source_trace_id/,
  )
  const capacityOutput = capacity.replacements.map((item) => item.after).join("\n")
  assert.match(capacityOutput, /minTraceToPadEdgeClearance/)
  assert.match(capacityOutput, /minViaEdgeToPadEdgeClearance/)
  assert.match(capacityOutput, /failOnUnresolvedDrc/)
  assert.match(capacityOutput, /Pipeline9 final repair/)

  const coreOutput = core.replacements.map((item) => item.after).join("\n")
  for (const required of [
    "capacity@${autorouterVersion}",
    "autorouterVersion",
    "effort",
    "capacityDepth",
    "targetMinCapacity",
    "traceClearance",
    "minTraceToPadEdgeClearance",
    "minViaEdgeToPadEdgeClearance",
  ]) {
    assert.ok(coreOutput.includes(required), required)
  }
  const fanoutOutput = coreFanout.replacements.map((item) => item.after).join("\n")
  assert.ok(fanoutOutput.includes("retryCandidateCount < 32"))
  assert.ok(fanoutOutput.includes("explicitDirectionBusIds.has(busId)"))
  assert.ok(
    fanoutOutput.includes(
      "candidateSummary.routedConnectionCount <= currentSummary.routedConnectionCount",
    ),
  )

  const regionOutput = coreRegion.replacements.map((item) => item.after).join("\n")
  assert.ok(
    regionOutput.includes(
      "plan.routingBounds = phaseProps?.reroute ? void 0 : phaseProps?.region",
    ),
  )
  const unknownPresetOutput = coreUnknownPreset.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(unknownPresetOutput.includes("normalizedPreset !== void 0"))
  assert.ok(unknownPresetOutput.includes("platform.autorouterMap"))

  const manualTraceOutput = coreManualTrace.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(manualTraceOutput.includes("trace._parsedProps.pcbPath !== void 0"))
  assert.ok(manualTraceOutput.includes("trace._parsedProps.pcbStraightLine === true"))
  assert.ok(
    manualTraceOutput.includes(
      "preservedRoutedSubcircuitTraces.map((trace) => trace.source_trace_id)",
    ),
  )

  const dynamicConnectivityOutput = capacityDynamicConnectivity.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(dynamicConnectivityOutput.includes("const t=i.connection_name"))

  const preloadedTraceExactDrcOutput =
    capacityPreloadedTraceExactDrc.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(
    preloadedTraceExactDrcOutput.includes(
      'obstacleId?.startsWith("trace_obstacle_")',
    ),
  )
  const throughObstacleDrcOutput = capacityThroughObstacleDrc.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(throughObstacleDrcOutput.includes("(?:wire|via)"))
  assert.ok(throughObstacleDrcOutput.includes("_approx_"))

  const authoredTreeTopologyOutput =
    capacityAuthoredTreeTopology.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(authoredTreeTopologyOutput.includes("__preserveConnectionTopology"))
  assert.ok(authoredTreeTopologyOutput.includes("e.filter"))

  const differentialPairOutput = capacityDifferentialPair.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(differentialPairOutput.includes("maxUncoupledLength"))
  assert.ok(differentialPairOutput.includes("acGetTotalUncoupledLength"))
  assert.ok(differentialPairOutput.includes("postProcessingErrors"))
  assert.ok(differentialPairOutput.includes("drcEvaluator"))
  assert.ok(differentialPairOutput.includes("inputNewHdRoutes"))

  const viaInSmdPadOutput = capacityViaInSmdPad.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(viaInSmdPadOutput.includes("checkViaObstacles"))
  assert.ok(viaInSmdPadOutput.includes("pcb_smtpad_center"))
  assert.ok(viaInSmdPadOutput.includes("minViaEdgeToPadEdgeClearance") ||
    viaInSmdPadOutput.includes("this.viaClearance"))
  assert.ok(viaInSmdPadOutput.includes("this.srj.allowViaInPad===!0"))

  const zeroLengthEdgeOutput =
    capacityDifferentialPairZeroLengthEdge.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(zeroLengthEdgeOutput.includes("Math.hypot"))
  assert.ok(zeroLengthEdgeOutput.includes("<=1e-10"))

  const explicitTraceWidthOutput = capacityExplicitTraceWidth.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(explicitTraceWidthOutput.includes("Math.max"))
  assert.ok(explicitTraceWidthOutput.includes("cannot satisfy explicit minimum"))
  assert.ok(explicitTraceWidthOutput.includes("traceThickness:e"))
  assert.ok(!explicitTraceWidthOutput.includes("finalizeCurrentTrace(this.minTraceWidth)"))

  const layerReversalRetryOutput =
    capacityLayerReversalRetry.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(layerReversalRetryOutput.includes("__disableLayerReversalRetry"))
  assert.ok(layerReversalRetryOutput.includes("p7-layer-reversal-v1:"))
  assert.ok(layerReversalRetryOutput.includes("acReverseP7Options"))
  assert.ok(layerReversalRetryOutput.includes('key === "zLayers"'))
  assert.ok(layerReversalRetryOutput.includes('key === "availableZ"'))
  assert.ok(layerReversalRetryOutput.includes("layerReversalRetrySolver?.solved"))
  assert.ok(
    layerReversalRetryOutput.includes(
      "failed in the original orientation",
    ),
  )

  const planeTerminatedNetOutput = corePlaneTerminatedNet.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(planeTerminatedNetOutput.includes("fanoutPourNetMap"))
  assert.ok(
    planeTerminatedNetOutput.includes(
      "if (planeTerminatedNetNames.has(net.name)) continue",
    ),
  )

  const sameLayerPlaneTerminationOutput =
    coreSameLayerPlaneTermination.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(
    sameLayerPlaneTerminationOutput.includes(
      "plan.trace.route[0].is_inside_copper_pour = true",
    ),
  )
  assert.ok(sameLayerPlaneTerminationOutput.includes("plan.segments = []"))
  assert.ok(
    sameLayerPlaneTerminationOutput.includes("planeLayersBySourceNetId"),
  )
  assert.ok(
    sameLayerPlaneTerminationOutput.includes("pcbPorts: db.pcb_port.list()"),
  )
  assert.ok(
    sameLayerPlaneTerminationOutput.includes(
      "source_trace_id: preparedConnection.connection.source_trace_id",
    ),
  )

  const manualPcbPathViaOutput = coreManualPcbPathViaRules.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(manualPcbPathViaOutput.includes("minimumViaHoleDiameter"))
  assert.ok(manualPcbPathViaOutput.includes("minimumViaPadDiameter"))
  assert.ok(manualPcbPathViaOutput.includes("point6.via_hole_diameter"))
  assert.ok(manualPcbPathViaOutput.includes("point6.via_diameter"))

  const authoredTreeOutput = coreAuthoredTree.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(authoredTreeOutput.includes("authoredNetTreeBoundary === true"))
  assert.ok(authoredTreeOutput.includes("componentPortOnlyEdges.size"))
  assert.ok(authoredTreeOutput.includes("marked subtree must have exactly one"))
  assert.ok(authoredTreeOutput.includes("__preserveConnectionTopology: true"))
  assert.ok(authoredTreeOutput.includes("pointsToConnect.length <= 1"))
  assert.ok(authoredTreeOutput.includes("pcb_autorouting_error.insert"))

  const decouplingMaxLengthOutput = coreDecouplingMaxLength.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(decouplingMaxLengthOutput.includes("ports.length !== 2"))
  assert.ok(decouplingMaxLengthOutput.includes("capacitorComponents.length !== 1"))
  assert.ok(decouplingMaxLengthOutput.includes("planeTerminatedSourceTraceIds"))
  assert.ok(decouplingMaxLengthOutput.includes("fanoutPourNetMap"))

  const coreDifferentialPairOutput = coreDifferentialPair.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(coreDifferentialPairOutput.includes("pcb_autorouting_error.insert"))
  assert.ok(coreDifferentialPairOutput.includes("direct two-port source trace"))
  assert.ok(coreDifferentialPairOutput.includes("named-net aggregate"))
  assert.ok(
    coreDifferentialPairOutput.includes(
      "declares maxUncoupledLength without pcbTraceGap",
    ),
  )

  const routedTraceViaStyleOutput = coreRoutedTraceViaStyle.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(routedTraceViaStyleOutput.includes("traceComponentBySourceTraceId"))
  assert.ok(routedTraceViaStyleOutput.includes("minimumViaHoleDiameter"))
  assert.ok(routedTraceViaStyleOutput.includes("minimumViaPadDiameter"))
  assert.ok(routedTraceViaStyleOutput.includes("getInheritedMergedProperty"))
  assert.ok(routedTraceViaStyleOutput.includes("via_hole_diameter"))
  assert.ok(routedTraceViaStyleOutput.includes("outer_diameter"))

  const aggregateRouteIdentityOutput =
    coreAggregateRouteIdentity.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(aggregateRouteIdentityOutput.includes("trace.connection_name"))
  assert.ok(aggregateRouteIdentityOutput.includes("db.source_net.get"))
  assert.ok(aggregateRouteIdentityOutput.includes("return void 0"))

  const differentialPairTraceEndpointOutput =
    coreDifferentialPairTraceEndpoints.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(
    differentialPairTraceEndpointOutput.includes(
      "selectedSourceTrace.connected_source_port_ids",
    ),
  )
  assert.ok(differentialPairTraceEndpointOutput.includes("db.source_port.get"))

  const coreViaInSmdPadOutput = coreViaInSmdPadOutputGate.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(coreViaInSmdPadOutput.includes("minViaEdgeToPadEdgeClearance"))
  assert.ok(coreViaInSmdPadOutput.includes("allowViaInPad === true"))
  assert.ok(coreViaInSmdPadOutput.includes("The via and pad are not connected"))
  assert.ok(coreViaInSmdPadOutput.includes("traces = await routingPromise"))
  assert.ok(
    coreViaInSmdPadOutput.includes(
      "allowViaInPad: simpleRouteJson.allowViaInPad ?? false",
    ),
  )

  const phasedTraceSelectionOutput =
    coreDifferentialPairPhasedTraceSelection.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(phasedTraceSelectionOutput.includes("sourceTraceId"))
  assert.ok(
    phasedTraceSelectionOutput.includes(
      "srjConnection2.source_trace_id === traceSourceTraceId",
    ),
  )
  assert.ok(
    !phasedTraceSelectionOutput.includes("differentialPairSourceTraceIds"),
  )

  const layerReversalCacheOutput =
    coreLayerReversalRetryCacheIdentity.replacements
      .map((item) => item.after)
      .join("\n")
  assert.ok(
    layerReversalCacheOutput.includes(
      'capacityLayerReversalRetry: "p7-layer-reversal-v1"',
    ),
  )

  const propsRuntimeOutput = propsRuntime.replacements
    .map((item) => item.after)
    .join("\n")
  assert.ok(propsRuntimeOutput.includes("z70.boolean().optional()"))
  assert.ok(
    propsTypes.replacements.every((replacement) =>
      replacement.expectedMatches === 3,
    ),
  )

  assert.equal(capacity.patchedSha256, capacityDynamicConnectivity.pristineSha256)
  assert.ok(
    capacity.successorSha256s.includes(
      capacityPreloadedTraceExactDrc.patchedSha256,
    ),
  )
  assert.equal(
    capacityDynamicConnectivity.patchedSha256,
    capacityPreloadedTraceExactDrc.pristineSha256,
  )
  assert.equal(
    capacityPreloadedTraceExactDrc.patchedSha256,
    capacityThroughObstacleDrc.pristineSha256,
  )
  assert.equal(
    capacityThroughObstacleDrc.patchedSha256,
    capacityAuthoredTreeTopology.pristineSha256,
  )
  assert.equal(
    capacityAuthoredTreeTopology.patchedSha256,
    capacityDifferentialPair.pristineSha256,
  )
  assert.ok(
    capacityAuthoredTreeTopology.successorSha256s.includes(
      capacityDifferentialPair.patchedSha256,
    ),
  )
  assert.equal(
    capacityDifferentialPair.patchedSha256,
    capacityViaInSmdPad.pristineSha256,
  )
  assert.equal(
    capacityViaInSmdPad.patchedSha256,
    capacityDifferentialPairZeroLengthEdge.pristineSha256,
  )
  assert.equal(
    capacityDifferentialPairZeroLengthEdge.patchedSha256,
    capacityExplicitTraceWidth.pristineSha256,
  )
  assert.equal(
    capacityExplicitTraceWidth.patchedSha256,
    capacityLayerReversalRetry.pristineSha256,
  )

  assert.equal(coreRegion.patchedSha256, coreUnknownPreset.pristineSha256)
  assert.ok(coreRegion.successorSha256s.includes(coreUnknownPreset.patchedSha256))
  assert.equal(coreUnknownPreset.patchedSha256, coreManualTrace.pristineSha256)
  assert.ok(
    coreUnknownPreset.successorSha256s.includes(coreManualTrace.patchedSha256),
  )
  assert.equal(
    coreManualTrace.patchedSha256,
    corePlaneTerminatedNet.pristineSha256,
  )
  assert.equal(
    corePlaneTerminatedNet.patchedSha256,
    coreSameLayerPlaneTermination.pristineSha256,
  )
  assert.ok(
    corePlaneTerminatedNet.successorSha256s.includes(
      coreSameLayerPlaneTermination.patchedSha256,
    ),
  )
  assert.equal(
    coreSameLayerPlaneTermination.patchedSha256,
    coreManualPcbPathViaRules.pristineSha256,
  )
  assert.ok(
    coreSameLayerPlaneTermination.successorSha256s.includes(
      coreManualPcbPathViaRules.patchedSha256,
    ),
  )
  assert.equal(
    coreManualPcbPathViaRules.patchedSha256,
    coreAuthoredTree.pristineSha256,
  )
  assert.equal(
    coreAuthoredTree.patchedSha256,
    coreDecouplingMaxLength.pristineSha256,
  )
  assert.ok(
    coreAuthoredTree.successorSha256s.includes(
      coreDecouplingMaxLength.patchedSha256,
    ),
  )
  assert.equal(
    coreDecouplingMaxLength.patchedSha256,
    coreDifferentialPair.pristineSha256,
  )
  assert.ok(
    coreDecouplingMaxLength.successorSha256s.includes(
      coreDifferentialPair.patchedSha256,
    ),
  )
  assert.equal(
    coreDifferentialPair.patchedSha256,
    coreRoutedTraceViaStyle.pristineSha256,
  )
  assert.ok(
    coreDifferentialPair.successorSha256s.includes(
      coreRoutedTraceViaStyle.patchedSha256,
    ),
  )
  assert.equal(
    coreRoutedTraceViaStyle.patchedSha256,
    coreAggregateRouteIdentity.pristineSha256,
  )
  assert.ok(
    coreRoutedTraceViaStyle.successorSha256s.includes(
      coreAggregateRouteIdentity.patchedSha256,
    ),
  )
  assert.equal(
    coreAggregateRouteIdentity.patchedSha256,
    coreDifferentialPairTraceEndpoints.pristineSha256,
  )
  assert.equal(
    coreDifferentialPairTraceEndpoints.patchedSha256,
    coreViaInSmdPadOutputGate.pristineSha256,
  )
  assert.equal(
    coreViaInSmdPadOutputGate.patchedSha256,
    coreDifferentialPairPhasedTraceSelection.pristineSha256,
  )
  assert.equal(
    coreDifferentialPairPhasedTraceSelection.patchedSha256,
    coreLayerReversalRetryCacheIdentity.pristineSha256,
  )
  for (let index = 0; index < TOOLCHAIN_PATCHES.length; index += 1) {
    const patch = TOOLCHAIN_PATCHES[index]
    for (const laterPatch of TOOLCHAIN_PATCHES.slice(index + 1)) {
      if (
        laterPatch.packageName === patch.packageName &&
        laterPatch.file === patch.file
      ) {
        assert.ok(patch.successorSha256s.includes(laterPatch.patchedSha256))
      }
    }
  }
  for (const patch of TOOLCHAIN_PATCHES) {
    assert.match(patch.pristineSha256, /^[0-9a-f]{64}$/)
    assert.match(patch.patchedSha256, /^[0-9a-f]{64}$/)
  }
})

test("final DRC fails closed without blocking Pipeline9's preliminary handoff", async (t) => {
  const capacityPath = join(
    toolchainDir,
    "node_modules",
    "@tscircuit",
    "capacity-autorouter",
    "dist",
    "index.js",
  )
  let GlobalDrcBranchPortfolioSolver
  try {
    ;({ GlobalDrcBranchPortfolioSolver } = await import(
      pathToFileURL(capacityPath).href
    ))
  } catch (error) {
    if (String(error).includes("ERR_MODULE_NOT_FOUND")) {
      t.skip("pinned toolchain is not installed")
      return
    }
    throw error
  }

  const params = {
    srj: {
      connections: [],
      obstacles: [],
      traces: [],
      layerCount: 2,
      bounds: { minX: 0, minY: 0, maxX: 1, maxY: 1 },
      minTraceWidth: 0.2,
    },
    hdRoutes: [],
    drcEvaluator: () => ({ errors: [], errorsWithCenters: [] }),
    broadMaxIterations: 1,
    broadPassMultiplier: 1,
    maxIterations: 1,
  }
  const unresolved = {
    count: 2,
    issueScore: 2,
    errors: [],
    errorsWithCenters: [],
  }

  const finalSolver = new GlobalDrcBranchPortfolioSolver({
    ...params,
    failOnUnresolvedDrc: true,
  })
  finalSolver.finishWithOutput([], unresolved)
  assert.equal(finalSolver.solved, false)
  assert.equal(finalSolver.failed, true)
  assert.match(finalSolver.error, /Unresolved DRC issues.*2/)
  assert.equal(finalSolver.stats.drcBranchPortfolioFinalDrcIssueCount, 2)

  const preliminarySolver = new GlobalDrcBranchPortfolioSolver(params)
  preliminarySolver.finishWithOutput([], unresolved)
  assert.equal(preliminarySolver.solved, true)
  assert.equal(preliminarySolver.failed, false)
})
