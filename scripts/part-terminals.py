#!/usr/bin/env python3.12
"""Ask whether each physical terminal of a multi-pad part carries one net.

    scripts/part-terminals.py              # every board in the corpus
    scripts/part-terminals.py <board>...   # named boards only
    scripts/part-terminals.py --declared   # grade by the block's declaration instead

**Why this is not the same question the pipeline already asks.** A part whose
pads are internally tied has fewer terminals than pads. The board source
declares that tie with `internallyConnectedPins`, every downstream check reads
the declaration, and so a board can be perfectly self-consistent with a *wrong
model of the part* while a terminal quietly shorts signal to ground.

That is not hypothetical — it is #26, and it cost every button on eight boards.
weather-badge-25 reads **0 shorted terminals against the pairing its own block
declared, and 5 against the pairing the part actually has**. The declaration is
the thing under test, so this script must never grade with it. It grades by
supplier part number against `PART_TERMINALS` below, where every entry carries
where its pairing came from.

`--declared` re-runs against `internally_connected_source_port_ids` — not a
second opinion, but a way to see the gap between what a board believes and what
it is. On a board with a correct block the two agree.

A part that is not in the table is **reported, never skipped in silence**: a
check that could not run is not a clean result. The same applies one level up:
the summary names every project that produced no readable board, so a corpus
total is never quietly short of the corpus.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys

PROJECTS = os.environ.get(
    "CIRCUIT_PROJECTS", os.path.expanduser("~/.autonomous-circuit/projects")
)

#: LCSC part number -> the terminals the *part* has, as groups of pad names.
#:
#: Every entry states its provenance. Nothing goes in here off a block's own
#: declaration — that is the thing being graded.
PART_TERMINALS: dict[str, dict] = {
    "C318884": {
        "desc": "4-pad tactile switch, 2 terminals",
        # Datasheet circuit diagram joins each ROW with a bar; our land pattern
        # `dfn4_p3.6998mm_w7mm_pw0.75mm` numbers down the left column and up the
        # right (DFN convention), measured off a built board's pad coordinates:
        #   pin1 top-left     pin4 top-right    <- one row, one terminal
        #   pin2 bottom-left  pin3 bottom-right <- the other
        # So pin1/pin2 are one COLUMN: one pad from each terminal. Tying those
        # is what shorted the button. First-article continuity is still the
        # final word: pin1-pin4 closed with the button up, pin1-pin2 open.
        "terminals": [("pin1", "pin4"), ("pin2", "pin3")],
        "source": "datasheet diagram + land pattern measured off a built board, 2026-08-21",
    },
    "C20625731": {
        "desc": "ABM8-272-T3, 4-pad SMD3225 crystal, 2 crystal terminals + case",
        # Abracon drawing #456603 rev B, issued 2024-09-16, page 4 TOP VIEW:
        # the crystal element is drawn between pin1 and pin3 — the diagonal —
        # and pin4 and pin2 are each labelled GND. Land pattern measured off
        # weather-badge-27's pad coordinates, which numbers the same way:
        #   pin4 (-20.10, -11.43)   pin3 (-17.90, -11.43)  <- top row
        #   pin1 (-20.10, -13.13)   pin2 (-17.90, -13.13)  <- bottom row
        # So pin1/pin3 are the two crystal terminals and each is its own
        # terminal: nothing ties them, and a board that joins them has shorted
        # the oscillator across itself.
        #
        # The one thing the drawing does NOT do is draw a tie between the two
        # GND pads. Grouping them is the reading that FLAGS a board putting
        # different nets on pin2 and pin4, and it changes no verdict in the
        # corpus as it stands — every board here grounds both. If a board ever
        # wants them separate, this entry is what it has to argue with.
        # First-article continuity is still the final word.
        "terminals": [("pin1",), ("pin3",), ("pin2", "pin4")],
        "source": "Abracon drawing #456603 rev B (2024-09-16) p.4 TOP VIEW + land pattern measured off weather-badge-27, 2026-08-24",
    },
    "C6186": {
        "desc": "AMS1117-3.3, SOT-223 LDO, 3 terminals across 4 lands",
        # Advanced Monolithic Systems' own AMS1117 datasheet, PIN CONNECTIONS:
        # "TAB IS OUTPUT", and the SOT-223 entry lists "3 PIN FIXED/ADJUSTABLE
        # VERSION — 1- Ground/Adjust, 2- VOUT, 3- VIN". The package drawing on
        # the same sheet (AMS DRW# 042292) is headed "3 LEAD SOT-223 PLASTIC
        # PACKAGE" and the part is described as a three-terminal regulator, so
        # the four lands carry three electrical nodes and the tab is one of the
        # three — the only one the sheet names is the output.
        #
        # The land pattern says the same thing mechanically. Measured off
        # weather-badge-27:
        #   pin1 GND   (12.93,  5.70)  2.50 x 1.10 = 2.75mm^2
        #   pin2 VOUT  (12.93,  8.00)  2.50 x 1.10 = 2.75mm^2
        #   pin3 VIN   (12.93, 10.30)  2.50 x 1.10 = 2.75mm^2
        #   pin4 TAB   ( 6.99,  8.00)  2.34 x 3.60 = 8.42mm^2
        # The tab sits on pin2's own centreline, opposite the three leads and
        # three times their area: it is the centre lead carried through the
        # package, and the centre lead is VOUT.
        #
        # **What this catches.** A board that grounds the tab for heatsinking
        # shorts the 3.3V rail to GND through the regulator. Nothing checked
        # that before this entry, on a part placed on every board in the corpus.
        # First-article continuity is still the final word.
        "terminals": [("pin1",), ("pin2", "pin4"), ("pin3",)],
        "source": "Advanced Monolithic Systems AMS1117 datasheet, PIN CONNECTIONS + AMS DRW# 042292 + land pattern measured off weather-badge-27, 2026-08-24",
    },
}


def _elements(path: str) -> list[dict]:
    with open(path) as fh:
        doc = json.load(fh)
    els = doc if isinstance(doc, list) else doc.get("elements", doc)
    return [e for e in els if isinstance(e, dict)]


def _nets_by_port(els: list[dict]) -> dict[str, set[str]]:
    """Every source_port -> the net(s) reachable from it, named or not.

    Union-find over `source_trace`, so a port reaches a net through any number
    of hops. More than one net on a port means those nets are joined there.

    **Not every net has a name.** A point-to-point trace carries
    `connected_source_port_ids` and an empty `connected_source_net_ids`, so the
    group it forms has no `source_net` in it and no name to report — the
    crystal is wired exactly this way on all 31 boards
    (`Y1.pin1 -> U3.XIN`, `source_trace_46` on weather-badge-27). Reporting
    those pads as reaching nothing would read as "unconnected" for a pad that
    is soldered to a pin, so an unnamed group is labelled by the pads in it
    instead. Reaching *no* trace at all is still the empty set, and still
    prints as FLOATING.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in els:
        if e.get("type") != "source_trace":
            continue
        ids = list(e.get("connected_source_port_ids") or [])
        ids += ["NET:" + n for n in (e.get("connected_source_net_ids") or [])]
        for a, b in zip(ids, ids[1:]):
            union(a, b)

    names = {
        "NET:" + e["source_net_id"]: e.get("name", "?")
        for e in els if e.get("type") == "source_net"
    }
    by_root: dict[str, set[str]] = collections.defaultdict(set)
    for key, name in names.items():
        by_root[find(key)].add(name)

    # Pads with no named net in their group: label the group by what is in it,
    # so "connected to U3.XIN through an unnamed net" cannot be misread as
    # "connected to nothing". One pad on its own gets no label and stays
    # FLOATING — that is the genuinely unconnected case and the only one.
    comps = {
        e["source_component_id"]: e.get("name") or e["source_component_id"]
        for e in els if e.get("type") == "source_component"
    }
    ports = [e for e in els if e.get("type") == "source_port"]
    members: dict[str, list[str]] = collections.defaultdict(list)
    for e in ports:
        members[find(e["source_port_id"])].append(
            f"{comps.get(e.get('source_component_id'), '?')}."
            f"{e.get('name') or (e.get('port_hints') or ['?'])[0]}"
        )
    for root, pads in members.items():
        if by_root.get(root) or len(pads) < 2:
            continue
        by_root[root] = {"unnamed net: " + " + ".join(sorted(pads))}

    return {
        e["source_port_id"]: by_root.get(find(e["source_port_id"]), set())
        for e in ports
    }


def check_board(path: str, declared: bool = False) -> tuple[list[str], int, int]:
    """Return (report lines, shorted terminal count, ungraded component count)."""
    els = _elements(path)
    nets = _nets_by_port(els)
    ports = {e["source_port_id"]: e for e in els if e.get("type") == "source_port"}
    lines: list[str] = []
    shorted = ungraded = 0

    for comp in sorted(
        (e for e in els if e.get("type") == "source_component"),
        key=lambda e: str(e.get("name") or ""),
    ):
        lcsc = ((comp.get("supplier_part_numbers") or {}).get("jlcpcb") or [None])[0]
        spec = PART_TERMINALS.get(lcsc or "")
        groups = comp.get("internally_connected_source_port_ids") or []
        if spec is None:
            # Only worth a line if the part is tied at all; an untied part has
            # one terminal per pad and nothing here to get wrong.
            if groups:
                ungraded += 1
                lines.append(
                    f"  {comp.get('name'):<6} {lcsc or '(no LCSC)':<10} "
                    f"NOT GRADED — no terminal pairing on file for this part"
                )
            continue

        # A pad is reachable by its name *and* by every hint it carries, so an
        # entry below can be written the way the datasheet writes it — by pin
        # number. It has to be: the pad's `name` is whatever the block called
        # it (`VOUT2`, `TAB`, `thermalpad`), and the block's account of the
        # part is the thing under test. Y1 happened to name its pads `pin1..4`
        # and hid this; U2 names them `GND`/`VOUT1`/`VIN`/`VOUT2` and a table
        # keyed on names would have silently matched nothing and reported the
        # terminal as floating. Name wins a collision, then hints in order.
        by_port_name = {}
        for pid, p in ports.items():
            if p.get("source_component_id") != comp.get("source_component_id"):
                continue
            for key in [p.get("name"), *(p.get("port_hints") or [])]:
                if key and key not in by_port_name:
                    by_port_name[key] = pid

        if declared:
            terminals = [
                tuple(ports[pid].get("name") or "?" for pid in grp) for grp in groups
            ] or [(n,) for n in by_port_name]
        else:
            terminals = spec["terminals"]

        for idx, term in enumerate(terminals):
            found = {by_port_name.get(pin) for pin in term} - {None}
            on: set[str] = set()
            for pid in found:
                on |= nets.get(pid, set())
            label = "+".join(term)
            if len(on) > 1:
                shorted += 1
                lines.append(
                    f"  {comp.get('name'):<6} {label:<12} "
                    f"{' + '.join(sorted(on)):<28} SHORTED"
                )
            else:
                lines.append(
                    f"  {comp.get('name'):<6} {label:<12} "
                    f"{(next(iter(on)) if on else '(floating)'):<28} "
                    f"{'ok' if on else 'FLOATING'}"
                )
    return lines, shorted, ungraded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("boards", nargs="*", help="board names; default is every board")
    ap.add_argument("--declared", action="store_true",
                    help="grade by the block's own declaration instead of the part")
    ap.add_argument("--quiet", action="store_true", help="totals only")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(PROJECTS, "*", "boards", "*.circuit.json")))
    if not paths:
        print(f"no built boards under {PROJECTS}", file=sys.stderr)
        return 2

    # A project that never built contributes nothing, and nothing is exactly
    # what a clean board also contributes. Name them, or the total silently
    # means "of the boards that happened to exist".
    built = {os.path.dirname(os.path.dirname(p)) for p in paths}
    unbuilt = [d.rstrip(os.sep) for d in glob.glob(os.path.join(PROJECTS, "*", ""))
               if d.rstrip(os.sep) not in built]

    total_short = total_ungraded = graded_boards = 0
    for path in paths:
        proj = os.path.dirname(os.path.dirname(path))
        try:
            with open(os.path.join(proj, "product.json")) as fh:
                name = json.load(fh).get("name", os.path.basename(proj))
        except OSError:
            name = os.path.basename(proj)
        if args.boards and name not in args.boards:
            continue
        try:
            lines, shorted, ungraded = check_board(path, declared=args.declared)
        except (OSError, ValueError, KeyError) as exc:
            print(f"{name}: unreadable — {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if not lines:
            continue
        graded_boards += 1
        total_short += shorted
        total_ungraded += ungraded
        flag = f"{shorted} SHORTED" if shorted else "clean"
        print(f"{name}: {flag}" + (f", {ungraded} ungraded" if ungraded else ""))
        if not args.quiet:
            print("\n".join(lines))

    basis = "the block's declaration" if args.declared else "the part"
    print(f"\n{graded_boards} board file(s) across {len(built)} project(s), graded "
          f"against {basis}: {total_short} shorted terminal(s), "
          f"{total_ungraded} component(s) ungraded")
    if unbuilt and not args.boards:
        print(f"{len(unbuilt)} project(s) produced no readable board and are NOT in "
              f"that total:")
        for d in sorted(unbuilt):
            try:
                with open(os.path.join(d, "product.json")) as fh:
                    label = json.load(fh).get("name", os.path.basename(d))
            except (OSError, ValueError):
                label = f"{os.path.basename(d)} (no readable product.json)"
            print(f"  {label}")
    return 1 if total_short else 0


if __name__ == "__main__":
    raise SystemExit(main())
