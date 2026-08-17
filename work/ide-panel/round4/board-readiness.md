# Lens 7 — Board readiness, round 4, 2026-08-17

**Score: 6/10** (down from round 3's flat 7). The repo-tree story is real progress —
the fleet grew 8→12 products, a planner fix (#52) measurably shrinks a whole defect
class, and a new dead-wiring check closed two more real bugs. But the three example
boards themselves are byte-for-byte unchanged since round 2 (same defect, same
judgement calls), and this round's own digging found the fleet's ready-count is being
read off the wrong copy: the **app-installed projects a human would actually open are
stale relative to the repo**, and materially worse on three products, including one
that reverts a claimed fix back to `fab.ready: false`.

## The three example boards, driven fresh today

Verified via `checks.floating_net_warnings` on each board's own `main.circuit.json`
and by reading `validation.warnings` in `main.board.json` (`file:line` evidence
inline; commands quoted below). All three: **0 blocking, `fab.ready: true`, complete
`main_fab/` packet** (checked one in full — see Fab packet section).

| Board | Edit 1 | Edit 2 | Edit 3 |
|---|---|---|---|
| `harness-puck` | No ESD/TVS clamp on USB-C — **judgement** | No test point on 3V3 (SWCLK/SWD/GND only) — **judgement** | USB D+/D− pair: `netclass_pair_skew` 10.39mm vs 3.8mm budget, plus `diffpair_not_routed` ("no corridor wide enough") — **our defect**, ledger #12 |
| `hydrate-coaster` | No ESD/TVS clamp — **judgement** | No 3V3 test point (only SWCLK/SWD/GND, `main.tsx:169-171`) — **judgement** | USB D+/D− pair: routed as a pair but still 5.87mm skew vs 3.8mm budget on `USB_DP_CONN`/`USB_DM_CONN` — **our defect** |
| `terminal-keyboard` | No ESD/TVS clamp — **judgement** | No 3V3 test point — **judgement** | USB D+/D− pair: 20.25mm skew on one segment, `diffpair_not_routed` on the other — **our defect** |

This is identical in shape and identical in specifics to round 2 and round 3: each
board still needs its "third edit" to stop being our defect (ledger #12/#45, still
open). The ship bar's "≤3 edits, all judgement" clause is still not met — one of
three is always ours. Checked whether the gap fix (#52) touches these three: it does
not — ledger #52 itself records that no board `.tsx` calls `place_board()` at build
time, so the planner change cannot regress or improve any committed board, examples
included. Confirmed: none of the three examples' geometry changed.

## New today: `checks.floating_net_warnings` over the whole fleet

Ran directly (`PYTHONPATH=packages/circuitpy/src`, `checks.floating_net_warnings`)
against every board's own `main.circuit.json`, all 15 boards (3 examples + 12
products):

```
examples/harness-puck        0
examples/hydrate-coaster     0
examples/terminal-keyboard   0
products/bench-i2c-scanner   0
products/desk-air-monitor    1   ["net BTN1 reaches only SW1"]
products/dual-rail-psu       0
products/dual-sensor-node    0
products/env-logger-usb      0
products/i2c-sensor-hub      0
products/macropad-6          0
products/pixel-badge         0
products/rgb-lamp-controller 0
products/sensor-node-mini    2   ["net USB_DM reaches only U3", "net USB_DP reaches only U3"]
products/two-key-footswitch  0
products/usb-c-breakout      0
```

Ledger #54 claims `sensor-node-mini` and `desk-air-monitor` were "fixed at the board
level" the same day. **That claim does not hold against the artifact that actually
ships.** For `desk-air-monitor`: `main.tsx` (mtime 11:28:23) carries a comment and a
real fix — `SwTact signal="BTN_USER"` traced to `.U3 > .GPIO22`
(`products/desk-air-monitor/boards/main.tsx:100-101`) — but `main.circuit.json` and
`main.board.json` on disk are from **11:23:14, five minutes older than the source
fix**, so the shipped sidecar still describes the broken board; a reviewer told to
trust the sidecar (per this task's own rules) sees no defect and also sees the wrong
one. For `sensor-node-mini`: the fix that shipped is different from what #54
describes — the board deliberately kept `usb-c-power` (no data pins) and added a
`DebugPort`/SWD instead, explained at length in `main.tsx:13-25` as a real product
decision, not an oversight. But `rp2040-core`'s own `USB_DP`/`USB_DM` breakout pins
(`blocks/rp2040-core/rp2040-core.tsx:69-70,196-197`) are wired to those net names
regardless of whether `usb-c-data` is present, so the check still fires — a
block-contract gap, not a board-author mistake, and not closeable from the product
board without either adding the USB stack back (defeating the design) or a block
change. Both findings are `severity: warning`, so neither moves `fab.ready`.
**`pixel-badge`** and **`harness-puck`** *are* genuinely fixed — confirmed by reading
the source (`pixel-badge` ties the dangling `PX_18_DIN` to a new `TP4` testpoint at
`main.tsx:150`; `harness-puck` renames the last hop `NC_PX_18_DIN` at
`main.tsx:137-138`, the check's own documented escape hatch) and by circuit.json
mtimes newer than the tsx edit in both cases (rebuilt after the fix, unlike the two
above).

## Fab packet — opened one in full

`products/two-key-footswitch/boards/main_fab/`: all four required files present.
`gerbers.zip` unzips to 22 files (all four fab + assembly layers, `board.drl` drill
file, `board-job.gbrjob`) — not just a subset. `bom.csv`: 16 of 19 lines carry an
LCSC part number (matches `bom.orderable: 16` in the sidecar exactly); Footprint
column blank on 6 of 16 rows, cosmetic (JLCPCB matches by LCSC number, not this
column) and not gated by any `bom_catalog_missing` warning in this board's sidecar.
`cpl.csv` has placement rows for every part. `ORDER.md` is a real, board-specific
JLCPCB walkthrough (correct dimensions, correct layer count, correct cost figures) —
not a template stub. This packet would get a human a real board back. Not the worst
finding; a genuine pass.

## The fleet, repo tree vs. the actual running app — the headline finding

`scripts/fleet-status`, re-run fresh, matches `products/README.md` exactly: **9 of 12
products `fab.ready: true`** in the repo checkout. That is the number every ledger
entry and every prior round's fleet claim is built on. But the repo checkout is not
what an outside EE would open — `~/.autonomous-circuit/projects/<uuid>` is (per
`CLAUDE.md`: "projects live under `~/.autonomous-circuit/projects/<uuid>`"). Read
every installed project's own `main.board.json` directly:

| Product | Repo (`products/`) | Installed app project | Match? |
|---|---|---|---|
| `two-key-footswitch` | ready, 0 blocking | **not ready, 3 blocking** | No — reverts ledger #55's headline fix |
| `macropad-6` | not ready, 10 blocking | not ready, **14 blocking** | No — installed copy is the *pre-#52* number |
| `pixel-badge` | not ready, 9 blocking | **never built** (`boards/` has no sidecar) | No |
| `rgb-lamp-controller` | ready, 0 blocking | **no project directory at all** | No — doesn't exist in the app |
| `dual-rail-psu` | ready, 0 blocking | **no project directory at all** | No — doesn't exist in the app |
| `bench-i2c-scanner` | not ready, 4 blocking | not ready, 3 blocking | close, both broken |
| desk-air-monitor, sensor-node-mini, usb-c-breakout, env-logger-usb, dual-sensor-node, i2c-sensor-hub | ready, 0 blocking each | ready, 0 blocking each | Yes |

The three example boards do **not** show this drift (installed and repo copies agree
on `fab.ready`, blocking count, and every pair-skew warning for all three — verified
by direct diff and by re-reading `validation.warnings`). The drift is confined to the
12 products, and it is corroborated independently: lens 4's round-4 report drove
`two-key-footswitch`'s *installed* project live via `POST /api/board_fast_check` and
got the same `blocking:3, fabReady:false` this report found by reading the sidecar
directly — two different methods, same wrong number. Counting only what is actually
installed and built today: **6 of the 10 installed products are ready**, 2 products
that the repo calls ready do not exist as app projects at all, and 1 (`pixel-badge`)
has no build. "9 of 12 fab-ready" is a repo-tree claim; the number a human opening
the app sees today is worse and internally inconsistent (a board that was fixed
three ledger entries ago reads as broken again).

## Scope note for the rubric

The rubric says "the three example boards"; there are now 12 products beyond them.
Judged both, as asked. The rubric's ship-bar clause about the example boards still
applies unchanged — they still fail it, unchanged since round 2. The fleet's own bar
("tens of boards... ready for someone else's hands") is not met: 12 is not tens, and
of those 12, the number that is actually live and correct for a human to open today
is smaller than the repo's own claim. Proposal: lens 7 should score against **what is
installed**, not what is checked into `products/`, since that is the only copy an
outside EE ever sees — a repo-tree number that the app doesn't serve is not board
readiness, it's commit-log optimism.

## Must-fix
1. Sync (or auto-rebuild) `~/.autonomous-circuit/projects/*` from `products/` before
   any readiness claim is made from the repo tree — `two-key-footswitch` and
   `macropad-6` are currently worse live than on disk, and `rgb-lamp-controller`/
   `dual-rail-psu` don't exist live at all.
2. `desk-air-monitor` and `sensor-node-mini`: rebuild so the shipped sidecar reflects
   the source fix already committed (`main.tsx` newer than `main.circuit.json` on
   `desk-air-monitor` specifically — a build gap, not a code gap).
3. USB D+/D− pair skew/coupling (ledger #12/#45) is open on every board sampled that
   uses `usb-c-data`, including the newest "fixed" ones (`two-key-footswitch` itself
   carries `netclass_pair_skew` at 5.75mm). Still the one recurring defect keeping
   every example board off the ship bar.

## Would an outside EE get this today: no

The three examples are honestly labelled and their packets are real, but each still
carries one of our defects, unchanged for three rounds. The fleet's headline number
is not what ships — a human opening the app today would find two "done" products
missing outright and one flagship fix reverted.
