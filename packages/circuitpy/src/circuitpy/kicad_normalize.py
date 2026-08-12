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

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from circuitpy.fab import FabProfile

#: KiCad silkscreen layer names, old and new spellings.
SILK_LAYERS = ("F.SilkS", "B.SilkS", "F.Silkscreen", "B.Silkscreen")

_TEXT_OPENERS = ("(fp_text", "(gr_text")
_GRAPHIC_OPENERS = ("(fp_line", "(gr_line", "(fp_rect", "(gr_rect",
                    "(fp_circle", "(gr_circle", "(fp_arc", "(gr_arc",
                    "(fp_poly", "(gr_poly")
_BOARD_GRAPHIC_OPENERS = (
    "(gr_line", "(gr_rect", "(gr_circle", "(gr_arc", "(gr_poly"
)

_SIZE_RE = re.compile(r"\(size\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*\)")
_THICKNESS_RE = re.compile(r"\(thickness\s+([0-9.eE+-]+)\s*\)")
_WIDTH_RE = re.compile(r"\(width\s+([0-9.eE+-]+)\s*\)")
_FONT_OPEN_RE = re.compile(r"\(font\b")
_HIDDEN_NODE_REFERENCE_RE = re.compile(
    r'\(property\s+"Reference"\s+"(N[1-9][0-9]*)"'
)
_HIDDEN_NODE_VALUE = '(property "Value" "MASKED_COPPER_NODE"'
_PAD_LAYERS_RE = re.compile(r"\(layers\s+([^)]*)\)")
_AT_RE = re.compile(
    r"\(at\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)"
    r"(?:\s+([0-9.eE+-]+))?\s*\)"
)
_LAYER_RE = re.compile(r'\(layer\s+"?([^"\s)]+)"?\s*\)')
_REFERENCE_TEXT_RE = re.compile(r'\(fp_text\s+reference\s+"([^"]+)"')
_HIDDEN_TEXT_RE = re.compile(r"(?:\(hide(?:\s+yes)?\)|\bhide\b)")
_TEXT_VALUE_RE = re.compile(
    r'\((?:fp_text|gr_text)\s+(?:(?:reference|value)\s+)?"([^"]*)"'
)
_SOLDER_MASK_MARGIN_RE = re.compile(
    r"\(solder_mask_margin\s+([0-9.eE+-]+)\s*\)"
)
_BOARD_MASK_CLEARANCE_RE = re.compile(
    r"\(pad_to_mask_clearance\s+([0-9.eE+-]+)\s*\)"
)
_POINT_RE = re.compile(
    r"\((?:start|end|mid|center|xy)\s+"
    r"([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*\)"
)

# A reference that merely touches a mask opening may lose half of its outer
# stroke when KiCad applies ``--subtract-soldermask``.  One fab-minimum stroke
# of breathing room is small enough to fit dense boards and large enough that
# our conservative glyph box remains wholly printable.
_REFERENCE_CLEARANCE_MM = 0.15


@dataclass
class Normalization:
    """What was changed, so the pipeline can say it out loud."""

    text_resized: int = 0
    text_thickened: int = 0
    strokes_widened: int = 0
    hidden_nodes_normalized: int = 0
    hidden_node_pads_covered: int = 0
    hidden_node_assembly_exclusions: int = 0
    references_relocated: int = 0
    unreadable_references: list[str] = field(default_factory=list)
    smallest_text_mm: float | None = None
    smallest_stroke_mm: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.text_resized
            or self.text_thickened
            or self.strokes_widened
            or self.hidden_nodes_normalized
            or self.references_relocated
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
        if self.hidden_nodes_normalized:
            parts.append(
                f"{self.hidden_nodes_normalized} mask-covered routing node(s) "
                "kept off paste, mask openings, BOM, and placement files"
            )
        if self.references_relocated:
            parts.append(
                f"{self.references_relocated} reference designator(s) moved "
                "clear of solder-mask openings"
            )
        return "; ".join(parts)

    def unreadable_findings(self, profile: FabProfile) -> list[dict]:
        """Blocking localized findings for populated refs with no legal slot."""
        return [
            {
                "part": reference,
                "kind": "silkscreen_refdes_unreadable",
                "detail": (
                    f"{reference} cannot fit a complete "
                    f"{profile.min_silk_text_mm:g}mm reference designator "
                    "inside the board and clear of same-face solder-mask "
                    "openings; mask subtraction would leave a clipped/partial "
                    "label"
                ),
                "severity": "error",
            }
            for reference in self.unreadable_references
        ]


@dataclass(frozen=True)
class _Box:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2, (self.min_y + self.max_y) / 2)

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def expanded(self, amount: float) -> "_Box":
        return _Box(
            self.min_x - amount,
            self.min_y - amount,
            self.max_x + amount,
            self.max_y + amount,
        )


@dataclass(frozen=True)
class _Placement:
    x: float
    y: float
    angle: float = 0.0


@dataclass
class _Reference:
    ref: str
    footprint_index: int
    block_start: int
    block_end: int
    footprint: _Placement
    local: _Placement
    layer: str
    box: _Box
    text_block: str


def _number(value: float) -> str:
    """Stable compact KiCad number formatting (and no ``-0`` churn)."""
    if abs(value) < 5e-10:
        value = 0.0
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _placement(block: str) -> _Placement:
    # Only the object's own ``(at ...)`` counts.  A footprint contains nested
    # text/pad positions; a plain regex would mistake the first child's
    # coordinate for the footprint origin when the footprint itself omits at.
    for match in _AT_RE.finditer(block):
        depth = 0
        for char in block[:match.start()]:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
        if depth != 1:
            continue
        return _Placement(
            float(match.group(1)),
            float(match.group(2)),
            float(match.group(3) or 0.0),
        )
    return _Placement(0.0, 0.0, 0.0)


def _layer(block: str) -> str | None:
    match = _LAYER_RE.search(block)
    return match.group(1) if match is not None else None


def _rotate(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return x * cos_a - y * sin_a, x * sin_a + y * cos_a


def _to_board(local: tuple[float, float], footprint: _Placement) -> tuple[float, float]:
    x, y = _rotate(local[0], local[1], footprint.angle)
    return footprint.x + x, footprint.y + y


def _to_local(board: tuple[float, float], footprint: _Placement) -> tuple[float, float]:
    return _rotate(board[0] - footprint.x, board[1] - footprint.y, -footprint.angle)


def _rotated_box(
    center: tuple[float, float], width: float, height: float, angle_deg: float
) -> _Box:
    """Axis-aligned envelope of a rotated rectangle."""
    angle = math.radians(angle_deg)
    cos_a, sin_a = abs(math.cos(angle)), abs(math.sin(angle))
    half_x = (width * cos_a + height * sin_a) / 2
    half_y = (width * sin_a + height * cos_a) / 2
    return _Box(
        center[0] - half_x,
        center[1] - half_y,
        center[0] + half_x,
        center[1] + half_y,
    )


def _boxes_overlap(left: _Box, right: _Box, clearance: float = 0.0) -> bool:
    right = right.expanded(clearance)
    return not (
        left.max_x <= right.min_x
        or left.min_x >= right.max_x
        or left.max_y <= right.min_y
        or left.min_y >= right.max_y
    )


def _inside(inner: _Box, outer: _Box) -> bool:
    return (
        inner.min_x >= outer.min_x
        and inner.max_x <= outer.max_x
        and inner.min_y >= outer.min_y
        and inner.max_y <= outer.max_y
    )


def _points_box(points: list[tuple[float, float]]) -> _Box | None:
    if not points:
        return None
    return _Box(
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


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


def _replace_at(block: str, placement: _Placement) -> str:
    match = _AT_RE.search(block)
    if match is None:
        return block
    angle = (
        f" {_number(placement.angle)}"
        if match.group(3) is not None or abs(placement.angle) > 1e-9
        else ""
    )
    return (
        block[:match.start()]
        + f"(at {_number(placement.x)} {_number(placement.y)}{angle})"
        + block[match.end():]
    )


def _text_metrics(block: str) -> tuple[float, float, float] | None:
    size = _SIZE_RE.search(block)
    if size is None:
        return None
    value = _TEXT_VALUE_RE.search(block)
    if value is None:
        return None
    size_x, size_y = float(size.group(1)), float(size.group(2))
    thickness_match = _THICKNESS_RE.search(block)
    thickness = float(thickness_match.group(1)) if thickness_match else 0.0
    # KiCad's stroke font is proportional.  0.70em per character is a
    # conservative envelope for the reference glyphs emitted by the
    # converter; spaces still consume a cell.  The stroke is included at both
    # outer edges so a bbox-clear result remains wholly printable.
    width = max(size_x, len(value.group(1)) * size_x * 0.70) + thickness
    height = size_y + thickness
    return width, height, thickness


def _text_box(block: str, footprint: _Placement | None = None) -> _Box | None:
    metrics = _text_metrics(block)
    if metrics is None:
        return None
    local = _placement(block)
    parent = footprint or _Placement(0.0, 0.0, 0.0)
    center = _to_board((local.x, local.y), parent)
    return _rotated_box(
        center,
        metrics[0],
        metrics[1],
        parent.angle + local.angle,
    )


def _board_outline_box(text: str) -> _Box | None:
    points: list[tuple[float, float]] = []
    # Footprint-owned Edge.Cuts geometry is relative and commonly describes a
    # slot/cutout, not the routed outer outline.  Only board-level graphics
    # define the envelope here.
    for opener in _BOARD_GRAPHIC_OPENERS:
        for start, end in _balanced_spans(text, opener):
            block = text[start:end]
            if "Edge.Cuts" not in block:
                continue
            points.extend(
                (float(match.group(1)), float(match.group(2)))
                for match in _POINT_RE.finditer(block)
            )
    return _points_box(points)


def _pad_boxes(
    footprint_block: str,
    footprint: _Placement,
    board_mask_clearance: float,
) -> dict[str, list[_Box]]:
    result: dict[str, list[_Box]] = {"F.SilkS": [], "B.SilkS": []}
    for start, end in _balanced_spans(footprint_block, "(pad"):
        pad = footprint_block[start:end]
        size = _SIZE_RE.search(pad)
        layers = _PAD_LAYERS_RE.search(pad)
        if size is None or layers is None:
            continue
        pad_at = _placement(pad)
        center = _to_board((pad_at.x, pad_at.y), footprint)
        margin_match = _SOLDER_MASK_MARGIN_RE.search(pad)
        margin = (
            float(margin_match.group(1))
            if margin_match is not None
            else board_mask_clearance
        )
        width = max(0.0, float(size.group(1)) + 2 * margin)
        height = max(0.0, float(size.group(2)) + 2 * margin)
        box = _rotated_box(
            center,
            width,
            height,
            footprint.angle + pad_at.angle,
        )
        layer_tokens = layers.group(1).replace('"', "").split()
        if "F.Mask" in layer_tokens or "*.Mask" in layer_tokens:
            result["F.SilkS"].append(box)
        if "B.Mask" in layer_tokens or "*.Mask" in layer_tokens:
            result["B.SilkS"].append(box)
    return result


def _footprint_geometry_box(
    footprint_block: str,
    footprint: _Placement,
    pad_boxes: dict[str, list[_Box]],
) -> _Box:
    """Courtyard when present, otherwise the footprint's mask-pad envelope."""
    points: list[tuple[float, float]] = []
    for opener in _GRAPHIC_OPENERS:
        for start, end in _balanced_spans(footprint_block, opener):
            block = footprint_block[start:end]
            if "CrtYd" not in block:
                continue
            for match in _POINT_RE.finditer(block):
                points.append(
                    _to_board(
                        (float(match.group(1)), float(match.group(2))), footprint
                    )
                )
    courtyard = _points_box(points)
    if courtyard is not None:
        return courtyard
    pads = pad_boxes["F.SilkS"] + pad_boxes["B.SilkS"]
    if pads:
        return _Box(
            min(box.min_x for box in pads),
            min(box.min_y for box in pads),
            max(box.max_x for box in pads),
            max(box.max_y for box in pads),
        )
    return _Box(footprint.x, footprint.y, footprint.x, footprint.y)


def _reference_records(text: str) -> tuple[list[_Reference], list[dict[str, object]]]:
    """Visible reference text plus the footprint geometry it belongs to."""
    board_clearance_match = _BOARD_MASK_CLEARANCE_RE.search(text)
    board_clearance = (
        float(board_clearance_match.group(1))
        if board_clearance_match is not None
        else 0.0
    )
    references: list[_Reference] = []
    footprints: list[dict[str, object]] = []
    for footprint_index, (start, end) in enumerate(
        _balanced_spans(text, "(footprint")
    ):
        block = text[start:end]
        fp_at = _placement(block)
        pads = _pad_boxes(block, fp_at, board_clearance)
        geometry = _footprint_geometry_box(block, fp_at, pads)
        footprint_info: dict[str, object] = {
            "start": start,
            "end": end,
            "placement": fp_at,
            "pads": pads,
            "geometry": geometry,
        }
        footprints.append(footprint_info)
        for local_start, local_end in _balanced_spans(block, "(fp_text"):
            text_block = block[local_start:local_end]
            match = _REFERENCE_TEXT_RE.search(text_block)
            layer = _layer(text_block)
            if (
                match is None
                or layer not in {"F.SilkS", "B.SilkS"}
                or _HIDDEN_TEXT_RE.search(text_block) is not None
            ):
                continue
            box = _text_box(text_block, fp_at)
            if box is None:
                continue
            references.append(
                _Reference(
                    ref=match.group(1),
                    footprint_index=footprint_index,
                    block_start=start + local_start,
                    block_end=start + local_end,
                    footprint=fp_at,
                    local=_placement(text_block),
                    layer=layer,
                    box=box,
                    text_block=text_block,
                )
            )
    return references, footprints


def _board_text_obstacles(text: str) -> dict[str, list[_Box]]:
    result: dict[str, list[_Box]] = {"F.SilkS": [], "B.SilkS": []}
    for start, end in _balanced_spans(text, "(gr_text"):
        block = text[start:end]
        layer = _layer(block)
        if layer not in result:
            continue
        box = _text_box(block)
        if box is not None:
            result[layer].append(box)
    return result


def _clear_reference_box(
    box: _Box,
    *,
    board: _Box | None,
    pads: list[_Box],
    text_obstacles: list[_Box],
) -> bool:
    if board is not None and not _inside(box, board):
        return False
    if any(_boxes_overlap(box, pad, _REFERENCE_CLEARANCE_MM) for pad in pads):
        return False
    if any(_boxes_overlap(box, other, _REFERENCE_CLEARANCE_MM) for other in text_obstacles):
        return False
    return True


def _compass_centers(component: _Box, text_box: _Box) -> list[tuple[float, float]]:
    """Eight deterministic slots just outside a component envelope."""
    margin = _REFERENCE_CLEARANCE_MM
    cx, cy = component.center
    half_w, half_h = text_box.width / 2, text_box.height / 2
    left = component.min_x - margin - half_w
    right = component.max_x + margin + half_w
    bottom = component.min_y - margin - half_h
    top = component.max_y + margin + half_h
    # North first only breaks equal-distance ties; the caller sorts by travel.
    return [
        (cx, top),
        (cx, bottom),
        (right, cy),
        (left, cy),
        (right, top),
        (left, top),
        (right, bottom),
        (left, bottom),
    ]


def _relocate_references(text: str, result: Normalization) -> str:
    """Keep every visible populated ref wholly printable after mask subtraction.

    This deliberately operates after text-size normalisation, because enlarging
    a previously clear 0.27mm label to the 1mm fab floor can create the very
    collision this pass prevents.
    """
    board = _board_outline_box(text)
    references, footprints = _reference_records(text)
    if not references:
        return text

    pad_obstacles: dict[str, list[_Box]] = {"F.SilkS": [], "B.SilkS": []}
    for footprint in footprints:
        pads = footprint["pads"]
        assert isinstance(pads, dict)
        pad_obstacles["F.SilkS"].extend(pads["F.SilkS"])
        pad_obstacles["B.SilkS"].extend(pads["B.SilkS"])

    board_text = _board_text_obstacles(text)
    # Greedy but deterministic: accepted references become obstacles for the
    # next one.  Invalid future positions do not reserve space that they will
    # themselves have to vacate, and a pre-existing ref/ref overlap keeps the
    # first source-order label while moving the second.
    current_boxes: dict[int, tuple[str, _Box]] = {}
    replacements: list[tuple[int, int, str]] = []

    for reference in references:
        other_text = list(board_text[reference.layer]) + [
            box
            for key, (layer, box) in current_boxes.items()
            if key != id(reference) and layer == reference.layer
        ]
        if _clear_reference_box(
            reference.box,
            board=board,
            pads=pad_obstacles[reference.layer],
            text_obstacles=other_text,
        ):
            current_boxes[id(reference)] = (reference.layer, reference.box)
            continue

        footprint = footprints[reference.footprint_index]
        component = footprint["geometry"]
        assert isinstance(component, _Box)
        current_center = reference.box.center
        candidates = list(enumerate(_compass_centers(component, reference.box)))
        candidates.sort(
            key=lambda item: (
                (item[1][0] - current_center[0]) ** 2
                + (item[1][1] - current_center[1]) ** 2,
                item[0],
            )
        )
        chosen: tuple[float, float] | None = None
        chosen_box: _Box | None = None
        for _, center in candidates:
            box = _Box(
                center[0] - reference.box.width / 2,
                center[1] - reference.box.height / 2,
                center[0] + reference.box.width / 2,
                center[1] + reference.box.height / 2,
            )
            if _clear_reference_box(
                box,
                board=board,
                pads=pad_obstacles[reference.layer],
                text_obstacles=other_text,
            ):
                chosen, chosen_box = center, box
                break
        if chosen is None or chosen_box is None:
            result.unreadable_references.append(reference.ref)
            continue

        local_x, local_y = _to_local(chosen, reference.footprint)
        replacement = _replace_at(
            reference.text_block,
            _Placement(local_x, local_y, reference.local.angle),
        )
        replacements.append((reference.block_start, reference.block_end, replacement))
        current_boxes[id(reference)] = (reference.layer, chosen_box)
        result.references_relocated += 1

    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


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


def _hidden_node_reference(block: str) -> str | None:
    """Return the reserved hidden-node ref when the converter kept its marker.

    The source primitive deliberately carries both an N-prefixed reference and
    ``MASKED_COPPER_NODE`` as its manufacturer/value identity.  Matching both
    makes this a component-class rewrite, never a coordinate or board-name
    special case.
    """
    if _HIDDEN_NODE_VALUE not in block:
        return None
    match = _HIDDEN_NODE_REFERENCE_RE.search(block)
    if match is None:
        return None
    reference_spans = _balanced_spans(block, '(property "Reference"')
    if len(reference_spans) != 1:
        return None
    start, end = reference_spans[0]
    if "(hide yes)" not in block[start:end]:
        return None
    return match.group(1)


def _normalize_hidden_node(
    block: str, result: Normalization
) -> str:
    """Restore the no-mask/no-paste/DNP semantics lost by the converter."""
    reference = _hidden_node_reference(block)
    if reference is None:
        return block

    pads = _balanced_spans(block, "(pad")
    if len(pads) != 1:
        result.notes.append(
            f"hidden routing node {reference} converted with {len(pads)} pads; "
            "expected exactly one and left it unchanged"
        )
        return block

    pad_start, pad_end = pads[0]
    pad = block[pad_start:pad_end]
    layers_match = _PAD_LAYERS_RE.search(pad)
    if layers_match is None:
        result.notes.append(
            f"hidden routing node {reference} has no KiCad pad layer list; "
            "left it unchanged"
        )
        return block
    layers = layers_match.group(1).split()
    copper_layers = [layer for layer in layers if layer in {"F.Cu", "B.Cu"}]
    if len(copper_layers) != 1:
        result.notes.append(
            f"hidden routing node {reference} has unexpected layers "
            f"{layers_match.group(1)!r}; left it unchanged"
        )
        return block

    attr_spans = _balanced_spans(block, "(attr")
    if len(attr_spans) > 1:
        result.notes.append(
            f"hidden routing node {reference} has multiple KiCad attr blocks; "
            "left it unchanged"
        )
        return block

    changed = False
    wanted_layers = f"(layers {copper_layers[0]})"
    if layers_match.group(0) != wanted_layers:
        pad = (
            pad[: layers_match.start()]
            + wanted_layers
            + pad[layers_match.end():]
        )
        block = block[:pad_start] + pad + block[pad_end:]
        result.hidden_node_pads_covered += 1
        changed = True

    missing_attrs = [
        token
        for token in ("exclude_from_pos_files", "exclude_from_bom")
        if token not in block
    ]
    if missing_attrs:
        if attr_spans:
            attr_start, attr_end = attr_spans[0]
            attr = block[attr_start:attr_end]
            attr = attr[:-1] + " " + " ".join(missing_attrs) + ")"
            block = block[:attr_start] + attr + block[attr_end:]
        else:
            required_attr = "(attr smd exclude_from_pos_files exclude_from_bom)"
            refreshed_pads = _balanced_spans(block, "(pad")
            insert_at = refreshed_pads[0][0]
            block = (
                block[:insert_at]
                + required_attr
                + "\n    "
                + block[insert_at:]
            )
        result.hidden_node_assembly_exclusions += 1
        changed = True

    if changed:
        result.hidden_nodes_normalized += 1
    return block


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
        # circuit-json-to-kicad currently re-adds F.Paste/F.Mask to every
        # custom SMT pad even when circuit JSON marks it mask-covered and has
        # no paste. Restore that semantic before KiCad sees the board.
        for start, end in reversed(_balanced_spans(text, "(footprint")):
            block = _normalize_hidden_node(text[start:end], result)
            text = text[:start] + block + text[end:]

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

        # Text is deliberately placed *after* it is enlarged.  Otherwise a
        # 0.27mm converter label can look clear during placement and overlap a
        # pad once this same function raises it to the 1mm fab floor.  KiCad's
        # mask subtraction remains the final soldering-safety net; this pass
        # ensures that safety does not leave a half-erased designator.
        text = _relocate_references(text, result)

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
