# OSS reuse decisions — the circuit stack (bake-off 2026-08-10)

Ruling from the substrate bake-off (2026-08-10, ~70% confidence, conditional on the
3-board flip-trigger test — see `circuit-research-2026-08-10.md`): tscircuit authors,
kicad-cli verifies and ships, JLCPCB fabs. Full surveys (f1–f7) and the
advocate/adversary/verdict briefs live in the org repo (`projects/circuit/`).

## Build on (production dependencies)

| Layer | Choice | License | Why |
|---|---|---|---|
| Board authoring + compile | **tscircuit** (exact-pinned `0.0.2279`; `toolchain/package.json` is the single pin site) | MIT | The only open stack that closes schematic → placement → routing headlessly. No OSS auto-placer exists, so placement must come from the LLM — and tscircuit is the only source format where the LLM writes the whole job (`pcbX`/`pcbY` in code). ~7 releases/day with no semver: pin exact; upgrades are deliberate PRs that re-run every golden-block snapshot. |
| Independent re-check | **@tscircuit/checks** `0.0.152` (`runAllChecks`, async) | MIT | Separate codepath over the same circuit.json. Bench 2026-08-10: 7 errors on a seeded-defect board, 0 on the clean one. |
| Second substrate + shipping gerber exporter | **kicad-cli** (KiCad 10.0.5), subprocess only | GPL-3.0 (fine out-of-process; never linked) | Independent C++ implementation runs ERC/DRC on the converted `.kicad_sch`/`.kicad_pcb` (`--format json --exit-code-violations`: exit 0 clean, exit 5 violations). The fab packet's gerbers also export from here — never from the 3-star tscircuit exporter alone (hard-avoid list). |
| Review images | **circuit-to-svg** `0.0.401` + **sharp** `0.33.5` | ISC on npm but the repo has no LICENSE file — file the PR before shipping / Apache-2.0 | Schematic / PCB / assembly SVGs from circuit.json in-process; sharp rasterizes (`density: 300` → 3333×2500 PNG, verified on this Mac) with no external rasterizer. |
| Parts data (authoring-time only) | **jlcsearch** (jlcsearch.tscircuit.com) | MIT | Best agent-facing parts source found: stock, unit price, `is_basic`/`is_preferred` per LCSC part. Cold queries take 47–90s or time out — never in the generation loop; parts-book only, with retries and a local cache. Mirror plan in the crib list. |
| Fab target | **JLCPCB economy PCBA** | — (service) | 5× 2-layer PCBs $2 + shipping (~$4–20 all-in); 5× assembled ESP32-class ~$75–110 all-in, ~1–2 weeks. JLCPCB has no assembly-order API and gates API access on order history — v1 ships a packet + walkthrough, not an integration. |

## Crib patterns from (no code reuse where license forbids)

- **KiKit** (2,004★, MIT) + **Fabrication-Toolkit** (660★, Apache-2.0) + **Bouni/kicad-jlcpcb-tools** (2,017★, MIT): the battle-tested JLCPCB export references — exact packet formats, and above all the **rotation-correction databases** (JLCPCB's zero-rotation convention differs from KiCad's for SOT-23s, SOICs, connectors; their DBs are the hard-won landmine data that seeds our fab profile).
- **Zener / diodeinc/pcb** (414★, MIT): the graded-testbench idiom — every golden block ships topology assertions + graded checks, seeded from Diode's ~250 open reference designs — and in-source toolchain pinning (`pcb-version`), which our generated TSX copies as a pinned-dialect header comment.
- **SKiDL** (1,612★, MIT): netlist diff as a third independent check — the best OSS pin-type/drive-conflict ERC found, and it audits our own circuit-json→KiCad conversion (a same-org converter sits in the stage-3 trust path; the diff is what makes that check honest).
- **kicad-happy** (927★, MIT): the audit checklist — power tree, ESD per connector, crystal load caps, BOM lock — reused as agent review skills over the converted KiCad files. It reviews, it doesn't generate: exactly the layer that belongs on top of deterministic checks.
- **jlcparts** (810★, MIT): the nightly-rebuilt SQLite mirror of the JLCPCB catalog — the parts-resilience pattern; never a live free service in the user path (the v1.1 upgrade for parts-book).
- **InteractiveHtmlBom** (4,508★, MIT) + **PcbDraw** (1,404★, MIT): the hand-assembly view (click a BOM row, parts highlight; documented `pcbdata` JSON) and populated-board renders — the consumer-facing assembly imagery for the kit story.
- **WireStudio** (26★, MIT): the structural twin — design file → solver → electrical validator → KiCad + JLC fab bundle + parametric enclosure. Study its validation architecture; it proves our exact shape at small scale.

## Hard-avoid list (license tripwires and trust holes, verified 2026-08-10)

Registry `@tsci` imports in the generation loop — packages are mutable by their owners on npm.tscircuit.com with no signing or review (a supply-chain / prompt-injection surface for an AI loop), and nothing there is import-grade anyway (best module: 2 stars, old hooks API, cloud-autorouter dependent); golden blocks live in-repo. · Network footprint strings at build time (`footprint="jlcpcb:C…"` / `"kicad:…"`) — nondeterministic, break offline CI, and a silent upstream footprint change means silently wrong gerbers; run `easyeda convert` once at authoring time and commit the result. · `circuit-json-to-gerber` as the sole gerber source — a 3-star, nearly-unreviewed repo is the worst single fact in the incumbent stack; ship gerbers via kicad-cli from the converted board, and when kicad-cli is absent the tscircuit-exported gerbers carry a blocking-for-ship `unverified_gerbers` warning. · KiCad official libraries, stated precisely: CC-BY-SA 4.0 **with an explicit exception** — for designs using the libraries, the copyright holder waives article 3 with respect to the designs and any generated files, so user boards and fab outputs carry no attribution or ShareAlike obligation; but redistributing the libraries, or any curated/modified collection derived from them, requires CC-BY-SA + attribution — if we ever ship a footprint pack derived from KiCad's, that pack must itself be CC-BY-SA in an open repo. · SnapEDA/SamacSys as a backend source — free for per-user design use, but redistributing their files from our servers is prohibited. · easyeda2kicad (AGPL-3.0) — subprocess only, never linked; prefer JLC2KiCad_lib (MIT) where possible. · Datasheet-invented circuits — no deterministic check knows Ohm's law; a swapped SDA/SCL or a wrong feedback divider passes every gate, which is why values, polarities, and pinouts freeze inside golden blocks.

## The proven pipeline shape

Every working loop found in the field (WireStudio, the kicad-happy review layer, the
tinycomputers vibe-coded-board workflow, our own bench) converges on the same shape:
**structured code source → deterministic compile → independent ERC/DRC on a second
substrate → fab packet (gerbers + BOM with LCSC numbers + CPL) → JLCPCB upload, with a
human eyeballing the placement preview.** Two standing rules from the bake-off, restated
in the contract: never trust an exit code (`tscircuit-cli build` exits 0 with real
errors — gate on parsed artifacts), and keep the verify/export spine consuming Circuit
JSON / KiCad files only, never TSX — so a different authoring front-end (Zener, if the
3-board test flips) could be absorbed without rewriting the spine. Our differentiation is
the consumer loop nobody in the field owns: golden-block composition inside a hard safety
envelope, a verified packet, and the Vibe-printed enclosure.
