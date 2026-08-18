"""Deterministic board verifiers — the donor's never-raise discipline.

Every harvester returns ``[{part, kind, detail, severity}]`` and **never
raises** — a verifier that itself errors is reported as ``check_failed``
(severity warning), so validation can never break generation. Severity is the
driver's ONLY gate (``error`` blocks, ``warning`` reviews, ``info`` advises);
the ``kind`` set is open. ``part`` is pin/net/refdes-localized wherever
possible — pin-level localization is what makes the repair loop converge.

Kind sources (contract §1):
  stage 1 — circuit.json element types verbatim (``*_error`` -> severity
            error, ``*_warning`` -> warning), except
            ``supplier_footprint_mismatch_warning`` which is owned by the
            IoU bander here so its severity follows the fab profile's bands;
  stage 2 — @tscircuit/checks finding types;
  stage 3 — ``erc_violation`` / ``drc_violation`` (kicad report parser),
            ``kicad_unavailable`` (info);
  stage 4 — ``dfm_*``, ``part_not_orderable``, ``extended_part``,
            ``part_drift``, ``board_exceeds_envelope``;
  anywhere — ``check_failed`` (a verifier itself raised).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Sequence

from circuitpy import toolchain
from circuitpy.fab import FabProfile
from circuitpy.spec import ResolvedProduct

Warning = dict  # {"part": str, "kind": str, "detail": str, "severity": str}

SEVERITIES = ("error", "warning", "info")

_REFDES_RE = re.compile(r"\b([A-Z]+\d+(?:\.\w+)?)\b")

IOU_FIELD = "footprint_copper_intersection_over_union"
_IOU_MESSAGE_RE = re.compile(r"copper IoU\s+([0-9.]+)")

_CHECKS_TIMEOUT_S = 120.0


def _warning(part: str, kind: str, detail: str, severity: str = "warning") -> Warning:
    return {"part": part, "kind": kind, "detail": detail, "severity": severity}


def check_failed(detail: str, part: str = "board") -> Warning:
    return _warning(part, "check_failed", detail)


def repair_declined(detail: str, part: str = "board") -> Warning:
    """A repair pass ran, looked, and chose to change nothing.

    Distinct from :func:`check_failed` on purpose. `check_failed` means a step
    the board was entitled to did not happen, so some part of it is unexamined
    — an absence, and a reason not to ship. This means the opposite: the step
    ran, saw the whole picture, and declined because acting was the more
    dangerous option. Nothing is unexamined; the condition is still measured by
    whichever gate owns it.

    Kept out of the pipeline's blocking and escalated sets, and reported at
    `info`, because the board is not worse for it. Measured 2026-08-18:
    weather-badge-12 and -13 both shipped `fab.ready` with zero errors while
    the app showed "a check could not finish" and marked it blocking — the one
    verdict contradicting itself across two surfaces.
    """
    return _warning(part, "repair_declined", detail, "info")


# ---------------------------------------------------------------------------
# Stage 1: circuit.json element scan.
# ---------------------------------------------------------------------------


def _component_names(circuit_json: Sequence[dict]) -> dict[str, str]:
    """Element-id -> human name (refdes) index for part localization."""
    names: dict[str, str] = {}
    try:
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            if element.get("type") == "source_component":
                sc_id = str(element.get("source_component_id") or "")
                name = str(element.get("name") or "")
                if sc_id and name:
                    names[sc_id] = name
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            if element.get("type") == "pcb_component":
                pc_id = str(element.get("pcb_component_id") or "")
                sc_id = str(element.get("source_component_id") or "")
                if pc_id and sc_id in names:
                    names[pc_id] = names[sc_id]
    except Exception:
        pass
    return names


def _f(value: Any) -> float | None:
    """A number, or None. Circuit JSON carries "1.5mm" strings beside floats."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).strip().removesuffix("mm"))
    except (TypeError, ValueError):
        return None


def _localize(element: dict, names: dict[str, str]) -> str:
    """Best-effort ``part`` for an element: explicit component-id fields
    first, then a refdes token from the message, else "board"."""
    for key in ("source_component_id", "pcb_component_id"):
        value = element.get(key)
        if isinstance(value, str) and value in names:
            return names[value]
    for key in ("pcb_component_ids",):
        value = element.get(key)
        if isinstance(value, list):
            resolved = [names[v] for v in value if isinstance(v, str) and v in names]
            if resolved:
                return ",".join(resolved)
    message = element.get("message")
    if isinstance(message, str):
        match = _REFDES_RE.search(message)
        if match:
            return match.group(1)
    return "board"


def harvest_circuit_json(circuit_json: Sequence[dict]) -> list[Warning]:
    """``*_error`` / ``*_warning`` elements verbatim as kinds. Skips
    ``supplier_footprint_mismatch_warning`` (see :func:`iou_warnings`)."""
    try:
        names = _component_names(circuit_json)
        warnings: list[Warning] = []
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            kind = str(element.get("type") or "")
            if kind == "supplier_footprint_mismatch_warning":
                continue
            if kind.endswith("_error"):
                severity = "error"
            elif kind.endswith("_warning"):
                severity = "warning"
            else:
                continue
            detail = element.get("message")
            if not isinstance(detail, str) or not detail.strip():
                detail = json.dumps(element, sort_keys=True)[:300]
            warnings.append(_warning(_localize(element, names), kind, detail, severity))
        return warnings
    except Exception as exc:
        return [check_failed(f"circuit.json scan raised {type(exc).__name__}: {exc}")]


#: Component ftypes drawn as a two-terminal actuator symbol. A wire drawn
#: between two pins of one of these reads as a permanent short across the
#: actuator — whatever the copper does.
_ACTUATOR_FTYPES = ("simple_push_button", "simple_switch")

_PORT_PAIR_RE = re.compile(r"^(schematic_port_\d+)-(schematic_port_\d+)$")


def schematic_truth_warnings(circuit_json: Sequence[dict]) -> list[Warning]:
    """A review artifact that misrepresents the design (ledger #29).

    The first human EE review read terminal-keyboard's schematic as "every
    key is shorted": the 4-pad pushbutton renders as a 2-pin symbol, and the
    block's redundant same-net tie (pins 1+2 both on the signal net) was drawn
    as a wire looping from one symbol terminal to the other. The copper was
    right; the artifact lied, and expert review time — the scarcest input this
    project has — went into debunking a phantom.

    The structural fix is on the component: declaring
    ``internallyConnectedPins`` folds same-terminal pins onto one symbol
    terminal and the loop is never drawn. This check is the smoke alarm that
    keeps that fix honest: it fires on any schematic wire whose two endpoints
    are pins of the *same* switch-symbol component. Scoped to actuator
    symbols on purpose — a same-net tie between two pins of a connector
    (USB-C's DP1/DP2) is real, intended copper and must keep drawing as a
    wire.
    """
    try:
        warnings: list[Warning] = []
        port_owner: dict[str, str] = {}
        port_source: dict[str, str] = {}
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            if element.get("type") == "schematic_port":
                pid = str(element.get("schematic_port_id") or "")
                if pid:
                    port_owner[pid] = str(element.get("schematic_component_id") or "")
                    port_source[pid] = str(element.get("source_port_id") or "")
        component_source: dict[str, str] = {}
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            if element.get("type") == "schematic_component":
                component_source[str(element.get("schematic_component_id") or "")] = str(
                    element.get("source_component_id") or ""
                )
        source_meta: dict[str, tuple[str, str]] = {}
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            if element.get("type") == "source_component":
                source_meta[str(element.get("source_component_id") or "")] = (
                    str(element.get("name") or "?"),
                    str(element.get("ftype") or ""),
                )
        flagged: set[str] = set()
        for element in circuit_json:
            if not isinstance(element, dict) or element.get("type") != "schematic_trace":
                continue
            match = _PORT_PAIR_RE.match(str(element.get("source_trace_id") or ""))
            if match is None:
                continue
            owner_a = port_owner.get(match.group(1))
            owner_b = port_owner.get(match.group(2))
            if not owner_a or owner_a != owner_b or owner_a in flagged:
                continue
            source_id = component_source.get(owner_a, "")
            name, ftype = source_meta.get(source_id, ("?", ""))
            if ftype not in _ACTUATOR_FTYPES:
                continue
            flagged.add(owner_a)
            warnings.append(
                _warning(
                    name,
                    "schematic_symbol_short",
                    f"the schematic draws a wire between two pins of {name} — "
                    "on paper that is a permanent short across the switch, and "
                    "a reviewer has to read it as a dead button. If the pins "
                    "are one internal terminal, declare the pairing with "
                    "internallyConnectedPins so it folds into the symbol "
                    "instead of drawing as a wire; if they are not, the design "
                    "itself shorts the switch",
                )
            )
        return warnings
    except Exception as exc:
        return [check_failed(f"schematic truth scan raised {type(exc).__name__}: {exc}")]


def iou_warnings(
    circuit_json: Sequence[dict], profile: FabProfile
) -> list[Warning]:
    """``supplier_footprint_mismatch_warning`` banded by copper IoU:
    < {error_below} error / < {warning_below} warning / < {info_below} info /
    else dropped (correct 0402 parts score ~0.73-0.77).

    **Only orthogonally-placed parts are graded** (measured 2026-08-11).
    Harness-puck flagged eight 0402 capacitors at 0.4739-0.4916, under the 0.5
    error floor, while thirteen more — same part number C1525, same
    `footprint="0402"` — scored 0.7249. The only difference was rotation: the
    eight sit tangentially around an LED ring at multiples of 22.5 degrees. A
    dedicated bench settles what the boards could only suggest; one part, six
    rotations:

        rot   0   90  180  270  |  45      22.5
        IoU  0.7249 (all four)  |  0.4215  0.4739

    So the metric survives a quarter turn and collapses off-axis: the supplier
    footprint is compared axis-by-axis, not turned with the part. Below 0.5 at
    45 degrees says nothing about the land pattern, and blocking on it would
    make any circular layout, angled connector or diagonal-edge part
    permanently un-orderable. Off-axis parts therefore keep their measurement
    and are reported at `info` with the reason attached rather than dropped —
    the north star's "a check that might be wrong is still worth running,
    provided its output is labelled honestly". Orthogonal parts are still
    graded in full, which is nearly every part on nearly every board.
    """
    try:
        names = _component_names(circuit_json)
        rotations: dict[str, float] = {}
        for element in circuit_json:
            if isinstance(element, dict) and element.get("type") == "pcb_component":
                angle = element.get("rotation")
                if isinstance(angle, (int, float)):
                    for key in ("source_component_id", "pcb_component_id"):
                        cid = element.get(key)
                        if isinstance(cid, str):
                            rotations[cid] = float(angle)
        warnings: list[Warning] = []
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            if element.get("type") != "supplier_footprint_mismatch_warning":
                continue
            iou = element.get(IOU_FIELD)
            if not isinstance(iou, (int, float)):
                message = element.get("message")
                match = _IOU_MESSAGE_RE.search(message) if isinstance(message, str) else None
                iou = float(match.group(1)) if match else None
            if iou is None:
                severity = "warning"  # unknown IoU: keep the element's own severity
            elif iou < profile.iou_error_below:
                severity = "error"
            elif iou < profile.iou_warning_below:
                severity = "warning"
            elif iou < profile.iou_info_below:
                severity = "info"
            else:
                continue
            detail = element.get("message")
            if not isinstance(detail, str) or not detail.strip():
                detail = f"supplier footprint IoU {iou}"
            angle = next(
                (rotations[cid] for cid in
                 (element.get("source_component_id"), element.get("pcb_component_id"))
                 if isinstance(cid, str) and cid in rotations),
                0.0,
            )
            if abs(((angle + 45.0) % 90.0) - 45.0) > 0.5 and severity != "info":
                severity = "info"
                detail += (
                    f" — reported, not graded: the part sits at {angle:g} "
                    f"degrees, off the orthogonal grid, and this IoU is "
                    f"measured axis-by-axis against the supplier footprint. "
                    f"Measured on one 0402 at six angles: 0.7249 at 0/90/180/"
                    f"270, 0.4739 at 22.5, 0.4215 at 45. Below 0.5 here is the "
                    f"angle, not the land pattern."
                )
            warnings.append(
                _warning(
                    _localize(element, names),
                    "supplier_footprint_mismatch_warning",
                    detail,
                    severity,
                )
            )
        return warnings
    except Exception as exc:
        return [check_failed(f"IoU band scan raised {type(exc).__name__}: {exc}")]


# ---------------------------------------------------------------------------
# Stage 2: @tscircuit/checks — an independent codepath over the same JSON.
# ---------------------------------------------------------------------------


def run_tscircuit_checks(
    circuit_json_path: Path,
    *,
    skip: Sequence[str] = (),
    max_outline_points: int = 0,
) -> list[Warning]:
    """``runAllChecks`` via the packaged node helper. Findings become
    warnings with kind = the finding's type (severity error — the library
    only reports genuine DRC failures). Never raises.

    ``skip`` drops named routing checks; only the IDE's sub-second edit gate
    passes it, and only for ``checkTracesAreContiguous`` (1,491ms of 2,319ms
    on terminal-keyboard). A build never skips — see the helper's own comment.
    """
    try:
        args = [toolchain.helper_js("run_all_checks.cjs")]
        if skip:
            args.append(f"--skip={','.join(skip)}")
        if max_outline_points:
            args.append(f"--max-outline-points={int(max_outline_points)}")
        args.append(str(circuit_json_path))
        output = toolchain.run_node(args, timeout=_CHECKS_TIMEOUT_S)
        findings = json.loads(output.strip().splitlines()[-1])
        if not isinstance(findings, list):
            return [check_failed("runAllChecks returned a non-list")]
        warnings: list[Warning] = []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            kind = str(
                finding.get("type") or finding.get("error_type") or "tscircuit_check"
            )
            detail = finding.get("message")
            if not isinstance(detail, str) or not detail.strip():
                detail = json.dumps(finding, sort_keys=True)[:300]
            warnings.append(_warning(_localize(finding, {}), kind, detail, "error"))
        return warnings
    except Exception as exc:
        return [check_failed(f"@tscircuit/checks run failed: {exc}")]


# ---------------------------------------------------------------------------
# Stage 3: kicad-cli report parsing.
# ---------------------------------------------------------------------------


def kicad_unavailable_warning() -> Warning:
    return _warning(
        "board",
        "kicad_unavailable",
        "kicad-cli not installed — ERC/DRC second-substrate checks skipped and "
        "gerbers cannot be independently verified (brew install --cask kicad)",
        "info",
    )


#: Finding types measured (2026-08-10) as artifacts of the tscircuit->KiCad
#: conversion rather than defects in the board, and the floor they are pinned
#: to. Method: export a *correct* board, run kicad-cli, and count what fires.
#:
#: ERC came back with 152 findings on a clean skeleton board, and **every type
#: it reported was an artifact** — the schematic converter does not produce
#: KiCad-recognised connectivity, so `pin_not_connected` lands on pins that are
#: demonstrably wired, wires read as dangling, and symbols sit off KiCad's
#: connection grid. The whole ERC leg is therefore advisory: it measures the
#: converter, not the design. It stays wired up because the converter is
#: upstream and improving, and the day it emits real nets this becomes a free
#: second opinion — flip these back to their reported severity then.
#:
#: DRC is real signal once the fab's design rules are supplied (see
#: fab.kicad_project_json): 207 findings -> 50, and the survivors match our own
#: stage-4 gate. Only the library/cosmetic types below stay pinned.
KICAD_NOISE_FLOOR: dict[str, str] = {
    # ERC — converter artifacts, all of them.
    "pin_not_connected": "info",
    "wire_dangling": "info",
    "label_dangling": "info",
    "endpoint_off_grid": "info",
    "unconnected_wire_endpoint": "info",
    "isolated_pin_label": "info",
    "lib_symbol_issues": "info",
    "lib_symbol_mismatch": "info",
    "simulation_model_issue": "info",
    # DRC — library/cosmetic noise from converted footprints.
    "lib_footprint_issues": "info",
    "lib_footprint_mismatch": "info",
    "text_height": "info",
    "text_thickness": "info",
    "silk_over_copper": "info",
    "silk_overlap": "info",
}

#: Everything from the ERC leg is advisory today; see KICAD_NOISE_FLOOR.
_ADVISORY_KINDS = {"erc_violation"}


def _kicad_severity(reported: str, type_tag: str, kind: str) -> str:
    """Reported severity, lowered to the measured noise floor where the finding
    describes the conversion rather than the board."""
    if kind in _ADVISORY_KINDS:
        return "info"
    floor = KICAD_NOISE_FLOOR.get(type_tag)
    if floor is not None:
        return floor
    return reported


#: A KiCad rule that fires this many times is one systemic pattern, not that
#: many separate defects. Below it, each violation keeps its own line and its
#: own location — a rule that fires twice really is two problems.
KICAD_COLLAPSE_AT = 4

#: How many refdes to name inside a collapsed line before saying "and N more".
KICAD_COLLAPSE_SAMPLE = 4


def _collapse_kicad_repeats(warnings: list[Warning]) -> list[Warning]:
    """One line per KiCad rule that floods, keeping the count and an example.

    **Why this is not filtering.** Measured on one real board: 521 warnings, of
    which 204 ERC + 272 DRC were the same handful of converter and library
    rules repeating, leaving about eight findings anyone could act on. An agent
    that must read 521 lines to find 8 is an agent that misses things, and
    adding a new check to that pile buys nothing.

    Nothing is dropped and nothing is hidden: every rule still appears, with
    how many times it fired and one worked example, which is exactly what the
    house style asks for — the measurement goes in the detail. The per-instance
    coordinates stay in the kicad report the pipeline already writes.

    Grouping is by ``(kind, severity, [type_tag])``. Severity is part of the
    key because collapsing an error into a group of infos would hide it, and
    the type tag is KiCad's own name for "the same rule fired again".
    """
    groups: dict[tuple[str, str, str], list[Warning]] = {}
    order: list[tuple[str, str, str]] = []
    for warning in warnings:
        detail = str(warning.get("detail") or "")
        tag = detail[1:detail.index("]")] if detail.startswith("[") and "]" in detail else ""
        key = (str(warning.get("kind")), str(warning.get("severity")), tag)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(warning)

    out: list[Warning] = []
    for key in order:
        members = groups[key]
        kind, severity, tag = key
        if not tag or len(members) < KICAD_COLLAPSE_AT:
            out.extend(members)
            continue
        parts = [str(w.get("part") or "board") for w in members]
        named = [p for p in dict.fromkeys(parts) if p != "board"]
        where = ", ".join(named[:KICAD_COLLAPSE_SAMPLE])
        if len(named) > KICAD_COLLAPSE_SAMPLE:
            where += f" and {len(named) - KICAD_COLLAPSE_SAMPLE} more"
        example = str(members[0].get("detail") or "")
        example = example[example.index("]") + 1:].strip() if "]" in example else example
        out.append(
            _warning(
                named[0] if named else "board",
                kind,
                f"[{tag}] x{len(members)} — {example}"
                + (f" (on {where})" if where else "")
                + ". Same rule every time; the location above is one instance, "
                "the rest are in the kicad report",
                severity,
            )
        )
    return out


#: How many of a violation's objects to name. KiCad reports clearance,
#: shorting and courtyard rules as a *pair*; anything past two is a pile-up and
#: the first two are enough to find it.
KICAD_ITEMS_NAMED = 2


def _violation_sides(items: object) -> str:
    """``A @ (x, y) <-> B @ (x, y)`` for the objects a violation is between.

    A clearance is a distance between **two** things. Naming one of them and
    dropping the other is not a location — it is "this via is too close to
    something", which no agent can act on and no human can find.

    Measured on weather-badge-12, 2026-08-18: the board was blocked by
    ``0.0674mm actual against 0.0900mm`` and the verdict named only
    ``Via [V3_3] on F.Cu - B.Cu``. The other side was ``Pad 2 [GND] of C40`` —
    the decoupling capacitor of the first WS2812 pixel, and the thing that
    actually had to move. Nine build attempts went past before that was known,
    because it was only ever in the raw kicad report nobody reads.

    101 of the 362 violations on weather-badge-11's real DRC report carry two
    or more items, and every item already carries a ``pos``. This throws none
    of it away.
    """
    if not isinstance(items, list):
        return ""
    sides: list[str] = []
    for item in items[:KICAD_ITEMS_NAMED]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("description") or "").strip()
        pos = item.get("pos")
        if isinstance(pos, dict):
            x, y = pos.get("x"), pos.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                at = f"({x:.3f}, {y:.3f})"
                text = f"{text} @ {at}" if text else at
        if text:
            sides.append(text)
    return " <-> ".join(sides)


def parse_kicad_report(report: object, *, kind: str) -> list[Warning]:
    """A kicad-cli ``--format json`` ERC/DRC report -> warnings with the given
    kind (``erc_violation`` / ``drc_violation``). Accepts a dict, JSON text,
    or a path. Never raises."""
    try:
        if isinstance(report, (str, Path)) and Path(str(report)).is_file():
            report = json.loads(Path(str(report)).read_text(encoding="utf-8"))
        elif isinstance(report, str):
            report = json.loads(report)
        if not isinstance(report, dict):
            return [check_failed(f"kicad report is not an object ({kind})")]
        violations: list[dict] = []
        for key in ("violations", "unconnected_items", "schematic_parity"):
            value = report.get(key)
            if isinstance(value, list):
                violations.extend(v for v in value if isinstance(v, dict))
        for sheet in report.get("sheets") or []:
            if isinstance(sheet, dict):
                value = sheet.get("violations")
                if isinstance(value, list):
                    violations.extend(v for v in value if isinstance(v, dict))
        warnings: list[Warning] = []
        for violation in violations:
            severity_raw = str(violation.get("severity") or "error").lower()
            severity = severity_raw if severity_raw in SEVERITIES else "info"
            description = str(violation.get("description") or violation.get("type") or "")
            part = "board"
            items = violation.get("items")
            if isinstance(items, list) and items:
                first = items[0]
                if isinstance(first, dict):
                    item_desc = str(first.get("description") or "")
                    match = _REFDES_RE.search(item_desc)
                    part = match.group(1) if match else (item_desc[:60] or "board")
            type_tag = str(violation.get("type") or "")
            severity = _kicad_severity(severity, type_tag, kind)
            detail = f"[{type_tag}] {description}".strip() if type_tag else description
            sides = _violation_sides(items)
            if sides:
                detail = f"{detail or 'violation'} — between {sides}"
            warnings.append(_warning(part, kind, detail or "violation", severity))
        return _collapse_kicad_repeats(warnings)
    except Exception as exc:
        return [check_failed(f"kicad report parse failed ({kind}): {exc}")]


# ---------------------------------------------------------------------------
# Stage 4: DFM gate + envelope, over circuit.json geometry.
# ---------------------------------------------------------------------------


def _point_segment_distance(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(
        0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq)
    )
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _segments_cross(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
) -> bool:
    def side(x1, y1, x2, y2, x3, y3) -> float:
        return (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)

    d1 = side(cx, cy, dx, dy, ax, ay)
    d2 = side(cx, cy, dx, dy, bx, by)
    d3 = side(ax, ay, bx, by, cx, cy)
    d4 = side(ax, ay, bx, by, dx, dy)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _segment_gap(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
) -> float:
    """Shortest distance between two 2-D segments. Exact, and zero if they
    cross — the hole spine and a track can genuinely overlap."""
    if _segments_cross(ax, ay, bx, by, cx, cy, dx, dy):
        return 0.0
    return min(
        _point_segment_distance(ax, ay, cx, cy, dx, dy),
        _point_segment_distance(bx, by, cx, cy, dx, dy),
        _point_segment_distance(cx, cy, ax, ay, bx, by),
        _point_segment_distance(dx, dy, ax, ay, bx, by),
    )


def _rotate(px: float, py: float, cx: float, cy: float, degrees: float
            ) -> tuple[float, float]:
    """``(px, py)`` turned ``degrees`` counter-clockwise about ``(cx, cy)``."""
    if not degrees:
        return px, py
    radians = math.radians(degrees)
    cos_r, sin_r = math.cos(radians), math.sin(radians)
    dx, dy = px - cx, py - cy
    return cx + dx * cos_r - dy * sin_r, cy + dx * sin_r + dy * cos_r


def _stadium(x: float, y: float, width: float, height: float,
             rotation: float = 0.0
             ) -> tuple[float, float, float, float, float]:
    """A drill or its pad as ``(ax, ay, bx, by, radius)`` — the segment the
    hole's centre sweeps, plus a radius. A round hole is the degenerate case
    where the segment is a point.

    Modelling a slot as a circle (what this check used to do) understates the
    long axis by half the slot's length: the USB-C shell drills are 0.8 x
    1.6mm pills, so copper 0.3mm off the end of one measured as 0.7mm clear.
    That was a false *negative*, and it is the reason this is a segment.

    ``rotation`` is the shape's own ``ccw_rotation`` in degrees. Ignoring it
    does not blur a shape, it **moves** one: a 2.25 x 0.63mm SOIC pad at 90
    degrees is copper 2.25mm tall, and the unrotated model puts that copper
    2.25mm wide instead — in the wrong place by more than a millimetre, in
    both directions at once. Defaults to zero so the existing four-argument
    call is unchanged.
    """
    radius = min(width, height) / 2.0
    if height >= width:
        half = (height - width) / 2.0
        ax, ay, bx, by = x, y - half, x, y + half
    else:
        half = (width - height) / 2.0
        ax, ay, bx, by = x - half, y, x + half, y
    ax, ay = _rotate(ax, ay, x, y, rotation)
    bx, by = _rotate(bx, by, x, y, rotation)
    return (ax, ay, bx, by, radius)


#: Copper as this check measures it: either a capsule — ``("capsule", ax, ay,
#: bx, by, radius)``, a segment swept by a disc, which is exactly a trace, a
#: via, a round pad or a pill — or ``("polygon", points)`` for a shape whose
#: corners are real. Two representations rather than one because a rectangle
#: is not a stadium and a stadium is not a polygon, and this check was wrong
#: about both.
Copper = tuple


def _rect_outline(x: float, y: float, width: float, height: float,
                  rotation: float = 0.0) -> list[tuple[float, float]]:
    """A rectangular pad as its four real corners, turned in place."""
    half_w, half_h = width / 2.0, height / 2.0
    return [
        _rotate(x + sx * half_w, y + sy * half_h, x, y, rotation)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]


def _point_in_polygon(px: float, py: float,
                      points: Sequence[tuple[float, float]]) -> bool:
    inside = False
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if (y1 > py) != (y2 > py):
            cross = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px < cross:
                inside = not inside
    return inside


def _segment_polygon_gap(ax: float, ay: float, bx: float, by: float,
                         points: Sequence[tuple[float, float]]) -> float:
    """Shortest distance from a segment to a polygon's outline, and 0.0 when
    the segment is inside it."""
    if _point_in_polygon(ax, ay, points) or _point_in_polygon(bx, by, points):
        return 0.0
    count = len(points)
    return min(
        _segment_gap(ax, ay, bx, by, points[i][0], points[i][1],
                     points[(i + 1) % count][0], points[(i + 1) % count][1])
        for i in range(count)
    )


def _copper_gap(shape: Copper, ax: float, ay: float, bx: float, by: float
                ) -> float:
    """Distance from the hole's spine to this copper's edge. The drill radius
    is the caller's to subtract — it belongs to the hole, not the copper."""
    if shape[0] == "capsule":
        _, cx, cy, dx, dy, radius = shape
        return _segment_gap(cx, cy, dx, dy, ax, ay, bx, by) - radius
    return _segment_polygon_gap(ax, ay, bx, by, shape[1])


def _copper_box(shape: Copper) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` — the axis-aligned box this copper occupies."""
    if shape[0] == "capsule":
        _, ax, ay, bx, by, radius = shape
        return (min(ax, bx) - radius, min(ay, by) - radius,
                max(ax, bx) + radius, max(ay, by) + radius)
    points = shape[1]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _copper_bound(shape: Copper) -> tuple[float, float, float]:
    """``(cx, cy, radius)`` — a disc that contains this copper, for rejecting
    the pairs that cannot possibly be close before measuring them exactly."""
    if shape[0] == "capsule":
        _, cx, cy, dx, dy, radius = shape
        return ((cx + dx) / 2.0, (cy + dy) / 2.0,
                math.hypot(dx - cx, dy - cy) / 2.0 + radius)
    points = shape[1]
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return (cx, cy, max(math.hypot(p[0] - cx, p[1] - cy) for p in points))


def _pad_copper(element: dict) -> Copper | None:
    """An SMD pad as the copper it actually is.

    Three shapes were being read wrong, and every one of them the same way —
    the check could not see the geometry, so it invented one:

    - **A rotated pad was measured unrotated.** ``rotated_pill`` and
      ``rotated_rect`` carry ``ccw_rotation``; the model dropped it and swung
      the copper back onto the x-axis.
    - **A round pad was measured at half size.** ``circle`` pads carry
      ``radius`` and no ``width``; ``width or radius`` then fed a *radius*
      into a *width*, so a 1.0mm test-point pad was modelled as 0.5mm.
    - **A polygon pad was not measured at all.** It carries ``points`` and no
      width or height, so it failed the numeric guard and was dropped —
      including the four shell pads of every USB-C receptacle we ship, which
      sit beside the very drills this check exists for.

    The first invents findings and hides them; the other two only hide them.
    """
    shape = element.get("shape")
    x, y = element.get("x"), element.get("y")
    points = element.get("points")
    if isinstance(points, list) and len(points) >= 3:
        outline = [
            (float(p["x"]), float(p["y"])) for p in points
            if isinstance(p, dict)
            and isinstance(p.get("x"), (int, float))
            and isinstance(p.get("y"), (int, float))
        ]
        if len(outline) >= 3:
            return ("polygon", outline)
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    radius = element.get("radius")
    if shape == "circle" and isinstance(radius, (int, float)):
        return ("capsule", float(x), float(y), float(x), float(y), float(radius))
    width = element.get("width") or radius
    height = element.get("height") or radius or width
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return None
    rotation = element.get("ccw_rotation")
    rotation = float(rotation) if isinstance(rotation, (int, float)) else 0.0
    if shape in ("pill", "rotated_pill", "oval"):
        return ("capsule", *_stadium(float(x), float(y), float(width),
                                     float(height), rotation))
    return ("polygon", _rect_outline(float(x), float(y), float(width),
                                     float(height), rotation))


def _connectivity_index(circuit_json: Sequence[dict]) -> tuple[dict, dict]:
    """``(pcb_port_id -> net key, source_net_id -> net key)``.

    The net key is tscircuit's ``subcircuit_connectivity_map_key``: two things
    carrying the same key are the same electrical net.
    """
    source_port_net: dict[str, str] = {}
    source_net_net: dict[str, str] = {}
    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        key = element.get("subcircuit_connectivity_map_key")
        if not isinstance(key, str) or not key:
            continue
        if element.get("type") == "source_port":
            sid = element.get("source_port_id")
            if isinstance(sid, str):
                source_port_net[sid] = key
        elif element.get("type") == "source_net":
            nid = element.get("source_net_id")
            if isinstance(nid, str):
                source_net_net[nid] = key
    port_net: dict[str, str] = {}
    for element in circuit_json:
        if not isinstance(element, dict) or element.get("type") != "pcb_port":
            continue
        key = source_port_net.get(str(element.get("source_port_id") or ""))
        pid = element.get("pcb_port_id")
        if key and isinstance(pid, str):
            port_net[pid] = key
    return port_net, source_net_net


def _hole_to_copper_warnings(
    circuit_json: Sequence[dict], names: dict, profile: FabProfile
) -> list[Warning]:
    """Copper too near a drill.

    Two rules, not one (jlcpcb.com/capabilities, read 2026-08-11): a
    non-plated mounting hole needs **0.20mm** to a track, a plated hole needs
    **0.28mm** (0.35mm recommended). This is the check that caught the defect
    blocking every example board — the router threads a ground track through
    the 0.525mm channel beside a USB-C connector's alignment holes because it
    is the shortest path, leaving 0.115mm where 0.20mm is required. A drill
    lands within its own positional tolerance of that, so the track can simply
    be cut: some boards in a batch work and some do not.

    **The rule does not apply to the hole's own net (settled 2026-08-11).**
    The same capabilities page that publishes "PTH to Track 0.28mm" also
    publishes "PTH annular ring >= 0.20mm" — copper the fab *requires* at
    0.20mm from the drill. Both can be true only if the hole-to-track figure
    measures copper approaching from outside, not the pad the barrel is
    plated into. The measurement settles it: every legitimate connection to a
    plated hole leaves its pad, and at the moment it crosses the pad boundary
    its edge is exactly one annular ring — 0.200mm — from the drill. Fifteen
    such segments across the three example boards measured 0.2000mm each, to
    four decimals, because that is not geometry, it is the pad boundary. A
    net-blind 0.28mm rule therefore makes every plated hole unconnectable,
    and KiCad's own hole-clearance DRC (enabled here at 0.2mm, and firing on
    NPTH holes) agrees: it reports nothing at these pads.

    So same-net copper is exempt, and copper inside a plated hole's own pad
    outline is exempt when no net is known either side. Everything else is
    measured — from the drill's real shape, a slot as a slot, **and from the
    copper's real shape**: a pad is turned by its own ``ccw_rotation``, a
    rectangle keeps its corners, a polygon is its own outline (2026-08-16,
    ledger #41 — the unrotated inscribed stadium was the last blocking finding
    on terminal-keyboard, and it was measuring a pad that is not there).
    """
    warnings: list[Warning] = []
    port_net, net_by_source_net = _connectivity_index(circuit_json)

    holes: list[dict] = []
    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        etype = element.get("type")
        if etype not in ("pcb_hole", "pcb_plated_hole", "pcb_via"):
            continue
        x, y = element.get("x"), element.get("y")
        hole_w = element.get("hole_width") or element.get("hole_diameter")
        hole_h = element.get("hole_height") or element.get("hole_diameter") or hole_w
        if not all(isinstance(v, (int, float)) for v in (x, y, hole_w, hole_h)):
            continue
        plated = etype in ("pcb_plated_hole", "pcb_via")
        annular = 0.0
        if plated:
            pad_w = element.get("outer_width") or element.get("outer_diameter")
            pad_h = (
                element.get("outer_height")
                or element.get("outer_diameter")
                or pad_w
            )
            if isinstance(pad_w, (int, float)) and isinstance(pad_h, (int, float)):
                annular = max(
                    0.0,
                    min((float(pad_w) - float(hole_w)) / 2.0,
                        (float(pad_h) - float(hole_h)) / 2.0),
                )
        is_via = etype == "pcb_via"
        drill_rotation = element.get("ccw_rotation")
        holes.append({
            "spine": _stadium(
                float(x), float(y), float(hole_w), float(hole_h),
                float(drill_rotation)
                if isinstance(drill_rotation, (int, float)) else 0.0,
            ),
            "x": float(x), "y": float(y),
            "plated": plated,
            "via": is_via,
            "annular": annular,
            "net": (
                element.get("subcircuit_connectivity_map_key") if is_via
                else port_net.get(str(element.get("pcb_port_id") or ""))
            ),
            "id": str(element.get("pcb_plated_hole_id")
                      or element.get("pcb_hole_id")
                      or element.get("pcb_via_id") or ""),
        })
    if not holes:
        return warnings

    #: (element, element id, net key, what, geometry, bounding disc) for every
    #: piece of copper the router places or a footprint lands. Traces and vias
    #: are the router's; SMD pads are the footprint's, and a pad beside a
    #: mounting hole is the same defect from the other direction.
    items: list[tuple[dict, str, str | None, str, Copper,
                      tuple[float, float, float]]] = []

    def _add(element: dict, item_id: str, net: str | None, what: str,
             shape: Copper) -> None:
        items.append((element, item_id, net, what, shape, _copper_bound(shape)))

    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        etype = element.get("type")
        if etype == "pcb_trace":
            net = net_by_source_net.get(str(element.get("connection_name") or ""))
            if net is None:
                for pid in element.get("connectsTo") or []:
                    net = port_net.get(str(pid))
                    if net:
                        break
            route = [
                p for p in (element.get("route") or [])
                if isinstance(p, dict) and isinstance(p.get("x"), (int, float))
                and isinstance(p.get("y"), (int, float))
            ]
            tid = str(element.get("pcb_trace_id") or "")
            for first, second in zip(route, route[1:]):
                _add(element, tid, net, "a track", (
                    "capsule",
                    float(first["x"]), float(first["y"]),
                    float(second["x"]), float(second["y"]),
                    float(first.get("width") or 0.2) / 2.0,
                ))
        elif etype == "pcb_via":
            x, y = element.get("x"), element.get("y")
            outer = element.get("outer_diameter")
            if not all(isinstance(v, (int, float)) for v in (x, y, outer)):
                continue
            _add(
                element, str(element.get("pcb_via_id") or ""),
                element.get("subcircuit_connectivity_map_key"), "a via",
                ("capsule", float(x), float(y), float(x), float(y),
                 float(outer) / 2.0),
            )
        elif etype == "pcb_smtpad":
            # A pad is the shape the fab plots: a rectangle keeps its corners,
            # a pill is a stadium, a polygon is its own outline, and every one
            # of them is turned by its own `ccw_rotation`. The inscribed
            # stadium this used to model is a *different shape* for anything
            # but a pill, and off-axis it is a different shape in a different
            # place — see `_pad_copper`.
            shape = _pad_copper(element)
            if shape is None:
                continue
            _add(
                element, str(element.get("pcb_smtpad_id") or ""),
                port_net.get(str(element.get("pcb_port_id") or "")), "a pad",
                shape,
            )

    # Worst gap per (hole, copper item), so a trace is judged on its closest
    # segment rather than on whichever segment the loop reached first.
    worst: dict[tuple[str, str], tuple[float, dict, dict, str]] = {}
    for hole in holes:
        ax, ay, bx, by, drill_r = hole["spine"]
        plated = hole["plated"]
        limit = _hole_floor(hole, profile)
        if plated and not hole["via"]:
            limit = max(limit, profile.warn_pth_to_copper_mm)
        # The hole's own bounding disc, so the exact measurement below only
        # runs on the pairs that could possibly be within `limit`.
        hole_cx, hole_cy = (ax + bx) / 2.0, (ay + by) / 2.0
        hole_reach = math.hypot(bx - ax, by - ay) / 2.0 + drill_r + limit
        for element, item_id, net, what, geom, bound in items:
            if item_id == hole["id"]:
                continue    # a via measured against its own drill
            item_cx, item_cy, item_r = bound
            if math.hypot(item_cx - hole_cx, item_cy - hole_cy) > \
                    hole_reach + item_r:
                continue
            gap = _copper_gap(geom, ax, ay, bx, by) - drill_r
            if gap >= limit - 1e-9:
                continue
            # The hole's own net. Its pad is copper the fab *requires* at one
            # annular ring from this drill, and same-net copper merges with
            # that pad — it adds nothing the ring did not already put there,
            # and a drill that wanders into it can neither short nor open a
            # net that is already this one.
            if plated and hole["net"] and net and hole["net"] == net:
                continue
            # No net known either side: fall back to geometry. Inside the
            # pad's own outline is the annular ring, which is not a
            # hole-to-track distance.
            if plated and (hole["net"] is None or net is None) \
                    and gap <= hole["annular"] + 1e-9:
                continue
            key = (hole["id"], item_id)
            if key not in worst or gap < worst[key][0]:
                worst[key] = (gap, hole, element, what)

    for gap, hole, element, what in worst.values():
        plated = hole["plated"]
        floor = _hole_floor(hole, profile)
        kind = (
            "via" if hole["via"]
            else "plated hole" if plated
            else "mounting hole"
        )
        if gap < floor - 1e-9:
            warnings.append(_warning(
                _localize(element, names),
                "dfm_hole_clearance",
                f"{what} passes {gap:.3f}mm from a {kind} at "
                f"({hole['x']:.2f}, {hole['y']:.2f}); the fab needs "
                f"{floor:g}mm — route around it, the drill's own "
                "tolerance can cut a track this close",
                "error",
            ))
        elif plated:
            warnings.append(_warning(
                _localize(element, names),
                "dfm_hole_clearance",
                f"{what} passes {gap:.3f}mm from a plated hole at "
                f"({hole['x']:.2f}, {hole['y']:.2f}) — legal, but "
                f"{profile.warn_pth_to_copper_mm:g}mm is the "
                "recommended margin",
                "warning",
            ))
    return warnings


#: Net names that mark a rail even when the compiler's ``is_power`` flag is
#: absent (a net brought in purely by name). Mirrors verifylib's rail table.
_POWER_NET_RE = re.compile(
    r"^(v?bus|v_?bus|vusb|\+?5v|v_?5v?|\+?3v?3|v_?3_?3|3_?3v|\+?1v?8|v_?1_?8"
    r"|vcc|vdd|vin|vsys|vbat)$",
    re.I,
)
_GROUND_NET_RE = re.compile(r"^(gnd|agnd|dgnd|vss|ground)$", re.I)


#: How far from a route's own terminal a point is still "the taper".
#:
#: The router steps the width down as it approaches a pad it cannot match, over
#: a short run — 0.6mm covers the 0402 land patterns and the QFN pads on every
#: board we ship, and is short enough that a genuinely thin *rail* is still
#: measured. A window rather than "drop the first and last point", because the
#: taper is a ladder of several points (`TRACE_WIDTH_SCHEDULE`), not one.
TAPER_WINDOW_MM = 0.6


def _within_taper(route: Sequence[dict], index: int) -> bool:
    """Is this route point inside the taper at either end of its trace?"""
    try:
        point = route[index]
        px, py = float(point.get("x")), float(point.get("y"))
    except (TypeError, ValueError, IndexError):
        return False
    for end in (0, len(route) - 1):
        try:
            ex, ey = float(route[end].get("x")), float(route[end].get("y"))
        except (TypeError, ValueError):
            continue
        if math.hypot(px - ex, py - ey) <= TAPER_WINDOW_MM:
            return True
    return False


def power_width_warnings(
    circuit_json: Sequence[dict], profile: FabProfile
) -> list[Warning]:
    """Ledger #31: a power net routed at signal width should say so.

    The EE review found 5V and 3V3 at 0.2mm — fine for a keyboard's current,
    wrong as a habit, and invisible: the current-driven check
    (``netclass_trace_width``) is satisfied whenever the maths squeaks by, so
    nothing distinguished a power net from a button signal. This holds power
    and ground nets to ``profile.warn_power_trace_mm`` (warn-band: the fab
    builds 0.2mm happily, so blocking would be a gate set to a preference).

    Ground that is *poured* rather than routed is exempt — the pour is the
    current path and its routed stubs are not what carries the rail.
    """
    try:
        warnings: list[Warning] = []
        net_name: dict[str, str] = {}
        is_rail: dict[str, bool] = {}
        poured: set[str] = set()
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            etype = element.get("type")
            if etype == "source_net":
                nid = str(element.get("source_net_id") or "")
                name = str(element.get("name") or "")
                if not nid or not name:
                    continue
                net_name[nid] = name
                is_rail[nid] = bool(
                    element.get("is_power")
                    or element.get("is_ground")
                    or _POWER_NET_RE.match(name)
                    or _GROUND_NET_RE.match(name)
                )
            elif etype == "pcb_copper_pour":
                nid = element.get("source_net_id")
                if isinstance(nid, str):
                    poured.add(nid)
        trace_nets: dict[str, tuple[str, ...]] = {}
        for element in circuit_json:
            if not isinstance(element, dict) or element.get("type") != "source_trace":
                continue
            tid = str(element.get("source_trace_id") or "")
            nets = tuple(
                nid
                for nid in (element.get("connected_source_net_ids") or [])
                if isinstance(nid, str) and is_rail.get(nid) and nid not in poured
            )
            if tid and nets:
                trace_nets[tid] = nets
        # Two numbers per net, because they mean different things and only one
        # of them is anybody's fault.
        #
        # `run` is the narrowest point of the rail away from its terminals.
        # `neck` is the narrowest point anywhere, which is always at a pad:
        # the router **tapers into a terminal pad narrower than the track**
        # (`getTerminalPadWidthLimit` / `getTaperWidthAtDistance` in the shipped
        # bundle), so on a board whose pads are 0.2mm — every 0402, every QFN —
        # the minimum over the whole route can never reach a 0.5mm floor. This
        # check used to report that minimum, which made it **unsatisfiable by
        # construction**: it fired on every board, forever, whatever anyone did,
        # and a check nobody can action is a check people learn to scroll past.
        #
        # Measured 2026-08-17: with V3_3 declared at its own 0.4mm ceiling,
        # terminal-keyboard still reported "routed at 0.2125mm" — the taper,
        # not the rail.
        narrowest: dict[str, float] = {}
        necked: dict[str, float] = {}
        for element in circuit_json:
            if not isinstance(element, dict) or element.get("type") != "pcb_trace":
                continue
            nets = trace_nets.get(str(element.get("source_trace_id") or ""))
            if not nets:
                continue
            route = [seg for seg in element.get("route") or [] if isinstance(seg, dict)]
            for index, segment in enumerate(route):
                width = segment.get("width")
                if not isinstance(width, (int, float)) or width <= 0:
                    continue
                for nid in nets:
                    if nid not in necked or width < necked[nid]:
                        necked[nid] = float(width)
                if _within_taper(route, index):
                    continue
                for nid in nets:
                    if nid not in narrowest or width < narrowest[nid]:
                        narrowest[nid] = float(width)
        floor = profile.warn_power_trace_mm
        for nid, width in sorted(narrowest.items(), key=lambda kv: net_name.get(kv[0], "")):
            if width >= floor - 1e-9:
                continue
            name = net_name.get(nid, nid)
            neck = necked.get(nid)
            tail = ""
            if neck is not None and neck < width - 1e-9:
                tail = (
                    f" (it necks to {neck:g}mm at its pads, which is the router "
                    "tapering into a pad narrower than the track — that part is "
                    "not something a width can fix)"
                )
            warnings.append(
                _warning(
                    name,
                    "dfm_power_trace_width",
                    f"power net {name} runs at {width:g}mm — the same copper as "
                    f"a signal{tail}. The current maths may pass on this board, "
                    "but power at signal width is the wrong habit; the profile's "
                    f"power floor is {floor:g}mm (warn_power_trace_mm — an EE "
                    "moves the line there)",
                )
            )
        return warnings
    except Exception as exc:
        return [check_failed(f"power width scan raised {type(exc).__name__}: {exc}")]


#: Width of one silkscreen character as a fraction of the font size.
#:
#: tscircuit plots silkscreen with a single-stroke font. Measured on the boards
#: we ship by comparing a label's plotted extent against its `font_size`: a
#: character advances about 0.6 x the size and the glyph is about 0.72 x tall.
#: Both are deliberately *slightly under* the truth — this check should be sure
#: before it speaks, and a near-miss that reads fine on the plot is not worth a
#: finding.
SILK_CHAR_ADVANCE = 0.6
SILK_CAP_HEIGHT = 0.72


def _silk_box(element: dict) -> tuple[float, float, float, float] | None:
    """The box a silkscreen label occupies, from its anchor and its size."""
    anchor = element.get("anchor_position") or {}
    x, y = _f(anchor.get("x")), _f(anchor.get("y"))
    size = _f(element.get("font_size")) or 0.0
    text = str(element.get("text") or "")
    if x is None or y is None or size <= 0 or not text.strip():
        return None
    # Rotated labels are skipped rather than approximated: a 90-degree label's
    # box is not this box turned, and guessing here would produce exactly the
    # false positives that make a check ignorable.
    if abs(_f(element.get("ccw_rotation")) or 0.0) > 1e-6:
        return None
    width = len(text) * size * SILK_CHAR_ADVANCE
    height = size * SILK_CAP_HEIGHT
    align = str(element.get("anchor_alignment") or "center")
    left = x - width / 2
    if "left" in align:
        left = x
    elif "right" in align:
        left = x - width
    bottom = y - height / 2
    if "bottom" in align:
        bottom = y
    elif "top" in align:
        bottom = y - height
    return (left, bottom, left + width, bottom + height)


def silk_overlap_warnings(circuit_json: Sequence[dict]) -> list[Warning]:
    """Two silkscreen labels printed on top of each other.

    Nothing in the pipeline saw this. An engineer building `usb-c-breakout`
    (2026-08-17) shipped a first build whose pad labels read **"VBUSGND"** —
    two legends fused into one word — and the only thing that caught it was
    looking at the PNG. A board can be electrically perfect and unusable at the
    bench because the pad you need is the one whose name ran into its
    neighbour's, and "read every silkscreen string in the render" is not a
    check, it is a hope.

    Only same-layer, unrotated pairs, and only a real overlap: this is a
    warning about legibility, and one that cries wolf gets switched off.
    """
    try:
        boxes: list[tuple[str, str, tuple[float, float, float, float]]] = []
        for element in circuit_json:
            if not isinstance(element, dict) or element.get("type") != "pcb_silkscreen_text":
                continue
            box = _silk_box(element)
            if box is None:
                continue
            boxes.append((str(element.get("text") or ""), str(element.get("layer") or "top"), box))

        warnings: list[Warning] = []
        seen: set[tuple[str, str]] = set()
        for i, (text_a, layer_a, a) in enumerate(boxes):
            for text_b, layer_b, b in boxes[i + 1:]:
                if layer_a != layer_b:
                    continue
                overlap_x = min(a[2], b[2]) - max(a[0], b[0])
                overlap_y = min(a[3], b[3]) - max(a[1], b[1])
                if overlap_x <= 0.01 or overlap_y <= 0.01:
                    continue
                key = tuple(sorted((text_a, text_b)))
                if key in seen:
                    continue
                seen.add(key)
                warnings.append(
                    _warning(
                        "board",
                        "silk_text_overlap",
                        f"silkscreen \"{text_a}\" and \"{text_b}\" overlap by "
                        f"{min(overlap_x, overlap_y):.2f}mm on {layer_a} — they print as one "
                        "word, and a label nobody can read is a pad nobody can find",
                    )
                )
        return warnings
    except Exception as exc:  # noqa: BLE001
        return [check_failed(f"silk overlap scan raised {type(exc).__name__}: {exc}")]


#: Nets that legitimately touch one part and nothing else. A test point is a
#: named pad by definition, and a no-connect is a declaration that the pin goes
#: nowhere on purpose.
_LONELY_NET_EXEMPT = ("TP", "TEST", "NC", "NO_CONNECT", "DNP", "RESERVED", "SPARE")


def floating_net_warnings(circuit_json: Sequence[dict]) -> list[Warning]:
    """A named net that reaches only one component — or none.

    Lesson F, in the one place it had not been looked for yet: *an absent value
    is a claim, and nothing checks a claim nobody makes*. Every connectivity
    check in this stack asks whether a connection that exists is correct.
    Nothing asked whether a connection that **should** exist is there at all,
    because a missing wire produces no element to be wrong about.

    Measured across the twelve boards in the repo on 2026-08-17, and it found
    three real defects on boards carrying ``fab.ready: true``:

    * ``sensor-node-mini`` — ``USB_DP`` and ``USB_DM`` reach only U3. The board
      composes ``usb-c-power``, which has no data pins, so the RP2040's USB
      pair terminates in nothing: a USB device that cannot do USB, and no way
      to flash it except SWD.
    * ``desk-air-monitor`` — ``BTN1`` reaches only SW1. The button is wired to
      itself; nothing reads it.
    * ``pixel-badge`` and ``harness-puck`` — ``PX_18_DIN`` reaches only D17,
      the end of a pixel chain that never gets its data in.

    All four boards route, plot, gate and pass every other check we own,
    because every trace they *do* have is correct. This is deliberately not
    scoped to power rails: the failure that prompted it was an engineer
    composing ``rp2040-core`` without ``ldo-3v3``, which would leave ``V3_3``
    lonely, and the same shape catches a dead button.

    Needs no routing, so it runs in the 17s pre-flight as well as the build —
    which is the whole point, since the alternative is finding it after a
    forty-minute build or, as here, not at all.

    Graded ``warning`` rather than ``error`` on purpose: a one-part net can be
    intentional (a mounting-hole net, a reserved pin), and three shipped boards
    would flip from ready to blocked on a call that is the fleet owner's to
    make. The detail says plainly that it is a functional defect.
    """
    try:
        ports = {
            str(e.get("source_port_id")): e
            for e in circuit_json
            if isinstance(e, dict) and e.get("type") == "source_port"
        }
        components = {
            str(e.get("source_component_id")): e
            for e in circuit_json
            if isinstance(e, dict) and e.get("type") == "source_component"
        }
        nets = {
            str(e.get("source_net_id")): str(e.get("name") or "")
            for e in circuit_json
            if isinstance(e, dict) and e.get("type") == "source_net"
        }
        if not nets:
            return []

        # Union-find over every id a trace joins. Connectivity here is by id
        # and a net is only one of the things a trace may name: a port-to-port
        # trace joins two pins with no net element at all, so counting a net's
        # own members would miss every component reached that way.
        parent = {key: key for key in list(ports) + list(nets)}

        def find(key: str) -> str:
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        for element in circuit_json:
            if not isinstance(element, dict) or element.get("type") != "source_trace":
                continue
            joined = [
                str(i)
                for i in list(element.get("connected_source_port_ids") or [])
                + list(element.get("connected_source_net_ids") or [])
                if str(i) in parent
            ]
            for other in joined[1:]:
                a, b = find(joined[0]), find(other)
                if a != b:
                    parent[a] = b

        members: dict[str, list[str]] = {}
        for key in parent:
            members.setdefault(find(key), []).append(key)

        warnings: list[Warning] = []
        for group in members.values():
            names = sorted(nets[m] for m in group if m in nets and nets[m])
            if not names:
                continue
            if any(
                name.upper().startswith(_LONELY_NET_EXEMPT) or "TESTPOINT" in name.upper()
                for name in names
            ):
                continue
            reached = sorted(
                {
                    str(components.get(str(ports[m].get("source_component_id")), {}).get("name") or "")
                    for m in group
                    if m in ports
                }
                - {""}
            )
            if len(reached) > 1:
                continue
            label = " / ".join(names)
            if reached:
                warnings.append(
                    _warning(
                        reached[0],
                        "net_reaches_one_part",
                        f"net {label} reaches only {reached[0]} — every pin on it "
                        f"belongs to the same part, so whatever was meant to be on "
                        f"the other end of this net is not on the board. The copper "
                        f"that exists is correct, which is why nothing else reports "
                        f"it; the wire that does not exist is the defect. Either "
                        f"connect it, or name it TP*/NC* to say the pad is the point",
                    )
                )
            else:
                warnings.append(
                    _warning(
                        "board",
                        "net_reaches_nothing",
                        f"net {label} is declared and connects to no component at "
                        f"all — it exists in the design and touches nothing",
                    )
                )
        return warnings
    except Exception as exc:  # noqa: BLE001
        return [check_failed(f"floating net scan raised {type(exc).__name__}: {exc}")]


def trace_anchor_warnings(circuit_json: Sequence[dict]) -> list[Warning]:
    """A route that no longer touches the pad it claims to start or end on.

    Written for the IDE's edit loop, and it exists because of a measured hole:
    connectivity in circuit.json is carried **by id, never by geometry**.
    ``checkEachPcbPortConnectedToPcbTraces`` walks
    ``connected_source_port_ids``; ``_connectivity_index`` above keys on
    ``subcircuit_connectivity_map_key``. Neither ever compares a pad's x/y to a
    route's endpoint, so moving a part 2mm away from its own copper changes
    nothing either of them can see.

    Exactly one check in the whole stack notices — @tscircuit/checks'
    ``checkTracesAreContiguous`` — and it is the single most expensive thing we
    run: **1,491ms of a 2,319ms node leg** on terminal-keyboard (measured
    2026-08-16, `each_check.cjs` per-check attribution). That is affordable
    once per build and not affordable after a drag, which is why this exists as
    a Python stand-in on the interactive path.

    The rule is the narrowest one that catches the defect: every route point
    carrying ``start_pcb_port_id``/``end_pcb_port_id`` must land **inside the
    copper of a pad belonging to that port**. Nothing about widths, nothing
    about clearances, nothing that duplicates a check we already run.

    **It is a stand-in, not a replacement.** It cannot see a misaligned via, or
    a trace with no endpoint annotation at all (504 of terminal-keyboard's
    2,627 route points carry one). ``checkTracesAreContiguous`` stays in the
    full build; this is what the sub-second gate has instead of it.
    """
    try:
        pads_by_port: dict[str, list[dict]] = {}
        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            if element.get("type") not in ("pcb_smtpad", "pcb_plated_hole"):
                continue
            port = element.get("pcb_port_id")
            if isinstance(port, str) and port:
                pads_by_port.setdefault(port, []).append(element)

        names = _component_names(circuit_json)
        pad_refdes: dict[str, str] = {}
        for port, pads in pads_by_port.items():
            for pad in pads:
                owner = pad.get("pcb_component_id")
                if isinstance(owner, str) and owner in names:
                    pad_refdes[port] = names[owner]
                    break

        warnings: list[Warning] = []
        for element in circuit_json:
            if not isinstance(element, dict) or element.get("type") != "pcb_trace":
                continue
            trace_id = str(element.get("pcb_trace_id") or "trace")
            route = element.get("route")
            if not isinstance(route, list):
                continue
            for point in route:
                if not isinstance(point, dict):
                    continue
                px, py = point.get("x"), point.get("y")
                if not isinstance(px, (int, float)) or not isinstance(py, (int, float)):
                    continue
                for key, end in (("start_pcb_port_id", "starts"),
                                 ("end_pcb_port_id", "ends")):
                    port = point.get(key)
                    if not isinstance(port, str) or not port:
                        continue
                    pads = pads_by_port.get(port)
                    # A port with no pad is a different defect and a different
                    # check's business; silence here beats a second opinion.
                    if not pads:
                        continue
                    gap = None
                    for pad in pads:
                        shape = _pad_copper(pad)
                        if shape is None:
                            continue
                        distance = _copper_gap(shape, float(px), float(py),
                                               float(px), float(py))
                        gap = distance if gap is None else min(gap, distance)
                    if gap is None or gap <= 0.0:
                        continue
                    warnings.append(
                        _warning(
                            pad_refdes.get(port, "board"),
                            "trace_left_its_pad",
                            f"trace {trace_id} {end} {gap:.3f}mm outside the "
                            f"copper of {port} — the part moved and its route "
                            "did not follow. The net is still connected in the "
                            "netlist; it is not connected on the board.",
                            "error",
                        )
                    )
        return warnings
    except Exception as exc:
        return [check_failed(f"trace anchor scan raised {type(exc).__name__}: {exc}")]


def _hole_floor(hole: dict, profile: FabProfile) -> float:
    """JLC publishes three drill-to-track figures, not one: 0.20mm for a via
    hole, 0.28mm for a component plated hole, 0.20mm for a non-plated one."""
    if hole["via"]:
        return profile.min_via_to_copper_mm
    if hole["plated"]:
        return profile.min_pth_to_copper_mm
    return profile.min_npth_to_copper_mm


def dfm_warnings(
    circuit_json: Sequence[dict],
    product: ResolvedProduct,
    profile: FabProfile,
) -> list[Warning]:
    """The fab profile's limit table over circuit.json geometry. Never raises."""
    try:
        names = _component_names(circuit_json)
        warnings: list[Warning] = []
        warnings.extend(_hole_to_copper_warnings(circuit_json, names, profile))
        warnings.extend(power_width_warnings(circuit_json, profile))
        board = next(
            (
                e
                for e in circuit_json
                if isinstance(e, dict) and e.get("type") == "pcb_board"
            ),
            None,
        )
        board_rect: tuple[float, float, float, float] | None = None
        if board is not None:
            width = float(board.get("width") or 0)
            height = float(board.get("height") or 0)
            center = board.get("center") or {}
            cx = float(center.get("x") or 0)
            cy = float(center.get("y") or 0)
            board_rect = (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)

            if width and height and min(width, height) < profile.min_board_mm:
                warnings.append(
                    _warning(
                        "board",
                        "dfm_board_size",
                        f"board {width:g}x{height:g}mm is below the fab minimum "
                        f"{profile.min_board_mm:g}mm",
                        "error",
                    )
                )
            if width and height and profile.price_tiers:
                # Ledger #30: the fab's sample subsidy is a price cliff and
                # the plan should only leave it knowingly. Name the money.
                fits_tier = any(
                    (width <= tw and height <= th)
                    or (height <= tw and width <= th)
                    for tw, th, _, _ in profile.price_tiers
                )
                if not fits_tier:
                    tw, th, usd, label = profile.price_tiers[0]
                    over = min(
                        max(width - tw, height - th, 0.0),
                        max(height - tw, width - th, 0.0),
                    )
                    warnings.append(
                        _warning(
                            "board",
                            "dfm_price_tier",
                            f"board {width:g}x{height:g}mm misses the "
                            f"{tw:g}x{th:g}mm ${usd:g} {label} tier by "
                            f"{over:g}mm — {profile.price_tier_evidence}. "
                            "Fit the tier unless the design cannot",
                        )
                    )
            if product.envelope_mm is not None and width and height:
                env_w, env_h = product.envelope_mm
                if width > env_w + 1e-9 or height > env_h + 1e-9:
                    warnings.append(
                        _warning(
                            "board",
                            "board_exceeds_envelope",
                            f"board {width:g}x{height:g}mm exceeds the product "
                            f"envelope {env_w:g}x{env_h:g}mm",
                            "error",
                        )
                    )
            thickness = board.get("thickness")
            if isinstance(thickness, (int, float)) and abs(
                float(thickness) - profile.standard_thickness_mm
            ) > 1e-9:
                warnings.append(
                    _warning(
                        "board",
                        "dfm_thickness",
                        f"board thickness {thickness:g}mm is not the fab standard "
                        f"{profile.standard_thickness_mm:g}mm — set thickness "
                        "explicitly on <board> (toolchain default is 1.4)",
                        "warning",
                    )
                )

        for element in circuit_json:
            if not isinstance(element, dict):
                continue
            etype = element.get("type")
            if etype == "pcb_trace":
                trace_part = names.get(str(element.get("source_trace_id") or ""), "")
                for segment in element.get("route") or []:
                    if not isinstance(segment, dict):
                        continue
                    seg_width = segment.get("width")
                    if not isinstance(seg_width, (int, float)):
                        continue
                    if seg_width < profile.min_trace_mm:
                        warnings.append(
                            _warning(
                                trace_part or "board",
                                "dfm_trace_width",
                                f"trace width {seg_width:g}mm is below the fab "
                                f"minimum {profile.min_trace_mm:g}mm",
                                "error",
                            )
                        )
                        break
                    if seg_width < profile.warn_trace_mm:
                        warnings.append(
                            _warning(
                                trace_part or "board",
                                "dfm_trace_width",
                                f"trace width {seg_width:g}mm is below the "
                                f"recommended {profile.warn_trace_mm:g}mm",
                                "warning",
                            )
                        )
                        break
            elif etype in ("pcb_via", "pcb_plated_hole"):
                # A via is a routing feature; a plated hole is where a component
                # leg goes. JLC specs them separately and the via numbers are
                # much finer — checking a via against the PTH annular-ring rule
                # flags every routed board, which trains everyone to ignore DFM.
                part = _localize(element, names)
                hole = element.get("hole_diameter")
                outer = element.get("outer_diameter")
                is_via = etype == "pcb_via"
                min_drill = (
                    profile.min_via_drill_mm if is_via else profile.min_pth_drill_mm
                )
                min_annular = (
                    profile.min_via_annular_mm if is_via else profile.min_pth_annular_mm
                )
                kindname = "via" if is_via else "plated hole"

                if isinstance(hole, (int, float)) and hole < min_drill - 1e-9:
                    warnings.append(
                        _warning(
                            part,
                            "dfm_drill_size",
                            f"{kindname} hole {hole:g}mm is below the fab minimum "
                            f"drill {min_drill:g}mm",
                            "error",
                        )
                    )
                elif (
                    is_via
                    and isinstance(hole, (int, float))
                    and hole < profile.warn_via_drill_mm - 1e-9
                ):
                    warnings.append(
                        _warning(
                            part,
                            "dfm_drill_size",
                            f"via hole {hole:g}mm is legal but below the "
                            f"{profile.warn_via_drill_mm:g}mm we prefer on the "
                            "cheap tier",
                            "warning",
                        )
                    )

                if is_via and isinstance(outer, (int, float)):
                    if outer < profile.min_via_diameter_mm - 1e-9:
                        warnings.append(
                            _warning(
                                part,
                                "dfm_via_diameter",
                                f"via diameter {outer:g}mm is below the fab "
                                f"minimum {profile.min_via_diameter_mm:g}mm",
                                "error",
                            )
                        )
                    elif outer < profile.warn_via_diameter_mm - 1e-9:
                        warnings.append(
                            _warning(
                                part,
                                "dfm_via_diameter",
                                f"via diameter {outer:g}mm is legal but below the "
                                f"{profile.warn_via_diameter_mm:g}mm we prefer on "
                                "the cheap tier",
                                "warning",
                            )
                        )

                if (
                    isinstance(hole, (int, float))
                    and isinstance(outer, (int, float))
                    and outer > hole
                ):
                    annular = (float(outer) - float(hole)) / 2
                    if annular < min_annular - 1e-9:
                        warnings.append(
                            _warning(
                                part,
                                "dfm_annular_ring",
                                f"{kindname} annular ring {annular:.3f}mm is below "
                                f"the fab minimum {min_annular:g}mm",
                                "error",
                            )
                        )
                    elif is_via and annular < profile.warn_via_annular_mm - 1e-9:
                        warnings.append(
                            _warning(
                                part,
                                "dfm_annular_ring",
                                f"via annular ring {annular:.3f}mm is legal but "
                                f"thin; {profile.warn_via_annular_mm:g}mm is the "
                                "safer cheap-tier target",
                                "warning",
                            )
                        )
            if board_rect is not None and etype in ("pcb_smtpad", "pcb_via", "pcb_plated_hole"):
                if etype == "pcb_smtpad":
                    # The same shape reading as the hole gate (ledger #41), for
                    # the same reason: `width`/`height` unrotated is the wrong
                    # box for a turned pad, is half size for a circle, and does
                    # not exist at all for a polygon — the four USB-C shell pads
                    # on every board were skipped here too.
                    shape = _pad_copper(element)
                    if shape is None:
                        continue
                    x0, y0, x1, y1 = _copper_box(shape)
                else:
                    x = element.get("x")
                    y = element.get("y")
                    if not isinstance(x, (int, float)) \
                            or not isinstance(y, (int, float)):
                        continue
                    radius = float(element.get("outer_diameter") or 0) / 2
                    x0, y0, x1, y1 = x - radius, y - radius, x + radius, y + radius
                margin = min(
                    x0 - board_rect[0],
                    y0 - board_rect[1],
                    board_rect[2] - x1,
                    board_rect[3] - y1,
                )
                if margin < profile.min_edge_clearance_mm - 1e-9:
                    warnings.append(
                        _warning(
                            _localize(element, names),
                            "dfm_edge_clearance",
                            f"copper is {margin:.3f}mm from the board edge "
                            f"(fab minimum {profile.min_edge_clearance_mm:g}mm)",
                            "error",
                        )
                    )
                elif margin < profile.warn_edge_clearance_mm - 1e-9:
                    warnings.append(
                        _warning(
                            _localize(element, names),
                            "dfm_edge_clearance",
                            f"copper is {margin:.3f}mm from the board edge — "
                            f"legal on a routed outline, but "
                            f"{profile.warn_edge_clearance_mm:g}mm is the safer "
                            "target and is required for a V-cut edge",
                            "warning",
                        )
                    )
        return warnings
    except Exception as exc:
        return [check_failed(f"DFM gate raised {type(exc).__name__}: {exc}")]


# ---------------------------------------------------------------------------
# Stage 4: BOM gate (rows already merged with the parts.json lock).
# ---------------------------------------------------------------------------


#: Refdes prefixes and part descriptions for board features that appear on a
#: BOM but are bare copper — nothing to source, nothing to place.
_UNSOURCED_PREFIXES = ("TP", "FID", "MH", "H")
_UNSOURCED_WORDS = ("testpoint", "test point", "fiducial", "mounting hole",
                    "mountinghole", "hole", "keepout", "logo")


def _is_unsourced_by_design(row: dict) -> bool:
    designator = str(row.get("designator") or "")
    text = " ".join(
        str(row.get(key) or "") for key in ("comment", "value", "footprint")
    ).lower()
    if any(word in text for word in _UNSOURCED_WORDS):
        return True
    prefix = "".join(c for c in designator if c.isalpha()).upper()
    # `H1` is a hole, `H` alone is not a prefix we should guess from, and a
    # bare `R`/`C` must never fall through here.
    return bool(prefix) and prefix in _UNSOURCED_PREFIXES and any(
        c.isdigit() for c in designator
    )


def bom_gate(rows: list[dict], *, assembly: bool) -> list[Warning]:
    """Orderability + lock agreement. ``part_not_orderable`` blocks assembly
    packets (error) and merely advises bare-PCB ones (info); ``part_drift``
    warns when a BOM row's part number disagrees with the parts.json lock;
    ``extended_part`` advises about the loading fee. Never raises."""
    try:
        warnings: list[Warning] = []
        for row in rows:
            designator = str(row.get("designator") or "part")
            lcsc = str(row.get("lcsc") or "")
            lock = row.get("lock") if isinstance(row.get("lock"), dict) else None
            if not lcsc and _is_unsourced_by_design(row):
                # A test point, fiducial or mounting hole is copper, not a
                # part: there is nothing to buy and nothing to place. Blocking
                # on it made test points impossible to add — while the review
                # panel's testability lens asks for them on every rail.
                continue
            if not lcsc:
                warnings.append(
                    _warning(
                        designator,
                        "part_not_orderable",
                        "BOM row has no LCSC part number — the fab cannot source it"
                        + ("" if assembly else " (bare-PCB order unaffected)"),
                        "error" if assembly else "info",
                    )
                )
            if lock is not None:
                locked_lcsc = str(lock.get("lcsc") or "")
                if lcsc and locked_lcsc and lcsc != locked_lcsc:
                    warnings.append(
                        _warning(
                            designator,
                            "part_drift",
                            f"BOM resolved {lcsc} but parts.json locks {locked_lcsc} "
                            "— re-run parts-book or rebuild before ordering",
                            "warning",
                        )
                    )
                if lock.get("basic") is False:
                    warnings.append(
                        _warning(
                            designator,
                            "extended_part",
                            f"{lcsc or 'part'} is an Extended part "
                            "(~$3/line loading fee at JLCPCB)",
                            "info",
                        )
                    )
        return warnings
    except Exception as exc:
        return [check_failed(f"BOM gate raised {type(exc).__name__}: {exc}")]


def dedupe(warnings: list[Warning]) -> list[Warning]:
    """Drop exact duplicates across stages (stage 2 re-reports many stage-1
    findings with identical kind + detail). First occurrence wins; order kept."""
    seen: set[tuple[str, str, str]] = set()
    out: list[Warning] = []
    for warning in warnings:
        key = (
            str(warning.get("kind")),
            str(warning.get("part")),
            str(warning.get("detail")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(warning)
    return out
