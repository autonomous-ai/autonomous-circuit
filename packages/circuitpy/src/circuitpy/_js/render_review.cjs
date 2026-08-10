// Review renderer: circuit.json -> _schematic.svg + _pcb.svg (+ optional
// rasters via sharp). Invoked by circuitpy.review via `node` with NODE_PATH
// at the repo toolchain's node_modules. Prints exactly one JSON object
// {ok, written:[...]} on stdout.
//
// Flags:
//   --bottom          also write _pcb_bottom.png (bottom-layer PCB view)
//   --schematic-png   also rasterize _schematic.png from the SVG (fallback
//   --pcb-png         when the build's native PNGs are missing)
const fs = require("node:fs")
const path = require("node:path")

const args = process.argv.slice(2)
const flags = new Set(args.filter((a) => a.startsWith("--")))
const positional = args.filter((a) => !a.startsWith("--"))
const [inputPath, outDir] = positional
if (!inputPath || !outDir) {
  process.stderr.write(
    "usage: node render_review.cjs <circuit.json> <out_dir> [--bottom] [--schematic-png] [--pcb-png]\n",
  )
  process.exit(2)
}

const circuitJson = JSON.parse(fs.readFileSync(inputPath, "utf8"))
const {
  convertCircuitJsonToSchematicSvg,
  convertCircuitJsonToPcbSvg,
} = require("circuit-to-svg")

fs.mkdirSync(outDir, { recursive: true })
const written = []

const schematicSvg = convertCircuitJsonToSchematicSvg(circuitJson)
const schematicSvgPath = path.join(outDir, "_schematic.svg")
fs.writeFileSync(schematicSvgPath, schematicSvg)
written.push(schematicSvgPath)

const pcbSvg = convertCircuitJsonToPcbSvg(circuitJson)
const pcbSvgPath = path.join(outDir, "_pcb.svg")
fs.writeFileSync(pcbSvgPath, pcbSvg)
written.push(pcbSvgPath)

const rasterJobs = []
function raster(svgText, pngName) {
  const sharp = require("sharp")
  const pngPath = path.join(outDir, pngName)
  rasterJobs.push(
    sharp(Buffer.from(svgText), { density: 150 })
      .png()
      .toFile(pngPath)
      .then(() => written.push(pngPath)),
  )
}

if (flags.has("--bottom")) {
  raster(
    convertCircuitJsonToPcbSvg(circuitJson, { layer: "bottom" }),
    "_pcb_bottom.png",
  )
}
if (flags.has("--schematic-png")) raster(schematicSvg, "_schematic.png")
if (flags.has("--pcb-png")) raster(pcbSvg, "_pcb.png")

Promise.all(rasterJobs)
  .then(() => {
    process.stdout.write(JSON.stringify({ ok: true, written }))
  })
  .catch((err) => {
    process.stderr.write(String((err && err.stack) || err))
    process.exit(1)
  })
