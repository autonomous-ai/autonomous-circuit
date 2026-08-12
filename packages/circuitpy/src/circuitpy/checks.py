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
            ``part_drift``, ``part_lock_stale``, ``board_exceeds_envelope``;
  anywhere — ``check_failed`` (a verifier itself raised).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Sequence

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


def iou_warnings(
    circuit_json: Sequence[dict], profile: FabProfile
) -> list[Warning]:
    """``supplier_footprint_mismatch_warning`` banded by copper IoU:
    < {error_below} error / < {warning_below} warning / < {info_below} info /
    else dropped (correct 0402 parts score ~0.73-0.77)."""
    try:
        names = _component_names(circuit_json)
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


def run_tscircuit_checks(circuit_json_path: Path) -> list[Warning]:
    """``runAllChecks`` via the packaged node helper. Findings become
    warnings with kind = the finding's type.  The library returns both
    ``*_error`` and ``*_warning`` elements, so retain that distinction; the
    routing retry compares blocking counts at this stage and must not choose
    an attempt based on advisory trace-length warnings. Never raises."""
    try:
        output = toolchain.run_node(
            [toolchain.helper_js("run_all_checks.cjs"), str(circuit_json_path)],
            timeout=_CHECKS_TIMEOUT_S,
        )
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
            raw_severity = str(finding.get("severity") or "").lower()
            if raw_severity in {"error", "warning", "info"}:
                severity = raw_severity
            elif kind.endswith("_warning") or finding.get("warning_type"):
                severity = "warning"
            else:
                # Unknown findings remain conservative.  Error elements in
                # circuit-json normally advertise `error_type` or `_error`;
                # an untyped finding must never silently make a bad board pass.
                severity = "error"
            warnings.append(
                _warning(_localize(finding, {}), kind, detail, severity)
            )
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
            warnings.append(_warning(part, kind, detail or "violation", severity))
        return warnings
    except Exception as exc:
        return [check_failed(f"kicad report parse failed ({kind}): {exc}")]


# ---------------------------------------------------------------------------
# Stage 4: DFM gate + envelope, over circuit.json geometry.
# ---------------------------------------------------------------------------


_Point = tuple[float, float]
_Stadium = tuple[_Point, _Point, float]


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _rotate_point(point: _Point, degrees: float) -> _Point:
    radians = math.radians(degrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (
        point[0] * cosine - point[1] * sine,
        point[0] * sine + point[1] * cosine,
    )


def _stadium(
    center: _Point,
    width: float,
    height: float,
    rotation_degrees: float = 0.0,
) -> _Stadium | None:
    """Exact capsule for a round drill or routed slot.

    KiCad and Excellon represent a slot as a round tool swept along its long
    axis.  Treating ``hole_width`` as a circle diameter loses the swept
    centreline and overstates clearance at both slot endpoints by half the
    slot travel — exactly how the USB-C shell-slot regression escaped.
    """
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        return None
    radius = min(width, height) / 2.0
    half_travel = (max(width, height) - min(width, height)) / 2.0
    local_axis = (1.0, 0.0) if width >= height else (0.0, 1.0)
    axis = _rotate_point(local_axis, rotation_degrees)
    offset = (axis[0] * half_travel, axis[1] * half_travel)
    return (
        (center[0] - offset[0], center[1] - offset[1]),
        (center[0] + offset[0], center[1] + offset[1]),
        radius,
    )


def _point_segment_distance(point: _Point, first: _Point, second: _Point) -> float:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-24:
        return math.hypot(point[0] - first[0], point[1] - first[1])
    ratio = max(
        0.0,
        min(
            1.0,
            ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy)
            / length_sq,
        ),
    )
    projection = (first[0] + ratio * dx, first[1] + ratio * dy)
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def _orientation(first: _Point, second: _Point, third: _Point) -> float:
    return ((second[0] - first[0]) * (third[1] - first[1])
            - (second[1] - first[1]) * (third[0] - first[0]))


def _segments_intersect(
    first_a: _Point, first_b: _Point, second_a: _Point, second_b: _Point
) -> bool:
    epsilon = 1e-12
    orientations = (
        _orientation(first_a, first_b, second_a),
        _orientation(first_a, first_b, second_b),
        _orientation(second_a, second_b, first_a),
        _orientation(second_a, second_b, first_b),
    )
    if (
        ((orientations[0] > epsilon and orientations[1] < -epsilon)
         or (orientations[0] < -epsilon and orientations[1] > epsilon))
        and ((orientations[2] > epsilon and orientations[3] < -epsilon)
             or (orientations[2] < -epsilon and orientations[3] > epsilon))
    ):
        return True

    def on_segment(point: _Point, start: _Point, end: _Point) -> bool:
        return (
            min(start[0], end[0]) - epsilon <= point[0]
            <= max(start[0], end[0]) + epsilon
            and min(start[1], end[1]) - epsilon <= point[1]
            <= max(start[1], end[1]) + epsilon
        )

    return any((
        abs(orientations[0]) <= epsilon and on_segment(second_a, first_a, first_b),
        abs(orientations[1]) <= epsilon and on_segment(second_b, first_a, first_b),
        abs(orientations[2]) <= epsilon and on_segment(first_a, second_a, second_b),
        abs(orientations[3]) <= epsilon and on_segment(first_b, second_a, second_b),
    ))


def _segment_distance(
    first_a: _Point, first_b: _Point, second_a: _Point, second_b: _Point
) -> float:
    if _segments_intersect(first_a, first_b, second_a, second_b):
        return 0.0
    return min(
        _point_segment_distance(first_a, second_a, second_b),
        _point_segment_distance(first_b, second_a, second_b),
        _point_segment_distance(second_a, first_a, first_b),
        _point_segment_distance(second_b, first_a, first_b),
    )


def _point_in_polygon(point: _Point, vertices: list[_Point]) -> bool:
    if len(vertices) < 3:
        return False
    previous = vertices[-1]
    inside = False
    for current in vertices:
        if _point_segment_distance(point, previous, current) <= 1e-12:
            return True
        if (current[1] > point[1]) != (previous[1] > point[1]):
            crossing_x = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < crossing_x:
                inside = not inside
        previous = current
    return inside


def _segment_polygon_distance(
    first: _Point, second: _Point, vertices: list[_Point]
) -> float:
    if not vertices:
        return math.inf
    if _point_in_polygon(first, vertices) or _point_in_polygon(second, vertices):
        return 0.0
    previous = vertices[-1]
    distance = math.inf
    for current in vertices:
        distance = min(
            distance,
            _segment_distance(first, second, previous, current),
        )
        previous = current
    return distance


def _element_rotation(element: dict) -> float:
    for field in ("ccw_rotation", "rotation"):
        value = _finite_number(element.get(field))
        if value is not None:
            return value
    return 0.0


def _drill_stadium(element: dict) -> _Stadium | None:
    x = _finite_number(element.get("x"))
    y = _finite_number(element.get("y"))
    if x is None or y is None:
        return None
    width = _finite_number(element.get("hole_width"))
    height = _finite_number(element.get("hole_height"))
    diameter = _finite_number(element.get("hole_diameter"))
    if width is None:
        width = diameter
    if height is None:
        height = diameter
    if width is None or height is None:
        return None
    return _stadium((x, y), width, height, _element_rotation(element))


def _own_pad_stadium(element: dict) -> _Stadium | None:
    x = _finite_number(element.get("x"))
    y = _finite_number(element.get("y"))
    if x is None or y is None:
        return None
    width = _finite_number(element.get("outer_width"))
    height = _finite_number(element.get("outer_height"))
    diameter = _finite_number(element.get("outer_diameter"))
    if width is None:
        width = diameter
    if height is None:
        height = diameter
    if width is None or height is None:
        return None
    return _stadium((x, y), width, height, _element_rotation(element))


def _smt_copper_shape(element: dict) -> tuple[str, object] | None:
    x = _finite_number(element.get("x"))
    y = _finite_number(element.get("y"))
    shape = str(element.get("shape") or "rect")
    if shape == "polygon":
        vertices: list[_Point] = []
        for point in element.get("points") or []:
            if not isinstance(point, dict):
                return None
            px = _finite_number(point.get("x"))
            py = _finite_number(point.get("y"))
            if px is None or py is None:
                return None
            vertices.append((px, py))
        return ("polygon", vertices) if len(vertices) >= 3 else None
    if x is None or y is None:
        return None
    if shape == "circle":
        radius = _finite_number(element.get("radius"))
        if radius is None:
            diameter = _finite_number(element.get("width"))
            radius = diameter / 2.0 if diameter is not None else None
        if radius is None or radius <= 0:
            return None
        return ("stadium", ((x, y), (x, y), radius))

    width = _finite_number(element.get("width"))
    height = _finite_number(element.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    rotation = _element_rotation(element) if shape.startswith("rotated_") else 0.0
    if shape in {"pill", "rotated_pill"}:
        stadium = _stadium((x, y), width, height, rotation)
        return ("stadium", stadium) if stadium is not None else None
    if shape not in {"rect", "rotated_rect"}:
        return None
    local = [
        (-width / 2.0, -height / 2.0),
        (width / 2.0, -height / 2.0),
        (width / 2.0, height / 2.0),
        (-width / 2.0, height / 2.0),
    ]
    vertices = [
        (x + rotated[0], y + rotated[1])
        for rotated in (_rotate_point(point, rotation) for point in local)
    ]
    return ("polygon", vertices)


def _shape_gap(drill: _Stadium, copper_shape: tuple[str, object]) -> float:
    first, second, drill_radius = drill
    kind, shape = copper_shape
    if kind == "stadium":
        copper_first, copper_second, copper_radius = shape  # type: ignore[misc]
        return (
            _segment_distance(first, second, copper_first, copper_second)
            - drill_radius
            - copper_radius
        )
    vertices = shape  # type: ignore[assignment]
    return _segment_polygon_distance(first, second, vertices) - drill_radius


def _shape_inside_stadium(
    copper_shape: tuple[str, object], container: _Stadium
) -> bool:
    container_first, container_second, container_radius = container
    kind, shape = copper_shape
    if kind == "stadium":
        first, second, radius = shape  # type: ignore[misc]
        return all(
            _point_segment_distance(point, container_first, container_second)
            + radius
            <= container_radius + 1e-9
            for point in (first, second)
        )
    vertices = shape  # type: ignore[assignment]
    return bool(vertices) and all(
        _point_segment_distance(point, container_first, container_second)
        <= container_radius + 1e-9
        for point in vertices
    )


def _hole_to_copper_warnings(
    circuit_json: Sequence[dict], names: dict, profile: FabProfile
) -> list[Warning]:
    """Exact drill-to-unrelated-copper clearance over circuit.json.

    Drills are round-tool stadiums, not bounding circles.  The gate includes
    NPTH/PTH slots and via drills, and compares them with trace capsules, SMD
    copper and other via pads.  Electrical identity exempts real same-net
    connections; geometry exempts unidentified copper only while it remains
    wholly inside the drill feature's own annular pad.
    """
    source_keys: dict[str, str] = {}
    pcb_port_keys: dict[str, str] = {}
    for element in circuit_json:
        if not isinstance(element, dict):
            continue
        key = str(element.get("subcircuit_connectivity_map_key") or "")
        if key:
            for field in ("source_net_id", "source_trace_id", "source_port_id"):
                element_id = str(element.get(field) or "")
                if element_id:
                    source_keys[element_id] = key
    for element in circuit_json:
        if not isinstance(element, dict) or element.get("type") != "pcb_port":
            continue
        pcb_port_id = str(element.get("pcb_port_id") or "")
        source_port_id = str(element.get("source_port_id") or "")
        key = str(element.get("subcircuit_connectivity_map_key") or "")
        if not key:
            key = source_keys.get(source_port_id, "")
        if pcb_port_id and key:
            pcb_port_keys[pcb_port_id] = key

    def element_key(element: dict, trace_keys: dict[str, str]) -> str:
        direct = str(element.get("subcircuit_connectivity_map_key") or "")
        if direct:
            return direct
        pcb_port_id = str(element.get("pcb_port_id") or "")
        if pcb_port_id in pcb_port_keys:
            return pcb_port_keys[pcb_port_id]
        for field in (
            "connection_name", "source_net_id", "source_trace_id", "source_port_id"
        ):
            candidate = str(element.get(field) or "")
            if candidate in source_keys:
                return source_keys[candidate]
        pcb_trace_id = str(element.get("pcb_trace_id") or "")
        return trace_keys.get(pcb_trace_id, "")

    trace_keys: dict[str, str] = {}
    for element in circuit_json:
        if not isinstance(element, dict) or element.get("type") != "pcb_trace":
            continue
        trace_id = str(element.get("pcb_trace_id") or "")
        key = element_key(element, {})
        if trace_id and key:
            trace_keys[trace_id] = key

    drills: list[dict] = []
    for index, element in enumerate(circuit_json):
        if not isinstance(element, dict):
            continue
        etype = str(element.get("type") or "")
        if etype not in {"pcb_hole", "pcb_plated_hole", "pcb_via"}:
            continue
        geometry = _drill_stadium(element)
        if geometry is None:
            continue
        x = _finite_number(element.get("x"))
        y = _finite_number(element.get("y"))
        if x is None or y is None:
            continue
        is_via = etype == "pcb_via"
        plated = etype in {"pcb_plated_hole", "pcb_via"}
        if is_via:
            floor = profile.min_via_to_copper_mm
            warn_floor = None
            kind = "via drill"
        elif plated:
            floor = profile.min_pth_to_copper_mm
            warn_floor = profile.warn_pth_to_copper_mm
            kind = "plated slot" if math.dist(geometry[0], geometry[1]) > 1e-12 else "plated hole"
        else:
            floor = profile.min_npth_to_copper_mm
            warn_floor = None
            kind = "mounting slot" if math.dist(geometry[0], geometry[1]) > 1e-12 else "mounting hole"
        element_id = str(
            element.get("pcb_via_id")
            or element.get("pcb_plated_hole_id")
            or element.get("pcb_hole_id")
            or f"drill_{index}"
        )
        pcb_port_id = str(element.get("pcb_port_id") or "")
        drills.append({
            "id": element_id,
            "element": element,
            "shape": geometry,
            "own_pad": _own_pad_stadium(element),
            "key": element_key(element, trace_keys),
            "port_id": pcb_port_id,
            "trace_id": str(element.get("pcb_trace_id") or ""),
            "floor": floor,
            "warn_floor": warn_floor,
            "kind": kind,
            "is_via": is_via,
            "center": (x, y),
        })
    if not drills:
        return []

    copper: list[dict] = []
    for index, element in enumerate(circuit_json):
        if not isinstance(element, dict):
            continue
        etype = str(element.get("type") or "")
        key = element_key(element, trace_keys)
        if etype == "pcb_trace":
            trace_id = str(element.get("pcb_trace_id") or f"trace_{index}")
            connected_ports = {
                str(port_id)
                for port_id in (element.get("connectsTo") or [])
                if port_id
            }
            route = [point for point in (element.get("route") or []) if isinstance(point, dict)]
            for segment_index, (first, second) in enumerate(zip(route, route[1:])):
                first_x = _finite_number(first.get("x"))
                first_y = _finite_number(first.get("y"))
                second_x = _finite_number(second.get("x"))
                second_y = _finite_number(second.get("y"))
                if None in (first_x, first_y, second_x, second_y):
                    continue
                widths = [
                    _finite_number(point.get("width"))
                    for point in (first, second)
                    if point.get("route_type") == "wire"
                ]
                widths = [width for width in widths if width is not None and width > 0]
                if not widths:
                    continue
                shape: tuple[str, object] = (
                    "stadium",
                    (
                        (float(first_x), float(first_y)),
                        (float(second_x), float(second_y)),
                        max(widths) / 2.0,
                    ),
                )
                copper.append({
                    "id": trace_id,
                    "segment": segment_index,
                    "element": element,
                    "shape": shape,
                    "key": key,
                    "port_id": "",
                    "trace_id": trace_id,
                    "connected_ports": connected_ports,
                    "kind": "track",
                    "label": f"track {trace_id}",
                })
        elif etype == "pcb_smtpad":
            shape = _smt_copper_shape(element)
            if shape is None:
                continue
            pad_id = str(element.get("pcb_smtpad_id") or f"pad_{index}")
            copper.append({
                "id": pad_id,
                "element": element,
                "shape": shape,
                "key": key,
                "port_id": str(element.get("pcb_port_id") or ""),
                "trace_id": "",
                "connected_ports": set(),
                "kind": "SMD pad",
                "label": f"SMD pad {pad_id}",
            })
        elif etype == "pcb_via":
            x = _finite_number(element.get("x"))
            y = _finite_number(element.get("y"))
            diameter = _finite_number(element.get("outer_diameter"))
            if x is None or y is None or diameter is None or diameter <= 0:
                continue
            via_id = str(element.get("pcb_via_id") or f"via_{index}")
            copper.append({
                "id": via_id,
                "element": element,
                "shape": ("stadium", ((x, y), (x, y), diameter / 2.0)),
                "key": key,
                "port_id": str(element.get("pcb_port_id") or ""),
                "trace_id": str(element.get("pcb_trace_id") or ""),
                "connected_ports": set(),
                "kind": "via pad",
                "label": f"via pad {via_id}",
            })

    closest: dict[tuple[str, str], tuple[float, dict, dict]] = {}
    for drill in drills:
        for conductor in copper:
            # A via's drill and annular pad are one physical feature.
            if drill["element"] is conductor["element"]:
                continue
            drill_key = str(drill["key"] or "")
            copper_key = str(conductor["key"] or "")
            known_different = bool(
                drill_key and copper_key and drill_key != copper_key
            )
            if drill_key and copper_key and drill_key == copper_key:
                continue
            if (
                drill["port_id"]
                and drill["port_id"] == conductor["port_id"]
            ):
                continue
            if (
                conductor["kind"] == "track"
                and drill["port_id"]
                and drill["port_id"] in conductor["connected_ports"]
            ):
                continue
            if (
                drill["is_via"]
                and conductor["kind"] == "track"
                and drill["trace_id"]
                and drill["trace_id"] == conductor["trace_id"]
            ):
                continue
            if (
                not drill_key
                and not copper_key
                and drill["own_pad"] is not None
                and _shape_inside_stadium(conductor["shape"], drill["own_pad"])
            ):
                continue
            # Legacy artifacts sometimes omit every connectivity identifier.
            # Preserve an explicit trace endpoint landing in a PTH as the
            # intended connection, but never apply this ambiguity exemption
            # when the artifact proves the nets are different.
            if (
                not known_different
                and not drill["is_via"]
                and conductor["kind"] == "track"
            ):
                trace_first, trace_second, _ = conductor["shape"][1]
                drill_first, drill_second, drill_radius = drill["shape"]
                if min(
                    _point_segment_distance(
                        trace_first, drill_first, drill_second
                    ),
                    _point_segment_distance(
                        trace_second, drill_first, drill_second
                    ),
                ) <= drill_radius + 0.05:
                    continue

            gap = _shape_gap(drill["shape"], conductor["shape"])
            if not math.isfinite(gap):
                continue
            if drill["is_via"] and conductor["kind"] == "via pad":
                via_ids = sorted((str(drill["id"]), str(conductor["id"])))
                pair = (f"via:{via_ids[0]}", f"via:{via_ids[1]}")
            else:
                pair = (str(drill["id"]), str(conductor["id"]))
            prior = closest.get(pair)
            if prior is None or gap < prior[0]:
                closest[pair] = (gap, drill, conductor)

    warnings: list[Warning] = []
    for gap, drill, conductor in closest.values():
        floor = float(drill["floor"])
        warn_floor = drill["warn_floor"]
        if gap >= floor - 1e-9 and (
            warn_floor is None or gap >= float(warn_floor) - 1e-9
        ):
            continue
        part = _localize(conductor["element"], names)
        if part == "board":
            part = _localize(drill["element"], names)
        x, y = drill["center"]
        if gap < floor - 1e-9:
            warnings.append(_warning(
                part,
                "dfm_hole_clearance",
                f"{conductor['label']} is {gap:.3f}mm from {drill['kind']} "
                f"{drill['id']} at ({x:.2f}, {y:.2f}); the fab needs "
                f"{floor:g}mm — move the copper or drill",
                "error",
            ))
        else:
            warnings.append(_warning(
                part,
                "dfm_hole_clearance",
                f"{conductor['label']} is {gap:.3f}mm from {drill['kind']} "
                f"{drill['id']} at ({x:.2f}, {y:.2f}) — legal, but "
                f"{float(warn_floor):g}mm is the recommended margin",
                "warning",
            ))
    return warnings


def _trace_endpoint_layer_warnings(
    circuit_json: Sequence[dict], names: dict
) -> list[Warning]:
    """Catch a routed trace that only *geometrically* reaches an SMD pad.

    A bottom-copper segment ending at the coordinates of a top-only pad looks
    connected in a 2-D renderer, but there is no copper path between them.  The
    hydrate-coaster's V5 rail reached a USB-C VBUS pad exactly this way; a later
    router attempt hid the open by putting a via inside the pad.  Detect the
    layer mismatch directly, before the KiCad conversion is available.
    """
    port_layers = {
        str(element.get("pcb_port_id")): {
            str(layer) for layer in (element.get("layers") or []) if layer
        }
        for element in circuit_json
        if element.get("type") == "pcb_port" and element.get("pcb_port_id")
    }
    warnings: list[Warning] = []
    seen: set[tuple[str, str]] = set()
    for element in circuit_json:
        if element.get("type") != "pcb_trace":
            continue
        trace_id = str(element.get("pcb_trace_id") or "trace")
        route = [point for point in (element.get("route") or []) if isinstance(point, dict)]
        for point in route:
            endpoint = ""
            port_id = ""
            if point.get("start_pcb_port_id"):
                endpoint = "start"
                port_id = str(point["start_pcb_port_id"])
            elif point.get("end_pcb_port_id"):
                endpoint = "end"
                port_id = str(point["end_pcb_port_id"])
            if not port_id or point.get("route_type") != "wire":
                continue
            allowed = port_layers.get(port_id) or set()
            layer = str(point.get("layer") or "")
            key = (trace_id, port_id)
            if allowed and layer and layer not in allowed and key not in seen:
                seen.add(key)
                warnings.append(_warning(
                    _localize(element, names),
                    "pcb_trace_endpoint_layer_mismatch",
                    f"the {endpoint} of {trace_id} reaches {port_id} on {layer}, "
                    f"but that pad only has copper on {', '.join(sorted(allowed))}; "
                    "the coordinates touch but the net is electrically open",
                    "error",
                ))
    return warnings


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
        warnings.extend(_trace_endpoint_layer_warnings(circuit_json, names))
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
                x = element.get("x")
                y = element.get("y")
                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    continue
                if etype == "pcb_smtpad":
                    half_w = float(element.get("width") or 0) / 2
                    half_h = float(element.get("height") or 0) / 2
                else:
                    radius = float(element.get("outer_diameter") or 0) / 2
                    half_w = half_h = radius
                margin = min(
                    (x - half_w) - board_rect[0],
                    (y - half_h) - board_rect[1],
                    board_rect[2] - (x + half_w),
                    board_rect[3] - (y + half_h),
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


def bom_gate(
    rows: list[dict],
    *,
    assembly: bool,
    parts_lock: dict[str, dict] | None = None,
) -> list[Warning]:
    """Orderability + exact lock agreement.

    ``part_not_orderable`` blocks assembly packets (error) and merely advises
    bare-PCB ones (info). ``part_drift`` blocks assembly when the compiled BOM
    disagrees with a matched ``parts.json`` identity. ``part_lock_stale``
    blocks an assembly lock entry that names no populated compiled BOM row;
    a compiled DNP is therefore stale too: an assembly parts lock describes
    populated supplier identities, not every source component. ``extended_part``
    advises about the loading fee. Never raises.
    """
    try:
        warnings: list[Warning] = []
        matched_lock_ids: set[str] = set()
        for row in rows:
            designator = str(row.get("designator") or "part")
            lcsc = str(row.get("lcsc") or "")
            lock = row.get("lock") if isinstance(row.get("lock"), dict) else None
            lock_id = str(row.get("lock_id") or "").strip()
            if lock_id:
                matched_lock_ids.add(lock_id.casefold())
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
                identity_matches = not (lcsc and locked_lcsc and lcsc != locked_lcsc)
                if not identity_matches:
                    warnings.append(
                        _warning(
                            designator,
                            "part_drift",
                            f"BOM resolved {lcsc} but parts.json locks {locked_lcsc} "
                            "— re-run parts-book or rebuild before ordering",
                            "error" if assembly else "warning",
                        )
                    )
                if identity_matches and lock.get("basic") is False:
                    warnings.append(
                        _warning(
                            designator,
                            "extended_part",
                            f"{lcsc or 'part'} is an Extended part "
                            "(~$3/line loading fee at JLCPCB)",
                            "info",
                        )
                    )
        if parts_lock is not None:
            if not isinstance(parts_lock, dict):
                raise TypeError("parts_lock must be a dict")
            seen_lock_ids: set[str] = set()
            for raw_part_id in parts_lock:
                part_id = str(raw_part_id).strip()
                folded = part_id.casefold()
                if folded in seen_lock_ids:
                    warnings.append(
                        _warning(
                            part_id or "part",
                            "part_lock_ambiguous",
                            f"parts.json contains more than one case-insensitive "
                            f"identity for {part_id or 'an empty ref'} — regenerate "
                            "a unique exact-ref lock before ordering",
                            "error" if assembly else "info",
                        )
                    )
                    continue
                seen_lock_ids.add(folded)
                if part_id and folded in matched_lock_ids:
                    continue
                warnings.append(
                    _warning(
                        part_id or "part",
                        "part_lock_stale",
                        f"parts.json locks {part_id or 'an empty ref'} but the compiled "
                        "populated BOM has no matching designator — regenerate the "
                        "parts lock from the selected blocks before ordering",
                        "error" if assembly else "info",
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
