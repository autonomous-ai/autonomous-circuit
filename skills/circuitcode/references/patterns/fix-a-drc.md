# Pattern: fix a DRC / ERC finding

**Trigger:** the verdict came back with `error`-severity warnings.

**Why this exists:** these findings cascade — one placement mistake generates
five warnings — and fixing the loudest one first wastes a build.

## Fix in this order

**1. Placement errors first.** `pcb_footprint_overlap_error`,
`pcb_pad_pad_clearance_error`, `pcb_courtyard_overlap_error`. Two parts are on
top of each other. Move one; adjust `pcbX/pcbY`.

**2. Then autorouting.** `pcb_autorouting_error`, `pcb_trace_missing_error`,
`pcb_port_not_connected_error` are usually *consequences* — the router refuses
to run when placement errors exist and says so. Fix (1) and re-run before
touching anything here. If routing still fails on a clean placement, the board
is too crowded: spread the blocks, shorten the runs, or reduce the part count.

**3. Then connectivity.** `source_trace_not_connected_error` means a trace names
a port that does not exist — almost always a pin-label typo. The block's
`BLOCK.md` has the real labels; the datasheet's pin *numbers* are not the labels.

**4. Then footprints.** `pcb_missing_footprint_error`: a component has no land
pattern. Glue components need an explicit `footprint="0402"`-style string;
blocks carry their own.

**5. Then DFM.** `dfm_*` findings are geometry below the fab floor — widen the
trace (`helpers.trace_width_for()`), enlarge the drill, or pull copper back from
the edge.

**6. Then sourcing.** `part_not_orderable` and `part_drift` are `parts-book`'s
territory, not yours — hand off rather than editing `parts.json`.

## Interpreting severity

- `error` — blocking. You may not declare done.
- `warning` — fix it or say out loud why you are not. `unverified_gerbers`
  (kicad-cli missing) is one you cannot fix from the board file; report it.
- `info` — worth mentioning. `extended_part` means a Basic alternative would
  save ~$3 per line.

## Test points are allowed — use them

A `<testpoint>` carries no LCSC number because it is copper rather than a part,
and the BOM gate used to call that unorderable and block the packet. It no
longer does (verified 2026-08-10: a board with a test point reaches
`fab.ready: true`). So when the review panel's testability lens asks where a
probe goes on a dead board, the answer is a test point on each rail — put them
in clear space, since one dropped on a pad cascades into overlap and
courtyard errors.

## Pitfalls

- **Don't disable checks.** The `--ignore-*-drc` flags exist; using one turns a
  known-broken board into a silently-broken board.
- **Don't trust a clean exit.** `tscircuit-cli build` exits 0 with real errors
  inside `circuit.json`. The verdict's `warnings` list is the truth.
- **Smallest responsible change.** A 0.5mm nudge, not a re-layout. Re-run after
  each fix; the build cache makes iteration cheap.
- **After four rounds, stop.** `tables.MAX_REPAIR_ITERATIONS`. Past that you are
  guessing — tell the user what is fighting you and what you would trade.
