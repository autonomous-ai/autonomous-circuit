# Native prototype manufacturing

Run `python3.12 -m kicadpy.publish --manufacturing design/main.kicad_pro`
with the same PYTHONPATH as the native workflow. This creates a checked copy,
Gerber/drill archive, factory BOM/CPL, manual assembly BOM, native position file,
KiCad project archive, ORDER.md and manufacturing-report.json in the versioned
preview bundle. It never uploads or purchases anything.

Prototype readiness is separate from physical hardware validation. It requires:

1. Native ERC, DRC and schematic parity with zero findings.
2. Exact source revision and evidence-backed engineering review.
3. Complete component identities and explicit factory/manual population choices.
4. Verified factory orientation corrections (including zero corrections).
5. Independent packet parsing and native geometry reconciliation.
6. Immutable packet hashes. Missing, changed or unparseable inputs block readiness.

## Review contract

At project root, write `manufacturing.json`:

```json
{
  "schemaVersion": 1,
  "reviewer": "reviewer identity and model/version when AI assisted",
  "designInputs": {"design/main.kicad_pcb": "actual sha256, plus EVERY design input"},
  "acceptedIgnoredChecks": {"drc": [], "erc": []},
  "checks": {
    "power": {
      "status": "blocked",
      "analysis": "Measured/calculated finding and why it is sufficient or not",
      "evidence": [{"path": "engineering/power.md", "sha256": "actual sha256"}]
    }
  },
  "assembly": {
    "R1": {"method": "factory", "rotationOffsetDeg": 0},
    "J1": {"method": "manual"}
  }
}
```

All seven checks are required: `power`, `protection`, `pinout`, `thermal`,
`assembly`, `fabricator`, `bringup`. Allowed pass requires a nonempty analysis
and actual hashed evidence in `engineering/`. Use `blocked` when unresolved.
`blocked` means DESIGN evidence is missing — a calculation, a datasheet
number, a routed-copper measurement, a pad/pin audit that could have been
done at the desk and was not. A bench measurement that needs a physical
board (temperature, inrush, fault current, enumeration) is never a
prerequisite for `pass`: write it as a step with a limit under `bringup`
and pass the area on the design evidence. Prototype-ready is a design
statement; hardware stays untested until someone measures it, and the
packet says so. A factory rotation counts as verified once the footprint's
zero orientation has been compared with the JLCPCB/EasyEDA library footprint
of that LCSC part (the community conversion table plus that comparison),
with the comparison written in `engineering/assembly.md`; the placement
preview on the JLCPCB site is the orderer's check, not a blocker.
Every populated, non-BOM-excluded footprint must have an assembly decision.
Factory components require exact manufacturer/MPN, footprint and LCSC identity.
Manual assembly remains explicitly documented; do not silently move difficult
parts to manual just to make the checker pass. Explain the process and tooling.

Compute `designInputs` with `kicadpy.manufacture.design_inputs(Project(path).root)`
AFTER all native/product/parts edits. It excludes manufacturing.json and the
engineering evidence directory to avoid a circular hash. Evidence is still
included in the overall publication revision. Use the exact ignored-check arrays
from the latest native check.json. Explain each ignored category in evidence;
do not disable electrical or geometric checks to hide defects.

Evidence must include calculations, assumptions, primary source URLs/versions
and remaining physical tests, not a statement that an earlier agent said pass.
For power/thermal, inspect the actual routed layer and narrowest current path,
vias, connector contacts, transient loads, component derating and return path.
For protection, review fuse coordination, reverse polarity, motor regeneration,
watchdog/disable paths and fault behavior. For pinout, compare manufacturer pin
tables to both schematic nets and actual footprint pad numbers.

For assembly, inspect polarity and native-vs-factory package orientation,
through-hole/manual work, exposed pads, via-in-pad process, placement access and
component identity. Zero rotation correction also needs evidence. Stock is not
implied by a part number. For fabricator, verify the selected stackup, copper,
drill/plating, clearances, stencil, rails/fiducials and any special process
against current capabilities. For bringup, write a staged, current-limited test
plan with measurable acceptance limits and loads disconnected initially.

## Coordinates and packet limitations

Gerbers and drills use KiCad absolute origin. CPL is millimetres, X unchanged,
Y inverted to match Gerber axes, with Top/Bottom and native angle plus the
explicit factory offset. Compare the factory placement preview before payment.
The native position export is included as a second reference.

The independent parser accepts KiCad absolute metric 4.6 X2; multi-quadrant arcs
are linearized only in memory at <=1 um chord error. Unsupported dialects block
readiness. Packet checks cover required layers, outline extents, drill locations,
pad copper/mask/paste, aperture floors, mask slivers and silkscreen over openings.
These are not a physical certification or an exhaustive electromagnetic/thermal
simulation. Engineering evidence and bench verification remain separate.

JLCPCB source references (check live before choosing a process):
- https://jlcpcb.com/help/article/pick-place-file-for-pcb-assembly
- https://jlcpcb.com/help/article/bill-of-materials-for-pcb-assembly
- https://jlcpcb.com/capabilities/pcb-capabilities
- https://jlcpcb.com/capabilities/pcb-assembly-capabilities
- https://jlcpcb.com/help/article/jlcpcb-copper-weight

The app runs at most two separate native review/repair rounds after an
implementation. Each round is followed by independent publication. It stops
on readiness, unchanged findings/source, cancellation or a provider failure,
and records the outcome in `.circuit/native-review.json`. Outstanding findings
remain visible and block ordering; no round limit converts a failure to a pass.

## Independent reviewer completion

The separate review process must write `.circuit/native-review-attestation.json`
with `status` (`pass` or `blocked`), `reviewer`, a substantive `summary`, and
`sourceFingerprints` containing the final `source.fingerprint` values from the
native sidecars. The driver removes the prior attestation before each round.
A packet that passes automated checks without a fresh matching independent
attestation remains blocked in the app. The server journal binds the completed
review to the final packet hashes as well as the source fingerprints.

The factory DRC uses a separate copy and appends the process floor after project
exceptions. The JLCPCB guide checked 2026-09-15 specifies 0.16 mm trace/spacing
for any 2 oz layer. Explicit copper stackup is required. Higher copper weights
need a different supported profile and remain blocked here.
