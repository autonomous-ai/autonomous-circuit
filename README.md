<img width="2551" height="1319" alt="Autonomous Circuit" src="https://github.com/user-attachments/assets/1b7bd739-b3b3-4219-9b2f-73c6888a806f" />

# Autonomous Circuit: chat with AI → a board you can order

[**Quickstart**](#quickstart) · [How a board gets made](#how-a-board-gets-made) · [The agents](#five-agents-one-job-each) · [The four loops](#four-loops-closing-over-four-different-things) · [Golden blocks](#golden-blocks-compose-never-invent) · [Where it stands](#where-it-actually-stands) · [Contributing](#contributing)

**Autonomous Circuit turns a sentence into a printed circuit board you can order.** You describe the
gadget, approve an engineering plan, and the pipeline emits a fab packet — gerbers, a bill of
materials with orderable part numbers, and a pick-and-place file — that goes straight to JLCPCB.

It is a web app with a chat on one side and the board on the other: schematic, PCB, 3D, BOM, fab.
Underneath, boards are composed from **nine validated subcircuits** rather than invented from
datasheets, and every build is graded by a seven-stage gauntlet that runs two independent
verification substrates against each other.

The limitation, stated up front: the catalog is nine blocks, so it designs the class of boards those
blocks cover — USB-C powered, 3.3V logic, RP2040-class microcontroller, I²C sensors, buttons, LEDs.
Ask for a motor driver and it will tell you the ask is out of catalog rather than improvise one.
That refusal is the feature. A wrong resistor value looks exactly like a right one in every
rendering, every check and every export, and you find out when the boards arrive.

---

## Quickstart

Requires Python 3.10+, Node 20+, and a `claude` CLI on your PATH.

```bash
git clone https://github.com/autonomous-ai/autonomous-circuit
cd autonomous-circuit
./scripts/dev.sh          # installs the pinned toolchain, starts the app on :4179
```

Open `http://localhost:4179`, pick a starter project, and type what you want. A three-block board
takes about 90 seconds to build; a 400-trace keyboard at maximum router effort takes about 20
minutes.

To build a board from the command line without the app:

```bash
python skills/circuitcode/scripts/circuit boards/main.tsx
# {"ok": true, "fab": {"ready": false}, "warnings": 12, ...}
```

`kicad-cli` is optional but strongly recommended — without it the pipeline still builds, but the
gerbers come from a single exporter with nothing to check them against, and it says so by refusing
to write `ORDER.md`.

---

## How a board gets made

![The path from an ask to a fab packet, with the two feedback loops that return underneath it](docs/architecture/loops.svg)

Six steps, two of which hand work back.

**Ask → Plan.** `circuit-analysis` turns "a coaster that reminds me to drink water" into an
engineering brief: capabilities, a power budget, a size class. `circuitcode` turns the brief into a
plan — the blocks it will use, the pin allocation table, the estimated cost band. **You approve the
plan before a line of board source exists**, because a plan is cheap to argue with and a routed
board is not.

**Plan → Board source.** The agent writes TSX — [tscircuit](https://github.com/tscircuit/tscircuit),
which is React for circuit boards. Components are elements, nets are props, and the layout is
computed. This is the substrate that makes an LLM a plausible board designer at all: it is code, so
the same model that is good at code is good at it.

**Board source → Gauntlet.** Compile, then grade. Details in the next section.

**Gauntlet → Panel.** Once nothing blocks, a seven-lens review panel asks the questions a checker
cannot: not "is this legal" but "will this work, and can we build it twice."

**Panel → Fab packet.** `ORDER.md` is written only when the packet is genuinely orderable.

---

## Nothing an agent says is a check

This is the design decision the rest of the system hangs off.

![circuit.json is graded twice — by our own checks and by KiCad — and both sets of findings converge](docs/architecture/substrates.svg)

An LLM writes the board and an LLM fixes the board. **No LLM ever decides whether the board is
good.** Every gate is deterministic code parsing artifacts on disk, and the agent's job is to react
to what that code found.

The reason is narrow and practical. A model reviewing its own work grades the reasoning it just
performed, so it reproduces its own blind spots — and on a PCB the failure is silent. Nothing about
a 10Ω resistor where 10kΩ belongs looks wrong in a schematic render, a 3D view, or a DRC report.
The board is beautiful and it does not work.

So the pipeline compiles the design to `circuit.json` and then grades that one artifact twice, along
two paths that share no code:

- **Our own checks** — element scan, `@tscircuit/checks`, and a DFM pass against the fab's published
  limits (trace and space ≥ 0.127mm, drill ≥ 0.3mm, copper-to-plated-hole ≥ 0.28mm, and so on).
- **KiCad** — the same design is exported to `kicad_sch` / `kicad_pcb` and handed to `kicad-cli` for
  ERC, DRC and schematic-parity. KiCad has never heard of tscircuit. When both agree a board is
  clean, two independent tools with different bugs agree.

A third stage diffs the netlists between the two representations, because a converter that quietly
drops a net would otherwise make the second opinion worthless.

Two rules keep this honest, and both were learned by being burned:

**Never trust an exit code.** `tscircuit-cli` exits 0 with real errors in its output. Every gate
parses the artifact it produced, never `$?`.

**The gerbers we ship are the gerbers we checked.** They come out of `kicad-cli` from the converted
board — the same file KiCad ran DRC against. If `kicad-cli` is missing, the pipeline falls back to
tscircuit's exporter, raises `unverified_gerbers`, and refuses to write the order instructions.

---

## Five agents, one job each

The agents are Claude Code skills under `skills/`. Each is a separate context with a separate
system prompt and a narrow job, orchestrated by a Node driver that spawns them as subprocesses. The
shape is the one Anthropic calls
[orchestrator-workers](https://www.anthropic.com/engineering/building-effective-agents).

![circuitcode orchestrates the other agents; the gauntlet is the one box where no model runs](docs/architecture/multi-agent-architecture.svg)

| Agent | Job | Why it is separate |
|---|---|---|
| **`circuit-analysis`** | vague ask → engineering brief | Interrogating a request and designing a board are different skills. Merged, the model starts designing before it knows what it is designing. |
| **`circuitcode`** | write the board source, run the generator, fix by severity | The only agent that writes TSX. It owns the build-fix cycle and reads the rendered images before calling anything done. |
| **`parts-book`** | lock the BOM to orderable parts | A part that is out of stock is not a part. This resolves every line to an LCSC number against a local mirror of JLCPCB's stocked library. |
| **`design-review`** | score seven lenses, hold the ship bar | Reviewing needs a different prompt from building — and a reviewer that also built it is not a reviewer. |
| **`board-viewer`** | render and explain what is on the board | Serves the app's canvases and the plain-language layer. |

`circuitcode` also ships `circuitlib`, a Python library that owns **every number the agents are
allowed to use** — IPC-2221 trace widths, regulator thermals, LED current, measured block extents,
fab limits. One owner per constant, so a value cannot be right in the planner and wrong in the
checker.

### The panel is seven passes, not one reviewer

`design-review` runs seven lenses — power integrity, manufacturability, layout and signal,
testability, cost and sourcing, safety, product fit — and **each is a separate pass with a separate
question**. They are deliberately not merged: one reviewer looking for everything finds the first
thing and stops. This is
[parallelization by sectioning](https://www.anthropic.com/engineering/building-effective-agents),
and it is the same reason a multi-agent research system beats a single agent with a longer prompt —
[independent contexts do not inherit each other's blind spots](https://www.anthropic.com/engineering/multi-agent-research-system).

Three of the lenses have real arithmetic available and are required to run it. A power lens that
scores by impression will approve a linear regulator dissipating a watt in a SOT-23.

The ship bar: **every lens scores ≥ 7, and no lens has an open must-fix note.** The panel caps at
four rounds. Past that, the design has a problem iteration cannot fix, and the honest move is to
take the disagreement to a human with options.

---

## Four loops, closing over four different things

![A full run, from the ask through the build-and-repair loop to the review panel](docs/architecture/multi-agent-process.svg)

Feedback is the whole architecture. There are four loops, and they are worth telling apart because
each closes over a different object on a different timescale.

**Loop 1 — the gauntlet, over one build.** Seven stages, each parsing artifacts. One of them is
itself a retry: if a blocking finding looks routing-related, the pipeline rewrites the mirrored
source with `autorouterEffortLevel="5x"` and compiles again, **keeping the retry only if it has
strictly fewer blocking warnings**. Measured on our own boards, that dial took a keyboard from 46
blocking errors to 18 and a puck from 5 to 1, with no design change — at roughly 14× the routing
time.

**Loop 2 — the repair loop, over one board.** After a build, the driver silently resumes the agent
against what the gauntlet found, in three phases with hard caps: structure (≤2 rounds, anything
blocking), electrical function (≤3), then craft (≤2, always runs once, breaks when a round changes
no files). This is
[evaluator-optimizer](https://www.anthropic.com/engineering/building-effective-agents) with a
deterministic evaluator — the generator grades, the agent fixes, and the caps exist because an
agent that cannot fix something in three tries is not going to fix it in ten.

**Loop 3 — the panel, over one design.** Seven lenses score, must-fix notes route back to
`circuitcode`, re-score only the lenses whose inputs changed. Cap: four rounds.

**Loop 4 — the library, over everything.** The first three make *this* board better. The fourth
makes the *next* board better, and it is the one that matters most:

- **The composition matrix** (`evals/composition.py`) builds every legal combination of golden
  blocks as a real board through the real pipeline. Each block already passed its own gauntlet; no
  *combination* ever had. Its first run scored 6 of 42 clean, and the cause was in the placement
  advice the skill gives every agent: it stored each block's *size* and assumed the geometry was
  centred on the origin. It is not — `usb-c-data`'s copper sits 6.04mm above its origin. Every board
  built on that advice was wrong by millimetres before anyone wrote a line of TSX.
- **The cold-brief eval** (`evals/agent/run.py`) gives the agent eight briefs it has never seen, an
  empty directory each, and no human turn — scored on first-build fab-ready rate. Everything else in
  the repo measures the pipeline starting from a board somebody already wrote. This measures the
  product.
- **The failure corpus** (`packages/circuitpy/tests/test_failure_corpus.py`) keeps every real defect
  permanently, each paired with the legal geometry just the other side of the line, so a fix cannot
  be a threshold quietly moved.

The rule that ties Loop 4 to the rest: **when a board fails and the agent fixes it, the fix belongs
in the block, the skeleton, the planner's defaults or `circuitlib` — never in advice a user has to
remember.** A block that needs correct handling to be safe is a block that will be handled wrong.

### Why the loops can afford to be slow

A missed defect costs a two-week fab round trip, and that time is opportunity cost, not just
waiting. So the budget for verification is **days of compute** — which only works if the compute is
parallel. `packages/circuitpy/src/circuitpy/batch.py` runs builds across a process pool and reports
compute spent against time waited as two separate numbers, because the claim being made is about
their ratio. Exhaustive in compute, fast in wall-clock.

---

## Golden blocks: compose, never invent

A golden block is a subcircuit whose values, polarities, pinouts and land patterns were verified
once by a human and then frozen. Nine exist today: USB-C power and data entry, a 3.3V LDO, an I²C
bus, a status LED, a tactile switch, an RP2040 core, a BME280 sensor, and a WS2812 chain.

Each ships a `BLOCK.md` datasheet — pin contract, rail budget, pinned LCSC parts with a verification
date, provenance — and a graded testbench that builds it as a **real board** through the real
pipeline, with routing on and production board constraints applied. A block that would block any
board built from it fails its own test, named after itself.

The doctrine exists because of the gap in the previous section: no deterministic check catches a
wrong value or a mirrored pinout, since every representation inherits the same wrong source. Blocks
eliminate that class by construction, and the gauntlet verifies everything *composition* can break.

`block_for(capability)` returns `None` when we have not built one yet, and that is a real answer.

### The safety envelope

Refused at spec time, in the plan, before any toolchain process runs:

- **No mains voltage, ever.** Low-voltage DC ≤ 24V only.
- **Battery power only through a sealed, validated charge/protect block.** None exists yet — the
  slot is deliberately empty rather than filled with something plausible.
- **Radio only as certified modules.**

---

## Where it actually stands

v1, in active development, and the honest numbers move daily. As of 2026-08-11:

- **Composition closure: 56%** of legal block combinations build clean, up from 14% before the
  placement fix above. The guarantee we are building toward is closure — if every block is clean
  alone and every combination is clean, then "any board a user can reach is fab-ready" stops being a
  hope about the future and becomes a property of the library. 56% is not that yet.
- **The three example boards are not orderable.** All three fail on USB-C shell plated-hole
  clearance, and one has an unconnected rail. The current state of each is in
  `examples/*/boards/main.board.json`.

We publish these because a fab-ready rate you cannot see is a fab-ready rate you cannot trust. The
bar itself does not move to meet the number: `fab.ready` requires zero error-severity findings **and**
gerbers independently verified by `kicad-cli`. Loosening that to improve the statistic would destroy
the only thing that makes the statistic worth having.

---

## Repo layout

```
viewer/            the web app — Vite + React chat and board workspace,
                   plus the Node driver that spawns the agents
  src/server/      driver (turns, review loop), HTTP commands, projects, JLCPCB client
  src/client/      canvases, findings, plain-language layer
packages/
  circuitpy/       the pipeline: source → compile → verify → fab packet
  golden-blocks/   the validated subcircuits, each with BLOCK.md + a testbench
  parts-catalog/   local mirror of JLCPCB's stocked library
  verify/          deeper verification — SPICE, gerber truth
skills/            the agents (SKILL.md each) + circuitlib, the number owner
evals/             composition matrix, cold-brief agent eval
examples/          real product boards — Hydrate coaster, Harness puck, Terminal keyboard
docs/              circuit-interfaces.md is the frozen contract; changes are append-only
toolchain/         pinned tscircuit, exact version
```

**`docs/circuit-interfaces.md` is the contract** between the pipeline, the server and the skills.
It is frozen: changes go through the append-only `-CHANGES.md` beside it. Read it before changing
anything that crosses those boundaries.

---

## Contributing

The most useful contributions, roughly in order:

1. **A golden block.** The catalog is the bottleneck on what can be designed. A block needs a
   `BLOCK.md`, pinned LCSC parts, and a testbench that passes the full gauntlet as a real board.
   A buck regulator, a motor driver and a battery charge/protect block are all wanted.
2. **A check that catches something real.** Add it with a failure-corpus entry: the defect, and the
   legal geometry just the other side of the line.
3. **A fab profile.** Everything fab-specific lives in one limit table (`circuitpy/fab.py`). JLCPCB
   is the only profile today; PCBWay and OSH Park are the same shape of work.

Run the tests before opening a PR:

```bash
PYTHONPATH=packages/circuitpy/src python -m pytest packages/circuitpy/tests -q
cd skills/circuitcode && PYTHONPATH=. python -m pytest tests -q
npm --prefix viewer test
```

Two conventions that are not negotiable, both above: no gate may trust an exit code, and no fix may
live in advice a user has to remember.

---

Built by [Autonomous](https://autonomous.ai). Licensed under the [MIT License](LICENSE).
