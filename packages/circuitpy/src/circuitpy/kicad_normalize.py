"""Make the converted KiCad board obey the fab's floors before it is plotted.

**The defect this closes, and why it is a platform fix rather than a check.**
Measured 2026-08-11 on `harness-puck`: **all 54 silkscreen texts on the
converted board are under JLCPCB's 1.0mm minimum height, 39 of them at
0.267mm**, and not one carries an explicit stroke thickness — so KiCad derives
it from the size and plots 1145 strokes at 0.033mm against a 0.15mm floor.

That is not a property of these three boards. `circuit-json-to-kicad` produces
it for *every* board, so every board this tool has ever made, or will make,
arrives with silkscreen that prints broken or not at all — no reference
designators, nothing an EE can review or a human can rework against.

The converter is upstream and we do not control it. The `.kicad_pcb` between
the converter and `kicad-cli` is the one place every board passes through, so
that is where the floor gets applied: one change, the whole catalog, forever.
Detecting it in the gerbers (`verifylib.gerber_truth`) stays as the smoke
alarm that proves this kept working.

**What this deliberately does not do.** Setting KiCad's
`solder_mask_min_width` looked like the matching fix for thin mask webs, and it
was measured and rejected: it does not remove a single web. It changes how the
mask is *plotted* — 174 flashed apertures become 223 filled regions — and the
same geometry then measures 92 sub-0.2mm gaps instead of 10, purely because
adjacent contours of one merged opening are read as two. It also made the
gerber check eight times slower. A change that alters the representation and
not the board is a placebo, and shipping one would have been worse than
shipping nothing, because it would have looked like the defect was handled.

Everything here edits the s-expression as text, with a balanced-paren scan
rather than a bare regex, and every edit is counted and reported. A rewrite
nobody can audit is a rewrite nobody should trust.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from circuitpy.fab import FabProfile

#: KiCad silkscreen layer names, old and new spellings.
SILK_LAYERS = ("F.SilkS", "B.SilkS", "F.Silkscreen", "B.Silkscreen")

_TEXT_OPENERS = ("(fp_text", "(gr_text")
_GRAPHIC_OPENERS = ("(fp_line", "(gr_line", "(fp_rect", "(gr_rect",
                    "(fp_circle", "(gr_circle", "(fp_arc", "(gr_arc",
                    "(fp_poly", "(gr_poly")

_SIZE_RE = re.compile(r"\(size\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*\)")
_THICKNESS_RE = re.compile(r"\(thickness\s+([0-9.eE+-]+)\s*\)")
_WIDTH_RE = re.compile(r"\(width\s+([0-9.eE+-]+)\s*\)")
_FONT_OPEN_RE = re.compile(r"\(font\b")


@dataclass
class Normalization:
    """What was changed, so the pipeline can say it out loud."""

    text_resized: int = 0
    text_thickened: int = 0
    strokes_widened: int = 0
    vias_bridged: int = 0
    #: Pour zones whose triangle mesh was re-expressed as outlines, and the
    #: `filled_polygon` count before and after, so the size of the change is
    #: on the record rather than asserted.
    pours_outlined: int = 0
    pour_polygons_before: int = 0
    pour_polygons_after: int = 0
    smallest_text_mm: float | None = None
    smallest_stroke_mm: float | None = None
    #: A step of this pass could not run: a file that would not read, a parse
    #: that threw. Something the board was entitled to and did not get, which
    #: is why the pipeline reports these as `check_failed` and the app treats
    #: them as a reason not to ship.
    notes: list[str] = field(default_factory=list)
    #: A repair that ran, looked, and correctly chose to do nothing. Nothing
    #: is missing and nothing is unexamined — the condition it declined to
    #: touch is still measured by DRC, which is the gate that decides.
    #: Reporting these as failures put "a check could not finish" on boards
    #: that were `fab.ready` with zero errors (weather-badge-12 and -13,
    #: 2026-08-18), which is the app disagreeing with its own verdict.
    declined: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.text_resized or self.text_thickened or self.strokes_widened
            or self.vias_bridged or self.pours_outlined
        )

    def summary(self) -> str:
        parts = []
        if self.text_resized:
            parts.append(
                f"{self.text_resized} silkscreen text(s) raised to the "
                f"fab's minimum height"
                + (
                    f" (smallest was {self.smallest_text_mm:.3f}mm)"
                    if self.smallest_text_mm is not None
                    else ""
                )
            )
        if self.text_thickened:
            parts.append(f"{self.text_thickened} given an explicit stroke")
        if self.strokes_widened:
            parts.append(
                f"{self.strokes_widened} silkscreen stroke(s) widened"
                + (
                    f" (thinnest was {self.smallest_stroke_mm:.4f}mm)"
                    if self.smallest_stroke_mm is not None
                    else ""
                )
            )
        if self.vias_bridged:
            parts.append(
                f"{self.vias_bridged} missing via(s) added under a B.Cu "
                "dead-end at a top-only pad"
            )
        if self.pours_outlined:
            parts.append(
                f"{self.pours_outlined} pour zone(s) re-expressed as outlines "
                f"({self.pour_polygons_before} triangles -> "
                f"{self.pour_polygons_after} polygon(s))"
            )
        return "; ".join(parts)


def _balanced_spans(text: str, opener: str) -> list[tuple[int, int]]:
    """Every balanced ``(...)`` beginning with ``opener``.

    A regex cannot do this: an ``fp_text`` contains nested ``(effects (font
    (size ...))))`` and stopping at the first ``)`` would rewrite the wrong
    numbers. Paren counting is cheap and it is right.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        i = text.find(opener, start)
        if i < 0:
            return spans
        depth = 0
        for j in range(i, len(text)):
            char = text[j]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    spans.append((i, j + 1))
                    start = j + 1
                    break
        else:
            return spans  # unbalanced tail; leave the rest alone


def _on_silk(block: str) -> bool:
    return any(f'"{layer}"' in block or f"({layer}" in block or layer in block
               for layer in SILK_LAYERS)


def _raise_text(block: str, min_height: float, min_thickness: float,
                result: Normalization) -> str:
    match = _SIZE_RE.search(block)
    if match is None:
        return block
    width = float(match.group(1))
    height = float(match.group(2))
    smallest = min(width, height)
    if result.smallest_text_mm is None or smallest < result.smallest_text_mm:
        result.smallest_text_mm = smallest
    if smallest < min_height - 1e-9:
        scale = min_height / smallest if smallest > 0 else 1.0
        block = (
            block[: match.start()]
            + f"(size {width * scale:.6g} {height * scale:.6g})"
            + block[match.end():]
        )
        result.text_resized += 1

    thickness_match = _THICKNESS_RE.search(block)
    if thickness_match is None:
        # No explicit thickness: KiCad derives one from the size and lands
        # well under the floor. Insert it right after `(font`.
        font = _FONT_OPEN_RE.search(block)
        if font is not None:
            insert_at = font.end()
            block = (
                block[:insert_at]
                + f"\n          (thickness {min_thickness:.6g})"
                + block[insert_at:]
            )
            result.text_thickened += 1
    elif float(thickness_match.group(1)) < min_thickness - 1e-9:
        block = (
            block[: thickness_match.start()]
            + f"(thickness {min_thickness:.6g})"
            + block[thickness_match.end():]
        )
        result.text_thickened += 1
    return block


def _raise_stroke(block: str, min_width: float, result: Normalization) -> str:
    """Widen a silkscreen graphic's stroke.

    Only the innermost ``(width N)`` token is rewritten, never the ``(stroke``
    wrapper around it. KiCad writes both the modern
    ``(stroke (width 0.1) (type default))`` and the bare ``(width 0.1)``, and
    an earlier version of this matched across the wrapper and ate its closing
    paren — which produced a `.kicad_pcb` kicad-cli refused to load at all.
    Rewriting the leaf token is correct for both spellings.
    """
    width = _WIDTH_RE.search(block)
    if width is None:
        return block
    value = float(width.group(1))
    if result.smallest_stroke_mm is None or value < result.smallest_stroke_mm:
        result.smallest_stroke_mm = value
    if value >= min_width - 1e-9 or value <= 0:
        return block
    result.strokes_widened += 1
    return (
        block[: width.start()] + f"(width {min_width:.6g})" + block[width.end():]
    )


def normalize_for_fab(pcb_path: Path, profile: FabProfile) -> Normalization:
    """Apply the fab's silkscreen and solder-mask floors to a ``.kicad_pcb``.

    Idempotent: run twice and the second run changes nothing. Returns what it
    did, and never raises — a normaliser that takes the build down is worse
    than a board with small text.
    """
    result = Normalization()
    try:
        text = pcb_path.read_text(encoding="utf-8")
    except OSError as exc:
        result.notes.append(f"could not read {pcb_path.name}: {exc}")
        return result

    original = text
    try:
        # Rewrite from the end so earlier spans keep their offsets.
        spans: list[tuple[int, int, str]] = []
        for opener in _TEXT_OPENERS:
            spans.extend((a, b, "text") for a, b in _balanced_spans(text, opener))
        for opener in _GRAPHIC_OPENERS:
            spans.extend((a, b, "graphic") for a, b in _balanced_spans(text, opener))
        for start, end, kind in sorted(spans, reverse=True):
            block = text[start:end]
            if not _on_silk(block):
                continue
            if kind == "text":
                block = _raise_text(
                    block,
                    profile.min_silk_text_mm,
                    profile.min_silk_line_mm,
                    result,
                )
            else:
                block = _raise_stroke(block, profile.min_silk_line_mm, result)
            text = text[:start] + block + text[end:]

        # Second pass: bridge B.Cu dead-ends that stop under a top-only pad.
        # Runs after (not instead of) the silk floors; both edit the same file.
        text = _fix_dead_end_vias(text, profile, result)

        # Third pass: re-express a triangulated pour as its outline. Runs LAST
        # on purpose — the via pass reads `filled_polygon` for clearance, and
        # it must keep seeing exactly the geometry it sees today.
        text = _outline_pours(text, result)

        if text != original:
            pcb_path.write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(
            f"normalisation aborted ({type(exc).__name__}: {exc}); the board "
            "was left exactly as the converter produced it"
        )
        try:
            pcb_path.write_text(original, encoding="utf-8")
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# Route dead-ends: a B.Cu net that stops under a top-only pad needs a via.
# ---------------------------------------------------------------------------
#
# Measured 2026-08-13 on harness-puck: the router ran two nets (LED_DATA,
# V5) to the end of their run on B.Cu, straight under an F.Cu-only SMD pad,
# and never added the final via. KiCad reports each as an
# ``unconnected_items`` DRC error — the board is electrically right but the
# copper never reaches the pad, so no fab will build it.
#
# This is not a property we can fix by re-running the router: every source
# edit that perturbs the route moves the same class of defect elsewhere (the
# router is at its convergence ceiling; see DESIGN-REVIEW.md rounds 8-11).
# The `.kicad_pcb` between the converter and ``kicad-cli`` is the one place
# every board passes through and the route is already settled, so that is
# where the missing via gets added: one change here repairs the whole
# catalogue of nets that do the same thing, without the router ever running
# again. Like the silk fix above, it is text surgery with a balanced-paren
# scan, it is counted and reported, and it is idempotent.
#
# Placement is adaptive on purpose: the dead-end often sits in a tight
# corridor (harness-puck's LED_DATA pad is 0.1mm from a neighbour's track), so
# a via parked exactly on the dead-end can violate copper/hole clearance. Each
# candidate via is checked against every pad, track and via of the other nets
# before it is accepted, and the center is nudged around the pad until the
# largest via that fits is found. A dead-end with no safe placement is left
# alone (and noted) rather than repaired with a via that shorts the board.

import math
from dataclasses import dataclass

#: (via pad diameter, via drill diameter) — the converter's own vocabulary,
#: ordered larger-first so the fattest via that fits wins.
_VIA_CANDIDATES = ((0.6, 0.3), (0.4, 0.2), (0.3, 0.15))
_NUDGE_STEPS = (0.08, 0.16, 0.24, 0.32)
_HALF_SQRT2 = math.sqrt(0.5)


@dataclass(frozen=True)
class _PadGeom:
    ref: str
    num: str
    net: str
    cx: float
    cy: float
    w: float
    h: float
    fcu: bool
    bcu: bool


@dataclass(frozen=True)
class _SegGeom:
    x0: float
    y0: float
    x1: float
    y1: float
    layer: str
    net: str
    width: float


@dataclass(frozen=True)
class _ViaGeom:
    x: float
    y: float
    drill: float
    net: str


def _point_seg_dist(px: float, py: float, s: _SegGeom) -> float:
    """Distance from a point to a finite segment."""
    dx = s.x1 - s.x0
    dy = s.y1 - s.y0
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-12:
        return math.hypot(px - s.x0, py - s.y0)
    t = ((px - s.x0) * dx + (py - s.y0) * dy) / seg2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (s.x0 + t * dx), py - (s.y0 + t * dy))


def _point_rect_dist(px: float, py: float, p: _PadGeom) -> float:
    """Distance from a point to an axis-aligned pad rectangle (0 if inside)."""
    dx = max(abs(px - p.cx) - p.w / 2, 0.0)
    dy = max(abs(py - p.cy) - p.h / 2, 0.0)
    return math.hypot(dx, dy)


def _parse_number(text: str, tag: str) -> float | None:
    m = re.search(rf"\({tag}\s+([0-9.eE+-]+)", text)
    return float(m.group(1)) if m else None


def _parse_xy(text: str, tag: str) -> tuple[float, float] | None:
    m = re.search(rf"\({tag}\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", text)
    return (float(m.group(1)), float(m.group(2))) if m else None


def _parse_geometry(text: str) -> tuple[list[_PadGeom], list[_SegGeom], list[_ViaGeom]]:
    """Parse the pads, copper segments and vias a via-placement must respect."""
    pads: list[_PadGeom] = []
    segs: list[_SegGeom] = []
    vias: list[_ViaGeom] = []
    for fp_start, fp_end in _balanced_spans(text, "  (footprint"):
        fp = text[fp_start:fp_end]
        refm = re.search(r'\(property "Reference" "([^"]+)"', fp)
        ref = refm.group(1) if refm else "?"
        fpm = re.search(
            r"^\s*\(at\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)(?:\s+([0-9.eE+-]+))?\)",
            fp,
            re.M,
        )
        if fpm is None:
            continue
        fx, fy = float(fpm.group(1)), float(fpm.group(2))
        frot = float(fpm.group(3)) if fpm.group(3) else 0.0
        for pa, pb in _balanced_spans(fp, "  (pad"):
            blk = fp[pa:pb]
            nm = re.match(r"\s*\(pad\s+\"([^\"]+)\"\s+\w+\b", blk)
            at = _parse_xy(blk, "at")
            sz = re.search(r"\(size\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\)", blk)
            lay = re.search(r"\(layers\s+([^)]+)\)", blk)
            net = re.search(r'\(net\s+(\d+)\s+"[^"]*"\)', blk)
            if not (nm and at and sz and lay):
                continue
            width, height = float(sz.group(1)), float(sz.group(2))
            layers = lay.group(1).split()
            # Rotate the pad center into the board frame (axis-aligned bbox
            # under rotation, so the pad's own rotation is honored safely).
            lx, ly = at
            if frot:
                r = math.radians(frot)
                lx, ly = lx * math.cos(r) - ly * math.sin(r), \
                        lx * math.sin(r) + ly * math.cos(r)
            rot = abs(frot) % 360.0
            if abs(rot) > 0.01 and (abs(rot - 90) > 0.01 and abs(rot - 180) > 0.01
                                    and abs(rot - 270) > 0.01):
                # Arbitrary rotation: use the rotated bounding box.
                hw, hh = width / 2, height / 2
                r = math.radians(frot)
                c, s = abs(math.cos(r)), abs(math.sin(r))
                width, height = 2 * (hw * c + hh * s), 2 * (hw * s + hh * c)
            # A pad with no net is still copper (an unused pin); it has to take
            # part in clearance checks even though it can never be a mate.
            net_num = net.group(1) if net else "0"
            pads.append(_PadGeom(
                ref, nm.group(1), net_num, fx + lx, fy + ly,
                width, height, "F.Cu" in layers, "B.Cu" in layers,
            ))
    for ga, gb in _balanced_spans(text, "  (segment"):
        blk = text[ga:gb]
        st = _parse_xy(blk, "start")
        en = _parse_xy(blk, "end")
        layer = re.search(r"\(layer\s+(\S+)\)", blk)
        net = re.search(r"\(net\s+(\d+)\)", blk)
        w = _parse_number(blk, "width")
        if st and en and layer and net and w is not None:
            segs.append(_SegGeom(
                st[0], st[1], en[0], en[1], layer.group(1), net.group(1), w,
            ))
    for va, vb in _balanced_spans(text, "  (via"):
        blk = text[va:vb]
        at = _parse_xy(blk, "at")
        net = re.search(r"\(net\s+(\d+)\)", blk)
        drill = _parse_number(blk, "drill")
        if at and net and drill is not None:
            vias.append(_ViaGeom(at[0], at[1], drill, net.group(1)))
    return pads, segs, vias


def _candidate_centers(ex: float, ey: float, p: _PadGeom) -> list[tuple[float, float]]:
    """Dead-end first, pad center next, then radial nudges — deduped."""
    cands: list[tuple[float, float]] = [(ex, ey), (p.cx, p.cy)]
    for dx, dy in (
        (1, 0), (-1, 0), (0, 1), (0, -1),
        (_HALF_SQRT2, _HALF_SQRT2), (_HALF_SQRT2, -_HALF_SQRT2),
        (-_HALF_SQRT2, _HALF_SQRT2), (-_HALF_SQRT2, -_HALF_SQRT2),
    ):
        for s in _NUDGE_STEPS:
            cands.append((ex + dx * s, ey + dy * s))
    out: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for cx, cy in cands:
        key = (round(cx, 4), round(cy, 4))
        if key not in seen:
            seen.add(key)
            out.append((cx, cy))
    return out


def _zone_fills(text: str) -> list[tuple[str, list[tuple[float, float]]]]:
    """Every filled zone polygon in the board, as ``(net, points)``.

    A pour is copper. Leaving it out of the feasibility test is how the
    via-bridge repair — which exists to *fix* a clearance problem — created
    one: on terminal-keyboard it added three vias and landed one **0.09807mm**
    from the ground plane against the 0.15mm clearance the zone declares about
    itself, and that was the last blocking finding on the board.

    Ledger #11 said this in so many words after a via-nudge script that
    validated new positions against pads only and produced six shorts. Same
    lesson, one kind of copper further out: *all* the copper, or none of it.
    """
    out: list[tuple[str, list[tuple[float, float]]]] = []
    for start, end in _balanced_spans(text, "(zone"):
        block = text[start:end]
        match = re.search(r'\(net_name\s+"?([^")\s]*)"?\s*\)', block)
        net = match.group(1) if match else ""
        for fill_start, fill_end in _balanced_spans(block, "(filled_polygon"):
            pts = [
                (float(a), float(b))
                for a, b in re.findall(
                    r"\(xy ([-\d.eE+]+) ([-\d.eE+]+)\)", block[fill_start:fill_end]
                )
            ]
            if len(pts) >= 3:
                out.append((net, pts))
    return out


def _point_poly_dist(px: float, py: float,
                     pts: list[tuple[float, float]]) -> float:
    """Distance from a point to a polygon's boundary; 0 if it is inside."""
    inside = False
    n = len(pts)
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i - 1) % n]
        if (ay > py) != (by > py) and px < (bx - ax) * (py - ay) / (by - ay) + ax:
            inside = not inside
    if inside:
        return 0.0
    best = math.inf
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, (
            (px - ax) * dx + (py - ay) * dy) / length))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def _via_feasible(
    cx: float,
    cy: float,
    size: float,
    drill: float,
    net: str,
    pad: _PadGeom,
    seg: _SegGeom,
    pads: list[_PadGeom],
    segs: list[_SegGeom],
    vias: list[_ViaGeom],
    clearance: float,
    hole_clear: float,
    hole_to_hole: float,
    zones: list[tuple[str, list[tuple[float, float]]]] | None = None,
    zone_clearance: float = 0.0,
) -> bool:
    """True if a via of this size at this center heals the dead-end safely."""
    radius = size / 2
    drill_r = drill / 2
    if _point_rect_dist(cx, cy, pad) > radius - 1e-6:
        return False  # must touch the pad it is bridging to
    if _point_seg_dist(cx, cy, seg) > radius - 1e-6:
        return False  # must touch the B.Cu track it is bridging from
    for other in pads:
        if other.net == net:
            continue
        d = _point_rect_dist(cx, cy, other)
        if d - radius < clearance - 1e-9 or d - drill_r < hole_clear - 1e-9:
            return False
    for other in segs:
        if other.net == net:
            continue
        d = _point_seg_dist(cx, cy, other)
        halfw = other.width / 2
        if (d - radius - halfw < clearance - 1e-9
                or d - drill_r - halfw < hole_clear - 1e-9):
            return False
    for via in vias:
        d = math.hypot(cx - via.x, cy - via.y)
        if d - drill_r - via.drill / 2 < hole_to_hole - 1e-9:
            return False
    # The pour. A via placed to heal one clearance problem must not create
    # another against the copper that covers most of the board.
    for zone_net, pts in zones or ():
        if zone_net == net:
            continue
        d = _point_poly_dist(cx, cy, pts)
        if d - radius < zone_clearance - 1e-9 or d - drill_r < hole_clear - 1e-9:
            return False
    return True


def _via_text(cx: float, cy: float, size: float, drill: float, net: str) -> str:
    return (
        f"  (via\n    (at {cx:.9g} {cy:.9g})\n    (size {size:.6g})\n"
        f"    (drill {drill:.6g})\n    (layers F.Cu B.Cu)\n"
        f"    (net {net})\n    (uuid {uuid.uuid4()})\n  )\n"
    )


def _fix_dead_end_vias(text: str, profile: FabProfile, result: Normalization) -> str:
    """Insert the final via under every B.Cu dead-end that stops at a top-only
    pad of the same net. Returns the text; never raises."""
    clearance = profile.min_clearance_mm - profile.drc_tolerance_mm
    hole_clear = 0.2  # kicad_project_json's min_hole_clearance
    hole_to_hole = 0.2  # kicad_project_json's min_hole_to_hole
    # A zone is held to the clearance it declares about itself, which the
    # tscircuit converter writes and which is stricter than the board's
    # netclass. Satisfying the looser one is how a via passes this test and
    # fails the DRC on the very packet this function is preparing.
    zone_clearance = max(
        clearance, float(getattr(profile, "kicad_zone_clearance_mm", 0.15) or 0.15)
    )
    try:
        zones = _zone_fills(text)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(
            f"via-bridge could not read the copper pours "
            f"({type(exc).__name__}: {exc}); no vias were added, because "
            "placing one without seeing the plane is how #11 happened"
        )
        return text
    try:
        pads, segs, vias = _parse_geometry(text)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(
            f"via-bridge parse failed ({type(exc).__name__}: {exc}); "
            "no vias were added"
        )
        return text

    top_only = [p for p in pads if p.fcu and not p.bcu]
    placements: list[tuple[float, float, float, float, str]] = []
    skipped: set[str] = set()
    for seg in segs:
        if seg.layer != "B.Cu":
            continue
        for ex, ey in ((seg.x0, seg.y0), (seg.x1, seg.y1)):
            mate = next((p for p in top_only if p.net == seg.net
                         and _point_rect_dist(ex, ey, p) <= 1e-6), None)
            if mate is None:
                continue
            if any(p.net != seg.net and _point_rect_dist(ex, ey, p) <= 1e-6
                   for p in pads):
                skipped.add(seg.net)
                continue
            if any(math.hypot(ex - v.x, ey - v.y) < 0.2 for v in vias):
                continue  # a via already bridges this dead-end
            placed = False
            for cx, cy in _candidate_centers(ex, ey, mate):
                for size, drill in _VIA_CANDIDATES:
                    if _via_feasible(cx, cy, size, drill, seg.net, mate, seg,
                                     pads, segs, vias, clearance,
                                     hole_clear, hole_to_hole,
                                     zones, zone_clearance):
                        placements.append((cx, cy, size, drill, seg.net))
                        vias.append(_ViaGeom(cx, cy, drill, seg.net))
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                skipped.add(seg.net)
    if skipped:
        # Declined, not failed. There was no via position that cleared every
        # obstacle, so none was invented — the posture #11 was written in
        # blood for. The dead-end stays, DRC measures it, and DRC decides.
        result.declined.append(
            "the dead-end(s) on net(s) "
            f"{', '.join(sorted(skipped))} were left as the router laid them: "
            "no via position clears every pad, trace, via and pour at once, "
            "and a via placed without that margin is how a repair pass "
            "creates the clearance violation it exists to remove. DRC sees "
            "the same copper and reports it there if it matters"
        )
    if not placements:
        return text

    keyed = {(round(cx, 6), round(cy, 6), net) for cx, cy, _, _, net in placements}
    body = ""
    for cx, cy, size, drill, net in placements:
        if (round(cx, 6), round(cy, 6), net) not in keyed:
            continue
        keyed.discard((round(cx, 6), round(cy, 6), net))
        body += _via_text(cx, cy, size, drill, net)
        result.vias_bridged += 1

    insert_at = len(text.rstrip())
    # Prefer sitting next to the router's own vias; fall back to the file end.
    for va, vb in _balanced_spans(text, "  (via"):
        insert_at = vb
    text = text[:insert_at] + "\n" + body + text[insert_at:]
    return text


# ---------------------------------------------------------------------------
# Pours: the converter writes a plane as a triangle mesh; KiCad wants outlines.
# ---------------------------------------------------------------------------
#
# Measured 2026-08-19 across the corpus: `circuit-json-to-kicad` writes a copper
# pour as a **triangle mesh** — one 3-vertex `filled_polygon` per triangle, 2403
# of them on pixel-badge, 2423 on weather-badge-15. The copper those triangles
# describe is continuous; union them across shared edges and pixel-badge's plane
# is one region of 4179.79mm2. The mesh is the only thing that is in pieces.
#
# That representation is not cosmetic. It costs three separate things:
#
#   * KiCad's connectivity treats each triangle as its own island and calls 199
#     of them isolated — 2394 `isolated_copper` instances over 17 boards, the
#     largest warning-severity category in the corpus, every one of them noise.
#     `checks._remeasure_isolated_copper` unions the mesh to judge the rule
#     honestly; this removes the reason it has to.
#   * `kicad-cli pcb drc` **segfaults** on a board with two triangulated pours
#     (5800 polygons / 1.67MB, `exit=139`, reproduced 4/4). That is what blocks
#     pouring the top layer, and a crashed gate is the one failure shape this
#     pipeline must never have.
#   * The B.Cu gerber inflates to 457KB and 2639 G36 regions against 64KB on
#     F.Cu — a real CAM risk even when the copper is fine.
#
# One defect, three symptoms. The converter is upstream and we do not control
# it, so the fix goes where the silk floors and the dead-end vias already go:
# the `.kicad_pcb` between the converter and `kicad-cli`, the one file every
# board passes through.
#
# **This changes the spelling and not the copper, and that distinction is
# load-bearing.** The rejected `solder_mask_min_width` change (see the module
# docstring) altered a representation while leaving the geometry untouched and
# made a check *worse* — a placebo. Here the representation is precisely what
# segfaults kicad-cli and what KiCad's island analysis mis-reads. So the fix is
# held to the strictest possible faithfulness bar: the fractured outlines must
# reproduce the mesh's area **exactly, in integer nanometres**, or the zone is
# left exactly as the converter produced it. Everything is quantised to nm
# (KiCad's own internal unit, and what six decimal places of mm already spell),
# so "exactly" means exactly and not within a tolerance.
#
# **The convention this imitates is KiCad's own.** Asked to refill this very
# board, `kicad-cli pcb drc --refill-zones --save-board` writes a pour with
# holes as a *single* `filled_polygon`: 4253 vertices carrying 38 repeated ones,
# which is 19 keyhole slits. A hole is not a separate element in this format; it
# is cut into the outline by a zero-width corridor. So that is what gets
# written here. (Refilling was measured as the alternative fix and rejected:
# KiCad reads the converter's 24-polygon zone outline differently and left the
# main plane with *no fill at all*. It also hands the fill computation to KiCad,
# which would move copper the netlist comparison just proved faithful.)
#
# A zone that is not a mesh is left to KiCad entirely — on that shape KiCad's
# own island analysis is the right one, exactly as `_remeasure_isolated_copper`
# already decided.

#: Nanometres per millimetre. Every pour coordinate is quantised to this grid
#: before any geometry runs, so unions, containment and area are integer exact.
#: The converter already writes six decimal places of mm, which is this grid.
_POUR_SCALE = 1_000_000

_XY_RE = re.compile(r"\(xy\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\s*\)")
_LAYER_RE = re.compile(r"\(layer\s+\"?([^)\"\s]+)\"?\s*\)")


def _ring_area2(ring: list[tuple[int, int]]) -> int:
    """Twice the signed area of a ring. Positive is counter-clockwise."""
    total = 0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return total


def _mesh_rings(
    triangles: list[list[tuple[int, int]]],
) -> list[list[tuple[int, int]]] | None:
    """Union a triangle mesh into its boundary rings, or ``None`` if it is not
    a clean mesh.

    Pure combinatorics, no floating point on the copper and no boolean
    geometry: a triangle edge shared by two triangles is interior, an edge that
    appears once is boundary. Orient every triangle counter-clockwise first and
    the boundary edges chain head-to-tail into closed rings — outer boundaries
    come out counter-clockwise (positive area), holes clockwise (negative).

    **Pinch points are the case that makes this more than a walk.** A poured
    plane routinely squeezes to nothing between two pads and touches itself at
    a single vertex — 21 times on weather-badge-16's top pour, which is the
    board that blocks #15. At such a vertex two boundary chains arrive and two
    leave, and pairing them wrongly stitches two separate regions into one
    figure-of-eight. The pairing is decided by direction, not by chance: a
    boundary keeps copper on its left, so the wedge of copper at a vertex runs
    counter-clockwise from an outgoing edge round to an incoming one. Each
    chain therefore continues along the first outgoing edge found rotating
    **clockwise** from the one it arrived on.

    **A collapsed triangle is dropped, not declined.** The converter emits a
    few triangles whose three points are collinear — 3 of 2325 on
    `rgb-lamp-controller`, strung along one diagonal. They enclose no copper,
    so removing them cannot move the plane, and the caller's exact-area check
    still has to account for the result either way. Refusing the zone over
    them cost that board its whole pour for nothing.

    Anything that is still not a manifold triangulation — two triangles
    covering the same ground, a vertex where more boundary leaves than arrives
    — returns ``None``, and the caller leaves the zone alone. Declining is
    free; a silently mangled plane is not.
    """
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for tri in triangles:
        if len(tri) != 3:
            return None
        if _ring_area2(tri) == 0:
            continue  # collapsed: three collinear points, no copper to union
        if _ring_area2(tri) < 0:
            tri = tri[::-1]
        for i in range(3):
            edge = (tri[i], tri[(i + 1) % 3])
            if edge in edges:
                return None  # two triangles wound the same way over one edge
            edges.add(edge)

    outgoing: dict[tuple[int, int], list[tuple[int, int]]] = {}
    incoming: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for tail, head in edges:
        if (head, tail) in edges:
            continue  # interior: some other triangle covers the far side
        outgoing.setdefault(tail, []).append(head)
        incoming.setdefault(head, []).append(tail)
    if not outgoing:
        return None

    successor: dict[
        tuple[tuple[int, int], tuple[int, int]],
        tuple[tuple[int, int], tuple[int, int]],
    ] = {}
    for vertex, leaving in outgoing.items():
        arriving = incoming.get(vertex, [])
        if len(arriving) != len(leaving):
            return None
        if len(leaving) == 1:
            successor[(arriving[0], vertex)] = (vertex, leaving[0])
            continue
        bearings = [
            math.atan2(q[1] - vertex[1], q[0] - vertex[0]) for q in leaving
        ]
        taken: set[int] = set()
        for tail in arriving:
            arrival = math.atan2(tail[1] - vertex[1], tail[0] - vertex[0])
            pick = None
            for j, bearing in enumerate(bearings):
                if j in taken:
                    continue
                turn = (arrival - bearing) % (2 * math.pi)
                if turn == 0.0:
                    turn = 2 * math.pi  # a zero-width spike doubles back
                if pick is None or turn < pick[0]:
                    pick = (turn, j)
            if pick is None:
                return None
            taken.add(pick[1])
            successor[(tail, vertex)] = (vertex, leaving[pick[1]])

    rings: list[list[tuple[int, int]]] = []
    walked: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for edge in successor:
        if edge in walked:
            continue
        ring: list[tuple[int, int]] = []
        cursor = edge
        while cursor not in walked:
            walked.add(cursor)
            ring.append(cursor[0])
            cursor = successor[cursor]
        if cursor != edge:
            return None  # walked into an earlier ring: not a set of loops
        rings.append(ring)
    if len(walked) != len(successor):
        return None
    return rings or None


def _ring_contains(outer: list[tuple[int, int]], ring: list[tuple[int, int]]) -> bool:
    """Is ``ring`` inside ``outer``?

    Voted over sampled edge midpoints rather than decided by one vertex: where
    a pour pinches, a hole can share a vertex with the region around it, and a
    single sample taken exactly on the boundary answers neither yes nor no.
    """
    step = max(1, len(ring) // 12)
    inside = total = 0
    for i in range(0, len(ring), step):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        total += 1
        if _point_in_ring(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), outer):
            inside += 1
    return inside * 2 > total


def _point_in_ring(
    point: tuple[float, float], ring: list[tuple[int, int]]
) -> bool:
    """Crossing-number containment. Points exactly on the ring are undefined,
    and never asked about here: every point tested is a bridge midpoint."""
    px, py = point
    inside = False
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        if (y1 > py) != (y2 > py):
            crossing = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < crossing:
                inside = not inside
    return inside


def _orient(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _between(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> bool:
    """``c`` lies strictly inside segment ``ab``, given it is already collinear."""
    if c == a or c == b:
        return False
    return (
        min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
    )


def _blocks(
    a: tuple[int, int], b: tuple[int, int],
    c: tuple[int, int], d: tuple[int, int],
) -> bool:
    """Does edge ``cd`` stop the bridge ``ab`` from being drawn?

    Sharing an endpoint is fine and expected — the bridge starts and ends on
    rings whose edges meet it there. Anything else that touches is not: a
    proper crossing, a collinear overlap, or an endpoint landing in the middle
    of the other segment would all put the slit somewhere it does not belong.
    """
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    if o1 == 0 and o2 == 0:
        # Collinear. Only an overlap of more than a single point is a problem.
        return _between(a, b, c) or _between(a, b, d) or _between(c, d, a) \
            or _between(c, d, b) or (a in (c, d) and b in (c, d))
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and o1 and o2 and o3 and o4:
        return True
    if o1 == 0 and _between(a, b, c):
        return True
    if o2 == 0 and _between(a, b, d):
        return True
    if o3 == 0 and _between(c, d, a):
        return True
    if o4 == 0 and _between(c, d, b):
        return True
    return False


def _is_reflex(ring: list[tuple[int, int]], index: int) -> bool:
    """Reflex, for a counter-clockwise ring. A collinear vertex counts as
    reflex: that is the conservative answer, and every candidate the caller
    takes from here is validated against the whole boundary anyway."""
    before = ring[index - 1]
    here = ring[index]
    after = ring[(index + 1) % len(ring)]
    return _orient(before, here, after) <= 0


def _in_triangle(
    point: tuple[int, int],
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float],
) -> bool:
    def side(p: tuple[float, float], q: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (point[1] - p[1]) - (q[1] - p[1]) * (point[0] - p[0])

    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _bridge_vertex(
    ring: list[tuple[int, int]], mouth: tuple[int, int]
) -> int | None:
    """Which vertex of ``ring`` the hole at ``mouth`` can be joined to.

    Eberly's hole-cutting rule, and the reason it is this rule rather than
    "the nearest vertex": the outline of a poured plane is often a bare
    rectangle, so the nearest *vertex* to a hole in the middle of the board is
    a far corner and the segment to it crosses every other hole on the way.
    Casting a ray instead is what makes the choice local.

    Cast +x from the hole's rightmost point, take the first edge it meets, and
    bridge to that edge's right-hand endpoint. If any reflex vertex of the
    outline sits inside the triangle those three points make, it is in the way,
    and the one at the shallowest angle from the ray takes its place.

    Holes are cut right-to-left, so nothing still waiting can lie to the right
    of the ray, and only the outline this walks is ever in the way.
    """
    mx, my = mouth
    hit_x: float | None = None
    hit_edge: int | None = None
    for i in range(len(ring)):
        a = ring[i]
        b = ring[(i + 1) % len(ring)]
        if (a[1] > my) == (b[1] > my):
            continue
        crossing = a[0] + (my - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        if crossing < mx:
            continue
        if hit_x is None or crossing < hit_x:
            hit_x, hit_edge = crossing, i
    if hit_edge is None:
        return None

    a, b = ring[hit_edge], ring[(hit_edge + 1) % len(ring)]
    chosen = hit_edge if a[0] >= b[0] else (hit_edge + 1) % len(ring)

    corner = (float(mx), float(my))
    entry = (hit_x, float(my))
    best: tuple[float, float] | None = None
    for i in range(len(ring)):
        vertex = ring[i]
        if vertex == mouth or i == chosen:
            continue
        if not _is_reflex(ring, i):
            continue
        if not _in_triangle(vertex, corner, entry, ring[chosen]):
            continue
        dx, dy = vertex[0] - mx, vertex[1] - my
        rank = (abs(math.atan2(dy, dx)), float(dx * dx + dy * dy))
        if best is None or rank < best:
            best, chosen = rank, i
    return chosen


def _fracture(
    outer: list[tuple[int, int]],
    holes: list[list[tuple[int, int]]],
) -> list[tuple[int, int]] | None:
    """Cut every hole into the outline with a zero-width keyhole slit.

    This is the shape KiCad's own filler writes (measured: 4253 vertices with
    19 repeated pairs on a refilled pixel-badge), because the format has no way
    to say "hole" — a `filled_polygon` is one closed outline and that is all.

    Holes are taken right-to-left so a hole cut earlier becomes part of the
    outline the next ray can land on, which is what lets a hole reach the plane
    through another hole when that is the only way out. Every bridge the rule
    picks is then checked against the whole boundary before it is used, and if
    the rule's answer somehow touches something it should not, **every** other
    vertex is tried in turn, nearest first. It has to be every one: the ray
    routinely lands on a slit cut for an earlier hole, whose two endpoints are
    the doubled vertices this may not anchor on, and on a coarse outline the
    next vertex that works can be a hundred places down the list. Only when
    the whole ring has been tried does this return ``None``, and the caller
    leaves the whole zone as the converter wrote it.
    """
    ring = list(outer)
    ordered = sorted(holes, key=lambda h: max(p[0] for p in h), reverse=True)
    pending = list(ordered)

    for hole in ordered:
        pending.remove(hole)
        pivot = max(range(len(hole)), key=lambda i: (hole[i][0], hole[i][1]))
        mouth = hole[pivot]
        rotated = hole[pivot:] + hole[:pivot]

        obstacles = [
            (other[i], other[(i + 1) % len(other)])
            for other in pending + [hole]
            for i in range(len(other))
        ]

        preferred = _bridge_vertex(ring, mouth)
        candidates = [] if preferred is None else [preferred]
        candidates += sorted(
            range(len(ring)),
            key=lambda i: (ring[i][0] - mouth[0]) ** 2 + (ring[i][1] - mouth[1]) ** 2,
        )

        # Both of these are fixed for the whole sweep over one hole, and
        # rebuilding them per candidate is what made a *bounded* sweep look
        # necessary. Hoisted, the sweep is one pass over the ring per
        # candidate, and it can afford to try every vertex — which it must:
        # measured on `i2c-sensor-hub`, the first anchor that works is the
        # 124th nearest, because the pour's outline is coarse there and every
        # vertex within 14mm of that hole is either already carrying a slit or
        # screened off by one.
        #
        # What an unbounded sweep costs, measured rather than feared, on the
        # largest pour that exists here (weather-badge-16 with a top pour,
        # 3134 triangles, 19 holes, a 2903-point ring): the whole pass takes
        # **0.43s**, and a single hole exhausting every candidate — the
        # give-up path, which no board in `products/` takes — takes **1.7s**.
        # One is the ceiling per zone, not one per hole: the first hole with
        # no anchor returns `None` below and the zone is done. A work budget
        # here would buy 1.7s back and cost a pour, which is the trade this
        # function was just fixed for making.
        edges = [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]
        blocking = edges + obstacles
        visits: dict[tuple[int, int], int] = {}
        for point in ring:
            visits[point] = visits.get(point, 0) + 1

        chosen = None
        for index in candidates:
            anchor = ring[index]
            if anchor == mouth:
                continue
            # Never let a third slit meet at one point. `kicad-cli pcb drc`
            # segfaults on a fill whose outline visits the same vertex four
            # times — measured on weather-badge-16's F.Cu pour, reproduced
            # with that one zone as the only zone on the board. Two slits per
            # vertex is what KiCad's own filler writes; this pass will not
            # write more.
            if visits[anchor] > 1:
                continue
            if any(_blocks(anchor, mouth, c, d) for c, d in blocking):
                continue
            midpoint = ((anchor[0] + mouth[0]) / 2, (anchor[1] + mouth[1]) / 2)
            if not _point_in_ring(midpoint, outer):
                continue
            if any(_point_in_ring(midpoint, other) for other in holes):
                continue
            chosen = index
            break

        if chosen is None:
            return None
        ring = ring[: chosen + 1] + rotated + [rotated[0]] + ring[chosen:]

    return ring


def _fill_blocks(block: str) -> list[tuple[int, int]]:
    return _balanced_spans(block, "(filled_polygon")


def _render_fill(layer: str, ring: list[tuple[int, int]]) -> str:
    def mm(value: int) -> str:
        text = f"{value / _POUR_SCALE:.6f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"

    points = "\n".join(f"        (xy {mm(x)} {mm(y)})" for x, y in ring)
    return (
        "    (filled_polygon\n"
        f"      (layer {layer})\n"
        "      (pts\n"
        f"{points}\n"
        "      )\n"
        "    )"
    )


def _outline_zone(block: str, result: Normalization) -> str | None:
    """One zone's mesh, re-expressed as outlines. ``None`` means: not touched."""
    spans = _fill_blocks(block)
    if len(spans) < 2:
        return None  # a single polygon is already an outline

    fills: list[tuple[str, list[tuple[int, int]]]] = []
    for start, end in spans:
        chunk = block[start:end]
        layer = _LAYER_RE.search(chunk)
        if layer is None:
            return None
        ring = [
            (round(float(x) * _POUR_SCALE), round(float(y) * _POUR_SCALE))
            for x, y in _XY_RE.findall(chunk)
        ]
        if len(ring) != 3:
            return None  # not a mesh; KiCad's own island analysis is right here
        fills.append((layer.group(1), ring))

    # Everything between the fills must be whitespace, or splicing them out
    # would take something else with it.
    for (_, end), (start, _) in zip(spans, spans[1:]):
        if block[end:start].strip():
            return None

    by_layer: dict[str, list[list[tuple[int, int]]]] = {}
    for layer, ring in fills:
        by_layer.setdefault(layer, []).append(ring)

    rendered: list[str] = []
    produced = 0
    for layer, triangles in by_layer.items():
        rings = _mesh_rings(triangles)
        if rings is None:
            return None
        outers = [r for r in rings if _ring_area2(r) > 0]
        holes = [r for r in rings if _ring_area2(r) < 0]
        if not outers:
            return None

        assigned: dict[int, list[list[tuple[int, int]]]] = {
            i: [] for i in range(len(outers))
        }
        for hole in holes:
            # Smallest containing outer wins: a hole inside an island that is
            # itself inside a bigger region belongs to the island.
            owner, best = None, None
            for i, outer in enumerate(outers):
                if not _ring_contains(outer, hole):
                    continue
                area = _ring_area2(outer)
                if best is None or area < best:
                    owner, best = i, area
            if owner is None:
                return None  # a hole outside every region: not a plane we know
            assigned[owner].append(hole)

        outline_area = 0
        for i, outer in enumerate(outers):
            ring = _fracture(outer, assigned[i])
            if ring is None:
                return None
            outline_area += _ring_area2(ring)
            rendered.append(_render_fill(layer, ring))
            produced += 1

        # The whole justification for this pass is that it re-spells copper
        # without moving it. Integer nanometres make that checkable exactly,
        # so it is checked exactly: a slit contributes nothing to the shoelace
        # sum because it is walked once in each direction.
        if sum(abs(_ring_area2(t)) for t in triangles) != outline_area:
            return None

    first, last = spans[0][0], spans[-1][1]
    result.pours_outlined += 1
    result.pour_polygons_before += len(spans)
    result.pour_polygons_after += produced
    return block[:first] + "\n".join(rendered) + block[last:]


def _outline_pours(text: str, result: Normalization) -> str:
    """Re-express every triangulated pour in the board as its outline."""
    already_outlines = 0
    for start, end in reversed(_balanced_spans(text, "(zone")):
        block = text[start:end]
        if len(_fill_blocks(block)) < 2:
            already_outlines += 1
            continue
        rewritten = _outline_zone(block, result)
        if rewritten is None:
            result.declined.append(
                "a pour zone was left exactly as the converter wrote it: its "
                "fills are not a clean triangle mesh, so KiCad's own reading "
                "of that copper is the right one"
            )
            continue
        text = text[:start] + rewritten + text[end:]
    if already_outlines:
        result.declined.append(
            f"{already_outlines} pour zone(s) were already single outlines "
            "rather than meshes and were left untouched"
        )
    return text
