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
    warnings with kind = the finding's type (severity error — the library
    only reports genuine DRC failures). Never raises."""
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


def _hole_to_copper_warnings(
    circuit_json: Sequence[dict], names: dict, profile: FabProfile
) -> list[Warning]:
    """Tracks passing too near a drill.

    Two rules, not one (jlcpcb.com/capabilities, read 2026-08-11): a
    non-plated mounting hole needs 0.20mm to copper, a plated hole needs
    0.28mm. This is the check that caught the defect blocking every example
    board — the router threads a ground track through the 0.525mm channel
    beside a USB-C connector's alignment holes because it is the shortest
    path, leaving 0.115mm where 0.2mm is required. A drill lands within its
    own positional tolerance of that, so the track can simply be cut: some
    boards in the batch work and some do not, which is the worst way to fail.

    A track *terminating* at a hole is the connection to it, not a violation.
    """
    warnings: list[Warning] = []
    holes: list[tuple[float, float, float, bool]] = []
    for element in circuit_json:
        etype = element.get("type")
        if etype not in ("pcb_hole", "pcb_plated_hole"):
            continue
        x, y = element.get("x"), element.get("y")
        diameter = (
            element.get("hole_diameter")
            or element.get("hole_width")
            or element.get("outer_diameter")
        )
        if not all(isinstance(v, (int, float)) for v in (x, y, diameter)):
            continue
        holes.append((float(x), float(y), float(diameter) / 2.0,
                      etype == "pcb_plated_hole"))
    if not holes:
        return warnings

    seen: set[tuple[str, str]] = set()
    for element in circuit_json:
        if element.get("type") != "pcb_trace":
            continue
        route = [
            p for p in (element.get("route") or [])
            if isinstance(p, dict) and isinstance(p.get("x"), (int, float))
        ]
        for first, second in zip(route, route[1:]):
            x1, y1 = float(first["x"]), float(first["y"])
            x2, y2 = float(second["x"]), float(second["y"])
            half = float(first.get("width") or 0.2) / 2.0
            dx, dy = x2 - x1, y2 - y1
            length_sq = dx * dx + dy * dy
            for hx, hy, radius, plated in holes:
                # The segment that lands on a hole is its connection.
                if min(math.hypot(x1 - hx, y1 - hy),
                       math.hypot(x2 - hx, y2 - hy)) <= radius + 0.05:
                    continue
                t = 0.0 if length_sq == 0 else max(
                    0.0, min(1.0, ((hx - x1) * dx + (hy - y1) * dy) / length_sq)
                )
                gap = math.hypot(x1 + t * dx - hx, y1 + t * dy - hy) - radius - half
                floor = (
                    profile.min_pth_to_copper_mm if plated
                    else profile.min_npth_to_copper_mm
                )
                kind = "plated hole" if plated else "mounting hole"
                key = (f"{hx:.2f},{hy:.2f}", element.get("pcb_trace_id", ""))
                if gap < floor - 1e-9 and key not in seen:
                    seen.add(key)
                    warnings.append(_warning(
                        _localize(element, names),
                        "dfm_hole_clearance",
                        f"a track passes {gap:.3f}mm from a {kind} at "
                        f"({hx:.2f}, {hy:.2f}); the fab needs {floor:g}mm — "
                        "route around it, the drill's own tolerance can cut "
                        "a track this close",
                        "error",
                    ))
                elif (
                    plated
                    and gap < profile.warn_pth_to_copper_mm - 1e-9
                    and key not in seen
                ):
                    seen.add(key)
                    warnings.append(_warning(
                        _localize(element, names),
                        "dfm_hole_clearance",
                        f"a track passes {gap:.3f}mm from a plated hole at "
                        f"({hx:.2f}, {hy:.2f}) — legal, but "
                        f"{profile.warn_pth_to_copper_mm:g}mm is the "
                        "recommended margin",
                        "warning",
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
