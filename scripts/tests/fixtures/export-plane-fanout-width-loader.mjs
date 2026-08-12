/** Test-only access to the staged core plane-fanout width guards. */
export async function load(url, context, nextLoad) {
  const result = await nextLoad(url, context)
  if (!new URL(url).pathname.endsWith("/node_modules/@tscircuit/core/dist/index.js")) {
    return result
  }
  return {
    ...result,
    source: `${result.source}\nconst __testPlaneFanoutWidthGuard = typeof Group_assertNoRequestedTraceWidthUndercut === "function" ? Group_assertNoRequestedTraceWidthUndercut : undefined;\nexport { FanoutAutorouter, __testPlaneFanoutWidthGuard as Group_assertNoRequestedTraceWidthUndercut, getLocalAutoroutingCacheKey };\n`,
  }
}
