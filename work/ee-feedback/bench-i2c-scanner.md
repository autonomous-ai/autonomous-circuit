# bench-i2c-scanner — EE feedback

Built `products/bench-i2c-scanner`: USB-C bench tool with two `PadHeader`
bus connectors (bus + pass-through, one shared I2C bus per BLOCK.md's "place
exactly ONE i2c-bus per bus" rule), four status LEDs, two buttons. Composed
entirely from golden blocks (`usb-c-data`, `ldo-3v3`, `rp2040-core`,
`i2c-bus`, `status-led` x4, `sw-tact` x2) plus `glue.tsx` (`PadHeader` x2,
`DebugPort`, `MountingHole`, `GndPour`).

**Verdict: not ready.** 91.9 x 63.8mm, 2 layers, `fab.ready: false`, 3
blocking (1 `dfm_hole_clearance`, 2 `drc_violation`), 187 advisory, 398
info. BOM 28 lines, 17 orderable. Took 4 full `circuit` builds (~75 min) to
reach a confirmed, unfixable-at-my-level verdict.

## Friction, ranked by time cost

**1. A block-internal `rp2040-core` routing defect survives every lever
this pipeline currently offers (~55 minutes, 3 builds).** All 3 blocking
findings cluster at one spot inside the RP2040's own decoupling cluster: a
DVDD-net via at 0.0008-0.026mm clearance against a 0.09mm rule, and a
matching hole-clearance violation. Built first at the documented `5x` floor
— the pipeline's own escalation ladder (`generation.py`) saw the routing-class
`dfm_hole_clearance`, auto-retried at `10x`, tied 2-for-2, and kept the
cheaper build with a note: "rebuilding by hand will not clear it — the
remaining lever is placement." I tested that literally two ways: declaring
`10x` myself (identical 3 errors, same location) and widening the
`rp2040-core`-to-neighbor gap from the documented 5mm floor
(`BLOCK_GAP_OVERRIDE_MM`, `layout.py`) to 9mm — which made it **worse** (4
errors, a new via-via pair). The escalation note's claim held; the
"remaining lever" it named did not exist. This is the same 3-finding
signature (`dfm_hole_clearance` + 2 `drc_violation`) reported in
`two-key-footswitch.md`, and confirms `layout.py`'s own hedge — "5mm is a
floor... not a guarantee" — with a board dense enough to actually need more
than 5mm, except more didn't work either. `rp2040-core` + `usb-c-data` now
has three fleet boards hitting this; it reads as a defect in the block's own
decoupling layout, not something a board author can place around.

**2. `PadHeader` has no offset parameter, so two instances collide on
refdes — undocumented (~15 minutes).** `PadHeader`'s pads are always named
`${prefix}${i+1}`, starting at 1. Two headers at `prefix="TP"` both want
`TP1`-`TP4`. The BOM gate's bare-copper carve-out (`checks.py:
_UNSOURCED_PREFIXES`) only cares about the *alpha* characters of a refdes,
so `prefix="TP1"`/`"TP2"` (→ `TP11`-`14` / `TP21`-`24`) sidesteps the
collision and stays exempt — a real trick, found only by reading
`_is_unsourced_by_design`'s regex logic, not from `glue.tsx`'s own docs.

**3. `DebugPort` is structurally mandatory but absent from the brief's own
compose list (~10 minutes).** `rp2040-core` brings out `SWCLK`/`SWD` as
bare nets; `review_debug_unreachable` is escalated warning→error
(`fab.py:VERIFY_ESCALATED_KINDS`), so any board that skips a debug header
is blocked. The assignment's compose list named `PadHeader x2` for the bus
breakout and never mentioned `DebugPort` — correct per the brief's own
device description, but silently wrong if followed literally; caught only
by reading `fab.py` before building, not from anything at the block or
brief level.

**4. Positive — `board_fast_check` now names its own blind spot by count,
not just by kind.** `two-key-footswitch.md` reported discovering the
fast-check/full-build gap only by diffing manually. This build's
`board_fast_check` response carries `lastBuild.invisibleHere: 2` and
`invisibleKinds: ["drc_violation", ...]` directly — the fast path is honest
about exactly how many of its own blocking findings are invisible to it,
without a second build to find out.

**5. Positive — `project_create`'s flat-body fix works as documented.**
`{"name":"bench-i2c-scanner"}` produced a project named exactly that, not
"New project."
