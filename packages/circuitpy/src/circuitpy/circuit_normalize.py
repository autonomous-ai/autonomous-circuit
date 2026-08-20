"""Open closed polygon pads in the compiled circuit.json, before anything reads it.

**The defect this closes.** A polygon pad is normally written the way every
other geometry format writes one — last vertex repeated to close the ring.
`@tscircuit/checks` tests "is this via inside this pad" with a ray cast whose
on-the-boundary shortcut is `isPointOnSegment`, and that function answers
**true for every point in the plane** when the segment has zero length:
`dotProduct` and `squaredLength` are both 0, so `0 <= 0 + 1e-9` passes. A
closed ring contains exactly one zero-length edge — first vertex to last — so
the pad swallows the whole board, and whichever via the spatial index happens
to hand the check first is reported as being inside a pad it is nowhere near.

Measured 2026-08-16 on `hydrate-coaster`: the blocking finding was

    Via at (2.40mm, -30.88mm) is inside SMD pad J1.B4A9 at (2.40mm, -31.83mm)

and the via center sits **0.3009mm above the pad's top edge** — 0.0009mm of
clear copper between the via's own annulus and the pad, on the outside. There
was no via in any pad on that board: all 108 vias, tested against all 174 pads,
zero containments. Deleting the one repeated vertex from the four USB-C polygon
pads takes `runAllChecks` from 2 findings to 1, and the survivor is a trace
length.

**Why it is a landmine and not a hydrate-coaster bug.** The same closed rings
are in every board built on the USB-C blocks. `harness-puck` and
`terminal-keyboard` carry them too and passed anyway — the check only tests
pads the spatial index returns for the via's own cell, so whether a board is
blocked depends on whether some via happened to land in the same bucket as a
connector pad. Two of three boards were fab-ready by luck.

**Where the real fix is, and why this exists as well.** The rings are opened at
source in the golden blocks, which is what keeps our own catalogue clean. This
runs on every board anyway, because the shape arrives from outside too: a
footprint imported from EasyEDA/KiCad closes its rings, and a user who pastes
one in would hit the same false block with nothing to tell them why.

**What it deliberately does not do.** It does not touch a board that has no
closed ring — no ring opened, not one byte written — so the byte conservatism
`_canonicalise_file` works for is preserved everywhere the defect is absent.
And it only drops a `pcb_placement_error` whose claim its own repair falsifies,
judged by the most generous test available: the via center is not inside the
pad's *bounding box*. Real containment implies bbox containment, so a genuine
via-in-pad can never be dropped by this.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

#: Two vertices this close are the same vertex. The converter emits pad
#: geometry at 1e-7mm precision; 1e-9mm is a hundred times finer than that and
#: a hundred thousand times finer than any fab can hold.
_SAME_POINT_MM = 1e-9

#: How far a reported coordinate may sit from the element it names. The message
#: carries two decimals (`toFixed(2)`), so half a count is 0.005mm; 0.01mm is
#: that with room for the rounding mode to disagree.
_REPORT_TOLERANCE_MM = 0.01

_VIA_IN_PAD_RE = re.compile(
    r"^Via at \((?P<vx>-?[0-9.]+)mm, (?P<vy>-?[0-9.]+)mm\) is inside "
    r"(?:SMD pad|through-hole pad) (?P<pad>.+) at "
    r"\((?P<px>-?[0-9.]+)mm, (?P<py>-?[0-9.]+)mm\)$"
)


@dataclass
class CircuitNormalization:
    """What was changed, so the pipeline can say it out loud."""

    rings_opened: int = 0
    vertices_dropped: int = 0
    stale_via_in_pad_dropped: int = 0
    #: Vias the router emitted more than once for one drilled hole — one per
    #: spanning-tree branch that lands on the same junction. Physically one
    #: hole; in the artifact, up to four.
    vias_deduped: int = 0
    vias_before: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.rings_opened or self.stale_via_in_pad_dropped
            or self.vias_deduped
        )

    def summary(self) -> str:
        parts = []
        if self.rings_opened:
            parts.append(
                f"{self.rings_opened} polygon pad(s) had a closed ring opened "
                f"({self.vertices_dropped} repeated vertex/vertices dropped) — "
                "a zero-length pad edge makes every via read as inside that pad"
            )
        if self.stale_via_in_pad_dropped:
            parts.append(
                f"{self.stale_via_in_pad_dropped} via-in-pad error(s) dropped: "
                "the via center is not inside the named pad's bounding box"
            )
        if self.vias_deduped:
            parts.append(
                f"{self.vias_deduped} duplicate via(s) merged "
                f"({self.vias_before} -> {self.vias_before - self.vias_deduped}) "
                "— one drilled hole was written once per spanning-tree branch "
                "that landed on it"
            )
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _pad_bounds(pad: dict) -> tuple[float, float, float, float] | None:
    """(minX, maxX, minY, maxY) of a pad, or None when the shape is unreadable.

    Deliberately the *outer* extent of every representation: an unreadable pad
    returns None and is then treated as "cannot falsify", never as "empty".
    """
    kind = pad.get("type")
    shape = pad.get("shape")
    if kind == "pcb_smtpad":
        if shape == "polygon":
            points = pad.get("points")
            if not isinstance(points, list) or not points:
                return None
            try:
                xs = [float(p["x"]) for p in points]
                ys = [float(p["y"]) for p in points]
            except (KeyError, TypeError, ValueError):
                return None
            return min(xs), max(xs), min(ys), max(ys)
        if shape == "circle":
            try:
                x, y, r = float(pad["x"]), float(pad["y"]), float(pad["radius"])
            except (KeyError, TypeError, ValueError):
                return None
            return x - r, x + r, y - r, y + r
        try:
            x, y = float(pad["x"]), float(pad["y"])
            w, h = float(pad["width"]), float(pad["height"])
        except (KeyError, TypeError, ValueError):
            return None
        if shape in ("rotated_rect", "rotated_pill"):
            # The rotated bounding box, so the extent can only ever grow.
            try:
                deg = float(pad.get("ccw_rotation") or 0.0)
            except (TypeError, ValueError):
                deg = 0.0
            rad = math.radians(deg)
            cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
            w, h = w * cos + h * sin, w * sin + h * cos
        return x - w / 2, x + w / 2, y - h / 2, y + h / 2
    if kind == "pcb_plated_hole":
        try:
            x, y = float(pad["x"]), float(pad["y"])
        except (KeyError, TypeError, ValueError):
            return None
        w = pad.get("outer_width") or pad.get("rect_pad_width") \
            or pad.get("outer_diameter")
        h = pad.get("outer_height") or pad.get("rect_pad_height") \
            or pad.get("outer_diameter")
        try:
            w, h = float(w), float(h)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return x - w / 2, x + w / 2, y - h / 2, y + h / 2
    return None


def _open_ring(points: Sequence[dict]) -> tuple[list[dict], int]:
    """Drop repeated vertices, including the one that closes the ring.

    Returns (points, dropped). A ring that collapses below a triangle is
    returned unchanged with 0 dropped — a pad we cannot repair is a pad we
    leave exactly as the toolchain wrote it.
    """
    out: list[dict] = []
    for point in points:
        try:
            x, y = float(point["x"]), float(point["y"])
        except (KeyError, TypeError, ValueError):
            return list(points), 0
        if out:
            px, py = float(out[-1]["x"]), float(out[-1]["y"])
            if abs(px - x) <= _SAME_POINT_MM and abs(py - y) <= _SAME_POINT_MM:
                continue
        out.append(point)
    while len(out) > 1:
        first, last = out[0], out[-1]
        if (abs(float(first["x"]) - float(last["x"])) <= _SAME_POINT_MM
                and abs(float(first["y"]) - float(last["y"])) <= _SAME_POINT_MM):
            out.pop()
            continue
        break
    dropped = len(points) - len(out)
    if dropped <= 0 or len(out) < 3:
        return list(points), 0
    return out, dropped


# ---------------------------------------------------------------------------
# The repair
# ---------------------------------------------------------------------------


def _falsified_via_in_pad(element: dict, elements: Sequence[dict]) -> bool:
    """True when the element's via-in-pad claim is provably wrong.

    Provably wrong means: every via the message could be naming sits outside
    the bounding box of every pad the message could be naming. Anything the
    message does not let us identify — an unparsable message, no matching via,
    no matching pad, a pad whose shape we cannot read — is not provably wrong,
    and the element stays.
    """
    message = element.get("message")
    if not isinstance(message, str):
        return False
    match = _VIA_IN_PAD_RE.match(message.strip())
    if match is None:
        return False
    vx, vy = float(match.group("vx")), float(match.group("vy"))
    px, py = float(match.group("px")), float(match.group("py"))

    vias = [
        e for e in elements
        if e.get("type") == "pcb_via"
        and isinstance(e.get("x"), (int, float))
        and isinstance(e.get("y"), (int, float))
        and abs(float(e["x"]) - vx) <= _REPORT_TOLERANCE_MM
        and abs(float(e["y"]) - vy) <= _REPORT_TOLERANCE_MM
    ]
    if not vias:
        return False

    pads: list[tuple[float, float, float, float]] = []
    for candidate in elements:
        if candidate.get("type") not in ("pcb_smtpad", "pcb_plated_hole"):
            continue
        bounds = _pad_bounds(candidate)
        if bounds is None:
            continue
        min_x, max_x, min_y, max_y = bounds
        center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        if (abs(center_x - px) <= _REPORT_TOLERANCE_MM
                and abs(center_y - py) <= _REPORT_TOLERANCE_MM):
            pads.append(bounds)
    if not pads:
        return False

    for via in vias:
        x, y = float(via["x"]), float(via["y"])
        for min_x, max_x, min_y, max_y in pads:
            if min_x <= x <= max_x and min_y <= y <= max_y:
                return False
    return True


#: A via's centre this close to another's is the same drilled hole. One
#: nanometre — a hundred thousand times finer than any fab positions a drill,
#: and the duplicates measured are bit-identical anyway.
_SAME_VIA_MM = 1e-6


def _via_key(via: dict) -> tuple[Any, ...] | None:
    """What makes two via records the same drilled hole, or ``None`` for
    "cannot tell, do not merge".

    The net is the part that has to be right. `connected_source_net_id` is
    ``null`` on every via the router writes — keying on it would merge vias of
    *different* nets that happen to coincide, which is a short, and the only
    failure here that would ship copper. `subcircuit_connectivity_map_key` is
    the field that actually carries the net (27 distinct values across
    weather-badge-19's 133 vias, and every duplicate group agreed on it), so
    that is the key, and a via without one is never merged.

    The layer pair is stored in both orders — `["bottom","top"]` on two of the
    four duplicates measured and `["top","bottom"]` on the other two — so it is
    compared as a set.
    """
    net = via.get("subcircuit_connectivity_map_key")
    if not net:
        return None
    x, y = via.get("x"), via.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    layers = via.get("layers")
    layer_key: Any
    if isinstance(layers, list):
        layer_key = frozenset(str(layer) for layer in layers)
    else:
        layer_key = frozenset(
            str(via.get(k)) for k in ("from_layer", "to_layer") if via.get(k)
        )
    if not layer_key:
        return None
    quantum = _SAME_VIA_MM
    return (
        str(net),
        round(float(x) / quantum),
        round(float(y) / quantum),
        via.get("outer_diameter"),
        via.get("hole_diameter"),
        layer_key,
    )


def _dedupe_vias(elements: Sequence[Any]) -> tuple[list[Any], int, int]:
    """Keep one ``pcb_via`` per drilled hole, in document order.

    The router routes a net as several spanning-tree branches and each branch
    drops its own via where it changes layer, so a junction several branches
    share is written several times: measured on weather-badge-19, 133 vias at
    113 distinct positions, one point carrying four. KiCad reads that as
    `holes_co_located` and a fab reads it as drilling the same hole twice.

    The first record wins, which keeps the pass deterministic. Each duplicate
    names a different `pcb_trace_id`, and that is the one thing lost — `diffpair`
    skips a via whose trace it is about to delete, and after this it sees one
    trace id where there were several. Safe here because every duplicate group
    shares a net (checked, not assumed: the key above carries the net), so the
    branches stand or fall together.
    """
    seen: set[tuple[Any, ...]] = set()
    kept: list[Any] = []
    total = dropped = 0
    for element in elements:
        if isinstance(element, dict) and element.get("type") == "pcb_via":
            total += 1
            key = _via_key(element)
            if key is not None:
                if key in seen:
                    dropped += 1
                    continue
                seen.add(key)
        kept.append(element)
    return kept, dropped, total


def normalize_circuit_json(path: Path) -> CircuitNormalization:
    """Open closed polygon pads in ``path`` and drop what that falsifies.

    Idempotent, and a no-op on any board without a closed ring — the file is
    only rewritten when something actually changed. Never raises: a normaliser
    that takes the build down is worse than a board with a redundant vertex.
    """
    result = CircuitNormalization()
    try:
        original = path.read_text(encoding="utf-8")
        elements = json.loads(original)
    except (OSError, ValueError) as exc:
        result.notes.append(f"could not read {path.name}: {exc}")
        return result
    if not isinstance(elements, list):
        return result

    try:
        repaired: list[Any] = []
        for element in elements:
            if (isinstance(element, dict)
                    and element.get("type") == "pcb_smtpad"
                    and element.get("shape") == "polygon"
                    and isinstance(element.get("points"), list)):
                points, dropped = _open_ring(element["points"])
                if dropped:
                    element = dict(element)
                    element["points"] = points
                    result.rings_opened += 1
                    result.vertices_dropped += dropped
            repaired.append(element)

        repaired, result.vias_deduped, result.vias_before = _dedupe_vias(repaired)

        if not result.rings_opened and not result.vias_deduped:
            return result  # nothing was invalidated, so nothing is rewritten

        if result.rings_opened:
            # Only a ring that was opened can falsify a via-in-pad error; a
            # deduped via changes no geometry, so it invalidates no finding.
            kept = [
                e for e in repaired
                if not (isinstance(e, dict)
                        and e.get("type") == "pcb_placement_error"
                        and _falsified_via_in_pad(e, repaired))
            ]
            result.stale_via_in_pad_dropped = len(repaired) - len(kept)
        else:
            kept = repaired

        text = json.dumps(kept, indent=2)
        # Prove the rewrite before it replaces the artifact of record.
        if json.loads(text) != kept:
            result.notes.append(
                "polygon-ring repair did not round-trip; the circuit.json was "
                "left exactly as the toolchain produced it"
            )
            return CircuitNormalization(notes=result.notes)
        path.write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(
            f"polygon-ring repair aborted ({type(exc).__name__}: {exc}); the "
            "circuit.json was left exactly as the toolchain produced it"
        )
        try:
            path.write_text(original, encoding="utf-8")
        except OSError:
            pass
        return CircuitNormalization(notes=result.notes)
    return result
