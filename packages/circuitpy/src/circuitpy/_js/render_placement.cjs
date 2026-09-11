// Placement renderer: circuit.json + ratsnest edges -> _placement.png.
// The board as circuit-to-svg draws it (footprints, pads, outline) with the
// ratsnest drawn on top as hairline traces, one per MST edge, so the agent
// sees where the copper will have to go before any router runs. Invoked by
// circuitpy.placement_image via `node` with NODE_PATH at the toolchain's
// node_modules. Prints exactly one JSON object {ok, written:[...]} on stdout.
//
//   node render_placement.cjs <circuit.json> <edges.json> <out.png>
//
// edges.json = [{net, a:[x,y], b:[x,y]}, …] in board millimetres.
const fs = require("node:fs")

const [inputPath, edgesPath, outPath] = process.argv.slice(2)
if (!inputPath || !edgesPath || !outPath) {
  process.stderr.write("usage: node render_placement.cjs <circuit.json> <edges.json> <out.png>\n")
  process.exit(2)
}

const circuitJson = JSON.parse(fs.readFileSync(inputPath, "utf8"))
const edges = JSON.parse(fs.readFileSync(edgesPath, "utf8"))
const { convertCircuitJsonToPcbSvg } = require("circuit-to-svg")

// Copper is the one thing this picture must not show: the ratsnest is the
// question, the traces are the router's answer. Drop traces, vias and pours
// (a pour's cutouts draw the traces' negative, which reads as copper), then
// add one hairline trace per ratsnest edge on the top layer.
const COPPER = new Set(["pcb_trace", "pcb_via", "pcb_copper_pour"])
const unrouted = circuitJson.filter((e) => e && !COPPER.has(e.type))
edges.forEach((edge, i) => {
  unrouted.push({
    type: "pcb_trace",
    pcb_trace_id: `ratsnest_${i}`,
    source_trace_id: `ratsnest_${i}`,
    route: [
      { route_type: "wire", x: edge.a[0], y: edge.a[1], width: 0.12, layer: "top" },
      { route_type: "wire", x: edge.b[0], y: edge.b[1], width: 0.12, layer: "top" },
    ],
  })
})

const svg = convertCircuitJsonToPcbSvg(unrouted)
const sharp = require("sharp")
sharp(Buffer.from(svg), { density: 150 })
  .png()
  .toFile(outPath)
  .then(() => {
    process.stdout.write(JSON.stringify({ ok: true, written: [outPath] }) + "\n")
  })
  .catch((err) => {
    process.stdout.write(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }) + "\n")
    process.exit(1)
  })
