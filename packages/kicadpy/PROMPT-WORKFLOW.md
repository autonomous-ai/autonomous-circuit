# Experimental prompt-to-KiCad app workflow

The app selects this workflow only for a project whose project.json has
`engine: "kicad-native"`. Existing projects retain v1. The plan/approval/chat
protocol is unchanged. Native authoring remains agent-driven; this is not a
complete automatic circuit synthesizer or a qualified fabrication pipeline.

## Start a new design

1. Read the brief and resolve exact size, connectors, supply/current and pin
   mapping. Write product.json at the workspace root. Preserve parts identity
   and sourcing in parts.json; never invent an LCSC/MPN match.
2. Create design/main.kicad_pro, main.kicad_sch and main.kicad_pcb. Keep child
   sheets, .kicad_sym libraries, .pretty footprints, fp-lib-table and
   sym-lib-table inside design/. `${KIPRJMOD}` refers to that directory.
3. Use KiCad's native footprint/symbol libraries or documented, licensed
   reference circuits as appropriate. Copy the needed definitions locally and
   retain provenance. Read existing BLOCK.md knowledge when useful; TSX golden
   blocks are not native KiCad sheets. Check symbol pin numbers, footprint pad
   numbers, pin functions and net parity explicitly.
4. The native schematic must be real and electrically connected, not just an
   illustration. Match PCB footprint paths to schematic instance UUID paths.
   Write .kicad_pro design rules/netclasses before routing. Preserve the safety
   envelope in the plan and implementation: low-voltage only, no mains, no
   unvalidated battery charging/protection, certified radio modules only.
5. Place and inspect native geometry. Use KiCad bundled Python to load/create
   PCB objects; its interpreter may be Python 3.9 and is separate from host
   Python 3.10+. On this Mac the interpreter is
   `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3`.
   The CLI is `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`.
   Discover/verify paths rather than assuming another machine has them.
6. Initial routing may use pcbnew.ExportSpecctraDSN and ImportSpecctraSES with
   the existing circuitpy.toolchain.run_freerouting launcher. Work on a copy;
   the importer replaces tracks and protected wiring may be absent from SES.
   For incremental repairs prefer the kicadpy route transaction, which preserves
   unaffected native objects, or apply for replace_track/set_width operations.
7. Publish after every complete revision with host Python:
   `python3.12 -m kicadpy.publish --manufacturing design/main.kicad_pro` (use the PYTHONPATH
   supplied in the app system prompt). Read the returned native findings under
   boards/main_review/<revision>/reports/, fix their causes and repeat.
   The server also independently publishes at the end of the implementation turn.

## Existing design

Inspect and snapshot before changing it. Use UUID + expected revision + region
for native transaction tools. For authoring operations outside those tools,
work on a separate copy and preserve the original snapshot. Do not rebuild the
whole board to fix a local routing defect. Re-run native checks after every
source change. Close any other writer to the same project during commit/undo.

## Deliverables and review

The app displays native PCB/schematic SVG and KiCad GLB. All sheets, native
reports, the prototype packet and engineering findings remain in the versioned
bundle. Canvas placement editing is disabled; edit in KiCad or through the agent.

Read MANUFACTURING.md for engineering evidence, assembly decisions and packet
verification. The server runs up to two independent native review/repair rounds
after implementation, with independent publication after each round. A preview
may have findings; prototype ordering only unlocks after all blocking checks
pass for the current source and intact packet. Never hand-edit derived sidecars
or report files. Hardware testing is separate and must not be claimed without
actual measurements. Exporting a packet never authorizes upload or payment.
