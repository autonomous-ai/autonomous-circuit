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
            or self.vias_bridged
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
