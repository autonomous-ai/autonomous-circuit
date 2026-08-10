# Autonomous Circuit: chat with AI → a board you can order and build

With Autonomous Circuit, designing a PCB is a conversation: describe the gadget, approve
the plan, and the pipeline produces a verified fab packet you upload to JLCPCB — then
assemble the boards into a 3D-printed enclosure.

**1. Describe:**
Tell Circuit what you want to build — a sensor, a macropad, a little machine.

**2. Approve:**
Review the engineering spec — chosen blocks, power budget, size, cost band. Circuit
writes the board source, compiles it, and runs every check twice (its own toolchain,
then KiCad's).

**3. Iterate:**
Review the schematic and PCB in the built-in viewer. Give notes — Circuit regenerates
only what changed. You supply taste; Circuit supplies the engineering labor.

**Then order.** A fab-ready board comes with a packet — gerbers, BOM with orderable LCSC
part numbers, pick-and-place file, and an ORDER.md that walks the exact clicks at
JLCPCB. Five bare boards run a few dollars; five assembled ESP32-class boards land
around $75–110, at your door in one to two weeks.

Boards are composed from golden blocks — proven subcircuits with frozen values,
polarities, and pinouts — inside a hard safety envelope: no mains ever, battery only via
a sealed validated block, radio only as certified modules.

## Status

v1 in active development.

## Repo layout

- `viewer/` — the web app: Vite + React chat surface + board workspace
  (Schematic / PCB / BOM / Fab tabs), and the Node server driver that runs the
  `claude` subprocess
- `packages/circuitpy/` — the Python pipeline: board source → compile → verify →
  fab packet (the staged gauntlet in `docs/circuit-interfaces.md` §1)
- `skills/` — Claude Code skills bundled with the app: `circuitcode` (write board
  source, run the generator, fix by severity), `circuit-analysis` (vague ask →
  engineering brief), `parts-book` (lock the BOM to orderable parts), `board-viewer`
- `toolchain/` — exact-pinned Node toolchain (tscircuit and friends); the pipeline
  invokes it out-of-process
- `docs/` — `circuit-interfaces.md` (the frozen contract every track codes against),
  `oss-decisions.md` (what we build on), `circuit-research-2026-08-10.md`,
  `vision-context.md`
- `scripts/` — dev/build helpers
- `evals/` — end-to-end structural evals, fully offline

## Prerequisites

- Claude Code installed on PATH: <https://claude.ai/install>
- Node 22.12+
- Python 3.10+
- kicad-cli (KiCad 10) — optional to build, required to ship: without it, boards still
  compile and render but the fab packet carries a blocking `unverified_gerbers` warning

## v1 LLM stance

Autonomous Circuit uses the user's existing Claude Code subscription.
