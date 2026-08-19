"""Is there a ground plane on this board, and how much of the board is it?

**The gap this closes.** Nothing in the pipeline asked whether a board has a
ground pour at all. Grepped 2026-08-19: `netclass._ground_shapes` reads pours
when they exist, and no check anywhere requires one. Measured across the
21-board corpus on the same day, that hole is not theoretical —
`weather-badge-5` carries `fab.ready = True` with **zero copper pours**, and
`harness-puck` and `terminal-keyboard` have none either. A board can therefore
be declared orderable with no reference plane under any signal on it, and the
verdict says nothing at all, because nobody wrote the sentence.

**What this measures.**

*Existence* — a ground pour, on any layer. Absent, the board's return currents
travel only on whatever `GND` tracks the router happened to lay, every
`netclass_pair_reference` figure on the board is computed against nothing, and
the impedance of every routed signal is undefined. That is a real and
board-wide loss of quality.

*Which layers carry one* — because the answer today is always the same, and it
is half a plane. All 17 pourable boards in the corpus pour on `bottom` only, at
98% coverage, and **never** on `top`. Every return current on the component
side has to find a via before it has anywhere to go. Worth saying out loud with
its number rather than leaving it to be rediscovered.

*Coverage* — the pour's own outline against the board outline. A pour that
exists but reaches 10% of the board (`hydrate-coaster`, top layer) is closer to
absent than to present, and a single number distinguishes them.

**Why none of this blocks.** The bar this repo sets is "block only what makes
the delivered board unusable or the order refused", and a plane-less board is
neither: the netlist is complete, the fab builds it, it powers up. Measured on
the same corpus, every `ready` board has `unconnected_items = 0` — no pad
depends on pour copper to reach its net. So this advises, loudly, with the
measurement attached, and the decision to raise it belongs on the fab profile
in `fab.VERIFY_ESCALATED_KINDS` where an EE can move the line in one place.

**What this cannot see.** Whether the pour is *stitched* — a plane with no vias
tying it to the pours on other layers is continuous copper that still does not
carry return current where it is needed. That needs the routed via set, and it
is a separate measurement. It also cannot see fragmentation: in `circuit.json`
a pour is a brep outline, and the only place a pour is ever cut into pieces is
the KiCad conversion, which `circuitpy.checks` re-measures at that end.
"""

from __future__ import annotations

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board

#: A pour reaching less than this share of the board outline is reported as
#: partial rather than counted as a plane. 0.5 is deliberately generous: the
#: corpus splits cleanly into 98% and 10%, so any threshold between them says
#: the same thing about real boards, and a loose one cannot manufacture a
#: finding on a board that genuinely has a plane.
POUR_COVERAGE_FLOOR = 0.5


def _polygon_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2


def _ring_points(ring: object) -> list[tuple[float, float]]:
    vertices = ring.get("vertices") if isinstance(ring, dict) else ring
    if not isinstance(vertices, list) or len(vertices) < 3:
        return []
    points: list[tuple[float, float]] = []
    for vertex in vertices:
        try:
            points.append((float(vertex["x"]), float(vertex["y"])))
        except (KeyError, TypeError, ValueError):
            return []
    return points


def _ground_net_ids(board: Board) -> set[str]:
    return {
        str(net.get("source_net_id"))
        for net in board.of_type("source_net")
        if net.get("is_ground") and net.get("source_net_id")
    }


def _pour_area_by_layer(board: Board) -> dict[str, float]:
    """Largest ground-pour outline per layer, in mm².

    The largest outline rather than the sum: pours arrive as one real outline
    plus a scatter of three-vertex offcuts left over from subtracting the
    traces, and summing them would count slivers as plane.
    """
    ground = _ground_net_ids(board)
    by_layer: dict[str, float] = {}
    for pour in board.of_type("pcb_copper_pour"):
        if ground and str(pour.get("source_net_id") or "") not in ground:
            continue
        points = _ring_points((pour.get("brep_shape") or {}).get("outer_ring"))
        if not points:
            continue
        layer = str(pour.get("layer") or "?")
        by_layer[layer] = max(by_layer.get(layer, 0.0), _polygon_area(points))
    return by_layer


def _board_area(board: Board) -> float | None:
    outline = getattr(board, "outline", None)
    if outline is None:
        return None
    area = abs(outline.x1 - outline.x0) * abs(outline.y1 - outline.y0)
    return area or None


@never_raises
def _pour_findings(board: Board, coverage: Coverage) -> list[Finding]:
    layers = _pour_area_by_layer(board)
    total_pours = len(board.of_type("pcb_copper_pour"))
    area = _board_area(board)

    if not layers:
        coverage.examined += 1
        detail = (
            "no ground pour anywhere on this board, so every signal's return "
            "current travels on routed GND track alone and no routed signal "
            "has a defined impedance"
        )
        if total_pours:
            detail += (
                f" ({total_pours} copper pour(s) exist but none is on a "
                "ground net)"
            )
        return [finding("board", "ground_pour_missing", detail)]

    findings: list[Finding] = []
    for layer, pour_area in sorted(layers.items()):
        coverage.examined += 1
        if area is None:
            continue
        share = pour_area / area
        if share < POUR_COVERAGE_FLOOR:
            findings.append(
                finding(
                    "board",
                    "ground_pour_partial",
                    f"the ground pour on {layer} reaches {share:.0%} of the "
                    f"{area:.0f}mm2 board ({pour_area:.0f}mm2); under "
                    f"{POUR_COVERAGE_FLOOR:.0%} it is not a reference plane, "
                    "it is a patch",
                )
            )

    missing = sorted({"top", "bottom"} - set(layers))
    if missing and area is not None:
        findings.append(
            finding(
                "board",
                "ground_pour_one_sided",
                f"ground is poured on {', '.join(sorted(layers))} but not on "
                f"{', '.join(missing)}, so every return current on the "
                f"{', '.join(missing)} side has to reach a via before it has a "
                "plane to travel on",
                severity="info",
            )
        )
    return findings


def check(board: Board) -> CheckResult:
    coverage = Coverage(unit="ground pour layers", total=0)
    findings = _pour_findings(board, coverage)
    coverage.total = max(coverage.examined, 1)
    coverage.skip(
        "whether the pour is stitched to the other layers with vias — that "
        "needs the routed via set and is a separate measurement"
    )

    notes = [
        "a pour is read from its brep outline in circuit.json; the only place "
        "a pour is cut into pieces is the KiCad conversion, and circuitpy's "
        "own DRC pass re-measures it there",
        f"a pour reaching under {POUR_COVERAGE_FLOOR:.0%} of the board is "
        "reported as partial rather than counted as a plane",
        "nothing here blocks: measured across 21 boards, every ready board "
        "has unconnected_items = 0, so no pad depends on pour copper to reach "
        "its net",
    ]
    return CheckResult(name="pour", findings=findings, coverage=coverage, notes=notes)
