#!/usr/bin/env python3.12
"""Measure the whole board corpus at once.

Every finding on the board so far came from hand-rolling this across all
projects; the corpus is the instrument, so it should be one command.

    scripts/board-table.py                 # per-board summary
    scripts/board-table.py --rules         # DRC/ERC rules, by instance, per board
    scripts/board-table.py --netconflict   # split net_conflict by what disagrees
    scripts/board-table.py --errors        # every error-severity finding, with its board
    scripts/board-table.py --netlist       # copper netlist vs circuit.json, per board
    scripts/board-table.py --pour          # is the ground pour actually one piece

Reads the `.board.json` sidecars under ~/.autonomous-circuit/projects (or
$CIRCUIT_PROJECTS). Counts *instances*, not rows: `_collapse_kicad_repeats`
folds repeats into one row carrying `xN`, so a row is a group and a bare row
count says nothing about how much of the board is affected.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys

PROJECTS = os.environ.get(
    "CIRCUIT_PROJECTS", os.path.expanduser("~/.autonomous-circuit/projects")
)

RULE_RE = re.compile(r"^\[(\w+)\](?:\s+x(\d+))?")
SCHEMATIC_NET_RE = re.compile(
    r"Pad net \((?P<pad>[^)]*)\) doesn't match net given by schematic \((?P<sch>.*?)\)(?:\s|$)"
)


def load():
    """Yield (board_name, project_dir, sidecar) for every board with a sidecar."""
    for path in sorted(glob.glob(os.path.join(PROJECTS, "*", "boards", "*.board.json"))):
        try:
            with open(path) as fh:
                sidecar = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skipped {path}: {exc}", file=sys.stderr)
            continue
        name = sidecar.get("board", {}).get("name") or "?"
        yield name, os.path.basename(os.path.dirname(os.path.dirname(path))), sidecar


def rule_of(warning):
    """(rule, instances) for a kicad-sourced finding, else (None, 1).

    The instance count lives in the detail as `xN` and is 1 when the collapser
    found nothing to fold.
    """
    match = RULE_RE.match(warning.get("detail", ""))
    if not match:
        return None, 1
    return match.group(1), int(match.group(2) or 1)


def summary():
    print(
        f"{'board':22s} {'ready':6s} {'gerbers':10s} {'eff':7s} {'try':3s} "
        f"{'err':>4s} {'warn':>5s} {'info':>5s}   project"
    )
    for name, project, sidecar in load():
        counts = collections.Counter(
            w.get("severity") for w in sidecar.get("validation", {}).get("warnings", [])
        )
        build = sidecar.get("build", {})
        fab = sidecar.get("fab", {})
        print(
            f"{name:22s} {str(fab.get('ready')):6s} {str(fab.get('gerberSource')):10s} "
            f"{str(build.get('autorouterEffort')):7s} {str(build.get('attempts')):3s} "
            f"{counts['error']:4d} {counts['warning']:5d} {counts['info']:5d}   {project[:8]}"
        )


def rules():
    instances = collections.Counter()
    boards = collections.defaultdict(set)
    severity = {}
    for name, _project, sidecar in load():
        for warning in sidecar.get("validation", {}).get("warnings", []):
            rule, n = rule_of(warning)
            if rule is None:
                continue
            instances[rule] += n
            boards[rule].add(name)
            # A rule keeps the worst severity anyone gave it.
            order = {"info": 0, "warning": 1, "error": 2}
            if order.get(warning.get("severity"), 0) >= order.get(severity.get(rule), -1):
                severity[rule] = warning.get("severity")
    print(f"{'instances':>9s} {'boards':>6s} {'severity':8s} rule")
    for rule, n in instances.most_common():
        print(f"{n:9d} {len(boards[rule]):6d} {severity[rule]:8s} {rule}")


def netconflict():
    """Split net_conflict by what actually disagrees.

    KiCad autonames a net it was given no label for. Three outcomes, and only
    one of them is an electrical disagreement:
      unnamed   schematic net is `Net-(REF-PIN)` — same copper, no label
      floating  schematic net is `unconnected-(...)` — the schematic has no
                net there at all while the PCB pad sits on one
      renamed   two different named nets, which is a real netlist mismatch
    """
    buckets = collections.Counter()
    per_board = collections.defaultdict(collections.Counter)
    samples = collections.defaultdict(list)
    for name, _project, sidecar in load():
        for warning in sidecar.get("validation", {}).get("warnings", []):
            rule, n = rule_of(warning)
            if rule != "net_conflict":
                continue
            match = SCHEMATIC_NET_RE.search(warning.get("detail", ""))
            if not match:
                bucket = "unparsed"
            elif match.group("sch").startswith("unconnected-("):
                bucket = "floating"
            elif match.group("sch").startswith("Net-("):
                bucket = "unnamed"
            else:
                bucket = "renamed"
            buckets[bucket] += n
            per_board[name][bucket] += n
            if len(samples[bucket]) < 3:
                samples[bucket].append(warning["detail"][:120])
    total = sum(buckets.values())
    print(f"net_conflict instances: {total}\n")
    for bucket, n in buckets.most_common():
        print(f"{n:7d}  {bucket:9s} {100 * n / total:5.1f}%")
        for sample in samples[bucket]:
            print(f"           {sample}")
    print()
    print(f"{'board':22s} " + " ".join(f"{b:>9s}" for b in buckets))
    for name in sorted(per_board):
        print(
            f"{name:22s} "
            + " ".join(f"{per_board[name][b]:9d}" for b in buckets)
        )


def errors():
    for name, _project, sidecar in load():
        for warning in sidecar.get("validation", {}).get("warnings", []):
            if warning.get("severity") != "error":
                continue
            print(
                f"{name:22s} {warning.get('kind',''):28s} {warning.get('part','')[:24]:24s} "
                f"{warning.get('detail','')[:110]}"
            )



def _sexp_block(text, start):
    """The balanced-paren block beginning at `start`."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _kicad_pcb(project_zip):
    import zipfile

    with zipfile.ZipFile(project_zip) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".kicad_pcb"))
        return archive.read(name).decode("utf8", "replace")


def _pcb_nets(text):
    """{net name: {(refdes, pad)}} for every multi-pad net in a .kicad_pcb."""
    nets = collections.defaultdict(set)
    for m in re.finditer(r"\(footprint\b", text):
        block = _sexp_block(text, m.start())
        ref = re.search(r'\(property "Reference"\s+"([^"]*)"', block)
        ref = ref.group(1) if ref else "?"
        for pm in re.finditer(r'\(pad\s+"([^"]*)"', block):
            pad = _sexp_block(block, pm.start())
            net = re.search(r'\(net \d+ "([^"]*)"\)', pad)
            if net:
                nets[net.group(1)].add((ref, pm.group(1)))
    return {k: v for k, v in nets.items() if len(v) > 1}


def _cj_nets(circuit_json):
    """{frozenset of (refdes, pin)} for every multi-pin net in a circuit.json.

    Connectivity is the transitive closure of `source_trace` (which joins ports
    *and* nets, so a rail is one class), plus each component's declared internal
    connections.
    """
    of = lambda t: [e for e in circuit_json if e.get("type") == t]
    comp = {c["source_component_id"]: c.get("name") for c in of("source_component")}
    port = {
        p["source_port_id"]: (comp.get(p["source_component_id"]), p.get("name"))
        for p in of("source_port")
    }
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for trace in of("source_trace"):
        ids = (trace.get("connected_source_port_ids") or []) + (
            trace.get("connected_source_net_ids") or []
        )
        for other in ids[1:]:
            union(ids[0], other)
    for internal in of("source_component_internal_connection"):
        ids = internal.get("source_port_ids") or []
        for other in ids[1:]:
            union(ids[0], other)
    groups = collections.defaultdict(set)
    for pid, ref_pin in port.items():
        groups[find(pid)].add(ref_pin)
    return {k: v for k, v in groups.items() if len(v) > 1}


def netlist():
    """Is the copper the fab receives the netlist the source asked for?

    The gerbers are plotted from the converted `.kicad_pcb`, so the question
    that decides whether a fabbed board can work is whether that board's pad
    nets partition the pins the same way `circuit.json` — the IR of record —
    does. Compared on refdes groups with multiplicity, because the two models
    name pins differently and naming is not the question.
    """
    print(f"{'board':22s} {'ready':6s} {'cj':>4s} {'pcb':>4s} {'same':>5s}  verdict")
    for name, _project, sidecar in load():
        directory = os.path.join(PROJECTS, _project, "boards")
        stem = sidecar.get("board", {}).get("path", "").removesuffix(".circuit.json")
        circuit = os.path.join(directory, f"{stem}.circuit.json")
        project_zip = os.path.join(directory, f"{stem}_fab", "kicad-project.zip")
        ready = str(sidecar.get("fab", {}).get("ready"))
        if not (os.path.exists(circuit) and os.path.exists(project_zip)):
            print(f"{name:22s} {ready:6s} {'-':>4s} {'-':>4s} {'-':>5s}  artifact missing")
            continue
        with open(circuit) as fh:
            source = _cj_nets(json.load(fh))
        copper = _pcb_nets(_kicad_pcb(project_zip))
        a = collections.Counter(frozenset(r for r, _ in v) for v in source.values())
        b = collections.Counter(frozenset(r for r, _ in v) for v in copper.values())
        same = sum((a & b).values())
        verdict = (
            "IDENTICAL"
            if a == b
            else f"DIFFERS source-only={sum((a - b).values())} copper-only={sum((b - a).values())}"
        )
        print(f"{name:22s} {ready:6s} {len(source):4d} {len(copper):4d} {same:5d}  {verdict}")


def pour():
    """Is the ground pour one piece of copper, or is it in fragments?

    The converter writes a pour as a *triangle mesh* — thousands of 3-vertex
    `filled_polygon` entries — so counting those entries measures the mesh, not
    the copper, and KiCad's own `isolated_copper` counts them the same wrong
    way. Union the triangles across shared edges and the real answer falls out:
    how many pieces of copper there actually are, and how much of the pour the
    biggest one holds.
    """
    print(
        f"{'board':22s} {'ready':6s} {'layers':14s} {'tris':>6s} {'pieces':>7s} "
        f"{'area':>9s} {'biggest':>8s}"
    )
    for name, _project, sidecar in load():
        directory = os.path.join(PROJECTS, _project, "boards")
        stem = sidecar.get("board", {}).get("path", "").removesuffix(".circuit.json")
        project_zip = os.path.join(directory, f"{stem}_fab", "kicad-project.zip")
        ready = str(sidecar.get("fab", {}).get("ready"))
        circuit = os.path.join(directory, f"{stem}.circuit.json")
        layers = "-"
        if os.path.exists(circuit):
            with open(circuit) as fh:
                pours = [e for e in json.load(fh) if e.get("type") == "pcb_copper_pour"]
            layers = ",".join(sorted({p.get("layer", "?") for p in pours})) or "NONE"
        if not os.path.exists(project_zip):
            print(f"{name:22s} {ready:6s} {layers:14s}  (no kicad project)")
            continue
        text = _kicad_pcb(project_zip)
        zones = [_sexp_block(text, m.start()) for m in re.finditer(r"\(zone\b", text)]
        if not zones:
            print(f"{name:22s} {ready:6s} {layers:14s}  (no zone)")
            continue
        biggest_zone = max(zones, key=len)
        tris = []
        for m in re.finditer(r"\(filled_polygon\b", biggest_zone):
            pts = [
                (round(float(x), 6), round(float(y), 6))
                for x, y in re.findall(
                    r"\(xy ([-\d.]+) ([-\d.]+)\)", _sexp_block(biggest_zone, m.start())
                )
            ]
            if len(pts) == 3:
                tris.append(pts)
        if not tris:
            print(f"{name:22s} {ready:6s} {layers:14s}  (zone is not a triangle mesh)")
            continue
        parent = list(range(len(tris)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        edges = collections.defaultdict(list)
        for i, t in enumerate(tris):
            for a in range(3):
                edges[tuple(sorted((t[a], t[(a + 1) % 3])))].append(i)
        for shared in edges.values():
            for other in shared[1:]:
                ra, rb = find(shared[0]), find(other)
                if ra != rb:
                    parent[ra] = rb

        def tri_area(t):
            (x1, y1), (x2, y2), (x3, y3) = t
            return abs(x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2)) / 2

        by_piece = collections.Counter()
        for i, t in enumerate(tris):
            by_piece[find(i)] += tri_area(t)
        total = sum(by_piece.values())
        biggest = max(by_piece.values())
        print(
            f"{name:22s} {ready:6s} {layers:14s} {len(tris):6d} {len(by_piece):7d} "
            f"{total:8.0f}mm {100 * biggest / total:7.1f}%"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", action="store_true", help="DRC/ERC rules by instance")
    parser.add_argument("--netconflict", action="store_true", help="split net_conflict")
    parser.add_argument("--errors", action="store_true", help="every error-severity finding")
    parser.add_argument("--netlist", action="store_true", help="copper netlist vs circuit.json")
    parser.add_argument("--pour", action="store_true", help="is the ground pour one piece")
    args = parser.parse_args()
    if args.rules:
        rules()
    elif args.netconflict:
        netconflict()
    elif args.errors:
        errors()
    elif args.netlist:
        netlist()
    elif args.pour:
        pour()
    else:
        summary()


if __name__ == "__main__":
    main()
