"""The sub-second verdict on a placement edit — and an honest list of what it
cannot see.

A drag in the IDE changes one numeric literal in ``boards/<stem>.tsx``. The
compiled ``circuit.json`` still holds the old position, and recompiling it takes
~20s for placement only and minutes for the full gauntlet. Neither is an IDE
interaction. So this module answers the only question that can be answered
between a mouse-up and the next frame: **is the geometry the user is looking at
legal on copper?**

Three things make that answer honest rather than merely fast.

1. **It grades predicted geometry, and says so.** The board on disk was built
   from the old literal. This translates the moved placement's elements in
   memory — the same anchor arithmetic ``boardSource.js:502`` uses to bind a
   source line to the built board — and grades that. ``geometry`` in the result
   is ``"predicted"`` whenever a move was applied.
2. **It carries its own blind spots as data.** ``not_checked`` is part of the
   result, not a footnote in the UI. Nothing here sees the copper pour, the fab
   packet, or what the router will do on the next build, and the caller is
   given those words to print.
3. **It has a stand-in for the check it drops, and a cheaper ruler for the one
   it cannot.** ``checkTracesAreContiguous`` is the only thing in the whole
   stack that notices a part has left its copper, and costs **1,491ms** on
   terminal-keyboard; ``checks.trace_anchor_warnings`` catches the same defect
   in **3.8ms**, so the node leg runs without it.

   ``checkPcbComponentsOutOfBoard`` costs **5,368ms on harness-puck** — 84% of
   that board's whole answer — against 49ms on terminal-keyboard. Not part
   count (61 components against 137): the *board outline*, which tscircuit
   emits as 2,205 points for a round board and omits for a rectangle. It is
   **not** dropped, because it lives in a group that also holds an internal
   ``checkCourtyardOverlap`` that is not exported and cannot be composed
   around. Instead the node leg is handed a board whose outline is thinned to
   ``MAX_OUTLINE_POINTS``; the error that buys is bounded and named in the
   helper (0.042mm on harness-puck's 35mm radius, against a 0.2mm rule the DFM
   leg measures exactly).

**The whole answer, on all three shipped boards, warm** (2026-08-16, this
module, `--disable-parts-engine` not involved — the gate never resolves parts):

    harness-puck        6,332ms -> 726ms   (70x70 round, 2,442 elements)
    hydrate-coaster     2,009ms -> 455ms   (80x80 squircle, 1,835 elements)
    terminal-keyboard   1,949ms -> 753ms   (100x90 rect, 5,463 elements)

All three boards, not the flattering one: an earlier version of this docstring
quoted terminal-keyboard alone, which was the only board the promise held on.
The Python leg and the node leg run concurrently because neither touches the
other's data.

CLI (one JSON line on stdout, last line wins — the house convention):

    python -m circuitpy.fastcheck <project_dir> --board boards/main.circuit.json \\
        --move -45.8,-32:2.8,0
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from circuitpy import checks, fab as fab_mod, spec as spec_mod, verify_bridge

#: The check dropped from the node leg, and the reason it is affordable to drop.
#: Kept as a constant because two places have to agree about it: the node
#: invocation and the `checked`/`not_checked` bookkeeping the UI prints.
SKIPPED_NODE_CHECKS = ("checkTracesAreContiguous",)

#: How many outline points the node leg is given. The board's *shape* is what
#: makes `checkPcbComponentsOutOfBoard` expensive — 2,205 points on a round
#: board — and the check is kept rather than dropped because
#: `runAllPlacementChecks` also holds an internal `checkCourtyardOverlap` that
#: is not exported and cannot be composed around. See the helper's own comment
#: for the error bound (0.042mm on harness-puck).
MAX_OUTLINE_POINTS = 64

#: Anchor keying, to 0.1µm. Identical to `KEY` in `boardSource.js:436` — a
#: placement binds to geometry by exact arithmetic or it does not bind, and the
#: two sides of that arithmetic must round the same way.
def _key(x: Any, y: Any) -> str:
    return f"{round(float(x) * 10000)},{round(float(y) * 10000)}"


#: Element kinds a board line places directly, with no component behind them:
#: a `<MountingHole>`'s drill, a `<silkscreentext>` label. Mirrors `LOOSE_TYPES`
#: (`boardSource.js:445`). Copper is deliberately absent — a trace or a via is
#: something the router made, never something a line of the board file put
#: there.
LOOSE_TYPES = frozenset({
    "pcb_hole",
    "pcb_cutout",
    "pcb_silkscreen_text",
    "pcb_silkscreen_path",
    "pcb_silkscreen_rect",
    "pcb_silkscreen_circle",
})

#: Copper the router owns. A placement move never drags these, which is the
#: whole reason `trace_anchor_warnings` exists: the copper stays where it was
#: and the part walks away from it.
ROUTER_OWNED = frozenset({"pcb_trace", "pcb_via", "pcb_copper_pour"})

#: Where a translation has to land, per element kind. Scalar `x`/`y`, a
#: `{x, y}` point under some name, or a list of `{x, y}` route points.
_POINT_FIELDS = ("center", "anchor_position")
_ROUTE_FIELDS = ("route", "points", "outline")


@dataclass(frozen=True)
class Move:
    """One placement move: the anchor the board file named before the edit, and
    how far it moved. The anchor is the *pre-edit* value because that is what
    the built board still holds."""

    x: float
    y: float
    dx: float
    dy: float


def parse_move(text: str) -> Move:
    """``"-45.8,-32:2.8,0"`` — anchor, then delta."""
    anchor, _, delta = str(text).partition(":")
    ax, _, ay = anchor.partition(",")
    dx, _, dy = delta.partition(",")
    return Move(float(ax), float(ay), float(dx or 0), float(dy or 0))


# ---------------------------------------------------------------------------
# Resolving a move to the elements it drags
# ---------------------------------------------------------------------------


def _group_tree(elements: Sequence[dict]) -> tuple[dict, dict, dict]:
    """``(children_by_source_group, components_by_source_group, root_ids)``.

    `pcb_group.pcb_component_ids` is empty on every board we ship (checked on
    terminal-keyboard: all 61 groups), so group membership is read from the
    source side — `source_component.source_group_id` — exactly as
    `boardIndex.js:584` does it.
    """
    children: dict[str, list[str]] = {}
    components: dict[str, list[str]] = {}
    roots: set[str] = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "source_group":
            gid = str(element.get("source_group_id") or "")
            if not gid:
                continue
            parent = str(element.get("parent_source_group_id") or "")
            if parent:
                children.setdefault(parent, []).append(gid)
            else:
                roots.add(gid)
        elif element.get("type") == "source_component":
            gid = str(element.get("source_group_id") or "")
            cid = str(element.get("source_component_id") or "")
            if gid and cid:
                components.setdefault(gid, []).append(cid)
    return children, components, roots


def _pcb_component_by_source(elements: Sequence[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for element in elements:
        if isinstance(element, dict) and element.get("type") == "pcb_component":
            source = str(element.get("source_component_id") or "")
            pcb = str(element.get("pcb_component_id") or "")
            if source and pcb:
                out[source] = pcb
    return out


def _refdes_by_source(elements: Sequence[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for element in elements:
        if isinstance(element, dict) and element.get("type") == "source_component":
            sid = str(element.get("source_component_id") or "")
            name = str(element.get("name") or "")
            if sid and name:
                out[sid] = name
    return out


def resolve_move(elements: Sequence[dict], move: Move) -> dict:
    """Which elements a move drags, or why it cannot be resolved.

    Returns ``{"ok": bool, "reason": str, "pcb_component_ids": set,
    "element_ids": set, "refdes": [str], "kind": str}``. The refusal strings are
    the same three the UI already shows for an unbindable placement, because a
    placement that the client bound and the server cannot is a disagreement
    worth reading in the same words.
    """
    key = _key(move.x, move.y)
    children, group_components, roots = _group_tree(elements)
    pcb_by_source = _pcb_component_by_source(elements)
    refdes_by_source = _refdes_by_source(elements)

    # Only direct children of the board's own group are candidates, matching
    # both the parser (`boardSource.js:296`, `depth === 0` inside `<board>`) and
    # the binder (`boardSource.js:523`). Without this filter a block whose lead
    # part sits exactly on the block's own anchor — the normal case for a
    # two-part block like `<StatusLed>` — reads as two candidates and the move
    # is refused as ambiguous. Measured on terminal-keyboard: `pcb_group_53`
    # and LED1 both sit at (-45.8, -32), and LED1 is inside the group, not
    # beside it.
    group_parent: dict[str, str] = {}
    component_group: dict[str, str] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "source_group":
            group_parent[str(element.get("source_group_id") or "")] = str(
                element.get("parent_source_group_id") or ""
            )
        elif element.get("type") == "source_component":
            component_group[str(element.get("source_component_id") or "")] = str(
                element.get("source_group_id") or ""
            )
    root = next(iter(sorted(roots)), "")

    candidates: list[tuple[str, dict]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        etype = element.get("type")
        if etype == "pcb_group":
            anchor = element.get("anchor_position")
            if not isinstance(anchor, dict) or _key(anchor.get("x", 1e9), anchor.get("y", 1e9)) != key:
                continue
            gid = str(element.get("source_group_id") or "")
            if root and group_parent.get(gid, "") != root:
                continue
            if not root and gid in roots:
                continue
            candidates.append(("group", element))
        elif etype == "pcb_component":
            center = element.get("center")
            if not isinstance(center, dict) or _key(center.get("x", 1e9), center.get("y", 1e9)) != key:
                continue
            if root and component_group.get(str(element.get("source_component_id") or ""), "") != root:
                continue
            candidates.append(("component", element))

    if len(candidates) > 1:
        return {
            "ok": False,
            "reason": "this spot matches more than one thing on the board",
            "pcb_component_ids": set(),
            "element_ids": set(),
            "refdes": [],
            "kind": "",
        }

    source_ids: list[str] = []
    kind = ""
    if candidates:
        kind, hit = candidates[0]
        if kind == "group":
            stack = [str(hit.get("source_group_id") or "")]
            seen: set[str] = set()
            while stack:
                gid = stack.pop()
                if not gid or gid in seen:
                    continue
                seen.add(gid)
                source_ids.extend(group_components.get(gid, ()))
                stack.extend(children.get(gid, ()))
        else:
            source_ids.append(str(hit.get("source_component_id") or ""))

    pcb_ids = {pcb_by_source[s] for s in source_ids if s in pcb_by_source}
    handle = candidates[0][1] if candidates else None
    element_ids: set[int] = set()
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        if element.get("type") in ROUTER_OWNED:
            continue
        owner = element.get("pcb_component_id")
        if (isinstance(owner, str) and owner in pcb_ids) or element is handle:
            element_ids.add(index)

    # A `<MountingHole>` is a group that owns no parts and a `<silkscreentext>`
    # is not a group at all. Both still draw something at exactly the point the
    # board file names, and both are worth dragging.
    if not pcb_ids:
        for index, element in enumerate(elements):
            if not isinstance(element, dict) or element.get("type") not in LOOSE_TYPES:
                continue
            if isinstance(element.get("pcb_component_id"), str):
                continue
            anchor = _element_anchor(element)
            if anchor is not None and _key(*anchor) == key:
                element_ids.add(index)

    if not element_ids:
        return {
            "ok": False,
            "reason": "nothing on the built board sits where this line says",
            "pcb_component_ids": set(),
            "element_ids": set(),
            "refdes": [],
            "kind": kind,
        }

    return {
        "ok": True,
        "reason": "",
        "pcb_component_ids": pcb_ids,
        "element_ids": element_ids,
        "refdes": sorted(refdes_by_source[s] for s in source_ids if s in refdes_by_source),
        "kind": kind or "loose",
    }


def _element_anchor(element: dict) -> tuple[float, float] | None:
    point = (
        element.get("anchor_position")
        if element.get("type") == "pcb_silkscreen_text"
        else element.get("center")
    )
    if isinstance(point, dict):
        x, y = point.get("x"), point.get("y")
    else:
        x, y = element.get("x"), element.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return (float(x), float(y))
    return None


# ---------------------------------------------------------------------------
# Applying a move
# ---------------------------------------------------------------------------


def _translate(element: dict, dx: float, dy: float) -> dict:
    """One element, moved. Copied first — the caller's list is shared with the
    on-disk artifact's parse and must not be mutated."""
    out = copy.deepcopy(element)
    for field in ("x", "y"):
        value = out.get(field)
        if isinstance(value, (int, float)):
            out[field] = float(value) + (dx if field == "x" else dy)
    for field in _POINT_FIELDS:
        point = out.get(field)
        if isinstance(point, dict):
            if isinstance(point.get("x"), (int, float)):
                point["x"] = float(point["x"]) + dx
            if isinstance(point.get("y"), (int, float)):
                point["y"] = float(point["y"]) + dy
    for field in _ROUTE_FIELDS:
        route = out.get(field)
        if isinstance(route, list):
            for point in route:
                if not isinstance(point, dict):
                    continue
                if isinstance(point.get("x"), (int, float)):
                    point["x"] = float(point["x"]) + dx
                if isinstance(point.get("y"), (int, float)):
                    point["y"] = float(point["y"]) + dy
    # `display_offset_*` shadows `center` on pcb_component/pcb_group. Left
    # behind, it makes the review render disagree with the geometry every check
    # just graded.
    for field, delta in (("display_offset_x", dx), ("display_offset_y", dy)):
        value = out.get(field)
        if isinstance(value, (int, float)):
            out[field] = float(value) + delta
    return out


def apply_moves(elements: Sequence[dict], moves: Sequence[Move]) -> tuple[list[dict], list[dict]]:
    """Predicted geometry, plus one report per move.

    Returns ``(elements, applied)``. A move that cannot be resolved is reported
    and *not* applied — grading geometry we could not locate would be a verdict
    about the wrong board.
    """
    out = list(elements)
    applied: list[dict] = []
    for move in moves:
        resolved = resolve_move(out, move)
        report = {
            "anchor": {"x": move.x, "y": move.y},
            "delta": {"dx": move.dx, "dy": move.dy},
            "ok": bool(resolved["ok"]),
            "reason": resolved["reason"],
            "kind": resolved["kind"],
            "refdes": resolved["refdes"],
            "components": len(resolved["pcb_component_ids"]),
            "elements": len(resolved["element_ids"]),
        }
        applied.append(report)
        if not resolved["ok"]:
            continue
        for index in resolved["element_ids"]:
            out[index] = _translate(out[index], move.dx, move.dy)
    return out, applied


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

#: What a fast answer may never claim, and why. Returned as data so the UI
#: prints our words rather than inventing reassuring ones. Every entry was
#: proven, not assumed: the pour line comes from moving a non-ground via 1mm
#: into solid GND copper (verified by point-in-polygon) and watching the Python
#: checks, the node checks, the 2.4s pour repair pass AND the 13.5s KiCad DRC
#: leg all report it clean (2026-08-16).
NOT_CHECKED: tuple[dict[str, str], ...] = (
    {
        "what": "the copper pour",
        "why": "the pour's cutouts were computed for the old position. Nothing "
               "in this pipeline measures anything against a pour — not this "
               "gate, not the node checks, not KiCad DRC — so a via that now "
               "sits in solid ground copper reads clean everywhere. Unchecked, "
               "never clear.",
    },
    {
        "what": "what the next build will route",
        "why": "five repair passes rewrite copper on every build (normalize, "
               "route, diff-pair, power-width, pour-clearance). A placement "
               "that is legal now can be made illegal by the widening pass.",
    },
    {
        "what": "the fab packet",
        "why": "the BOM gate and gerber truth have no input until the exporter "
               "has run. 'orderable' is not a word this answer may use.",
    },
    {
        "what": "KiCad's own ERC and DRC",
        "why": "the second substrate. The build converts the board and runs "
               "kicad-cli over it, and that leg is where clearance, shorting "
               "and unconnected findings come from — on hydrate-coaster it is "
               "**88% of everything the sidecar reports**. It costs ~13.5s and "
               "needs the converted board, so it cannot run between a mouse-up "
               "and the next frame. A clean answer here is a clean answer from "
               "one ruler out of two.",
    },
)


def fast_check(
    circuit_json_path: Path | str,
    *,
    project_root: Path | str | None = None,
    moves: Sequence[Move] = (),
    fab: str | None = None,
    node: bool = True,
) -> dict:
    """Grade a board — optionally with placement moves applied — in under a
    second. Never raises: every leg is a `checks.py` harvester, and a leg that
    fails reports `check_failed` rather than taking the answer down."""
    started = time.perf_counter()
    path = Path(circuit_json_path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve() if project_root else path.parent.parent

    try:
        elements = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"cannot read {path.name}: {type(exc).__name__}: {exc}",
            "warnings": [],
            "counts": {"error": 0, "warning": 0, "info": 0},
            "moves": [],
            "geometry": "unknown",
            "checked": [],
            "not_checked": [dict(entry) for entry in NOT_CHECKED],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    if not isinstance(elements, list):
        elements = []

    elements, applied = apply_moves(elements, list(moves))
    moved = any(report["ok"] for report in applied)

    # `load_product` raises `ProjectShapeError` on a project with no
    # `product.json`, and this function's contract is that it never raises: the
    # IDE asks for a verdict on whatever is open, and "the answer went away"
    # is the one response a gate may not give. A missing bible costs the one
    # leg that reads it (DFM: envelope, hole-to-copper, power width) and the
    # cost is reported as a finding rather than swallowed.
    preface: list[dict] = []
    try:
        product = spec_mod.load_product(root)
    except Exception as exc:  # noqa: BLE001
        product = None
        preface.append(checks.check_failed(
            f"DFM limits not checked: {type(exc).__name__}: {exc}"
        ))
    profile = fab_mod.get_profile(
        fab if fab is not None else (os.environ.get("CIRCUIT_FAB", "").strip() or "jlcpcb")
    )

    # Two legs read a file rather than the parsed list, so predicted geometry
    # has to be written down for them. It goes to a tempdir, never beside the
    # artifact: the catalog scanner and the artifact snapshotter both watch
    # `boards/`, and a scratch file there would fire an `artifact_changed`
    # event and make the workspace refetch a board that never existed.
    warnings: list[dict] = list(preface)
    with tempfile.TemporaryDirectory(prefix="circuit-fastcheck-") as scratch:
        node_input = path
        if moved:
            node_input = Path(scratch) / path.name
            node_input.write_text(json.dumps(elements), encoding="utf-8")

        def node_leg() -> list[dict]:
            if not node:
                return []
            return checks.run_tscircuit_checks(
                node_input,
                skip=SKIPPED_NODE_CHECKS,
                max_outline_points=MAX_OUTLINE_POINTS,
            )

        # Two legs, no shared state, so they overlap. The node leg is the long
        # pole even with the contiguity check dropped.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(node_leg)
            warnings.extend(checks.harvest_circuit_json(elements))
            warnings.extend(checks.schematic_truth_warnings(elements))
            warnings.extend(checks.iou_warnings(elements, profile))
            if product is not None:
                warnings.extend(checks.dfm_warnings(elements, product, profile))
            warnings.extend(checks.trace_anchor_warnings(elements))
            warnings.extend(checks.silk_overlap_warnings(elements))
            warnings.extend(
                verify_bridge.check_circuit_json(
                    node_input, profile=profile, assembly_order=True
                )
            )
            warnings.extend(pending.result())

    # Same finding, two legs, two severities — and without this the IDE
    # disagrees with the build about a board neither of them changed.
    #
    # `harvest_circuit_json` reads a `pcb_trace_too_long_warning` element and
    # grades it `warning` from its own suffix; `run_tscircuit_checks` reports
    # the identical finding from `@tscircuit/checks` and grades everything it
    # returns `error`. `generation._scan` collects both too and ends with
    # `checks.dedupe`, whose first-occurrence-wins keeps the harvested copy —
    # which is why harness-puck's sidecar carries those two findings at
    # `warning` and `fab.ready: true`. This leg was counting both copies, so
    # the same artifact came back `blocked`, with two "errors" whose own kind
    # ends in `_warning`, on all three shipped boards (measured 2026-08-16
    # through `board_fast_check`: harness-puck 2, hydrate-coaster 1,
    # terminal-keyboard 1).
    #
    # Deduping here rather than re-grading anything: the ordering that decides
    # which copy survives is the build's, and one policy in two places is the
    # bug, not the fix.
    warnings = checks.dedupe(warnings)

    counts = {"error": 0, "warning": 0, "info": 0}
    for warning in warnings:
        severity = str(warning.get("severity") or "warning")
        if severity in counts:
            counts[severity] += 1

    return {
        "ok": True,
        # A verdict on geometry that has not been compiled is a prediction, and
        # the word for it is not the word for a build result.
        "geometry": "predicted" if moved else "as_built",
        "verdict": "clean" if counts["error"] == 0 else "blocked",
        "moves": applied,
        "warnings": warnings,
        "counts": counts,
        "checked": [
            "circuit.json element scan",
            "schematic truth",
            "footprint IoU bands",
            "DFM limits (hole-to-copper, power width, board envelope)"
            if product is not None else "DFM limits: not run (no product.json)",
            "trace anchors (a part that left its copper)",
            "silkscreen labels printed over each other",
            "verifylib: assembly, netclass, dc, review, thermal",
            f"@tscircuit/checks minus {', '.join(SKIPPED_NODE_CHECKS)}"
            if node else "@tscircuit/checks: not run",
        ],
        "not_checked": [dict(entry) for entry in NOT_CHECKED],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m circuitpy.fastcheck",
        description="Sub-second verdict on a board, with placement moves applied.",
    )
    parser.add_argument("project", type=Path, help="the project root")
    parser.add_argument("--board", default="boards/main.circuit.json",
                        help="circuit.json, relative to the project root")
    parser.add_argument("--move", action="append", default=[],
                        metavar="X,Y:DX,DY",
                        help="move the placement anchored at X,Y by DX,DY (repeatable)")
    parser.add_argument("--fab", default=None, help="fab profile id (default: env CIRCUIT_FAB, else jlcpcb)")
    parser.add_argument("--no-node", action="store_true",
                        help="skip the @tscircuit/checks leg")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = args.project.expanduser().resolve()
    result = fast_check(
        root / args.board,
        project_root=root,
        moves=[parse_move(text) for text in args.move],
        fab=args.fab,
        node=not args.no_node,
    )
    # One JSON line, last line wins — parents parse `stdout.splitlines()[-1]`.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
