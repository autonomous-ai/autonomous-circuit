/** Test-only access to core's internal FanoutAutorouter wrapper. */
export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context)
  if (!new URL(url).pathname.endsWith("/node_modules/@tscircuit/core/dist/index.js")) {
    return result
  }
  return {
    ...result,
    source: `${result.source}\nexport { FanoutAutorouter, Group_applyAuthoredNetTreeContracts, Group_getRoutingPhasePlans, Group_filterSimpleRouteJsonForPhase, getLocalAutoroutingCacheKey, getPresetAutoroutingConfig, getPlaneTerminatedSourceTraceLayers };\n`,
  }
}
