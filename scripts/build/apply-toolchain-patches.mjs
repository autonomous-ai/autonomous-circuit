#!/usr/bin/env node
/**
 * Apply the small upstream fixes required by the exactly pinned tscircuit stack.
 *
 * This is intentionally stricter than patch-package. The upstream bundles
 * are generated, mostly minified files; a fuzzy patch could silently land in
 * the wrong release. Every input and output has an exact SHA-256, every text
 * edit must match once, and the capacity-router source map must still contain
 * the source that was audited. An upgrade therefore fails loudly until these
 * patches are either removed or deliberately rebased.
 */

import { createHash } from "node:crypto"
import { readFile, rename, unlink, writeFile } from "node:fs/promises"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const sha256 = (value) =>
  createHash("sha256").update(value).digest("hex")

const PROPS_AUTHORED_NET_TREE_RUNTIME_PATCH = {
  packageName: "@tscircuit/props",
  version: "0.0.618",
  file: "dist/index.js",
  pristineSha256:
    "ed97ae48b9af99131ab6bf57c4c66345d2b091215bb7e788eba955e6ea08b257",
  patchedSha256:
    "1bb4a3838f05e926bf5400978156b4184a6b70f62adc94d6d47b0fe8c356da98",
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source: "../lib/components/trace.ts",
      contains: "routingPhaseIndex: z.number().nullable().optional(),",
    },
  ],
  replacements: [
    {
      label: "trace parser retains the explicit authored-tree boundary marker",
      before:
        "  routingPhaseIndex: z70.number().nullable().optional(),\n  pcbStraightLine:",
      after:
        '  routingPhaseIndex: z70.number().nullable().optional(),\n  authoredNetTreeBoundary: z70.boolean().optional().describe("Marks the sole port-to-net boundary of an authored PCB routing subtree"),\n  pcbStraightLine:',
    },
  ],
}

const PROPS_AUTHORED_NET_TREE_TYPES_PATCH = {
  packageName: "@tscircuit/props",
  version: "0.0.618",
  file: "dist/index.d.ts",
  pristineSha256:
    "d07c579a868b831e6968ef8eada02be4de36f710336e653a8c2f8921d70e6883",
  patchedSha256:
    "45d5570d87d256212596ad84908f5066f75b2294b97dd4b5ce78fb0e52e40a6d",
  replacements: [
    {
      label: "trace schema type exposes the authored-tree boundary marker",
      before:
        "    routingPhaseIndex: z.ZodOptional<z.ZodNullable<z.ZodNumber>>;\n    pcbStraightLine:",
      after:
        "    routingPhaseIndex: z.ZodOptional<z.ZodNullable<z.ZodNumber>>;\n    authoredNetTreeBoundary: z.ZodOptional<z.ZodBoolean>;\n    pcbStraightLine:",
      expectedMatches: 3,
    },
    {
      label: "parsed trace output type exposes the authored-tree boundary marker",
      before:
        "    routingPhaseIndex?: number | null | undefined;\n    maxLength?: number | undefined;",
      after:
        "    routingPhaseIndex?: number | null | undefined;\n    authoredNetTreeBoundary?: boolean | undefined;\n    maxLength?: number | undefined;",
      expectedMatches: 3,
    },
    {
      label: "trace input type exposes the authored-tree boundary marker",
      before:
        "    routingPhaseIndex?: number | null | undefined;\n    maxLength?: string | number | undefined;",
      after:
        "    routingPhaseIndex?: number | null | undefined;\n    authoredNetTreeBoundary?: boolean | undefined;\n    maxLength?: string | number | undefined;",
      expectedMatches: 3,
    },
  ],
}

const CAPACITY_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "ea3076fd694527994b7cfdb973a2e6345052891dc90cd049ea4e08ee2e9eb876",
  patchedSha256:
    "e2c4c50a2b5bbde80d49cc3b32adbe45e45921620aafcccdd99b1f12c97fa516",
  successorSha256s: [
    "882d448da1ac0ac1d2c910ebd5a58ac19cde34239979ae6b53b2bfd0bff9c044",
    "250aa7e7bd93bac18d3384422b9a66ecca23452860280c8fd2d69473e059eb30",
    "a4fac8c772142d037d80eb78f42e7751767ba3095c0e2b9fa3b7c58125c4af99",
    "471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7",
    "e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973",
  ],
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline7_MultiGraph/create-pipeline7-autorouting-drc-evaluator.ts",
      contains:
        "traceClearance: AUTOROUTING_TRACE_CLEARANCE,\n    viaClearance: AUTOROUTING_VIA_CLEARANCE,",
    },
    {
      source: "../lib/testing/evaluate-relaxed-drc.ts",
      contains: "...getDrcErrors(circuitJson, RELAXED_DRC_OPTIONS),",
    },
    {
      source:
        "../node_modules/high-density-repair03/lib/solvers/GlobalDrcForceImproveSolver/GlobalDrcBranchPortfolioSolver.ts",
      contains:
        "drcBranchPortfolioViaInPadMaxIterations:\n        this.params.viaInPadMaxIterations,\n    }\n    this.solved = true",
    },
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline7_MultiGraph/AutoroutingPipelineSolver7_MultiGraph.ts",
      contains:
        "viaInPadMaxIterations: 32,\n            broadMaxIterations: 12,\n            broadPassMultiplier: 3,",
    },
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline9_PreloadedTraceGraph/pipeline9-joint-drc-repair-solver.ts",
      contains:
        "terminalEscapeAcceptedCount: terminalEscapeResult.acceptedCandidateCount,\n    }\n    this.solved = true",
    },
  ],
  replacements: [
    {
      label: "Pipeline7 uses the board's effective copper clearance",
      before:
        'n=new zy(e,{connMap:t.connMap,traceClearance:.1,viaClearance:.1});return({routes:e,hdRoutes:o})=>{const i=e??o;if(!i)throw new Error("Pipeline7 autorouting DRC evaluation requires HD routes")',
      after:
        'n=new zy(e,{connMap:t.connMap,traceClearance:Math.max(t.originalSrj.minTraceToPadEdgeClearance??.1,t.originalSrj.minViaEdgeToPadEdgeClearance??.1),viaClearance:t.originalSrj.minViaEdgeToPadEdgeClearance??.1});return({routes:e,hdRoutes:o})=>{const i=e??o;if(!i)throw new Error("Pipeline7 autorouting DRC evaluation requires HD routes")',
    },
    {
      label: "Pipeline9 uses the board's effective copper clearance",
      before: "return{circuitJson:i,...nK(i,oK)}}",
      after:
        "return{circuitJson:i,...nK(i,{...oK,traceClearance:Math.max(t.minTraceToPadEdgeClearance??.1,t.minViaEdgeToPadEdgeClearance??.1),viaClearance:t.minViaEdgeToPadEdgeClearance??.1})}}",
    },
    {
      label: "exact portfolio can fail closed on unresolved DRC",
      before:
        "drcBranchPortfolioViaInPadMaxIterations:this.params.viaInPadMaxIterations},this.solved=!0}startBaselineBranch()",
      after:
        "drcBranchPortfolioViaInPadMaxIterations:this.params.viaInPadMaxIterations,drcBranchPortfolioFinalDrcIssueCount:e.count},this.params.failOnUnresolvedDrc&&e.count>0?(this.error=`Unresolved DRC issues after exact repair: ${e.count}`,this.failed=!0):this.solved=!0}startBaselineBranch()",
    },
    {
      label: "Pipeline7 treats its exact DRC portfolio as a final gate",
      before:
        "enableViaInPadLayerMoves:t.originalSrj.allowViaInPad??!1,viaInPadMaxIterations:32,broadMaxIterations:12,broadPassMultiplier:3}",
      after:
        "enableViaInPadLayerMoves:t.originalSrj.allowViaInPad??!1,viaInPadMaxIterations:32,broadMaxIterations:12,broadPassMultiplier:3,failOnUnresolvedDrc:!0}",
    },
    {
      label: "Pipeline9 rechecks the output after its terminal/regional repairs",
      before:
        "regionalB01RepairFallbackCandidateCount:e.fallbackCandidateCount,terminalEscapeCandidateCount:t.attemptedCandidateCount,terminalEscapeAcceptedCount:t.acceptedCandidateCount},this.solved=!0}getCombinedOutput()",
      after:
        "regionalB01RepairFallbackCandidateCount:e.fallbackCandidateCount,terminalEscapeCandidateCount:t.attemptedCandidateCount,terminalEscapeAcceptedCount:t.acceptedCandidateCount};const n=this.drcEvaluator({routes:this.combinedOutput}),o=n.errors.length;this.stats={...this.stats,finalDrcIssueCount:o},o>0?(this.error=`Unresolved DRC issues after Pipeline9 final repair: ${o}`,this.failed=!0):this.solved=!0}getCombinedOutput()",
    },
  ],
}

const CAPACITY_DYNAMIC_TRACE_CONNECTIVITY_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "e2c4c50a2b5bbde80d49cc3b32adbe45e45921620aafcccdd99b1f12c97fa516",
  patchedSha256:
    "882d448da1ac0ac1d2c910ebd5a58ac19cde34239979ae6b53b2bfd0bff9c044",
  successorSha256s: [
    "250aa7e7bd93bac18d3384422b9a66ecca23452860280c8fd2d69473e059eb30",
    "a4fac8c772142d037d80eb78f42e7751767ba3095c0e2b9fa3b7c58125c4af99",
    "471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7",
    "e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973",
  ],
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../node_modules/high-density-repair03/lib/drc/AutoroutingDrcEngine.ts",
      contains:
        "const netId = this.resolveNetId(trace.connection_name)",
    },
  ],
  replacements: [
    {
      label: "dynamic DRC keeps trace connectivity identities until comparison",
      before: "const t=this.resolveNetId(i.connection_name),r=Ly(i);",
      after: "const t=i.connection_name,r=Ly(i);",
    },
  ],
}

const CAPACITY_PRELOADED_TRACE_EXACT_DRC_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "882d448da1ac0ac1d2c910ebd5a58ac19cde34239979ae6b53b2bfd0bff9c044",
  patchedSha256:
    "250aa7e7bd93bac18d3384422b9a66ecca23452860280c8fd2d69473e059eb30",
  successorSha256s: [
    "a4fac8c772142d037d80eb78f42e7751767ba3095c0e2b9fa3b7c58125c4af99",
    "471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7",
    "e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973",
  ],
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline7_MultiGraph/create-pipeline7-autorouting-drc-evaluator.ts",
      contains: `const tracesToEvaluate = [
      ...(conversionOptions.originalSrj.traces ?? []),
      ...candidateTraces,
    ]`,
    },
    {
      source: "../lib/utils/convertSrjTracesToObstacles.ts",
      contains:
        "obstacleId: `trace_obstacle_${trace.pcb_trace_id}_${traceIndex}_${pointIndex}_wire`",
    },
  ],
  replacements: [
    {
      label: "exact DRC uses preloaded line and via geometry, not routing AABBs",
      before:
        "const e={...t.srjWithPointPairs,minTraceWidth:t.originalSrj.minTraceWidth,minViaDiameter:t.originalSrj.minViaDiameter??t.srjWithPointPairs.minViaDiameter},n=new zy(e,{connMap:t.connMap",
      after:
        'const e={...t.srjWithPointPairs,obstacles:t.srjWithPointPairs.obstacles.filter(t=>!t.obstacleId?.startsWith("trace_obstacle_")),minTraceWidth:t.originalSrj.minTraceWidth,minViaDiameter:t.originalSrj.minViaDiameter??t.srjWithPointPairs.minViaDiameter},n=new zy(e,{connMap:t.connMap',
    },
  ],
}

const CAPACITY_THROUGH_OBSTACLE_DRC_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "250aa7e7bd93bac18d3384422b9a66ecca23452860280c8fd2d69473e059eb30",
  patchedSha256:
    "a4fac8c772142d037d80eb78f42e7751767ba3095c0e2b9fa3b7c58125c4af99",
  successorSha256s: [
    "471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7",
    "e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973",
  ],
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source: "../lib/utils/convertSrjTracesToObstacles.ts",
      contains:
        "obstacleId: `trace_obstacle_${trace.pcb_trace_id}_${traceIndex}_${pointIndex}_through`",
    },
    {
      source:
        "../node_modules/high-density-repair03/lib/drc/AutoroutingDrcEngine.ts",
      contains:
        'if (routePoint.route_type !== "via") continue',
    },
  ],
  replacements: [
    {
      label: "through-obstacle copper keeps its conservative DRC geometry",
      before:
        'obstacles:t.srjWithPointPairs.obstacles.filter(t=>!t.obstacleId?.startsWith("trace_obstacle_"))',
      after:
        "obstacles:t.srjWithPointPairs.obstacles.filter(t=>!t.obstacleId?.match(/^trace_obstacle_.*_(?:wire|via)(?:_approx_\\d+)?$/))",
    },
  ],
}

const CAPACITY_AUTHORED_NET_TREE_TOPOLOGY_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "a4fac8c772142d037d80eb78f42e7751767ba3095c0e2b9fa3b7c58125c4af99",
  patchedSha256:
    "471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7",
  successorSha256s: [
    "e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973",
  ],
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../lib/solvers/NetToPointPairsSolver/mergeConnections.ts",
      contains:
        "for (const connectionTempIdsSharingPoint of pointKeyToConnectionTempIds.values())",
    },
  ],
  replacements: [
    {
      label: "explicit authored-tree edges bypass shared-point DSU merging",
      before:
        "for(const t of o.values())if(t.length>1){const e=t[0];for(let o=1;o<t.length;o++)n.union(e,t[o])}",
      after:
        'for(const e of o.values()){const o=e.filter(e=>!t[Number(e.slice(5))].__preserveConnectionTopology);if(o.length>1){const t=o[0];for(let e=1;e<o.length;e++)n.union(t,o[e])}}',
    },
  ],
}

const CAPACITY_DIFFERENTIAL_PAIR_FAIL_CLOSED_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "471c49fbb77192e8161ac8dadbb3b51781c10b19dfc088a952964c71ded114b7",
  patchedSha256:
    "e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973",
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../node_modules/@tscircuit/length-matching-solver/lib/post-processing/routing/createCoupledPairCandidate.ts",
      contains:
        'if (bisectorLength <= 1e-10)\n      throw new Error(\n        "PostProcessingSolver: cannot offset a reversing spine corner",\n      )',
    },
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline7_MultiGraph/AutoroutingPipelineSolver7_MultiGraph.ts",
      contains:
        "minimumCenterlineDistance: centerlineDistance,\n              maximumCenterlineDistance: centerlineDistance,",
    },
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline9_PreloadedTraceGraph/autorouting-pipeline-solver9-preloaded-trace-graph.ts",
      contains: "return { ...pair, connectionNames }",
    },
  ],
  replacements: [
    {
      label: "a reversing coupled-spine station uses a bounded outgoing normal",
      before:
        'if(d<=1e-10)throw new Error("PostProcessingSolver: cannot offset a reversing spine corner");const u=h.x/d,p=h.y/d,m=a/(u*c.x+p*c.y);',
      after:
        "if(d<=1e-10)return{x:l.x*a,y:l.y*a};const u=h.x/d,p=h.y/d,m=a/(u*c.x+p*c.y);",
    },
    {
      label: "Pipeline7 retains the complete differential-pair contract",
      before:
        'const i=t.exactGeometryDrcForceImproveSolver.getOutput(),r=(t.srj.differentialPairs??[]).map(t=>{const e=t.connectionNames.map(t=>{const e=o.get(t);if(!e)throw new Error(`Pipeline7: differential pair connection "${t}" is missing from final routed output`);return e});if(e[0]===e[1])throw new Error(`Pipeline7: differential pair ${t.connectionNames.join("/")} resolves both members to "${e[0]}"`);if(void 0===t.traceGap)return{connectionNames:e,lengthTolerance:t.lengthTolerance};const n=e.map(t=>{const e=i.filter(e=>e.connectionName===t);if(1!==e.length)throw new Error(`Pipeline7: differential pair connection "${t}" must resolve to exactly one final HD route, got ${e.length}`);return e[0]}),r=t.traceGap+n.reduce((t,e)=>t+e.traceThickness/2,0);return{connectionNames:e,lengthTolerance:t.lengthTolerance,minimumCenterlineDistance:r,maximumCenterlineDistance:r}})',
      after:
        'const i=t.exactGeometryDrcForceImproveSolver.getOutput(),r=acResolveDifferentialPairs(t.srj.differentialPairs??[],o,i,"Pipeline7")',
    },
    {
      label: "Pipeline9 resolves trace gap and uncoupled length like Pipeline7",
      before:
        'const i=(t.srj.differentialPairs??[]).map(t=>{const e=t.connectionNames.map(t=>{const e=o.get(t);if(!e)throw new Error(`Pipeline9: differential pair connection "${t}" is missing from final routed output`);return e});if(e[0]===e[1])throw new Error(`Pipeline9: differential pair ${t.connectionNames.join("/")} resolves both members to "${e[0]}"`);return{...t,connectionNames:e}});return[{hdRoutes:t.pipeline9JointDrcRepairSolver.getOutput(),differentialPairs:i',
      after:
        'const i=t.pipeline9JointDrcRepairSolver.getOutput(),r=acResolveDifferentialPairs(t.srj.differentialPairs??[],o,i,"Pipeline9");return[{hdRoutes:i,differentialPairs:r',
    },
    {
      label: "Pipeline7 rejects post-processing diagnostics and uncoupled copper",
      before:
        '_getOutputHdRoutes(){if(this.lengthMatchingPostProcessingSolver){const{hdRoutes:t}=this.lengthMatchingPostProcessingSolver.getOutput();return t}',
      after:
        '_getOutputHdRoutes(){if(this.lengthMatchingPostProcessingSolver){const t=this.lengthMatchingPostProcessingSolver.getOutput();return acValidateDifferentialPairPostProcessing(t,this.lengthMatchingPostProcessingSolver.inputProblem.differentialPairs,"Pipeline7",this.exactGeometryDrcForceImproveSolver?.params?.drcEvaluator),t.hdRoutes}',
    },
    {
      label: "Pipeline9 rejects post-processing diagnostics and uncoupled copper",
      before:
        '_getOutputHdRoutes(){if((this.originalSrj.differentialPairs?.length??0)>0&&this.lengthMatchingPostProcessingSolver){const{hdRoutes:t}=this.lengthMatchingPostProcessingSolver.getOutput();return t}',
      after:
        '_getOutputHdRoutes(){if((this.originalSrj.differentialPairs?.length??0)>0&&this.lengthMatchingPostProcessingSolver){const t=this.lengthMatchingPostProcessingSolver.getOutput();return acValidateDifferentialPairPostProcessing(t,this.lengthMatchingPostProcessingSolver.inputProblem.differentialPairs,"Pipeline9",this.pipeline9JointDrcRepairSolver?.drcEvaluator,this.pipeline9JointDrcRepairSolver?.inputNewHdRoutes),t.hdRoutes}',
    },
    {
      label: "final differential-pair geometry is measured and gated",
      before: ",UQ=ps;export{",
      after: `,UQ=ps;
function acResolveDifferentialPairs(pairs, finalConnectionNames, hdRoutes, pipelineName) {
  return pairs.map((pair) => {
    const connectionNames = pair.connectionNames.map((connectionName) => {
      const finalConnectionName = finalConnectionNames.get(connectionName);
      if (!finalConnectionName) {
        throw new Error(pipelineName + ': differential pair connection "' + connectionName + '" is missing from final routed output');
      }
      return finalConnectionName;
    });
    if (connectionNames[0] === connectionNames[1]) {
      throw new Error(pipelineName + ': differential pair ' + pair.connectionNames.join('/') + ' resolves both members to "' + connectionNames[0] + '"');
    }
    const resolvedPair = {
      connectionNames,
      lengthTolerance: pair.lengthTolerance,
      ...(pair.maxUncoupledLength !== void 0 ? { maxUncoupledLength: pair.maxUncoupledLength } : {})
    };
    if (pair.traceGap === void 0) {
      if (pair.maxUncoupledLength !== void 0) {
        throw new Error(pipelineName + ': differential pair ' + pair.connectionNames.join('/') + ' declares maxUncoupledLength without pcbTraceGap; the coupling threshold is undefined');
      }
      return resolvedPair;
    }
    const pairRoutes = connectionNames.map((connectionName) => {
      const matchingRoutes = hdRoutes.filter((route) => route.connectionName === connectionName);
      if (matchingRoutes.length !== 1) {
        throw new Error(pipelineName + ': differential pair connection "' + connectionName + '" must resolve to exactly one final HD route, got ' + matchingRoutes.length);
      }
      return matchingRoutes[0];
    });
    const centerlineDistance = pair.traceGap + pairRoutes.reduce(
      (halfWidthTotal, route) => halfWidthTotal + route.traceThickness / 2,
      0
    );
    return {
      ...resolvedPair,
      minimumCenterlineDistance: centerlineDistance,
      maximumCenterlineDistance: centerlineDistance
    };
  });
}

function acGetPlanarSegments(route) {
  const segments = [];
  for (let index = 0; index < route.length - 1; index++) {
    const start = route[index];
    const end = route[index + 1];
    if (start.z !== end.z) continue;
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length = Math.hypot(dx, dy);
    if (length <= 1e-8) continue;
    segments.push({ start, end, length, ux: dx / length, uy: dy / length, z: start.z });
  }
  return segments;
}
function acGetTotalUncoupledLength(route, mateRoute, maximumCenterlineDistance) {
  const routeSegments = acGetPlanarSegments(route);
  const mateSegments = acGetPlanarSegments(mateRoute);
  let totalUncoupledLength = 0;
  for (const segment of routeSegments) {
    const coupledIntervals = [];
    for (const mate of mateSegments) {
      if (segment.z !== mate.z) continue;
      if (Math.abs(segment.ux * mate.uy - segment.uy * mate.ux) > 1e-6) continue;
      const startDx = mate.start.x - segment.start.x;
      const startDy = mate.start.y - segment.start.y;
      const endDx = mate.end.x - segment.start.x;
      const endDy = mate.end.y - segment.start.y;
      const startDistance = Math.abs(-segment.uy * startDx + segment.ux * startDy);
      const endDistance = Math.abs(-segment.uy * endDx + segment.ux * endDy);
      if (Math.max(startDistance, endDistance) > maximumCenterlineDistance + 1e-6) continue;
      const startProjection = segment.ux * startDx + segment.uy * startDy;
      const endProjection = segment.ux * endDx + segment.uy * endDy;
      const intervalStart = Math.max(0, Math.min(startProjection, endProjection));
      const intervalEnd = Math.min(segment.length, Math.max(startProjection, endProjection));
      if (intervalEnd > intervalStart + 1e-8) coupledIntervals.push([intervalStart, intervalEnd]);
    }
    coupledIntervals.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
    let coupledLength = 0;
    let intervalStart = -Infinity;
    let intervalEnd = -Infinity;
    for (const interval of coupledIntervals) {
      if (interval[0] > intervalEnd + 1e-8) {
        if (intervalEnd > intervalStart) coupledLength += intervalEnd - intervalStart;
        intervalStart = interval[0];
        intervalEnd = interval[1];
      } else {
        intervalEnd = Math.max(intervalEnd, interval[1]);
      }
    }
    if (intervalEnd > intervalStart) coupledLength += intervalEnd - intervalStart;
    totalUncoupledLength += Math.max(0, segment.length - coupledLength);
  }
  return totalUncoupledLength;
}
function acGetRouteLength(route) {
  return acGetPlanarSegments(route).reduce((total, segment) => total + segment.length, 0);
}
function acValidateDifferentialPairPostProcessing(output, pairs, pipelineName, drcEvaluator, knownDrcCleanRoutes) {
  const postProcessingErrors = output.postProcessingErrors ?? [];
  if (postProcessingErrors.length > 0 && pairs.some((pair) => pair.maxUncoupledLength === void 0)) {
    throw new Error(
      pipelineName + ': differential pair post-processing failed closed: ' +
      postProcessingErrors.map((error) => error.message).join('; ')
    );
  }
  for (const pair of pairs) {
    const pairRoutes = pair.connectionNames.map((connectionName) => {
      const matchingRoutes = output.hdRoutes.filter((route) => route.connectionName === connectionName);
      if (matchingRoutes.length !== 1) {
        throw new Error(pipelineName + ': differential pair connection "' + connectionName + '" must resolve to exactly one post-processed route, got ' + matchingRoutes.length);
      }
      return matchingRoutes[0];
    });
    const lengthDifference = Math.abs(
      acGetRouteLength(pairRoutes[0].route) - acGetRouteLength(pairRoutes[1].route)
    );
    if (lengthDifference > pair.lengthTolerance + 1e-6) {
      throw new Error(
        pipelineName + ': differential pair ' + pair.connectionNames.join('/') +
        ' exceeds length tolerance ' + pair.lengthTolerance +
        'mm with ' + lengthDifference.toFixed(6) + 'mm of skew'
      );
    }
    if (pair.maxUncoupledLength === void 0) continue;
    if (pair.maximumCenterlineDistance === void 0) {
      throw new Error(pipelineName + ': differential pair ' + pair.connectionNames.join('/') + ' cannot enforce maxUncoupledLength without a maximum centerline distance');
    }
    const uncoupledLength = Math.max(
      acGetTotalUncoupledLength(pairRoutes[0].route, pairRoutes[1].route, pair.maximumCenterlineDistance),
      acGetTotalUncoupledLength(pairRoutes[1].route, pairRoutes[0].route, pair.maximumCenterlineDistance)
    );
    if (uncoupledLength > pair.maxUncoupledLength + 1e-6) {
      throw new Error(
        pipelineName + ': differential pair ' + pair.connectionNames.join('/') +
        ' exceeds maxUncoupledLength ' + pair.maxUncoupledLength +
        'mm with ' + uncoupledLength.toFixed(6) + 'mm of uncoupled copper'
      );
    }
  }
  if (typeof drcEvaluator === 'function') {
    const finalDrc = drcEvaluator({ routes: output.hdRoutes });
    if ((finalDrc.errors?.length ?? 0) > 0) {
      throw new Error(
        pipelineName + ': differential pair post-processing produced ' +
        finalDrc.errors.length + ' final DRC issue(s)'
      );
    }
  } else if (!knownDrcCleanRoutes || JSON.stringify(output.hdRoutes) !== JSON.stringify(knownDrcCleanRoutes)) {
    throw new Error(pipelineName + ': final differential-pair DRC evaluator is unavailable for changed copper');
  }
}
export{`,
    },
  ],
}

const CAPACITY_VIA_IN_SMD_PAD_PREVENTION_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "e9646104761010ac37d935e839781b0a755870a7e56f0db7cfd4ccd9dbc7a973",
  patchedSha256:
    "ce318ec3a3120490459c0c5cdaea710a1345884aeb5f88f559188c255ab9c318",
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../node_modules/high-density-repair03/lib/drc/AutoroutingDrcEngine.ts",
      contains: `const bounds = expandBounds(
        getObstacleBounds(obstacle),
        this.traceClearance,
      )`,
    },
    {
      source:
        "../node_modules/high-density-repair03/lib/drc/AutoroutingDrcEngine.ts",
      contains: "errors.push(...this.checkViaPairs(vias))",
    },
    {
      source:
        "../node_modules/high-density-repair03/lib/solvers/GlobalDrcForceImproveSolver/solverHelpers.ts",
      contains: `const viaIds = error.pcb_via_ids
    if (Array.isArray(viaIds) && viaIds.length > 0) {`,
    },
  ],
  replacements: [
    {
      label: "static obstacle search covers the larger via clearance",
      before:
        "const e=Cy(wy(t),this.traceClearance);for(const n of t.layers)",
      after:
        "const e=Cy(wy(t),Math.max(this.traceClearance,this.viaClearance));for(const n of t.layers)",
    },
    {
      label: "exact DRC rejects vias inside physical SMD copper",
      before: "checkViaPairs(t){if(t.length<2)return[];",
      after:
        'checkViaObstacles(t){const e=[];for(const n of t){const t=new Set;for(const o of n.layers)for(const i of this.obstacleIndexesByLayer.get(o)?.query(Ny(n))??[]){if("pcb_smtpad"!==i.obstacleType||!i.connectedTo.some(t=>t.startsWith("pcb_smtpad_"))||t.has(i.obstacleId))continue;t.add(i.obstacleId),this.lastRunStats.broadPhaseCandidateCount+=1,this.lastRunStats.exactCheckCount+=1;const o=i.connectedTo.some(t=>this.areConnected(n.netId,t));if(o&&this.srj.allowViaInPad===!0)continue;const r=wy(i),s=Math.max(r.minX-n.x,0,n.x-r.maxX),a=Math.max(r.minY-n.y,0,n.y-r.maxY),c=s>0||a>0?Math.hypot(s,a):-Math.min(n.x-r.minX,r.maxX-n.x,n.y-r.minY,r.maxY-n.y),l=c-n.diameter/2;if(l+Iy>=this.viaClearance)continue;const h={x:n.x,y:n.y};e.push({type:"pcb_via_clearance_error",error_type:"pcb_via_clearance_error",pcb_error_id:`${o?"same_net":"different_net"}_via_smd_pad_${n.viaId}_${i.obstacleId}`,message:`Via ${n.viaId} is too close to pcb_smtpad "${i.obstacleId}" (gap: ${l.toFixed(3)}mm, required: ${this.viaClearance.toFixed(3)}mm)`,pcb_via_ids:[n.viaId],pcb_smtpad_id:i.obstacleId,pcb_smtpad_center:{x:i.x,y:i.y},minimum_clearance:this.viaClearance,actual_clearance:l,pcb_center:h,center:h})}}return e}checkViaPairs(t){if(t.length<2)return[];',
    },
    {
      label: "via-to-SMD findings participate in every exact evaluation",
      before:
        "i.push(...this.checkViaPairs(n));const s=i.filter(t=>t.center);",
      after:
        "i.push(...this.checkViaObstacles(n),...this.checkViaPairs(n));const s=i.filter(t=>t.center);",
    },
    {
      label: "a via-to-SMD finding moves only its reported via",
      before:
        "if(Array.isArray(m)&&m.length>0){p=SL(t,u,n);const o=sD(h,n);",
      after:
        'if(Array.isArray(m)&&m.length>0){if(u.pcb_smtpad_center&&typeof u.pcb_smtpad_center.x==="number"&&typeof u.pcb_smtpad_center.y==="number"){const o=rD(h,n);o&&(l=aD(e,o,u.pcb_smtpad_center,t)||l);continue}p=SL(t,u,n);const o=sD(h,n);',
    },
  ],
}

const CAPACITY_DIFFERENTIAL_PAIR_ZERO_LENGTH_EDGE_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "ce318ec3a3120490459c0c5cdaea710a1345884aeb5f88f559188c255ab9c318",
  patchedSha256:
    "38b34259144c87a5040b02a4f3958760ead65a3700550e4b205d1dab642a5853",
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../node_modules/@tscircuit/length-matching-solver/lib/post-processing/routing/CompositeRoutingGrid.ts",
      contains: `  private connect(firstId: number, secondId: number): void {
    if (firstId === secondId) return
    const first = this.nodes[firstId]!
    const second = this.nodes[secondId]!
    first.neighborIds.add(secondId)
    second.neighborIds.add(firstId)
  }`,
    },
    {
      source:
        "../node_modules/@tscircuit/length-matching-solver/lib/post-processing/routing/IncrementalCoupledPathSearch.ts",
      contains:
        '"PostProcessingSolver: composite grid produced a zero-length planar edge"',
    },
  ],
  replacements: [
    {
      label: "composite routing grids never expose directionless planar edges",
      before:
        "connect(t,e){if(t===e)return;const n=this.nodes[t],o=this.nodes[e];n.neighborIds.add(e),o.neighborIds.add(t)}keyFor",
      after:
        "connect(t,e){if(t===e)return;const n=this.nodes[t],o=this.nodes[e];if(Math.hypot(n.point.x-o.point.x,n.point.y-o.point.y)<=1e-10)return;n.neighborIds.add(e),o.neighborIds.add(t)}keyFor",
    },
  ],
}

const CAPACITY_EXPLICIT_TRACE_WIDTH_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "38b34259144c87a5040b02a4f3958760ead65a3700550e4b205d1dab642a5853",
  patchedSha256:
    "e7c2ab3d003ad010db4a648cfb15355256763c226bbf146f8f491640d321780c",
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source: "../lib/solvers/TraceWidthSolver/TraceWidthSolver.ts",
      contains: `const midWidth = (this.nominalTraceWidth + this.minTraceWidth) / 2
      this.TRACE_WIDTH_SCHEDULE = [this.nominalTraceWidth, midWidth]`,
    },
    {
      source: "../lib/solvers/TraceWidthSolver/TraceWidthSolver.ts",
      contains:
        "// Exhausted all widths in schedule, use minTraceWidth as fallback",
    },
    {
      source: "../lib/solvers/TraceWidthSolver/TraceWidthSolver.ts",
      contains:
        "point.traceThickness = this.getTaperWidthAtDistance({",
    },
  ],
  replacements: [
    {
      label: "explicit connection width is a single hard-minimum target",
      before:
        "this.currentTrace=t,this.nominalTraceWidth=e;const n=(this.nominalTraceWidth+this.minTraceWidth)/2;return this.TRACE_WIDTH_SCHEDULE=[this.nominalTraceWidth,n],this.currentTrace.route.length<2?(this.processedRoutes.push(this.createRouteWithWidth(this.currentTrace,this.minTraceWidth)),void(this.currentTrace=null)):(this.currentScheduleIndex=0,this.currentTargetWidth=this.TRACE_WIDTH_SCHEDULE[0],void this.initializeCursor())",
      after:
        "this.currentTrace=t,this.nominalTraceWidth=e,this.currentTargetWidth=Math.max(this.nominalTraceWidth,this.minTraceWidth);return this.TRACE_WIDTH_SCHEDULE=[this.currentTargetWidth],this.currentTrace.route.length<2?(this.processedRoutes.push(this.createRouteWithWidth(this.currentTrace,this.currentTargetWidth)),void(this.currentTrace=null)):(this.currentScheduleIndex=0,void this.initializeCursor())",
    },
    {
      label: "explicit connection width exhaustion fails closed",
      before:
        "this.currentScheduleIndex<this.TRACE_WIDTH_SCHEDULE.length?(this.currentTargetWidth=this.TRACE_WIDTH_SCHEDULE[this.currentScheduleIndex],this.initializeCursor()):this.finalizeCurrentTrace(this.minTraceWidth)",
      after:
        "this.currentScheduleIndex<this.TRACE_WIDTH_SCHEDULE.length?(this.currentTargetWidth=this.TRACE_WIDTH_SCHEDULE[this.currentScheduleIndex],this.initializeCursor()):(this.error=`Trace ${this.currentTrace.connectionName} cannot satisfy explicit minimum trace width ${this.currentTargetWidth}mm`,this.failed=!0)",
    },
    {
      label: "terminal taper never undercuts an explicit connection width",
      before: "route:this.createTerminalTaperedRoute(t,e)",
      after:
        "route:void 0===this.getNominalTraceWidthForRoute(t)?this.createTerminalTaperedRoute(t,e):t.route.map(t=>({...t,traceThickness:e}))",
    },
  ],
}

const CAPACITY_LAYER_REVERSAL_RETRY_PATCH = {
  packageName: "@tscircuit/capacity-autorouter",
  version: "0.0.782",
  file: "dist/index.js",
  pristineSha256:
    "e7c2ab3d003ad010db4a648cfb15355256763c226bbf146f8f491640d321780c",
  patchedSha256:
    "6d9e591861f3e6cc66af1cf86d230fdd0ac3a7673ec6f2565a2466527bf9a8b7",
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline7_MultiGraph/AutoroutingPipelineSolver7_MultiGraph.ts",
      contains: `      } else if (this.activeSubSolver.failed) {
        this.error = this.activeSubSolver?.error
        this.failed = true
        this.activeSubSolver = null
      }`,
    },
    {
      source:
        "../lib/autorouter-pipelines/AutoroutingPipeline7_MultiGraph/AutoroutingPipelineSolver7_MultiGraph.ts",
      contains: `  getOutputSimplifiedPcbTraces(): SimplifiedPcbTraces {
    if (!this.solved || !this.highDensityRouteSolver) {
      throw new Error("Cannot get output before solving is complete")
    }`,
    },
    {
      source: "../lib/utils/create-srj-with-board-valid-obstacle-layers.ts",
      contains: `    layers: zLayers.map((z) => mapZToLayerName(z, layerCount)),
    zLayers,
    __zLayers: zLayers,`,
    },
  ],
  replacements: [
    {
      label: "Pipeline7 records one bounded layer-reversal retry",
      scopeStart: "var dq=class",
      scopeEnd: "},uq=t=>",
      before:
        'sharedEdgeSegmentsWithNecessaryCrampedPortPoints;highDensityNodePortPoints;cacheProvider=null;pipelineDef=[hq("preprocessSimpleRouteJsonSolver"',
      after:
        'sharedEdgeSegmentsWithNecessaryCrampedPortPoints;highDensityNodePortPoints;cacheProvider=null;layerReversalRetrySolver=null;originalLayerReversalFailure=null;pipelineDef=[hq("preprocessSimpleRouteJsonSolver"',
    },
    {
      label: "Pipeline7 retries a failed route once with the layer stack reversed",
      scopeStart: "var dq=class",
      scopeEnd: "},uq=t=>",
      before:
        'currentPipelineStepIndex=0;_step(){const t=this.pipelineDef[this.currentPipelineStepIndex];if(!t)return void(this.solved=!0);if(this.activeSubSolver)return this.activeSubSolver.step(),void(this.activeSubSolver.solved?(this.endTimeOfPhase[t.solverName]=performance.now(),this.timeSpentOnPhase[t.solverName]=this.endTimeOfPhase[t.solverName]-this.startTimeOfPhase[t.solverName],t.onSolved?.(this),this.activeSubSolver=null,this.currentPipelineStepIndex++):this.activeSubSolver.failed&&(this.error=this.activeSubSolver?.error,this.failed=!0,this.activeSubSolver=null));const e=t.getConstructorParams(this);',
      after:
        'currentPipelineStepIndex=0;_step(){if(this.layerReversalRetrySolver){try{this.layerReversalRetrySolver.step()}catch(t){this.layerReversalRetrySolver.error??=String(t),this.layerReversalRetrySolver.failed=!0}return void(this.layerReversalRetrySolver.solved?this.solved=!0:this.layerReversalRetrySolver.failed&&(this.error=`Pipeline7 failed in the original orientation (${this.originalLayerReversalFailure}) and layer-reversal retry (${this.layerReversalRetrySolver.error})`,this.failed=!0))}const t=this.pipelineDef[this.currentPipelineStepIndex];if(!t)return void(this.solved=!0);if(this.activeSubSolver)return this.activeSubSolver.step(),void(this.activeSubSolver.solved?(this.endTimeOfPhase[t.solverName]=performance.now(),this.timeSpentOnPhase[t.solverName]=this.endTimeOfPhase[t.solverName]-this.startTimeOfPhase[t.solverName],t.onSolved?.(this),this.activeSubSolver=null,this.currentPipelineStepIndex++):this.activeSubSolver.failed&&(this.originalLayerReversalFailure=this.activeSubSolver?.error,this.activeSubSolver=null,this.opts.__disableLayerReversalRetry||this.originalSrj.layerCount<2?(this.error=this.originalLayerReversalFailure,this.failed=!0):this.layerReversalRetrySolver=new dq(acReverseSrjLayers(this.originalSrj),{...acReverseP7Options(this.opts,this.originalSrj.layerCount),__disableLayerReversalRetry:!0,cacheProvider:acNamespaceCacheProvider(this.cacheProvider,"p7-layer-reversal-v1:")})));const e=t.getConstructorParams(this);',
    },
    {
      label: "successful retry copper maps back to the authored layer stack",
      scopeStart: "var dq=class",
      scopeEnd: "},uq=t=>",
      before:
        'getOutputSimplifiedPcbTraces(){if(!this.solved||!this.highDensityRouteSolver)throw new Error("Cannot get output before solving is complete");return this.powerTraceExpansionSolver?this.powerTraceExpansionSolver.getOutput():this.getPrePowerTraceOutputSimplifiedPcbTraces()}',
      after:
        'getOutputSimplifiedPcbTraces(){if(this.layerReversalRetrySolver?.solved)return acReverseSrjLayers(this.layerReversalRetrySolver.getOutputSimplifiedPcbTraces(),void 0,this.originalSrj.layerCount);if(!this.solved||!this.highDensityRouteSolver)throw new Error("Cannot get output before solving is complete");return this.powerTraceExpansionSolver?this.powerTraceExpansionSolver.getOutput():this.getPrePowerTraceOutputSimplifiedPcbTraces()}',
    },
    {
      label: "layer reversal transforms every SRJ layer encoding and namespaces cache entries",
      before: ",UQ=ps;\nfunction acResolveDifferentialPairs",
      after: `,UQ=ps;
function acReverseLayerName(layer, layerCount) {
  if (!Number.isInteger(layerCount) || layerCount < 2) return layer;
  const z = layer === "top" ? 0 : layer === "bottom" ? layerCount - 1 : /^inner\\d+$/.test(layer) ? Number(layer.slice(5)) : NaN;
  if (!Number.isInteger(z) || z < 0 || z >= layerCount) return layer;
  const reversedZ = layerCount - 1 - z;
  return reversedZ === 0 ? "top" : reversedZ === layerCount - 1 ? "bottom" : \`inner\${reversedZ}\`;
}
function acReverseSrjLayers(value, key, layerCount = value?.layerCount) {
  if (Array.isArray(value)) {
    if (key === "layers") return value.map((layer) => acReverseLayerName(layer, layerCount));
    if ((key === "zLayers" || key === "__zLayers" || key === "availableZ") && Number.isInteger(layerCount)) return value.map((z) => layerCount - 1 - z);
    return value.map((item) => acReverseSrjLayers(item, void 0, layerCount));
  }
  if (value === null || typeof value !== "object") {
    if ((key === "layer" || key === "from_layer" || key === "to_layer") && typeof value === "string") return acReverseLayerName(value, layerCount);
    if ((key === "z" || key === "from_z" || key === "to_z") && typeof value === "number" && Number.isInteger(layerCount)) return layerCount - 1 - value;
    return value;
  }
  return Object.fromEntries(Object.entries(value).map(([entryKey, entryValue]) => [entryKey, acReverseSrjLayers(entryValue, entryKey, layerCount)]));
}
function acReverseP7Options(options, layerCount) {
  const { cacheProvider, ...structuredOptions } = options;
  return acReverseSrjLayers(structuredOptions, void 0, layerCount);
}
function acNamespaceCacheProvider(provider, namespace) {
  if (!provider) return null;
  const key = (cacheKey) => namespace + cacheKey;
  return {
    get isSyncCache() { return provider.isSyncCache; },
    get cacheHits() { return provider.cacheHits; },
    get cacheMisses() { return provider.cacheMisses; },
    get cacheHitsByPrefix() { return provider.cacheHitsByPrefix; },
    get cacheMissesByPrefix() { return provider.cacheMissesByPrefix; },
    getCachedSolutionSync: (cacheKey) => provider.getCachedSolutionSync(key(cacheKey)),
    getCachedSolution: (cacheKey) => provider.getCachedSolution(key(cacheKey)),
    setCachedSolutionSync: (cacheKey, solution) => provider.setCachedSolutionSync(key(cacheKey), solution),
    setCachedSolution: (cacheKey, solution) => provider.setCachedSolution(key(cacheKey), solution),
    getAllCacheKeys: () => provider.getAllCacheKeys().filter((cacheKey) => cacheKey.startsWith(namespace)).map((cacheKey) => cacheKey.slice(namespace.length)),
    clearCache: () => provider.clearCache()
  };
}
function acResolveDifferentialPairs`,
    },
  ],
}

const CORE_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "11a550a17956bd21322fbd0369483c46d95d658f802d3fda4bb0781b7f88a759",
  patchedSha256:
    "8e46388269506665537c37296ef98dab6073e5fc04d13a3d33392fc7d6adb73a",
  successorSha256s: [
    "94fdeb845b4325109f53bd9a0ccd3aed8820567f858c038abbed8b1f8ef21db7",
    "7b2a3a3052574df6ba9c642846033a920a9b54fcc918844530d141c60e119cce",
    "b69a0e7864af0899fb0b8a043a11b1766c3442b89311c3bd50452d8dd5227fa5",
    "4f99b17a5ec50462d0de9d0bf9e6092237866fb802d67a546bf0908c0ac1ca99",
    "cd6b449d8c03db0679d7cb4c4f8f4fedb0dc9aba8d219ccf3f025521a0c3d5f8",
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "route-cache key includes core, capacity, config, and SRJ",
      before:
        "var getLocalAutoroutingCacheKey = (simpleRouteJson) => `routes:core@${package_default.version}:srj:${getSrjHash(simpleRouteJson)}`;",
      after:
        "var getLocalAutoroutingCacheKey = (simpleRouteJson, routingConfig) => `routes:core@${package_default.version}:capacity@${autorouterVersion}:config:${getSrjHash(routingConfig)}:srj:${getSrjHash(simpleRouteJson)}`;",
    },
    {
      label: "route-cache descriptor contains every resolved routing input",
      before: `      const cacheEngine = phaseAutorouterConfig.algorithmFn || !localAutorouterStrategy.cacheable ? void 0 : this.root?.platform?.localCacheEngine;
      const cacheKey = cacheEngine ? getLocalAutoroutingCacheKey(simpleRouteJson) : void 0;`,
      after: `      const autorouterVersion2 = phaseAutorouterConfig.autorouterVersion ?? this.props.autorouterVersion;
      const effortLevel = this.props.autorouterEffortLevel;
      const effort = effortLevel ? Number.parseInt(effortLevel.replace("x", ""), 10) : void 0;
      const cacheEngine = phaseAutorouterConfig.algorithmFn || !localAutorouterStrategy.cacheable ? void 0 : this.root?.platform?.localCacheEngine;
      const cacheKey = cacheEngine ? getLocalAutoroutingCacheKey(simpleRouteJson, {
        preset: phaseAutorouterConfig.preset ?? "default",
        autorouterVersion: autorouterVersion2 ?? "default",
        effort: effort ?? 1,
        capacityDepth: phaseAutorouterConfig.capacityDepth ?? null,
        targetMinCapacity: phaseAutorouterConfig.targetMinCapacity ?? null,
        phaseStageIndex,
        phaseStageCount,
        useAssignableSolver: phaseIsLaserPrefabPreset || isSingleLayerBoard,
        useAutoJumperSolver: phaseIsAutoJumperPreset,
        useLaserPrefabSolver: phaseIsLaserPrefabPreset,
        busFanoutDirections: routingPhasePlan.busFanoutDirections ?? null,
        fanoutBounds: routingPhasePlan.fanoutBounds ?? null,
        fanoutRoutingLayers: routingPhasePlan.fanoutRoutingLayers ?? null,
        traceClearance: phaseAutorouterConfig.traceClearance ?? null,
        minTraceToPadEdgeClearance: simpleRouteJson.minTraceToPadEdgeClearance ?? null,
        minViaEdgeToPadEdgeClearance: simpleRouteJson.minViaEdgeToPadEdgeClearance ?? null,
        defaultObstacleMargin: simpleRouteJson.defaultObstacleMargin ?? null
      }) : void 0;`,
    },
    {
      label: "resolved route options are shared by cache and solver",
      before: `            const autorouterVersion2 = phaseAutorouterConfig.autorouterVersion ?? this.props.autorouterVersion;
            const effortLevel = this.props.autorouterEffortLevel;
            const effort = effortLevel ? Number.parseInt(effortLevel.replace("x", ""), 10) : void 0;
            const commonAutorouterOptions = {`,
      after: "            const commonAutorouterOptions = {",
    },
  ],
}

const CORE_FANOUT_DIRECTION_RETRY_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  // This is deliberately a second stage. A clean install goes pristine ->
  // cache-key patch -> fanout patch, while a developer who already applied
  // the first stage can safely advance without reinstalling node_modules.
  pristineSha256:
    "8e46388269506665537c37296ef98dab6073e5fc04d13a3d33392fc7d6adb73a",
  patchedSha256:
    "94fdeb845b4325109f53bd9a0ccd3aed8820567f858c038abbed8b1f8ef21db7",
  successorSha256s: [
    "7b2a3a3052574df6ba9c642846033a920a9b54fcc918844530d141c60e119cce",
    "b69a0e7864af0899fb0b8a043a11b1766c3442b89311c3bd50452d8dd5227fa5",
    "4f99b17a5ec50462d0de9d0bf9e6092237866fb802d67a546bf0908c0ac1ca99",
    "cd6b449d8c03db0679d7cb4c4f8f4fedb0dc9aba8d219ccf3f025521a0c3d5f8",
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "fanout retries inferred directions after a failed fixed-direction pass",
      before: `  solveFanout() {
    const fanoutSolverOptions = this.getFanoutSolverOptions();
    const fanoutSolver = new FanoutSolver(
      this.input,
      {
        ...fanoutSolverOptions,
        ...this.options.fanoutBounds ? { sharedBoundary: this.options.fanoutBounds } : {}
      }
    );
    fanoutSolver.solve();
    if (fanoutSolver.failed) {`,
      after: `  solveFanout() {
    const fanoutSolverOptions = this.getFanoutSolverOptions();
    const createFanoutSolver = (busDirections) => new FanoutSolver(
      this.input,
      {
        ...fanoutSolverOptions,
        ...busDirections ? { busDirections } : {},
        ...this.options.fanoutBounds ? { sharedBoundary: this.options.fanoutBounds } : {}
      }
    );
    let fanoutSolver = createFanoutSolver(fanoutSolverOptions.busDirections);
    fanoutSolver.solve();
    if (fanoutSolver.failed) {
      const explicitDirectionBusIds = new Set([
        ...Object.keys(this.options.busFanoutDirections ?? {}),
        ...this.input.buses?.filter((bus) => bus.direction !== void 0 || bus.preferredExit !== void 0).map((bus) => bus.busId) ?? []
      ]);
      const getBestSummary = (solver) => solver.attempts.reduce(
        (best, attempt) => !best || attempt.score < best.score ? attempt : best,
        void 0
      );
      let currentSummary = getBestSummary(fanoutSolver);
      let currentDirections = Object.fromEntries(
        fanoutSolver.preparedBuses.map((bus) => [bus.busId, bus.direction])
      );
      let retryCandidateCount = 0;
      const directionOrder = ["left", "right", "up", "down"];
      for (let retryRound = 0; currentSummary && fanoutSolver.failed && retryRound < (this.input.buses?.length ?? 0) && retryCandidateCount < 32; retryRound++) {
        let bestRetry;
        for (const busId of currentSummary.failedBusIds) {
          if (explicitDirectionBusIds.has(busId)) continue;
          for (const direction of directionOrder) {
            if (direction === currentDirections[busId] || retryCandidateCount >= 32) continue;
            retryCandidateCount++;
            let candidate;
            try {
              candidate = createFanoutSolver({
                ...currentDirections,
                [busId]: direction
              });
              candidate.solve();
            } catch {
              continue;
            }
            const candidateSummary = getBestSummary(candidate);
            if (!candidateSummary || candidateSummary.routedConnectionCount <= currentSummary.routedConnectionCount) continue;
            if (!bestRetry || candidateSummary.routedConnectionCount > bestRetry.summary.routedConnectionCount || candidateSummary.routedConnectionCount === bestRetry.summary.routedConnectionCount && candidateSummary.score < bestRetry.summary.score) {
              bestRetry = {
                solver: candidate,
                summary: candidateSummary,
                directions: {
                  ...currentDirections,
                  [busId]: direction
                }
              };
            }
          }
        }
        if (!bestRetry) break;
        fanoutSolver = bestRetry.solver;
        currentSummary = bestRetry.summary;
        currentDirections = bestRetry.directions;
      }
    }
    if (fanoutSolver.failed) {`,
    },
  ],
}

const CORE_ORDINARY_PHASE_REGION_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "94fdeb845b4325109f53bd9a0ccd3aed8820567f858c038abbed8b1f8ef21db7",
  patchedSha256:
    "7b2a3a3052574df6ba9c642846033a920a9b54fcc918844530d141c60e119cce",
  successorSha256s: [
    "b69a0e7864af0899fb0b8a043a11b1766c3442b89311c3bd50452d8dd5227fa5",
    "4f99b17a5ec50462d0de9d0bf9e6092237866fb802d67a546bf0908c0ac1ca99",
    "cd6b449d8c03db0679d7cb4c4f8f4fedb0dc9aba8d219ccf3f025521a0c3d5f8",
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "ordinary autorouting phase region becomes its routing bounds",
      before: `    plan.phaseName = phaseProps?.name;
    plan.reroute = phaseProps?.reroute;
    plan.region = phaseProps?.region;
    plan.connectionSelectors = phaseProps ? getConnectionSelectorsFromAutoroutingPhaseProps(phaseProps) : void 0;`,
      after: `    plan.phaseName = phaseProps?.name;
    plan.reroute = phaseProps?.reroute;
    plan.region = phaseProps?.region;
    plan.routingBounds = phaseProps?.reroute ? void 0 : phaseProps?.region;
    plan.connectionSelectors = phaseProps ? getConnectionSelectorsFromAutoroutingPhaseProps(phaseProps) : void 0;`,
    },
  ],
}

const CORE_UNKNOWN_AUTOROUTER_PRESET_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "7b2a3a3052574df6ba9c642846033a920a9b54fcc918844530d141c60e119cce",
  patchedSha256:
    "b69a0e7864af0899fb0b8a043a11b1766c3442b89311c3bd50452d8dd5227fa5",
  successorSha256s: [
    "4f99b17a5ec50462d0de9d0bf9e6092237866fb802d67a546bf0908c0ac1ca99",
    "cd6b449d8c03db0679d7cb4c4f8f4fedb0dc9aba8d219ccf3f025521a0c3d5f8",
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "unknown local autorouter presets fail instead of selecting capacity routing",
      before: `    default:
      return {
        local: true,
        groupMode: "subcircuit"
      };
  }
}`,
      after: `    default: {
      if (normalizedPreset !== void 0) {
        throw new Error(
          \`Unsupported autorouter preset "\${normalizedPreset}": @tscircuit/core has no local implementation for this preset. Use a supported preset or register it in platform.autorouterMap.\`
        );
      }
      return {
        local: true,
        groupMode: "subcircuit"
      };
    }
  }
}`,
    },
  ],
}

const CORE_MANUAL_TRACE_PRESERVATION_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "b69a0e7864af0899fb0b8a043a11b1766c3442b89311c3bd50452d8dd5227fa5",
  patchedSha256:
    "4f99b17a5ec50462d0de9d0bf9e6092237866fb802d67a546bf0908c0ac1ca99",
  successorSha256s: [
    "cd6b449d8c03db0679d7cb4c4f8f4fedb0dc9aba8d219ccf3f025521a0c3d5f8",
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "current-subcircuit fixed traces can be explicitly preserved",
      before: `var getPreservedRoutedSubcircuitTraces = ({
  scopedDb,
  currentSubcircuitId,
  relevantSubcircuitIds
}) => scopedDb.pcb_trace.list().filter((trace) => {
  if (!trace.subcircuit_id) return false;
  if (!currentSubcircuitId) return true;
  return trace.subcircuit_id !== currentSubcircuitId && relevantSubcircuitIds.has(trace.subcircuit_id);`,
      after: `var getPreservedRoutedSubcircuitTraces = ({
  scopedDb,
  currentSubcircuitId,
  relevantSubcircuitIds,
  currentSubcircuitPreservedSourceTraceIds
}) => scopedDb.pcb_trace.list().filter((trace) => {
  if (!trace.subcircuit_id) return false;
  if (!currentSubcircuitId) return true;
  if (trace.subcircuit_id === currentSubcircuitId) {
    return Boolean(
      trace.source_trace_id && currentSubcircuitPreservedSourceTraceIds.has(trace.source_trace_id)
    );
  }
  return trace.subcircuit_id !== currentSubcircuitId && relevantSubcircuitIds.has(trace.subcircuit_id);`,
    },
    {
      label: "pcbPath and pcbStraightLine copper enters the base SRJ as fixed traces",
      before: `  const preservedRoutedSubcircuitTraces = getPreservedRoutedSubcircuitTraces({
    scopedDb: db,
    currentSubcircuitId: subcircuit_id,
    relevantSubcircuitIds
  });`,
      after: `  const currentSubcircuitPreservedSourceTraceIds = new Set(
    (subcircuitComponent?.selectAll("trace") ?? []).filter(
      (trace) => trace._parsedProps.pcbPath !== void 0 || trace._parsedProps.pcbStraightLine === true
    ).map((trace) => trace.source_trace_id).filter((sourceTraceId) => Boolean(sourceTraceId))
  );
  const preservedRoutedSubcircuitTraces = getPreservedRoutedSubcircuitTraces({
    scopedDb: db,
    currentSubcircuitId: subcircuit_id,
    relevantSubcircuitIds,
    currentSubcircuitPreservedSourceTraceIds
  });`,
    },
    {
      label: "only actually preserved copper suppresses an autorouter connection",
      before: `  const sourceTraceIdsAlreadyPreservedAsSrjTraces = new Set(
    db.pcb_trace.list().filter((t) => {
      if (!t.source_trace_id) return false;
      if (subcircuit_id) return t.subcircuit_id === subcircuit_id;
      if (!t.subcircuit_id) return false;
      const sourceTrace = db.source_trace.get(t.source_trace_id);
      return sourceTrace?.subcircuit_id === t.subcircuit_id;
    }).map((t) => t.source_trace_id).filter((id) => Boolean(id))
  );`,
      after: `  const sourceTraceIdsAlreadyPreservedAsSrjTraces = new Set(
    preservedRoutedSubcircuitTraces.map((trace) => trace.source_trace_id).filter((id) => Boolean(id))
  );`,
    },
  ],
}

const CORE_PLANE_TERMINATED_NET_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "4f99b17a5ec50462d0de9d0bf9e6092237866fb802d67a546bf0908c0ac1ca99",
  patchedSha256:
    "cd6b449d8c03db0679d7cb4c4f8f4fedb0dc9aba8d219ccf3f025521a0c3d5f8",
  successorSha256s: [
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "plane-terminated nets do not become redundant aggregate routes",
      before: `  const groupFanoutProps = group._parsedProps;
  const hasDirectRoutingTargets = traces.length > 0 || nets.length > 0;`,
      after: `  const groupFanoutProps = group._parsedProps;
  const planeTerminatedNetNames = /* @__PURE__ */ new Set();
  const fanoutPourNetMaps = [
    groupFanoutProps.fanoutPourNetMap,
    ...Array.from(phasePropsByPhaseIndex.values()).map(
      (phaseProps) => phaseProps.fanoutPourNetMap
    )
  ].filter((fanoutPourNetMap) => fanoutPourNetMap !== void 0);
  for (const fanoutPourNetMap of fanoutPourNetMaps) {
    for (const netOrNets of Object.values(fanoutPourNetMap)) {
      const netNames = Array.isArray(netOrNets) ? netOrNets : [netOrNets];
      for (const netName of netNames) {
        planeTerminatedNetNames.add(normalizeNetName(netName));
      }
    }
  }
  const hasDirectRoutingTargets = traces.length > 0 || nets.length > 0;`,
    },
    {
      label: "skip aggregate connection for every plane-terminated net",
      before: `  for (const net of nets) {
    if (breakoutByNet.has(net)) continue;
    const routingPhaseIndex = getNetRoutingPhaseIndex(net);`,
      after: `  for (const net of nets) {
    if (breakoutByNet.has(net)) continue;
    if (planeTerminatedNetNames.has(net.name)) continue;
    const routingPhaseIndex = getNetRoutingPhaseIndex(net);`,
    },
  ],
}

const CORE_SAME_LAYER_PLANE_TERMINATION_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "cd6b449d8c03db0679d7cb4c4f8f4fedb0dc9aba8d219ccf3f025521a0c3d5f8",
  patchedSha256:
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
  successorSha256s: [
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "plane fanout accepts a target on the source pad layer",
      before: `  const sourceObstacle = bus.connections[0]?.sourceObstacle;
  if (!sourceObstacle || bus.termination.type !== "plane") return null;
  const sourceLayer = bus.connections[0].sourceLayer;
  if (targetLayer === sourceLayer) return null;`,
      after: `  const sourceObstacle = bus.connections[0]?.sourceObstacle;
  if (!sourceObstacle || bus.termination.type !== "plane") return null;`,
    },
    {
      label: "same-layer plane contact is an explicit non-copper marker",
      before: `          clearance,
          terminateAtVia: true
        });
        if (!planIsClear({`,
      after: `          clearance,
          terminateAtVia: true
        });
        if (preparedConnection.sourceLayer === targetLayer) {
          plan.exitPoint = {
            x: preparedConnection.sourcePoint.x,
            y: preparedConnection.sourcePoint.y
          };
          plan.trace.route = plan.trace.route.slice(0, 1);
          plan.trace.route[0].is_inside_copper_pour = true;
          plan.segments = [];
          plan.length = 0;
        }
        if (!planIsClear({`,
    },
    {
      label: "fanout traces carry their authored source trace identity",
      before: `      type: "pcb_trace",
      pcb_trace_id: \`fanout:\${preparedConnection.connection.name}\`,
      connection_name: preparedConnection.connection.name,`,
      after: `      type: "pcb_trace",
      pcb_trace_id: \`fanout:\${preparedConnection.connection.name}\`,
      source_trace_id: preparedConnection.connection.source_trace_id,
      connection_name: preparedConnection.connection.name,`,
    },
    {
      label: "fanout solver no longer rejects same-layer plane buses",
      before: `      if (bus.connections.some(
        (connection) => connection.sourceLayer === planeLayer
      )) {
        throw new Error(
          \`FanoutSolver: plane-terminated bus "\${bus.busId}" must target a layer below its source pad\`
        );
      }
`,
      after: "",
    },
    {
      label: "collect every authored plane layer for a net",
      before: `var getPlaneTerminatedSourceTraceLayers = ({
  fanoutPourNetMap,
  sourceNets,
  sourceTraces,
  subcircuitId
}) => {
  const traceLayers = /* @__PURE__ */ new Map();
  if (!fanoutPourNetMap) return traceLayers;
  const planeLayerBySourceNetId = /* @__PURE__ */ new Map();
  for (const [layer, netOrNets] of Object.entries(fanoutPourNetMap)) {
    const netNames = Array.isArray(netOrNets) ? netOrNets : [netOrNets];
    for (const netNameOrSelector of netNames) {
      const netName = normalizeNetName(netNameOrSelector);
      for (const sourceNet of sourceNets) {
        if (sourceNet.name !== netName) continue;
        const previousLayer = planeLayerBySourceNetId.get(
          sourceNet.source_net_id
        );
        if (previousLayer && previousLayer !== layer) {
          throw new Error(
            \`Fanout plane net "\${netName}" maps to multiple layers ("\${previousLayer}" and "\${layer}"); use fanoutPourNetMap to select one\`
          );
        }
        planeLayerBySourceNetId.set(sourceNet.source_net_id, layer);
      }
    }
  }`,
      after: `var getPlaneTerminatedSourceTraceLayers = ({
  fanoutPourNetMap,
  sourceNets,
  sourceTraces,
  pcbPorts,
  subcircuitId
}) => {
  const traceLayers = /* @__PURE__ */ new Map();
  if (!fanoutPourNetMap) return traceLayers;
  const planeLayersBySourceNetId = /* @__PURE__ */ new Map();
  for (const [layer, netOrNets] of Object.entries(fanoutPourNetMap)) {
    const netNames = Array.isArray(netOrNets) ? netOrNets : [netOrNets];
    for (const netNameOrSelector of netNames) {
      const netName = normalizeNetName(netNameOrSelector);
      for (const sourceNet of sourceNets) {
        if (sourceNet.name !== netName) continue;
        const layers = planeLayersBySourceNetId.get(sourceNet.source_net_id) ?? /* @__PURE__ */ new Set();
        layers.add(layer);
        planeLayersBySourceNetId.set(sourceNet.source_net_id, layers);
      }
    }
  }`,
    },
    {
      label: "two-sided plane nets choose each one-port trace's pad layer",
      before: `    const mappedLayers = new Set(
      (sourceTrace.connected_source_net_ids ?? []).map((sourceNetId) => planeLayerBySourceNetId.get(sourceNetId)).filter((layer2) => layer2 !== void 0)
    );
    if (mappedLayers.size > 1) {
      throw new Error(
        \`Source trace "\${sourceTrace.name ?? sourceTrace.source_trace_id}" connects to fanout planes on multiple layers\`
      );
    }
    const layer = mappedLayers.values().next().value;`,
      after: `    const mappedLayers = /* @__PURE__ */ new Set(
      (sourceTrace.connected_source_net_ids ?? []).flatMap(
        (sourceNetId) => Array.from(planeLayersBySourceNetId.get(sourceNetId) ?? [])
      )
    );
    let layer = mappedLayers.values().next().value;
    if (mappedLayers.size > 1) {
      const connectedSourcePortIds = new Set(sourceTrace.connected_source_port_ids);
      const portLayers = new Set(
        pcbPorts.filter((port) => port.source_port_id && connectedSourcePortIds.has(port.source_port_id)).flatMap((port) => port.layers ?? [])
      );
      const sameLayerCandidates = Array.from(mappedLayers).filter(
        (candidateLayer) => portLayers.has(candidateLayer)
      );
      if (sameLayerCandidates.length === 0) {
        throw new Error(
          \`Source trace "\${sourceTrace.name ?? sourceTrace.source_trace_id}" connects to fanout planes on multiple layers but its pad is on none of them\`
        );
      }
      layer = sameLayerCandidates[0];
    }`,
    },
    {
      label: "plane-layer selection receives physical PCB port layers",
      before: `    sourceNets: db.source_net.list(),
    sourceTraces,
    subcircuitId: subcircuit_id`,
      after: `    sourceNets: db.source_net.list(),
    sourceTraces,
    pcbPorts: db.pcb_port.list(),
    subcircuitId: subcircuit_id`,
    },
  ],
}

const CORE_MANUAL_PCB_PATH_VIA_RULES_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "745c879d39a8e2c505a6007c86a82d3906632b46de20faf12ca36d29f51fdb2e",
  patchedSha256:
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
  successorSha256s: [
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "manual pcbPath vias inherit board minima and local PCB style",
      before: `  const traceLength = getTraceLength(route);
  const pcb_trace = db.pcb_trace.insert({`,
      after: `  const traceLength = getTraceLength(route);
  const pcbStyle = trace.getInheritedMergedProperty("pcbStyle");
  const { holeDiameter, padDiameter } = getViaDiameterDefaults(pcbStyle);
  const board = db.pcb_board.list()[0];
  const minimumViaHoleDiameter = board?.min_via_hole_diameter ?? 0;
  const minimumViaPadDiameter = board?.min_via_pad_diameter ?? 0;
  for (const point of route) {
    if (point.route_type !== "via") continue;
    point.via_hole_diameter = Math.max(
      point.via_hole_diameter ?? point.hole_diameter ?? holeDiameter,
      minimumViaHoleDiameter
    );
    point.via_diameter = Math.max(
      point.via_diameter ?? point.outer_diameter ?? padDiameter,
      minimumViaPadDiameter
    );
  }
  const pcb_trace = db.pcb_trace.insert({`,
    },
    {
      label: "serialized manual copper and pcb_via share the legal dimensions",
      before: `  const subcircuitConnectivityMapKey = trace.subcircuit_connectivity_map_key ?? db.source_trace.get(trace.source_trace_id)?.subcircuit_connectivity_map_key;
  const pcbStyle = trace.getInheritedMergedProperty("pcbStyle");
  const { holeDiameter, padDiameter } = getViaDiameterDefaults(pcbStyle);
  for (const point6 of route) {
    if (point6.route_type === "via") {
      const fromLayer = point6.from_layer;
      const toLayer = point6.to_layer;
      db.pcb_via.insert({
        pcb_trace_id: pcb_trace.pcb_trace_id,
        x: point6.x,
        y: point6.y,
        hole_diameter: holeDiameter,
        outer_diameter: padDiameter,`,
      after: `  const subcircuitConnectivityMapKey = trace.subcircuit_connectivity_map_key ?? db.source_trace.get(trace.source_trace_id)?.subcircuit_connectivity_map_key;
  for (const point6 of route) {
    if (point6.route_type === "via") {
      const fromLayer = point6.from_layer;
      const toLayer = point6.to_layer;
      db.pcb_via.insert({
        pcb_trace_id: pcb_trace.pcb_trace_id,
        x: point6.x,
        y: point6.y,
        hole_diameter: point6.via_hole_diameter,
        outer_diameter: point6.via_diameter,`,
    },
  ],
}

const CORE_AUTHORED_NET_TREE_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "ea0435854d9be2b4b5dfba53e75c636e0208b2e0b72eea12d7cadcd304f25e41",
  patchedSha256:
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
  successorSha256s: [
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "validate and contract explicitly marked authored routing trees",
      before:
        "  return propsByPhaseIndex;\n}\nfunction toParsedDistance(value) {",
      after: "  return propsByPhaseIndex;\n}\nfunction Group_applyAuthoredNetTreeContracts(group, simpleRouteJson) {\n  const traceComponents = group.selectAll(\"trace\");\n  const markedBoundaryComponents = traceComponents.filter(\n    (trace) => trace._parsedProps.authoredNetTreeBoundary === true\n  );\n  if (markedBoundaryComponents.length === 0) return simpleRouteJson;\n  const { db } = group.root;\n  const selectedSourceTraceIds = new Set(\n    traceComponents.map((trace) => trace.source_trace_id).filter(Boolean)\n  );\n  const sourceTraces = db.source_trace.list().filter(\n    (trace) => selectedSourceTraceIds.has(trace.source_trace_id)\n  );\n  const sourceTraceById = new Map(\n    sourceTraces.map((trace) => [trace.source_trace_id, trace])\n  );\n  const traceComponentBySourceTraceId = new Map(\n    traceComponents.filter((trace) => trace.source_trace_id).map(\n      (trace) => [trace.source_trace_id, trace]\n    )\n  );\n  const portOnlyEdgesByPortId = /* @__PURE__ */ new Map();\n  for (const sourceTrace of sourceTraces) {\n    if ((sourceTrace.connected_source_net_ids?.length ?? 0) !== 0) continue;\n    if ((sourceTrace.connected_source_port_ids?.length ?? 0) !== 2) continue;\n    for (const sourcePortId of sourceTrace.connected_source_port_ids) {\n      const edges = portOnlyEdgesByPortId.get(sourcePortId) ?? [];\n      edges.push(sourceTrace);\n      portOnlyEdgesByPortId.set(sourcePortId, edges);\n    }\n  }\n  const contractsBySourceNetId = /* @__PURE__ */ new Map();\n  const claimedPortOnlySourceTraceIds = /* @__PURE__ */ new Set();\n  for (const markedBoundaryComponent of markedBoundaryComponents) {\n    const boundarySourceTrace = sourceTraceById.get(\n      markedBoundaryComponent.source_trace_id\n    );\n    if (!boundarySourceTrace) {\n      throw new Error(\n        `Authored net-tree boundary \"${markedBoundaryComponent.name}\" has no rendered source trace`\n      );\n    }\n    const boundaryPortIds = boundarySourceTrace.connected_source_port_ids ?? [];\n    const boundaryNetIds = boundarySourceTrace.connected_source_net_ids ?? [];\n    if (boundaryPortIds.length !== 1 || boundaryNetIds.length !== 1) {\n      throw new Error(\n        `Invalid authored net-tree boundary \"${boundarySourceTrace.name ?? boundarySourceTrace.source_trace_id}\": expected exactly one source port and one named net`\n      );\n    }\n    const boundaryPortId = boundaryPortIds[0];\n    const sourceNetId = boundaryNetIds[0];\n    const sourceNet = db.source_net.get(sourceNetId);\n    if (!sourceNet) {\n      throw new Error(\n        `Invalid authored net-tree boundary \"${boundarySourceTrace.name ?? boundarySourceTrace.source_trace_id}\": named net \"${sourceNetId}\" does not exist`\n      );\n    }\n    const componentPortIds = /* @__PURE__ */ new Set([boundaryPortId]);\n    const componentPortOnlyEdges = /* @__PURE__ */ new Map();\n    const pendingPortIds = [boundaryPortId];\n    while (pendingPortIds.length > 0) {\n      const sourcePortId = pendingPortIds.pop();\n      for (const edge of portOnlyEdgesByPortId.get(sourcePortId) ?? []) {\n        componentPortOnlyEdges.set(edge.source_trace_id, edge);\n        for (const adjacentPortId of edge.connected_source_port_ids) {\n          if (componentPortIds.has(adjacentPortId)) continue;\n          componentPortIds.add(adjacentPortId);\n          pendingPortIds.push(adjacentPortId);\n        }\n      }\n    }\n    if (componentPortOnlyEdges.size === 0) {\n      throw new Error(\n        `Invalid authored routing tree for net.${sourceNet.name}: boundary \"${boundarySourceTrace.name ?? boundarySourceTrace.source_trace_id}\" has no port-to-port tree branches`\n      );\n    }\n    if (componentPortOnlyEdges.size !== componentPortIds.size - 1) {\n      throw new Error(\n        `Invalid authored routing tree for net.${sourceNet.name}: ${componentPortOnlyEdges.size} port-to-port branches span ${componentPortIds.size} ports; expected ${componentPortIds.size - 1} for an acyclic tree`\n      );\n    }\n    const componentBoundaryTraces = sourceTraces.filter(\n      (sourceTrace) => (sourceTrace.connected_source_net_ids?.length ?? 0) > 0 && sourceTrace.connected_source_port_ids?.some(\n        (sourcePortId) => componentPortIds.has(sourcePortId)\n      )\n    );\n    if (componentBoundaryTraces.length !== 1 || componentBoundaryTraces[0].source_trace_id !== boundarySourceTrace.source_trace_id) {\n      const boundaryNames = componentBoundaryTraces.map(\n        (sourceTrace) => sourceTrace.name ?? sourceTrace.source_trace_id\n      );\n      throw new Error(\n        `Invalid authored routing tree for net.${sourceNet.name}: marked subtree must have exactly one port-to-net boundary, found ${componentBoundaryTraces.length}${boundaryNames.length > 0 ? ` (${boundaryNames.join(\", \")})` : \"\"}`\n      );\n    }\n    for (const sourceTraceId of componentPortOnlyEdges.keys()) {\n      if (claimedPortOnlySourceTraceIds.has(sourceTraceId)) {\n        throw new Error(\n          `Invalid authored routing tree for net.${sourceNet.name}: port-to-port branch \"${sourceTraceById.get(sourceTraceId)?.name ?? sourceTraceId}\" belongs to multiple marked subtrees`\n        );\n      }\n      claimedPortOnlySourceTraceIds.add(sourceTraceId);\n    }\n    const contract = contractsBySourceNetId.get(sourceNetId) ?? {\n      hiddenSourcePortIds: /* @__PURE__ */ new Set(),\n      boundarySourcePortIds: /* @__PURE__ */ new Set(),\n      boundaryWidths: []\n    };\n    for (const sourcePortId of componentPortIds) {\n      if (sourcePortId !== boundaryPortId) {\n        contract.hiddenSourcePortIds.add(sourcePortId);\n      }\n    }\n    contract.boundarySourcePortIds.add(boundaryPortId);\n    const boundaryTraceComponent = traceComponentBySourceTraceId.get(\n      boundarySourceTrace.source_trace_id\n    );\n    const boundaryWidth = Number(\n      boundaryTraceComponent?._parsedProps.thickness ?? boundaryTraceComponent?._parsedProps.width\n    );\n    if (Number.isFinite(boundaryWidth) && boundaryWidth > 0) {\n      contract.boundaryWidths.push(boundaryWidth);\n    }\n    contractsBySourceNetId.set(sourceNetId, contract);\n  }\n  const pcbPortIdsBySourcePortId = /* @__PURE__ */ new Map();\n  const sourcePortIdByPcbPortId = /* @__PURE__ */ new Map();\n  for (const pcbPort of db.pcb_port.list()) {\n    if (!pcbPort.source_port_id) continue;\n    const pcbPortIds = pcbPortIdsBySourcePortId.get(pcbPort.source_port_id) ?? [];\n    pcbPortIds.push(pcbPort.pcb_port_id);\n    pcbPortIdsBySourcePortId.set(pcbPort.source_port_id, pcbPortIds);\n    sourcePortIdByPcbPortId.set(pcbPort.pcb_port_id, pcbPort.source_port_id);\n  }\n  for (const [sourceNetId, contract] of contractsBySourceNetId) {\n    const netConnection = simpleRouteJson.connections.find(\n      (connection) => connection.name === sourceNetId\n    );\n    if (!netConnection) {\n      throw new Error(\n        `Invalid authored routing tree for \"${sourceNetId}\": the named-net aggregate is absent from the autorouter input`\n      );\n    }\n    const pointSourcePortIds = new Set(\n      netConnection.pointsToConnect.map(\n        (point) => sourcePortIdByPcbPortId.get(point.pcb_port_id ?? point.pointId)\n      ).filter(Boolean)\n    );\n    for (const sourcePortId of [\n      ...contract.hiddenSourcePortIds,\n      ...contract.boundarySourcePortIds\n    ]) {\n      const pcbPortIds = pcbPortIdsBySourcePortId.get(sourcePortId) ?? [];\n      if (pcbPortIds.length === 0 || !pointSourcePortIds.has(sourcePortId)) {\n        throw new Error(\n          `Invalid authored routing tree for \"${sourceNetId}\": source port \"${sourcePortId}\" has no named-net PCB endpoint to contract`\n        );\n      }\n    }\n  }\n  const connections = [];\n  for (const connection of simpleRouteJson.connections) {\n    if (claimedPortOnlySourceTraceIds.has(\n      connection.source_trace_id ?? connection.name\n    )) {\n      connections.push({\n        ...connection,\n        __preserveConnectionTopology: true\n      });\n      continue;\n    }\n    const contract = contractsBySourceNetId.get(connection.name);\n    if (!contract) {\n      connections.push(connection);\n      continue;\n    }\n    const pointsToConnect = connection.pointsToConnect.filter((point) => {\n      const sourcePortId = sourcePortIdByPcbPortId.get(\n        point.pcb_port_id ?? point.pointId\n      );\n      return !sourcePortId || !contract.hiddenSourcePortIds.has(sourcePortId);\n    });\n    if (pointsToConnect.length <= 1) continue;\n    const boundaryWidth = Math.max(0, ...contract.boundaryWidths);\n    connections.push({\n      ...connection,\n      pointsToConnect,\n      ...boundaryWidth > 0 ? {\n        nominalTraceWidth: Math.max(\n          connection.nominalTraceWidth ?? 0,\n          boundaryWidth\n        ),\n        width: Math.max(connection.width ?? 0, boundaryWidth)\n      } : {}\n    });\n  }\n  return {\n    ...simpleRouteJson,\n    connections\n  };\n}\nfunction toParsedDistance(value) {",
    },
    {
      label: "invalid authored routing trees serialize a PCB autorouting error",
      before: "    });\n    const outputTraces = [];",
      after: "    });\n    try {\n      baseSimpleRouteJson = Group_applyAuthoredNetTreeContracts(\n        this,\n        baseSimpleRouteJson\n      );\n    } catch (error) {\n      const pcbErrorId = `pcb_autorouting_authored_tree_error_${this.subcircuit_id ?? this.source_group_id ?? this._renderId}`;\n      if (!db.pcb_autorouting_error.list().some(\n        (existingError) => existingError.pcb_error_id === pcbErrorId\n      )) {\n        db.pcb_autorouting_error.insert({\n          pcb_error_id: pcbErrorId,\n          error_type: \"pcb_autorouting_error\",\n          subcircuit_id: this.subcircuit_id ?? void 0,\n          message: error instanceof Error ? error.message : String(error)\n        });\n      }\n      throw error;\n    }\n    const outputTraces = [];",
    },
  ],
}

const CORE_DECOUPLING_MAX_LENGTH_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "84be17d3b2beb909426dcf5140cd141bd6417bc2c22e465daa0e5c502d8684b8",
  patchedSha256:
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
  successorSha256s: [
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "planned plane fanouts are not measured against remote net ports",
      before: `  const subcircuitSourceTraces = db.source_trace.list().filter(
    (sourceTrace) => sourceTrace.subcircuit_id === subcircuit.subcircuit_id
  );
  const sourceConnectivityMap = new ConnectivityMap({});`,
      after: `  const subcircuitSourceTraces = db.source_trace.list().filter(
    (sourceTrace) => sourceTrace.subcircuit_id === subcircuit.subcircuit_id
  );
  const planeTerminatedSourceTraceIds = /* @__PURE__ */ new Set();
  if (typeof subcircuit._getRoutingPhasePlans === "function") {
    for (const phasePlan of subcircuit._getRoutingPhasePlans()) {
      const preset = typeof phasePlan.autorouter === "string" ? phasePlan.autorouter : phasePlan.autorouter?.preset;
      if (preset !== "fanout" && preset !== "single_layer_fanout") continue;
      const mappedNetNames = new Set(
        Object.values(phasePlan.fanoutPourNetMap ?? {}).flatMap(
          (netOrNets) => Array.isArray(netOrNets) ? netOrNets : [netOrNets]
        ).map((netName) => normalizeNetName(netName))
      );
      if (mappedNetNames.size === 0) continue;
      for (const traceComponent of phasePlan.traces) {
        if (!traceComponent.source_trace_id) continue;
        const sourceTrace = db.source_trace.get(traceComponent.source_trace_id);
        if (!sourceTrace) continue;
        if (sourceTrace.connected_source_net_ids.some((sourceNetId) => {
          const sourceNet = db.source_net.get(sourceNetId);
          return sourceNet && mappedNetNames.has(sourceNet.name);
        })) {
          planeTerminatedSourceTraceIds.add(sourceTrace.source_trace_id);
        }
      }
    }
  }
  const sourceConnectivityMap = new ConnectivityMap({});`,
    },
    {
      label: "explicit plane-drop limits wait for the solved fanout route",
      before: `      if (pcbEndpoints.length !== 1 || sourceTrace.connected_source_net_ids.length === 0)
        continue;
      const sourceNetworkId = sourceConnectivityMap.getNetConnectedToId(`,
      after: `      if (pcbEndpoints.length !== 1 || sourceTrace.connected_source_net_ids.length === 0)
        continue;
      if (planeTerminatedSourceTraceIds.has(sourceTrace.source_trace_id))
        continue;
      const sourceNetworkId = sourceConnectivityMap.getNetConnectedToId(`,
    },
    {
      label: "automatic decoupling limits apply only to cap-to-device branches",
      before: `var getMaxLengthFromConnectedComponents = (ports, { db }) => {
  const componentMaxLengths = ports.map((port) => {
    const sourcePort = db.source_port.get(port.source_port_id);
    if (!sourcePort?.source_component_id) return null;
    const sourceComponent = db.source_component.get(
      sourcePort.source_component_id
    );
    if (sourceComponent?.ftype === "simple_capacitor") {
      return sourceComponent.max_decoupling_trace_length;
    }
    return null;
  }).filter((length7) => typeof length7 === "number");
  if (componentMaxLengths.length === 0) return void 0;
  return Math.min(...componentMaxLengths);
};`,
      after: `var getMaxLengthFromConnectedComponents = (ports, { db }) => {
  if (ports.length !== 2) return void 0;
  const sourceComponents = ports.map((port) => {
    const sourcePort = db.source_port.get(port.source_port_id);
    if (!sourcePort?.source_component_id) return null;
    return db.source_component.get(
      sourcePort.source_component_id
    );
  }).filter(Boolean);
  if (sourceComponents.length !== 2) return void 0;
  const capacitorComponents = sourceComponents.filter(
    (sourceComponent) => sourceComponent.ftype === "simple_capacitor"
  );
  if (capacitorComponents.length !== 1) return void 0;
  return capacitorComponents[0].max_decoupling_trace_length;
};`,
    },
  ],
}

const CORE_DIFFERENTIAL_PAIR_SOURCE_CONTRACT_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "c081f2d668a6b594244058e5defbe3a464b9abf55afe4284a5ae4a6669b30c77",
  patchedSha256:
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
  successorSha256s: [
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "differential-pair source-contract failures serialize",
      before:
        "// lib/utils/autorouting/getDifferentialPairsForSimpleRouteJson.ts\nvar getDifferentialPairSourceTracesByTraceName",
      after: `// lib/utils/autorouting/getDifferentialPairsForSimpleRouteJson.ts
var throwDifferentialPairSourceContractError = (differentialPair, message) => {
  const db = differentialPair.root?.db;
  if (db) {
    const subcircuitId = differentialPair.getSubcircuit()?.subcircuit_id;
    const pcbErrorId = \`pcb_autorouting_differential_pair_contract_\${subcircuitId ?? differentialPair._renderId ?? differentialPair.name}\`;
    if (!db.pcb_autorouting_error.list().some(
      (error) => error.pcb_error_id === pcbErrorId
    )) {
      db.pcb_autorouting_error.insert({
        pcb_error_id: pcbErrorId,
        error_type: "pcb_autorouting_error",
        subcircuit_id: subcircuitId ?? void 0,
        message
      });
    }
  }
  throw new Error(message);
};
var getDifferentialPairSourceTracesByTraceName`,
    },
    {
      label: "differential pairs require direct two-port source traces",
      before: `  const subcircuitConnectivityMapKey = sourceTrace.subcircuit_connectivity_map_key;
  if (!subcircuitConnectivityMapKey) {`,
      after: `  if ((sourceTrace.connected_source_port_ids?.length ?? 0) !== 2 || (sourceTrace.connected_source_net_ids?.length ?? 0) !== 0) {
    throwDifferentialPairSourceContractError(
      differentialPair,
      \`Differential pair "\${differentialPair.name}" connection "\${traceNameOrPortSelector}" must select a direct two-port source trace; named-net aggregate and composed source traces are unsupported\`
    );
  }
  const subcircuitConnectivityMapKey = sourceTrace.subcircuit_connectivity_map_key;
  if (!subcircuitConnectivityMapKey) {`,
    },
    {
      label: "maxUncoupledLength requires an explicit coupling gap",
      before: `    const positiveTraceNameOrPortSelector = differentialPair._parsedProps.positiveConnection;
    const negativeTraceNameOrPortSelector = differentialPair._parsedProps.negativeConnection;`,
      after: `    const positiveTraceNameOrPortSelector = differentialPair._parsedProps.positiveConnection;
    const negativeTraceNameOrPortSelector = differentialPair._parsedProps.negativeConnection;
    if (differentialPair._parsedProps.maxUncoupledLength !== void 0 && differentialPair._parsedProps.pcbTraceGap === void 0) {
      throwDifferentialPairSourceContractError(
        differentialPair,
        \`Differential pair "\${differentialPair.name}" declares maxUncoupledLength without pcbTraceGap; the coupling threshold is undefined\`
      );
    }`,
    },
  ],
}

const CORE_ROUTED_TRACE_VIA_STYLE_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "ccdb7a1620393a9d3d7d4695f82e11d464116a4caa75bf8711f500da18bb502b",
  patchedSha256:
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
  successorSha256s: [
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  ],
  replacements: [
    {
      label: "routed vias resolve the owning trace's PCB style above board floors",
      before: `    const pcbStyle = this.getInheritedMergedProperty("pcbStyle");
    const { holeDiameter, padDiameter } = getViaDiameterDefaults(pcbStyle);
    const board = db.pcb_board.list()[0];
    const routedViaHoleDiameter = board?.min_via_hole_diameter ?? holeDiameter;
    const routedViaPadDiameter = board?.min_via_pad_diameter ?? padDiameter;`,
      after: `    const pcbStyle = this.getInheritedMergedProperty("pcbStyle");
    const board = db.pcb_board.list()[0];
    const minimumViaHoleDiameter = board?.min_via_hole_diameter ?? 0;
    const minimumViaPadDiameter = board?.min_via_pad_diameter ?? 0;
    const traceComponentBySourceTraceId = new Map(
      this.getDescendants().filter(
        (component) => component.componentName === "Trace" && component.source_trace_id
      ).map((component) => [component.source_trace_id, component])
    );
    const resolveRoutedViaDimensions = (sourceTraceId, point6) => {
      const traceComponent = traceComponentBySourceTraceId.get(sourceTraceId);
      const tracePcbStyle = traceComponent?.getInheritedMergedProperty("pcbStyle") ?? pcbStyle;
      const { holeDiameter, padDiameter } = getViaDiameterDefaults(tracePcbStyle);
      const routedViaHoleDiameter = Math.max(
        minimumViaHoleDiameter,
        holeDiameter,
        point6?.via_hole_diameter ?? point6?.hole_diameter ?? 0
      );
      const routedViaPadDiameter = Math.max(
        minimumViaPadDiameter,
        padDiameter,
        point6?.via_diameter ?? point6?.outer_diameter ?? 0
      );
      if (!Number.isFinite(routedViaHoleDiameter) || !Number.isFinite(routedViaPadDiameter) || routedViaHoleDiameter <= 0 || routedViaPadDiameter <= routedViaHoleDiameter) {
        throw new Error(
          \`Invalid routed via dimensions for "\${sourceTraceId ?? "aggregate connection"}": pad \${routedViaPadDiameter}mm must be greater than positive hole \${routedViaHoleDiameter}mm\`
        );
      }
      return {
        holeDiameter: routedViaHoleDiameter,
        padDiameter: routedViaPadDiameter
      };
    };`,
    },
    {
      label: "serialized pcb_trace via points carry the resolved dimensions",
      before: `      });
      cjRoute = ensureRouteStartsAtSourceTraceStart({
        db,
        route: cjRoute,
        sourceTraceId: routeSourceTraceId
      });`,
      after: `      });
      cjRoute = cjRoute.map((point6) => {
        if (point6.route_type !== "via") return point6;
        const viaDimensions = resolveRoutedViaDimensions(
          routeSourceTraceId,
          point6
        );
        return {
          ...point6,
          via_hole_diameter: viaDimensions.holeDiameter,
          via_diameter: viaDimensions.padDiameter
        };
      });
      cjRoute = ensureRouteStartsAtSourceTraceStart({
        db,
        route: cjRoute,
        sourceTraceId: routeSourceTraceId
      });`,
    },
    {
      label: "standalone pcb_via uses the same resolved route dimensions",
      before: `            const routedViaPoint = point6;
            const fromLayer = point6.from_layer;
            const toLayer = point6.to_layer;
            db.pcb_via.insert({
              pcb_trace_id: pcb_trace.pcb_trace_id,
              x: point6.x,
              y: point6.y,
              hole_diameter: routedViaPoint.via_hole_diameter ?? routedViaPoint.hole_diameter ?? routedViaHoleDiameter,
              outer_diameter: routedViaPoint.via_diameter ?? routedViaPoint.outer_diameter ?? routedViaPadDiameter,`,
      after: `            const routedViaPoint = point6;
            const viaDimensions = resolveRoutedViaDimensions(
              sourceTraceId,
              routedViaPoint
            );
            const fromLayer = point6.from_layer;
            const toLayer = point6.to_layer;
            db.pcb_via.insert({
              pcb_trace_id: pcb_trace.pcb_trace_id,
              x: point6.x,
              y: point6.y,
              hole_diameter: viaDimensions.holeDiameter,
              outer_diameter: viaDimensions.padDiameter,`,
    },
  ],
}

const CORE_AGGREGATE_ROUTE_IDENTITY_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "77a75ab63228a3bb0ea277e5c41836e7dedf200f424e6d50cd687b6d2e267b05",
  patchedSha256:
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  replacements: [
    {
      label: "named-net aggregate copper does not impersonate a local source edge",
      before: `function getSourceTraceIdForRoutedTrace({
  db,
  trace,
  subcircuit_id
}) {
  if (trace.source_trace_id && (db.source_trace.get(trace.source_trace_id) ?? db.source_net.get(trace.source_trace_id))) {`,
      after: `function getSourceTraceIdForRoutedTrace({
  db,
  trace,
  subcircuit_id
}) {
  if (trace.connection_name && db.source_net.get(trace.connection_name)) {
    return void 0;
  }
  if (trace.source_trace_id && (db.source_trace.get(trace.source_trace_id) ?? db.source_net.get(trace.source_trace_id))) {`,
    },
  ],
}

const CORE_DIFFERENTIAL_PAIR_TRACE_ENDPOINT_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "1b3842611b56102936e17fb33f4ccff18ea9d3562fbb6010e50cabdcb86000ae",
  patchedSha256:
    "2ccd8305aef9a52a6f12df388efcc53e91298e55d8afb261247f920bca958613",
  replacements: [
    {
      label: "differential-pair trace selectors validate their own physical endpoints",
      before: `  const matchingSourceTraces = subcircuitSourceTraces.filter(
    (sourceTrace) => sourceTrace.name === connectionSelector
  );
  const connectivityMapKeys = /* @__PURE__ */ new Set();`,
      after: `  const matchingSourceTraces = subcircuitSourceTraces.filter(
    (sourceTrace) => sourceTrace.name === connectionSelector
  );
  if (matchingSourceTraces.length === 1) {
    const selectedSourceTrace = matchingSourceTraces[0];
    const sourcePorts = (selectedSourceTrace.connected_source_port_ids ?? []).map(
      (sourcePortId) => db.source_port.get(sourcePortId)
    ).filter((sourcePort) => sourcePort !== void 0);
    return {
      sourcePorts,
      sourceTraceName: connectionSelector
    };
  }
  const connectivityMapKeys = /* @__PURE__ */ new Set();`,
    },
  ],
}

const CORE_VIA_IN_SMD_PAD_OUTPUT_GATE_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "2ccd8305aef9a52a6f12df388efcc53e91298e55d8afb261247f920bca958613",
  patchedSha256:
    "f16cc7ee806d3afa14b639e784eefde1014d141f22fdd087b9309a8a64b361c0",
  replacements: [
    {
      label: "local routing has a final via-to-SMD-pad clearance gate",
      before: "var Group5 = class extends NormalComponent3 {",
      after: `var Group_getSignedViaToSmdPadGap = ({ via, smdPad, viaDiameter }) => {
  const radius = viaDiameter / 2;
  if (smdPad.shape === "circle") {
    return Math.hypot(via.x - smdPad.center.x, via.y - smdPad.center.y) - smdPad.width / 2 - radius;
  }
  const rotationRadians = -Number(smdPad.ccwRotationDegrees ?? 0) * Math.PI / 180;
  const deltaX = via.x - smdPad.center.x;
  const deltaY = via.y - smdPad.center.y;
  const localX = Math.cos(rotationRadians) * deltaX - Math.sin(rotationRadians) * deltaY;
  const localY = Math.sin(rotationRadians) * deltaX + Math.cos(rotationRadians) * deltaY;
  const halfWidth = smdPad.width / 2;
  const halfHeight = smdPad.height / 2;
  const outsideX = Math.max(Math.abs(localX) - halfWidth, 0);
  const outsideY = Math.max(Math.abs(localY) - halfHeight, 0);
  const centerGap = outsideX > 0 || outsideY > 0 ? Math.hypot(outsideX, outsideY) : -Math.min(halfWidth - Math.abs(localX), halfHeight - Math.abs(localY));
  return centerGap - radius;
};
var Group_assertNoIllegalViaInSmdPad = ({ simpleRouteJson, traces, phaseName }) => {
  const clearance = simpleRouteJson.minViaEdgeToPadEdgeClearance ?? 0.1;
  const defaultViaDiameter = simpleRouteJson.minViaDiameter ?? 0.3;
  const smdPads = simpleRouteJson.obstacles.filter(
    (obstacle) => obstacle.layers.length === 1 && typeof obstacle.circuitJsonMetadata?.pcb_smtpad_id === "string"
  );
  const seenIssueKeys = /* @__PURE__ */ new Set();
  const issues = [];
  for (const trace of traces ?? []) {
    if (trace.type !== "pcb_trace" || !Array.isArray(trace.route)) continue;
    const traceConnectivityIds = /* @__PURE__ */ new Set([
      trace.connection_name,
      trace.source_trace_id,
      trace.pcb_trace_id,
      ...trace.connectsTo ?? []
    ].filter((id) => typeof id === "string"));
    for (const routePoint of trace.route) {
      if (routePoint.route_type !== "via") continue;
      const viaDiameter = routePoint.via_diameter ?? defaultViaDiameter;
      if (!Number.isFinite(viaDiameter) || viaDiameter <= 0) {
        throw new Error("Local autorouting output has an invalid via diameter for " + (trace.connection_name ?? trace.pcb_trace_id));
      }
      for (const smdPad of smdPads) {
        if (!smdPad.layers.includes(routePoint.from_layer) && !smdPad.layers.includes(routePoint.to_layer)) continue;
        const gap = Group_getSignedViaToSmdPadGap({
          via: routePoint,
          smdPad,
          viaDiameter
        });
        if (gap + 5e-3 >= clearance) continue;
        const pcbSmtpadId = smdPad.circuitJsonMetadata.pcb_smtpad_id;
        const sharesNet = smdPad.connectedTo.some(
          (connectedId) => traceConnectivityIds.has(connectedId)
        );
        if (sharesNet && simpleRouteJson.allowViaInPad === true) continue;
        const issueKey = (trace.pcb_trace_id ?? trace.connection_name) + ":" + routePoint.x + ":" + routePoint.y + ":" + pcbSmtpadId;
        if (seenIssueKeys.has(issueKey)) continue;
        seenIssueKeys.add(issueKey);
        issues.push({
          traceId: trace.pcb_trace_id ?? trace.connection_name ?? "trace",
          pcbSmtpadId,
          x: routePoint.x,
          y: routePoint.y,
          gap,
          sharesNet
        });
      }
    }
  }
  if (issues.length === 0) return;
  const first = issues[0];
  const phaseDescription = phaseName === void 0 ? "" : ' in phase "' + phaseName + '"';
  throw new Error(
    "Local autorouting output" + phaseDescription + " has " + issues.length +
    " illegal via-to-SMD-pad clearance issue(s); " + first.traceId +
    " via at (" + first.x.toFixed(3) + ", " + first.y.toFixed(3) + ") is " +
    first.gap.toFixed(3) + "mm from " + first.pcbSmtpadId +
    " (required " + clearance.toFixed(3) + "mm). " +
    (first.sharesNet ? "Via-in-pad is disabled." : "The via and pad are not connected.")
  );
};
var Group5 = class extends NormalComponent3 {`,
    },
    {
      label: "cached, Pipeline9, and fanout outputs pass the gate before use",
      before: `          traces = await routingPromise;
        }
        let transformedSimpleRouteJson`,
      after: `          traces = await routingPromise;
        }
        Group_assertNoIllegalViaInSmdPad({
          simpleRouteJson,
          traces,
          phaseName: routingPhasePlan.phaseName
        });
        let transformedSimpleRouteJson`,
    },
    {
      label: "via-in-pad permission participates in route-cache identity",
      before: `        defaultObstacleMargin: simpleRouteJson.defaultObstacleMargin ?? null
      }) : void 0;`,
      after: `        defaultObstacleMargin: simpleRouteJson.defaultObstacleMargin ?? null,
        allowViaInPad: simpleRouteJson.allowViaInPad ?? false
      }) : void 0;`,
    },
  ],
}

const CORE_DIFFERENTIAL_PAIR_PHASED_TRACE_SELECTION_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "f16cc7ee806d3afa14b639e784eefde1014d141f22fdd087b9309a8a64b361c0",
  patchedSha256:
    "8359d3082f85ccb2010810e8dfe9730fce9d2efb264d33aa96750d24d0a968d9",
  replacements: [
    {
      label: "differential-pair selection retains the exact source trace id",
      before:
        "var getDifferentialPairTraceSubcircuitConnectivityMapKeyOrThrow = ({",
      after: "var getDifferentialPairTraceSelectionOrThrow = ({",
    },
    {
      label: "differential-pair selection returns source and connectivity identities",
      before: `  return subcircuitConnectivityMapKey;
};`,
      after: `  return {
    sourceTraceId: sourceTrace.source_trace_id,
    subcircuitConnectivityMapKey
  };
};`,
    },
    {
      label: "SRJ differential-pair lookup accepts the exact source trace id",
      before: `  differentialPairSourceTraces,
  traceSubcircuitConnectivityMapKey,`,
      after: `  traceSourceTraceId,
  traceSubcircuitConnectivityMapKey,`,
    },
    {
      label: "SRJ differential-pair lookup does not expand adjacent electrical edges",
      before: `  const differentialPairSourceTraceIds = [];
  for (const sourceTrace of differentialPairSourceTraces) {
    if (sourceTrace.subcircuit_connectivity_map_key === traceSubcircuitConnectivityMapKey) {
      differentialPairSourceTraceIds.push(sourceTrace.source_trace_id);
    }
  }
`,
      after: "",
    },
    {
      label: "SRJ differential-pair lookup matches only the selected physical edge",
      before:
        "    if (srjConnection2.source_trace_id && differentialPairSourceTraceIds.includes(srjConnection2.source_trace_id)) {",
      after:
        "    if (srjConnection2.source_trace_id === traceSourceTraceId) {",
    },
    {
      label: "duplicate exact SRJ source identity fails closed descriptively",
      before:
        '      `Subcircuit connectivity map key "${traceSubcircuitConnectivityMapKey}" matches multiple SRJ connections for differential pair "${differentialPairName}"`',
      after:
        '      `Source trace "${traceSourceTraceId}" matches multiple SRJ connections for differential pair "${differentialPairName}"`',
    },
    {
      label: "positive and negative pair selections retain exact source identities",
      before: `    const positiveSubcircuitConnectivityMapKey = getDifferentialPairTraceSubcircuitConnectivityMapKeyOrThrow({
      differentialPair,
      differentialPairSourceTraces,
      traceNameOrPortSelector: positiveTraceNameOrPortSelector
    });
    const negativeSubcircuitConnectivityMapKey = getDifferentialPairTraceSubcircuitConnectivityMapKeyOrThrow({
      differentialPair,
      differentialPairSourceTraces,
      traceNameOrPortSelector: negativeTraceNameOrPortSelector
    });`,
      after: `    const positiveTraceSelection = getDifferentialPairTraceSelectionOrThrow({
      differentialPair,
      differentialPairSourceTraces,
      traceNameOrPortSelector: positiveTraceNameOrPortSelector
    });
    const negativeTraceSelection = getDifferentialPairTraceSelectionOrThrow({
      differentialPair,
      differentialPairSourceTraces,
      traceNameOrPortSelector: negativeTraceNameOrPortSelector
    });`,
    },
    {
      label: "pair SRJ names resolve from exact selected source traces",
      before: `      differentialPairSourceTraces,
      traceSubcircuitConnectivityMapKey: positiveSubcircuitConnectivityMapKey,
      traceNameOrPortSelector: positiveTraceNameOrPortSelector
    });
    const negativeSrjConnectionName = getDifferentialPairSrjConnectionNameOrThrow({
      srjConnections,
      differentialPairName: differentialPair.name,
      differentialPairSourceTraces,
      traceSubcircuitConnectivityMapKey: negativeSubcircuitConnectivityMapKey,`,
      after: `      traceSourceTraceId: positiveTraceSelection.sourceTraceId,
      traceSubcircuitConnectivityMapKey: positiveTraceSelection.subcircuitConnectivityMapKey,
      traceNameOrPortSelector: positiveTraceNameOrPortSelector
    });
    const negativeSrjConnectionName = getDifferentialPairSrjConnectionNameOrThrow({
      srjConnections,
      differentialPairName: differentialPair.name,
      traceSourceTraceId: negativeTraceSelection.sourceTraceId,
      traceSubcircuitConnectivityMapKey: negativeTraceSelection.subcircuitConnectivityMapKey,`,
    },
  ],
}

const CORE_LAYER_REVERSAL_RETRY_CACHE_IDENTITY_PATCH = {
  packageName: "@tscircuit/core",
  version: "0.0.1642",
  file: "dist/index.js",
  pristineSha256:
    "8359d3082f85ccb2010810e8dfe9730fce9d2efb264d33aa96750d24d0a968d9",
  patchedSha256:
    "6e014654d0bf4ce38d400ddf15ed3c6042d166771b3bc4e308785db48167a37b",
  replacements: [
    {
      label: "whole-phase route cache identifies bounded P7 layer reversal semantics",
      before: `        phaseStageCount,
        useAssignableSolver:`,
      after: `        phaseStageCount,
        capacityLayerReversalRetry: "p7-layer-reversal-v1",
        useAssignableSolver:`,
    },
  ],
}

const CHECKS_SOURCE_TRACE_WIDTH_IDENTITY_PATCH = {
  packageName: "@tscircuit/checks",
  version: "0.0.152",
  file: "dist/index.js",
  pristineSha256:
    "69dccac8dda12a4e32172f42e08671efecb0464838d8f270b8aa1882fda9600d",
  patchedSha256:
    "b77c5ae972302489becb20ddc1963c23eb01d7db610fd3bae3400aee6507192d",
  sourceMap: "dist/index.js.map",
  sourceGuards: [
    {
      source: "../lib/check-source-traces-match-pcb-trace-thickness.ts",
      contains:
        "const netElementIds = connectivityMap.getIdsConnectedToNet(referenceNetId)",
    },
  ],
  replacements: [
    {
      label: "trace-width checks prefer exact authored route identity",
      before: `    const referenceNetId = connectivityMap.getNetConnectedToId(
      connectedPcbPorts[0].pcb_port_id
    );
    if (!referenceNetId) continue;
    const netElementIds = connectivityMap.getIdsConnectedToNet(referenceNetId);
    const relatedPcbTraces = pcbTraces.filter(
      (pcbTrace) => netElementIds.includes(pcbTrace.pcb_trace_id)
    );`,
      after: `    const exactIdentityPcbTraces = pcbTraces.filter(
      (pcbTrace) => pcbTrace.source_trace_id === sourceTrace.source_trace_id
    );
    let relatedPcbTraces = exactIdentityPcbTraces;
    if (relatedPcbTraces.length === 0) {
      const referenceNetId = connectivityMap.getNetConnectedToId(
        connectedPcbPorts[0].pcb_port_id
      );
      if (!referenceNetId) continue;
      const netElementIds = connectivityMap.getIdsConnectedToNet(referenceNetId);
      relatedPcbTraces = pcbTraces.filter(
        (pcbTrace) => netElementIds.includes(pcbTrace.pcb_trace_id)
      );
    }`,
    },
  ],
}

export const TOOLCHAIN_PATCHES = [
  PROPS_AUTHORED_NET_TREE_RUNTIME_PATCH,
  PROPS_AUTHORED_NET_TREE_TYPES_PATCH,
  CAPACITY_PATCH,
  CAPACITY_DYNAMIC_TRACE_CONNECTIVITY_PATCH,
  CAPACITY_PRELOADED_TRACE_EXACT_DRC_PATCH,
  CAPACITY_THROUGH_OBSTACLE_DRC_PATCH,
  CAPACITY_AUTHORED_NET_TREE_TOPOLOGY_PATCH,
  CAPACITY_DIFFERENTIAL_PAIR_FAIL_CLOSED_PATCH,
  CAPACITY_VIA_IN_SMD_PAD_PREVENTION_PATCH,
  CAPACITY_DIFFERENTIAL_PAIR_ZERO_LENGTH_EDGE_PATCH,
  CAPACITY_EXPLICIT_TRACE_WIDTH_PATCH,
  CAPACITY_LAYER_REVERSAL_RETRY_PATCH,
  CORE_PATCH,
  CORE_FANOUT_DIRECTION_RETRY_PATCH,
  CORE_ORDINARY_PHASE_REGION_PATCH,
  CORE_UNKNOWN_AUTOROUTER_PRESET_PATCH,
  CORE_MANUAL_TRACE_PRESERVATION_PATCH,
  CORE_PLANE_TERMINATED_NET_PATCH,
  CORE_SAME_LAYER_PLANE_TERMINATION_PATCH,
  CORE_MANUAL_PCB_PATH_VIA_RULES_PATCH,
  CORE_AUTHORED_NET_TREE_PATCH,
  CORE_DECOUPLING_MAX_LENGTH_PATCH,
  CORE_DIFFERENTIAL_PAIR_SOURCE_CONTRACT_PATCH,
  CORE_ROUTED_TRACE_VIA_STYLE_PATCH,
  CORE_AGGREGATE_ROUTE_IDENTITY_PATCH,
  CORE_DIFFERENTIAL_PAIR_TRACE_ENDPOINT_PATCH,
  CORE_VIA_IN_SMD_PAD_OUTPUT_GATE_PATCH,
  CORE_DIFFERENTIAL_PAIR_PHASED_TRACE_SELECTION_PATCH,
  CORE_LAYER_REVERSAL_RETRY_CACHE_IDENTITY_PATCH,
  CHECKS_SOURCE_TRACE_WIDTH_IDENTITY_PATCH,
]

// Every stage recognizes every exact later digest for the same compiled file.
// This keeps an already-advanced install idempotent without weakening the hash
// guard to an arbitrary or merely syntactically valid file.
for (let index = 0; index < TOOLCHAIN_PATCHES.length; index += 1) {
  const patch = TOOLCHAIN_PATCHES[index]
  const laterStageDigests = TOOLCHAIN_PATCHES.slice(index + 1)
    .filter(
      (candidate) =>
        candidate.packageName === patch.packageName &&
        candidate.file === patch.file,
    )
    .map((candidate) => candidate.patchedSha256)
  patch.successorSha256s = [
    ...new Set([...(patch.successorSha256s ?? []), ...laterStageDigests]),
  ]
}

const assertPackageVersion = async (packageDir, patch) => {
  const packageJsonPath = join(packageDir, "package.json")
  const packageJson = JSON.parse(await readFile(packageJsonPath, "utf8"))
  if (packageJson.version !== patch.version) {
    throw new Error(
      `${patch.packageName}: expected ${patch.version}, found ${packageJson.version}; ` +
        "rebase or remove the pinned toolchain patch before upgrading",
    )
  }
}

const assertSourceMapGuards = async (packageDir, patch) => {
  if (!patch.sourceMap) return
  const map = JSON.parse(
    await readFile(join(packageDir, patch.sourceMap), "utf8"),
  )
  for (const guard of patch.sourceGuards ?? []) {
    const index = map.sources.indexOf(guard.source)
    if (index < 0) {
      throw new Error(
        `${patch.packageName}: audited source ${guard.source} is absent from ${patch.sourceMap}`,
      )
    }
    const source = map.sourcesContent?.[index]
    if (typeof source !== "string" || !source.includes(guard.contains)) {
      throw new Error(
        `${patch.packageName}: audited source changed in ${guard.source}; ` +
          "do not apply the compiled patch until it is reviewed again",
      )
    }
  }
}

const replaceExpectedMatches = (source, replacement, packageName) => {
  if (replacement.scopeStart !== undefined || replacement.scopeEnd !== undefined) {
    if (
      typeof replacement.scopeStart !== "string" ||
      typeof replacement.scopeEnd !== "string"
    ) {
      throw new Error(
        `${packageName}: ${replacement.label}: scoped replacements require both scopeStart and scopeEnd`,
      )
    }
    const startMatches = source.split(replacement.scopeStart).length - 1
    const endMatches = source.split(replacement.scopeEnd).length - 1
    if (startMatches !== 1 || endMatches !== 1) {
      throw new Error(
        `${packageName}: ${replacement.label}: expected unique replacement scope, found ${startMatches} start and ${endMatches} end markers`,
      )
    }
    const scopeStart = source.indexOf(replacement.scopeStart)
    const scopeEnd = source.indexOf(replacement.scopeEnd, scopeStart)
    if (scopeEnd < scopeStart) {
      throw new Error(
        `${packageName}: ${replacement.label}: replacement scope end precedes its start`,
      )
    }
    const scopedSource = source.slice(scopeStart, scopeEnd)
    const replacedScope = replaceExpectedMatches(
      scopedSource,
      {
        ...replacement,
        scopeStart: undefined,
        scopeEnd: undefined,
      },
      packageName,
    )
    return (
      source.slice(0, scopeStart) +
      replacedScope +
      source.slice(scopeEnd)
    )
  }
  const expectedMatches = replacement.expectedMatches ?? 1
  const actualMatches = source.split(replacement.before).length - 1
  if (actualMatches !== expectedMatches) {
    throw new Error(
      `${packageName}: ${replacement.label}: expected ${expectedMatches} compiled match${expectedMatches === 1 ? "" : "es"}, found ${actualMatches}`,
    )
  }
  return source.split(replacement.before).join(replacement.after)
}

export const applyToolchainPatch = async (nodeModulesDir, patch, checkOnly) => {
  const packageDir = join(nodeModulesDir, ...patch.packageName.split("/"))
  await assertPackageVersion(packageDir, patch)
  await assertSourceMapGuards(packageDir, patch)

  const target = join(packageDir, patch.file)
  const source = await readFile(target, "utf8")
  const inputSha256 = sha256(source)
  if (
    inputSha256 === patch.patchedSha256 ||
    patch.successorSha256s?.includes(inputSha256)
  ) {
    return { packageName: patch.packageName, status: "already-patched" }
  }
  if (inputSha256 !== patch.pristineSha256) {
    throw new Error(
      `${patch.packageName}: ${patch.file} SHA-256 is ${inputSha256}; expected ` +
        `${patch.pristineSha256} (pristine) or ${patch.patchedSha256} (patched)`,
    )
  }
  if (checkOnly) {
    throw new Error(
      `${patch.packageName}: required patch is not applied; run scripts/setup-toolchain.sh`,
    )
  }

  let patched = source
  for (const replacement of patch.replacements) {
    patched = replaceExpectedMatches(patched, replacement, patch.packageName)
  }
  const outputSha256 = sha256(patched)
  if (outputSha256 !== patch.patchedSha256) {
    throw new Error(
      `${patch.packageName}: patched SHA-256 is ${outputSha256}; expected ${patch.patchedSha256}`,
    )
  }

  const temporary = `${target}.autonomous-circuit-patch-${process.pid}`
  try {
    await writeFile(temporary, patched, "utf8")
    await rename(temporary, target)
  } catch (error) {
    await unlink(temporary).catch(() => {})
    throw error
  }
  return { packageName: patch.packageName, status: "patched" }
}

export const applyAllToolchainPatches = async ({
  toolchainDir,
  checkOnly = false,
}) => {
  const nodeModulesDir = join(resolve(toolchainDir), "node_modules")
  const results = []
  for (const patch of TOOLCHAIN_PATCHES) {
    results.push(await applyToolchainPatch(nodeModulesDir, patch, checkOnly))
  }
  return results
}

const invokedAsScript =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)

if (invokedAsScript) {
  const args = process.argv.slice(2)
  let toolchainDir = resolve(dirname(fileURLToPath(import.meta.url)), "../..", "toolchain")
  let checkOnly = false
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--check") {
      checkOnly = true
    } else if (args[index] === "--toolchain" && args[index + 1]) {
      toolchainDir = resolve(args[index + 1])
      index += 1
    } else {
      throw new Error(`unknown argument: ${args[index]}`)
    }
  }
  const results = await applyAllToolchainPatches({ toolchainDir, checkOnly })
  for (const result of results) {
    process.stdout.write(
      `toolchain patch ${result.packageName}: ${result.status}\n`,
    )
  }
}
