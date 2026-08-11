"""The failure corpus — every real defect, permanently, with its near miss.

**The mechanism.** A gauntlet only gets better if it gets *monotonically*
harder to fool. Every defect a real board has surfaced becomes a fixture here
and stays forever, so the check that caught it can never quietly stop catching
it. Nothing in this file is hypothetical: each entry carries the date and the
board that found it.

**Why each defect ships with a counter-case.** The standing lesson of
2026-08-10 is that *a gate set to a preference instead of a floor is noise, and
noise trains everyone to ignore the gate* — learned three times in one evening
(vias judged by the through-hole annular rule, clearance blocked at 0.127mm
when the fab's floor is 0.10mm, KiCad grading boards against its own defaults).
A test that only asserts "the bad board is caught" invites the fix of
tightening the rule until everything is caught. So every defect here is paired
with the legal geometry that sits just the other side of the line and **must
stay clean**. Together they pin the threshold from both directions, which is
the only way a floor stays a floor.

Adding to the corpus: append a ``Defect`` with the incident recorded in
``story``, the elements that reproduce it, and the near miss. Two rules — the
fixture must come from something that actually happened, and it must be the
smallest geometry that reproduces it.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from circuitpy import checks  # noqa: E402
from circuitpy.fab import get_profile  # noqa: E402
from circuitpy.spec import ResolvedProduct  # noqa: E402

PROFILE = get_profile("jlcpcb")


def _product(envelope: tuple[float, float] | None = (200.0, 200.0)) -> ResolvedProduct:
    return ResolvedProduct(
        name="corpus", description="", power="usb-c-5v", envelope_mm=envelope,
        layers=2, fab="jlcpcb", assembly=True, path=Path("product.json"),
    )


# ---------------------------------------------------------------------------
# Geometry builders — the smallest circuit.json that reproduces a defect
# ---------------------------------------------------------------------------


def board(width: float = 40.0, height: float = 30.0) -> dict:
    return {
        "type": "pcb_board", "width": width, "height": height,
        "center": {"x": 0, "y": 0}, "thickness": 1.6,
    }


def hole(x: float, y: float, diameter: float, *, plated: bool = False) -> dict:
    if plated:
        return {
            "type": "pcb_plated_hole", "pcb_plated_hole_id": f"ph_{x}_{y}",
            "x": x, "y": y, "hole_diameter": diameter,
            "outer_diameter": diameter + 0.6,
        }
    return {
        "type": "pcb_hole", "pcb_hole_id": f"h_{x}_{y}",
        "x": x, "y": y, "hole_diameter": diameter,
    }


def track(points: list[tuple[float, float]], *, width: float = 0.2,
          trace_id: str = "t1") -> dict:
    return {
        "type": "pcb_trace", "pcb_trace_id": trace_id,
        "route": [
            {"route_type": "wire", "x": x, "y": y, "width": width, "layer": "top"}
            for x, y in points
        ],
    }


def via(*, hole_d: float, outer_d: float, x: float = 0.0, y: float = 0.0) -> dict:
    return {
        "type": "pcb_via", "pcb_via_id": f"v_{x}_{y}", "x": x, "y": y,
        "hole_diameter": hole_d, "outer_diameter": outer_d,
        "layers": ["top", "bottom"],
    }


def plated_hole(*, hole_d: float, outer_d: float, x: float = 0.0,
                y: float = 0.0) -> dict:
    return {
        "type": "pcb_plated_hole", "pcb_plated_hole_id": f"ph_{x}_{y}",
        "x": x, "y": y, "hole_diameter": hole_d, "outer_diameter": outer_d,
    }


def pad(x: float, y: float, *, w: float = 0.6, h: float = 0.6) -> dict:
    return {
        "type": "pcb_smtpad", "pcb_smtpad_id": f"p_{x}_{y}", "shape": "rect",
        "x": x, "y": y, "width": w, "height": h, "layer": "top",
    }


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Defect:
    """One real defect, its reproduction, and the legal geometry beside it."""

    id: str
    found: str                    # when, and by which board
    story: str                    # what happened, in one paragraph
    elements: list[dict]          # reproduces the defect
    expect_kind: str              # the warning kind that must fire
    expect_severity: str = "error"
    near_miss: list[dict] = field(default_factory=list)   # must stay clean
    near_miss_why: str = ""


CORPUS: tuple[Defect, ...] = (
    Defect(
        id="npth-hole-clearance",
        found="2026-08-10 — harness-puck, hydrate-coaster and terminal-keyboard, "
              "all three at once",
        story=(
            "The USB-C connector footprint leaves a 0.525mm channel between an "
            "alignment hole and the pin-1 shell pad. The autorouter does not "
            "model holes, so it threads J1's own shell-to-GND tie straight "
            "through, leaving 0.115mm where the fab needs 0.20mm to a "
            "non-plated hole. The drill's positional tolerance is comparable to "
            "that gap, so some boards in a batch would work and some would have "
            "a cut track — the worst way to fail. Three independent boards, one "
            "root cause, and the finding pointed at the board rather than at "
            "the footprint that caused it."
        ),
        elements=[
            board(),
            hole(2.90, -10.0, 0.65),
            # 0.115mm of clearance: 0.540mm centre-to-track distance,
            # minus the 0.325mm hole radius, minus the 0.1mm half-width.
            track([(-5.0, -9.46), (5.0, -9.46)]),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            hole(2.90, -10.0, 0.65),
            # 0.25mm clearance — above the 0.20mm NPTH floor, below the 0.28mm
            # plated one. Legal, and must not be flagged.
            track([(-5.0, -9.325), (5.0, -9.325)]),
        ],
        near_miss_why=(
            "0.25mm from a *non-plated* hole is legal — JLC's two rules are "
            "0.20mm NPTH and 0.28mm PTH. Judging every hole by the stricter "
            "figure is exactly the preference-as-floor mistake."
        ),
    ),
    Defect(
        id="pth-hole-clearance",
        found="2026-08-11 — reading jlcpcb.com/capabilities against the "
              "single-rule check",
        story=(
            "The hole-clearance check originally used one figure for every "
            "drill. A plated hole is a different manufacturing operation and "
            "needs 0.28mm, not 0.20mm — so a track 0.25mm from a plated hole "
            "passed a check that should have blocked it. One rule for two "
            "processes is a check that is wrong in one direction or the other, "
            "and this one was wrong in the dangerous direction."
        ),
        elements=[
            board(),
            hole(0.0, 0.0, 0.9, plated=True),
            # 0.25mm of clearance: 0.80mm distance - 0.45mm radius - 0.1mm
            # half-width. Legal beside an NPTH, illegal beside a plated one.
            track([(-5.0, 0.80), (5.0, 0.80)]),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            hole(0.0, 0.0, 0.9, plated=True),
            # 0.40mm — clear of both the 0.28mm floor and the warn threshold.
            track([(-5.0, 0.95), (5.0, 0.95)]),
        ],
        near_miss_why="0.40mm from a plated hole is comfortably legal.",
    ),
    Defect(
        id="track-terminating-at-a-hole",
        found="2026-08-11 — writing the hole-clearance rule",
        story=(
            "The first draft of the hole rule flagged the track that *connects* "
            "to a plated hole, because its endpoint is by definition zero "
            "millimetres away. A check that fires on every correctly connected "
            "through-hole part is noise, and noise trains everyone to ignore "
            "the gate — the failure mode this whole corpus exists to prevent."
        ),
        elements=[],   # nothing to catch; the assertion is the near miss
        expect_kind="",
        near_miss=[
            board(),
            hole(0.0, 0.0, 0.9, plated=True),
            track([(0.0, 0.0), (5.0, 0.0)]),
        ],
        near_miss_why="a track landing on a hole is its connection, not a violation.",
    ),
    Defect(
        id="via-judged-by-the-through-hole-rule",
        found="2026-08-10 — the skeleton board, 14 blocking errors down to 2",
        story=(
            "Vias were graded against the component through-hole annular-ring "
            "rule (0.20mm), which no ordinary via meets — the router's rings "
            "are a fraction of that, and JLC specs vias separately and much "
            "finer. The gate was set to a preference rather than a floor, so it "
            "rejected perfectly legal boards. Twelve of the skeleton's fourteen "
            "blocking errors were this one mistake."
        ),
        elements=[
            board(),
            # A genuinely sub-spec via: 0.05mm ring, below even the via floor.
            via(hole_d=0.3, outer_d=0.4),
        ],
        expect_kind="dfm_annular_ring",
        near_miss=[
            board(),
            # 0.15mm ring: fine for a via, would fail the through-hole rule.
            via(hole_d=0.3, outer_d=0.6),
        ],
        near_miss_why=(
            "a 0.15mm annular ring is a legal via and an illegal component "
            "through-hole. Judge each by its own rule or the check is noise."
        ),
    ),
    Defect(
        id="component-through-hole-annular-ring",
        found="2026-08-10 — the same fix, from the other side",
        story=(
            "Splitting the via rule out must not soften the rule that still "
            "applies to component holes: a leg through a barrel with a 0.10mm "
            "ring is a real reliability defect. The corpus pins both sides so "
            "the split cannot be widened into a loophole."
        ),
        elements=[board(), plated_hole(hole_d=0.8, outer_d=1.0)],
        expect_kind="dfm_annular_ring",
        near_miss=[board(), plated_hole(hole_d=0.8, outer_d=1.3)],
        near_miss_why="0.25mm of ring on a component hole clears the 0.20mm floor.",
    ),
    Defect(
        id="copper-past-the-board-edge",
        found="2026-08-10 — the ground-plane investigation",
        story=(
            "`<copperpour>` fills to exactly 0.200mm of the board edge and "
            "cannot be told otherwise. The edge gate was blocking at 0.30mm, so "
            "no board could carry a ground plane at all. 0.30mm is the *V-cut* "
            "figure; 0.20mm is the routed-outline floor, and the condensed "
            "research table had dropped that parenthetical. The gate was "
            "blocking a legal geometry — the third preference-as-floor mistake "
            "of one evening."
        ),
        elements=[board(40.0, 30.0), pad(19.95, 0.0)],
        expect_kind="dfm_edge_clearance",
        near_miss=[board(40.0, 30.0), pad(19.5, 0.0)],
        near_miss_why=(
            "0.20mm of copper-to-edge is JLC's routed-outline floor, which is "
            "exactly where a copper pour lands. Blocking there bans ground planes."
        ),
    ),
    Defect(
        id="board-below-the-fab-minimum",
        found="2026-08-10 — pipeline bring-up",
        story=(
            "A board smaller than 3x3mm cannot be routed out of a panel. It is "
            "the cheapest possible catch and it belongs in the corpus because "
            "'make it as small as you sensibly can' is a thing users say."
        ),
        elements=[board(2.0, 2.0)],
        expect_kind="dfm_board_size",
        near_miss=[board(10.0, 10.0)],
        near_miss_why="10x10mm is a perfectly ordinary small board.",
    ),
    Defect(
        id="board-over-the-product-envelope",
        found="2026-08-10 — the enclosure handoff",
        story=(
            "The board is going inside a printed body whose size is already "
            "decided in product.json. A board that outgrows it is a product "
            "failure discovered at assembly, which is the most expensive place "
            "to discover it."
        ),
        elements=[board(120.0, 90.0)],
        expect_kind="board_exceeds_envelope",
        near_miss=[board(40.0, 30.0)],
        near_miss_why="inside the declared envelope, so nothing to say.",
    ),
)


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def _kinds(elements: list[dict], *, envelope=(60.0, 40.0)) -> list[dict]:
    return checks.dfm_warnings(elements, _product(envelope), PROFILE)


class FailureCorpus(unittest.TestCase):
    """Each defect must still be caught; each near miss must stay clean."""

    def test_every_defect_is_still_caught(self) -> None:
        for defect in CORPUS:
            if not defect.expect_kind:
                continue
            with self.subTest(defect=defect.id):
                envelope = (60.0, 40.0)
                warnings = _kinds(defect.elements, envelope=envelope)
                matching = [
                    w for w in warnings
                    if w["kind"] == defect.expect_kind
                    and w["severity"] == defect.expect_severity
                ]
                self.assertTrue(
                    matching,
                    f"{defect.id}: the check that caught this stopped catching "
                    f"it. Found {[(w['kind'], w['severity']) for w in warnings]}. "
                    f"Incident: {defect.found}",
                )

    def test_every_near_miss_stays_clean(self) -> None:
        """The other half of a floor: legal geometry must not be flagged.

        A check that fires here is a check people will learn to ignore, and an
        ignored check is worse than no check.
        """
        for defect in CORPUS:
            if not defect.near_miss:
                continue
            with self.subTest(defect=defect.id):
                warnings = _kinds(defect.near_miss, envelope=(60.0, 40.0))
                blocking = [w for w in warnings if w["severity"] == "error"]
                self.assertFalse(
                    blocking,
                    f"{defect.id}: legal geometry flagged as an error — "
                    f"{[(w['kind'], w['detail']) for w in blocking]}. "
                    f"{defect.near_miss_why}",
                )

    def test_corpus_entries_carry_their_incident(self) -> None:
        """A fixture nobody can attribute is a fixture nobody dares change."""
        for defect in CORPUS:
            with self.subTest(defect=defect.id):
                self.assertRegex(defect.found, r"20\d\d-\d\d-\d\d")
                self.assertGreater(
                    len(defect.story), 120,
                    "say what happened — the next person needs the reason, "
                    "not just the assertion",
                )

    def test_checks_never_raise_on_corpus_geometry(self) -> None:
        """Contract §1: a verifier may never break generation."""
        for defect in CORPUS:
            for elements in (defect.elements, defect.near_miss):
                if not elements:
                    continue
                with self.subTest(defect=defect.id):
                    checks.dfm_warnings(elements, _product(None), PROFILE)
                    checks.harvest_circuit_json(elements)
                    checks.iou_warnings(elements, PROFILE)


if __name__ == "__main__":
    unittest.main()


class RoutingEscalation(unittest.TestCase):
    """Stage 0b's decision logic, without paying for a twenty-minute build.

    The escalation itself is measured end to end elsewhere (harness-puck: 5
    blocking errors to 1 at ``"5x"``, 1240s). What has to be pinned here is
    *when* it fires and *what it refuses to touch* — an escalation that runs on
    a placement overlap burns twelve minutes to reproduce the same verdict, and
    one that overwrites an author's own effort setting silently disagrees with
    the source.
    """

    def test_only_routing_class_errors_escalate(self) -> None:
        from circuitpy import generation

        blockers = generation._routing_blockers([
            {"severity": "error", "kind": "dfm_hole_clearance"},
            {"severity": "error", "kind": "pcb_autorouting_error"},
            # Placement and footprint problems: a harder router cannot help.
            {"severity": "error", "kind": "pcb_footprint_overlap_error"},
            {"severity": "error", "kind": "pcb_component_outside_board_error"},
            {"severity": "error", "kind": "board_exceeds_envelope"},
            # Not blocking at all.
            {"severity": "warning", "kind": "pcb_autorouting_error"},
        ])
        self.assertEqual(
            sorted(b["kind"] for b in blockers),
            ["dfm_hole_clearance", "pcb_autorouting_error"],
        )

    def test_effort_is_injected_once_and_never_over_the_author(self) -> None:
        import tempfile

        from circuitpy import generation

        tmp = Path(tempfile.mkdtemp(prefix="escalation-"))
        board = tmp / "main.tsx"
        board.write_text(
            'export default () => (\n  <board width="20mm" height="20mm" '
            "thickness={1.6}>\n  </board>\n)\n",
            encoding="utf-8",
        )
        self.assertTrue(generation._set_autorouter_effort(board, "5x"))
        self.assertIn('autorouterEffortLevel="5x"', board.read_text())
        # Idempotent: a second pass must not stack a second prop.
        self.assertFalse(generation._set_autorouter_effort(board, "5x"))
        self.assertEqual(board.read_text().count("autorouterEffortLevel"), 1)

        # An author who turned routing off meant it.
        off = tmp / "off.tsx"
        off.write_text('<board routingDisabled={true}>', encoding="utf-8")
        self.assertFalse(generation._set_autorouter_effort(off, "5x"))
        self.assertNotIn("autorouterEffortLevel", off.read_text())
