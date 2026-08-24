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
check that could not run is not a clean result.
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
}


def _elements(path: str) -> list[dict]:
    with open(path) as fh:
        doc = json.load(fh)
    els = doc if isinstance(doc, list) else doc.get("elements", doc)
    return [e for e in els if isinstance(e, dict)]


def _nets_by_port(els: list[dict]) -> dict[str, set[str]]:
    """Every source_port -> the net name(s) reachable from it.

    Union-find over `source_trace`, so a port reaches a net through any number
    of hops. More than one net on a port means those nets are joined there.
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
    return {
        e["source_port_id"]: by_root.get(find(e["source_port_id"]), set())
        for e in els if e.get("type") == "source_port"
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

        by_port_name = {}
        for pid, p in ports.items():
            if p.get("source_component_id") != comp.get("source_component_id"):
                continue
            by_port_name[p.get("name") or (p.get("port_hints") or [""])[0]] = pid

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
    print(f"\n{graded_boards} board(s) graded against {basis}: "
          f"{total_short} shorted terminal(s), {total_ungraded} component(s) ungraded")
    return 1 if total_short else 0


if __name__ == "__main__":
    raise SystemExit(main())
