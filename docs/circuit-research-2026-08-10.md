# Circuit research — condensed recon (2026-08-10)

Condensed from the 2026-08-10 recon (R1 tscircuit, R2 verification, R3 fab, R4
competition, R5 parts/blocks) and field surveys (F1–F7); full documents in the org repo
(`projects/circuit/`). "Verified" below means run on this machine that day. Fab rules and
costs are read from vendor docs — nothing was verified by placing an order.

## The verification gauntlet (verified on this machine)

**The exit-code lie.** `tscircuit-cli build` with real errors (nonexistent port, missing
footprint, overlapping parts, failed autoroute) prints "Build completed with errors" but
**exits 0** and reports "Circuits 1 passed". Only fatal eval errors (TSX won't compile)
exit 1. Errors and warnings are structured elements inside circuit.json (`type` ending
`_error` / `_warning`). Every gate parses produced artifacts, never `$?`.

Verified emitted element types per seeded defect:

| Injected defect | circuit.json element |
|---|---|
| trace to nonexistent port | `source_trace_not_connected_error` |
| chip with no footprint | `pcb_missing_footprint_error` |
| two 0805s at 0.2mm offset | `pcb_footprint_overlap_error`, `pcb_pad_pad_clearance_error`, `pcb_courtyard_overlap_error` |
| consequence cascade | `pcb_autorouting_error`, `pcb_port_not_connected_error`, `pcb_trace_missing_error` |
| pin with no trace | `source_pin_missing_trace_warning` |
| footprint vs chosen JLC part | `supplier_footprint_mismatch_warning` — copper IoU; correct 0402 parts score ~0.73–0.77, so threshold accordingly (block <0.5, warn-band up to 0.85) |

Other verified facts:

- **`@tscircuit/checks`** `runAllChecks(circuitJson)` (async) is a genuine independent
  re-check: 7 errors on the seeded-defect board, 0 on the clean one. ~23 individual
  checks (overlap, pad-pad clearance, traces contiguous, vias off board, …).
- **Defaults that bite:** trace width 0.15mm, board thickness **1.4mm** — JLC standard is
  1.6mm, set `thickness` explicitly.
- **KiCad exports work:** `-f kicad_sch` / `kicad_pcb` / `kicad_zip` produce real files
  (named nets, footprints, a routed segment; verified content). kicad-cli itself was NOT
  installed on the bench (`brew install --cask kicad`, 1.4GB, KiCad 10.0.5) — the ERC/DRC
  commands are from docs: `--format json --exit-code-violations` gives exit 0 clean /
  exit 5 violations, JSON report either way. **Unverified risk:** whether the converter's
  output is ERC-clean on a good board. First task after install: baseline the noise floor
  on a known-good board before wiring exit-5 as blocking.
- **Renders:** `build --schematic-png --pcb-png` verified legible; in-process
  `circuit-to-svg` + `sharp` (density 300 → 3333×2500 PNG) verified; bottom layer via
  `snapshot --pcb-only --layer bottom`.
- **The CLI trap:** the bin is `tscircuit-cli`. `npx tsci` is an unrelated 2023 package
  that hard-requires bun and dies with exit 127.

### DFM limits to encode (JLCPCB 2-layer, read from jlcpcb.com/capabilities)

| Rule | JLC spec | Encode as |
|---|---|---|
| Track width / spacing (1oz) | 0.10/0.10mm | block <0.127mm, warn <0.15mm |
| Via hole / diameter | 0.15 / 0.25mm min | block drill <0.3mm, diameter <0.5mm (conservative) |
| PTH annular ring | ≥0.20mm | block <0.20mm |
| Pad-to-trace clearance | 0.1mm | already blocked by @tscircuit/checks (verified) |
| Via hole-to-hole / pad hole-to-hole / NPTH | 0.2 / 0.45 / 0.50mm | block |
| Copper-to-board-edge | ≥0.3mm | block <0.3mm |
| Board | min 3×3mm; thickness 0.4–4.5mm | block; force 1.6mm explicitly |
| Silkscreen | line ≥0.15mm, text ≥1.0mm | warn |

Plus two BOM gates: every assembly BOM row needs a non-empty LCSC number, and the
footprint-IoU thresholds above. JLC's upload-time DFM check stays the final authority;
ours is a pre-filter.

### What no deterministic check catches (why golden blocks exist)

1. **Electrical correctness of a novel circuit.** A board passes every gate identically
   with a 10Ω or 10MΩ resistor, reversed LED polarity, no decoupling, or a wrong feedback
   divider. ERC checks pin types; tscircuit checks connectivity. Neither knows Ohm's law.
2. **Footprint ↔ real part mismatch.** IoU is noisy (~0.73 on correct parts) and cannot
   catch pin-1 orientation, mirrored pinouts (a flipped SOIC-8 passes everything), or
   polarity marks.
3. **Chip pin mapping.** A hand-typed pinout that swaps SDA/SCL is self-consistent in
   every representation — both substrates inherit the same wrong source.
4. **Thermal / current capacity / EMI / SI.** No trace-width-vs-amps or antenna-keepout
   check exists.
5. **KiCad ERC depth depends on the exporter.** If pins export as generic types, ERC
   degenerates to connectivity. Unverified until KiCad is installed.
6. **Part stock drift.** LCSC stock and basic/extended status change; only JLC's upload
   check is final.

Gaps 1–3 are the failure class golden blocks eliminate: values, polarities, and pinouts
are frozen in the block, verified once by a human; the AI composes blocks and the
gauntlet verifies everything composition can break.

## Fab handoff (read from vendor docs)

- **Formats:** one gerber zip, Protel extensions auto-detected (GTL/GBL/GTS/GBS/GTO/GBO,
  outline GKO/GM1, drill XLN). BOM csv: `Comment, Designator, Footprint, LCSC Part #`
  (one line per part type, designators comma-separated). CPL csv: `Designator, Mid X,
  Mid Y, Layer, Rotation` — component **centers**, in **mm**.
- **Costs:** 5× 2-layer ≤100×100mm PCB = flat $2 (live promo) + shipping — ~$4–5 slow,
  ~$20 fast; fab 24–48h. Economy PCBA: $8.00 setup + $1.50 stencil per side,
  $0.0017/SMT joint, extended part $3.00/BOM line (Basic parts ~700, no fee), capped at
  30 assembled pieces, single-side SMT. 5× assembled ESP32-class ballpark **~$75–110
  all-in, ~1–2 weeks to door**.
- **The rotation gotcha:** JLCPCB's zero-rotation convention differs from KiCad/EDA
  output for many packages (SOT-23, SOICs, connectors). Auto-correction is imperfect —
  ORDER.md must tell the user to eyeball the placement preview (pin-1 dots) before
  paying; that screen is the safety net.
- **ORDER.md walkthrough source:** R3 §6 has the exact-clicks JLCPCB economy PCBA flow
  (quote → gerber upload → PCBA toggle → BOM/CPL upload → parts-match table → placement
  preview → cart). The fab profile template reproduces it.
- **No ordering API worth having:** JLCPCB's API covers PCB, stencil, 3D printing, and
  parts data — **no assembly endpoint** — and approval is gated on order history.
  PCBWay has the closest real order API (partner approval by email). MacroFab is the only
  self-serve API but has no checkout endpoint and runs 3–10× the price. v1 ships the
  packet.

## Parts and blocks reality

- **The tscircuit registry is not import-grade.** ~90% of hits are zero-star
  auto-generated single-part wrappers. The best module (`seveibar/rp2040-module`, 2★) is
  a real RPi-design-guide board but uses the old hooks API, a cloud autorouter, and a
  `manual_edits.json` — a reference design, not an import. **We author all ~14 golden
  blocks**; references: seveibar/rp2040-module, keyboard-default60,
  seveibar/pico-w-3x5-led-matrix, MrPicklePinosaur/lipo_charger.
- **jlcsearch latency:** cold queries took 47s (`DRV8833`) or timed out at 25–90s
  (`BME280`, `AHT20`), instant once warm. Never call it in the generation loop —
  parts-book only (retries, 90s timeout, local cache); the jlcparts-style SQLite mirror
  is the v1.1 upgrade.
- **Verified LCSC numbers for the three demo boards** (stock/price checked live
  2026-08-10):
  - **Board A "Air"** (ESP32-S3 sensor): USB-C TYPE-C-31-M-12 **C165948** ($0.16, 336k) +
    USBLC6-2SC6 ESD **C7519**; AMS1117-3.3 **C6186** (Basic, $0.15, 1.49M);
    ESP32-S3-MINI-1-N8 **C2913206** ($4.57) or WROOM-1-N8R8 **C2913201** ($4.72);
    BME280 **C92489** ($2.86). Variants: AHT20 **C2757850** ($0.80), SHT40-AD1B-R2
    **C2909890** ($1.70), VEML7700-TT **C1850416** ($1.13), SGP40-D-R4 **C2874215** ($6.87).
  - **Board B "Deck"** (RP2040 macropad): RP2040 **C2040** ($0.87) + W25Q128JVSIQ
    **C97521** (Basic, $1.22) + ABM8-272-T3 **C20625731** ($0.33); tactile switch
    TS-1187A **C318884** (Basic, $0.018, 918k); WS2812B-B/T **C2761795** ($0.076) or
    2020 **C965555**; SK6812MINI-E **C5149201**.
  - **Board C "Mover"** (motor): DRV8833PWPR **C50506** ($0.97, TSSOP-16-EP). Envelope:
    2.7–10.8V, inside the low-voltage rules.
  - **Gated:** lipo-tp4056 — TP4056-42-ESOP8 **C16581** (preferred, $0.16) + DW01A +
    FS8205; ships only as one sealed block after hardware sign-off.
- **Footprints:** the builtin footprinter (~100 parametric generators) covers everything
  v1 needs (SOT-223, ESOP-8, TSSOP-16-EP, LGA-8, DFN, 0402/0603/0805, usbcmidmount,
  crystal, led5050, jst, pinrow). Where a part needs its exact land pattern, run
  `easyeda convert -i C####` **once at authoring time** and commit the TSX — zero network
  in the generation loop.

## Competitive position (August 2026)

Every player sells a design tool or a design service; nobody sells a finished object.
Flux ($37M raised, $20–60/mo) is the best chat-to-schematic tool and still hands you an
EDA canvas; Quilter, JITX, CELUS, and Cadence AuraStack serve professionals; Diode is a
$10k-per-board service; atopile's public repo is ~2 months stale; siliXon is a seed-stage
bet. tscircuit — our upstream — is the only one with an order button, and its loop starts
at code and ends at a bare PCBA in a bag. Our wedge is the consumer complete-product
loop: chat → golden-block board → verified fab packet → ~$75–110 assembled run → Vibe
enclosure. The tool layer is deliberately not the moat (it's all open); the moat is the
loop.

Top risks: (1) **tscircuit closes the loop above us** — same-day release cadence, an
official agent skill, in-app JLCPCB ordering with Stripe; mitigation is the part they
won't build (safety envelope, block curation, enclosure pairing) plus staying a good
upstream citizen. (2) **Model gravity toward a rival DSL** — Anthropic's partnership is
with Diode/Zener (Sonnet 4.5 preferred 8/10 in Diode's own blind grading, on schematics);
our hedge is that golden-block composition minimizes what the model must invent, which
general Claude does well. (3) **Flux adds one-click fab ordering** — so our pitch is
never "our AI designs boards" but the complete product.

## Bake-off verdict summary (2026-08-10) — and the open test

The incumbent (tscircuit + kicad-cli + JLCPCB) beat the Zener/KiCad alternative
(pcb/pcbc + LLM placement glue + freerouting + KiKit) at **~70% confidence**. Deciding
fact: the pipeline must close place+route headlessly with no human in KiCad — the
alternative's layout joint doesn't exist (unintegrated glue across four runtimes with
documented silent-failure modes), while the incumbent's losses are adoptable components
(kicad-cli/KiKit fab export), narrowable gaps (netlist diff), or unmeasured claims
(Claude-on-Zener quality). Mandatory hardening regardless: never ship gerbers from the
3-star exporter alone; never trust `$?`; pin every tscircuit version hard; verify the
converter with a netlist diff.

**Open item — the 3-board flip-trigger test (not yet run):** author the same three
boards (ESP32-C3 sensor node, USB-C LDO power, RP2040 carrier) with Claude in tscircuit
AND in Zener; score first-pass compile, pin-level correctness, DRC-clean rate, packet
completeness, human minutes. Flip only if Claude-on-Zener is materially better on
first-pass pin-level correctness AND freerouting closes 2-layer boards with zero
hand-edits. Absent both, the incumbent stands.
