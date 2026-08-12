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
        id="via-drill-to-track-at-0132mm",
        found="2026-08-12 — rp2040-core KiCad drill-clearance audit",
        story=(
            "The stage-4 gate inspected component holes but never treated a "
            "pcb_via as a drill. KiCad therefore found a routed track only "
            "0.132mm from a via drill after the pipeline had already called "
            "the circuit artifact clean. A late-only finding cannot trigger "
            "routing escalation, so this exact geometry belongs in the early "
            "gate and in the permanent corpus."
        ),
        elements=[
            board(),
            dict(
                via(hole_d=0.3, outer_d=0.6),
                subcircuit_connectivity_map_key="via-net",
            ),
            dict(
                track([(-3.0, 0.382), (3.0, 0.382)], width=0.2),
                subcircuit_connectivity_map_key="other-net",
            ),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            dict(
                via(hole_d=0.3, outer_d=0.6),
                subcircuit_connectivity_map_key="via-net",
            ),
            dict(
                track([(-3.0, 0.451), (3.0, 0.451)], width=0.2),
                subcircuit_connectivity_map_key="other-net",
            ),
        ],
        near_miss_why=(
            "0.201mm from the via drill is just beyond the distinct 0.20mm "
            "via-hole-to-copper floor and must remain legal."
        ),
    ),
    Defect(
        id="via-drill-to-smd-pad-at-0148mm",
        found="2026-08-12 — rp2040-core KiCad drill-clearance audit",
        story=(
            "The same missing via-drill model ignored SMD copper entirely. "
            "KiCad measured 0.148mm from a via drill to a neighbouring SMD "
            "pad, while stage 4 only searched component-hole-to-track pairs. "
            "This pins the second observed distance and the copper-kind split: "
            "restoring trace checks alone is not enough."
        ),
        elements=[
            board(),
            dict(
                via(hole_d=0.3, outer_d=0.6),
                subcircuit_connectivity_map_key="via-net",
            ),
            dict(
                pad(0.498, 0.0, w=0.4, h=0.4),
                subcircuit_connectivity_map_key="other-net",
            ),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            dict(
                via(hole_d=0.3, outer_d=0.6),
                subcircuit_connectivity_map_key="via-net",
            ),
            dict(
                pad(0.551, 0.0, w=0.4, h=0.4),
                subcircuit_connectivity_map_key="other-net",
            ),
        ],
        near_miss_why=(
            "The pad begins 0.201mm beyond the via drill edge, so the exact "
            "different-net geometry clears the 0.20mm floor."
        ),
    ),
    Defect(
        id="usb-c-slot-endpoint-modeled-as-a-circle",
        found="2026-08-12 — USB-C imported-footprint rule audit",
        story=(
            "The checker reduced every hole to hole_width/2 around its centre. "
            "A USB-C shell drill is a 0.8 by 1.6mm routed slot, so that shortcut "
            "discarded 0.4mm of drill travel at each endpoint and could call "
            "copper beside the real slot legal. The drill must be the stadium "
            "swept by the round tool, including component rotation."
        ),
        elements=[
            board(),
            {
                "type": "pcb_plated_hole",
                "pcb_plated_hole_id": "usb_slot",
                "x": 0.0,
                "y": 0.0,
                "shape": "pill",
                "hole_width": 0.8,
                "hole_height": 1.6,
                "outer_width": 1.2,
                "outer_height": 2.0,
                "subcircuit_connectivity_map_key": "shell-net",
            },
            dict(
                track([(-3.0, 1.0), (3.0, 1.0)], width=0.15),
                subcircuit_connectivity_map_key="other-net",
            ),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            {
                "type": "pcb_plated_hole",
                "pcb_plated_hole_id": "usb_slot",
                "x": 0.0,
                "y": 0.0,
                "shape": "pill",
                "hole_width": 0.8,
                "hole_height": 1.6,
                "outer_width": 1.2,
                "outer_height": 2.0,
                "subcircuit_connectivity_map_key": "shell-net",
            },
            dict(
                track([(-3.0, 1.155), (3.0, 1.155)], width=0.15),
                subcircuit_connectivity_map_key="other-net",
            ),
        ],
        near_miss_why=(
            "The trace is exactly 0.280mm from the swept slot endpoint: at the "
            "PTH floor, therefore legal even though it remains below the "
            "optional 0.35mm preference."
        ),
    ),
    Defect(
        id="hole-rule-applied-to-the-hole-s-own-pad",
        found="2026-08-12 — harness-puck and hydrate-coaster shell-tie audit",
        story=(
            "A geometry-only pass counted copper still inside a plated hole's "
            "own annular pad as an unrelated track and reported the annular "
            "ring itself as a clearance defect. Compiled net identity is the "
            "primary exemption; when old artifacts carry no identity, copper "
            "wholly contained by the feature's own pad is still the connection, "
            "not a spacing violation."
        ),
        elements=[],
        expect_kind="",
        near_miss=[
            board(),
            plated_hole(hole_d=0.8, outer_d=1.2),
            track([(0.45, 0.0), (0.50, 0.0)], width=0.1),
        ],
        near_miss_why=(
            "The unidentified trace capsule is wholly inside the PTH's own "
            "1.2mm annular pad and must not be judged as foreign copper."
        ),
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

    The old harness/Terminal improvement claim was cache-contaminated and is
    explicitly withdrawn. What this unit owns is only *when* a bounded alternate
    candidate may run and *what it refuses to touch* — an escalation that runs
    on a placement overlap burns twelve minutes to reproduce the same verdict,
    and one that overwrites an author's own effort setting silently disagrees
    with the source. Any claim that 5x improves a real board still requires a
    cold, configuration-keyed, end-to-end comparison of parsed artifacts.
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

        authored = tmp / "authored.tsx"
        authored.write_text(
            '<board autorouterEffortLevel="10x"></board>', encoding="utf-8"
        )
        self.assertEqual(generation._source_routing_effort(authored), "10x")
        self.assertFalse(generation._set_autorouter_effort(authored, "5x"))

        dynamic = tmp / "dynamic.tsx"
        dynamic.write_text(
            "<board autorouterEffortLevel={chosen}></board>", encoding="utf-8"
        )
        self.assertEqual(generation._source_routing_effort(dynamic), "authored")
        self.assertFalse(generation._set_autorouter_effort(dynamic, "5x"))

        definite_false = tmp / "definite-false.tsx"
        definite_false.write_text(
            '<board routingDisabled={false} width={value > 0 ? "20mm" : "10mm"}>',
            encoding="utf-8",
        )
        self.assertEqual(
            generation._source_routing_effort(definite_false), "default"
        )
        self.assertTrue(generation._set_autorouter_effort(definite_false, "5x"))

        default_false_wrapper = tmp / "default-false-wrapper.tsx"
        default_false_wrapper.write_text(
            "<board\n"
            "  routingDisabled={props.routingDisabled ?? false}\n"
            "  width={pickWidth(value > threshold)}\n"
            ">",
            encoding="utf-8",
        )
        self.assertEqual(
            generation._source_routing_effort(default_false_wrapper), "default"
        )
        self.assertTrue(
            generation._set_autorouter_effort(default_false_wrapper, "5x")
        )

        dynamic_disabled = tmp / "dynamic-disabled.tsx"
        dynamic_disabled.write_text(
            "<board routingDisabled={chosen}></board>", encoding="utf-8"
        )
        self.assertEqual(
            generation._source_routing_effort(dynamic_disabled), "authored"
        )
        self.assertFalse(
            generation._set_autorouter_effort(dynamic_disabled, "5x")
        )

        true_overrides_effort = tmp / "true-overrides-effort.tsx"
        true_overrides_effort.write_text(
            '<board routingDisabled autorouterEffortLevel="10x"></board>',
            encoding="utf-8",
        )
        self.assertEqual(
            generation._source_routing_effort(true_overrides_effort), "disabled"
        )

    def test_changed_retry_clears_only_the_private_route_cache(self) -> None:
        import tempfile

        from circuitpy import generation

        with tempfile.TemporaryDirectory(prefix="escalation-cache-") as tmp:
            root = Path(tmp)
            work = root / "private-build-mirror"
            cache = work / ".tscircuit" / "cache"
            cache.mkdir(parents=True)
            (cache / "stale-route.json").write_text("attempt-one", encoding="utf-8")
            project_cache = root / "user-project" / ".tscircuit" / "cache"
            project_cache.mkdir(parents=True)
            (project_cache / "keep.json").write_text("user-owned", encoding="utf-8")

            self.assertTrue(generation._clear_tscircuit_route_cache(work))
            self.assertFalse(cache.exists())
            self.assertEqual(
                (project_cache / "keep.json").read_text(encoding="utf-8"),
                "user-owned",
            )
            self.assertFalse(generation._clear_tscircuit_route_cache(work))

    def test_retry_stash_removes_the_live_artifact_before_the_next_cli(self) -> None:
        import tempfile

        from circuitpy import generation

        with tempfile.TemporaryDirectory(prefix="retry-artifact-") as tmp:
            built = Path(tmp) / "dist" / "main"
            built.mkdir(parents=True)
            (built / "circuit.json").write_text("attempt-one", encoding="utf-8")
            kept = generation._stash_completed_build_for_retry(built)
            self.assertFalse(built.exists())
            self.assertEqual(
                (kept / "circuit.json").read_text(encoding="utf-8"),
                "attempt-one",
            )

    def test_completed_attempt_evidence_is_content_addressed_and_parsed(self) -> None:
        import json
        import tempfile

        from circuitpy import generation

        with tempfile.TemporaryDirectory(prefix="effort-evidence-") as tmp:
            boards = Path(tmp) / "boards"
            boards.mkdir()
            artifact = boards / "main.circuit.json"
            artifact.write_text(
                json.dumps(
                    [
                        {
                            "type": "pcb_trace_error",
                            "pcb_trace_error_id": "error_1",
                            "message": "first",
                        },
                        {
                            "type": "pcb_trace_error",
                            "pcb_trace_error_id": "error_2",
                            "message": "second",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            warnings = generation._pre_export_scan(
                artifact, _product(), PROFILE
            )
            evidence = generation._routing_attempt_evidence(
                attempt_index=1,
                effort="default",
                warnings=warnings,
                circuit_json_path=artifact,
                staged_dir=Path(tmp) / "staged",
                stem="main",
            )
            generation._publish_routing_attempt_evidence(
                Path(tmp) / "staged", boards, "main"
            )
            self.assertEqual(evidence["effort"], "default")
            self.assertEqual(evidence["status"], "completed")
            self.assertRegex(str(evidence["circuitSha256"]), r"^[0-9a-f]{64}$")
            self.assertTrue((boards / str(evidence["circuitPath"])).is_file())
            self.assertTrue(
                (boards / str(evidence["preExportScanPath"])).is_file()
            )
            self.assertEqual(evidence["blocking"], 2)
            self.assertEqual(evidence["routingBlocking"], 2)
            self.assertEqual(
                evidence["blockingKinds"],
                {"pcb_trace_error": 2},
            )

            build = {
                "autorouterEffort": "default",
                "attempts": 2,
                "blockingByAttempt": [2],
                "attemptEvidence": [
                    evidence,
                    {"effort": "5x", "status": "failed"},
                ],
            }
            self.assertIsNone(
                generation.routing_attempt_evidence_error(
                    build,
                    circuit_json_path=artifact,
                    final_warnings=warnings,
                    fab_ready=False,
                    product=_product(),
                    profile=PROFILE,
                )
            )
            build["attemptEvidence"][0]["circuitSha256"] = "0" * 64
            self.assertEqual(
                generation.routing_attempt_evidence_error(
                    build,
                    circuit_json_path=artifact,
                    product=_product(),
                    profile=PROFILE,
                ),
                "build.attemptEvidence[0].circuitPath is not its canonical "
                "content-addressed path",
            )

    def test_attempt_evidence_enforces_the_bounded_winner_state_machine(self) -> None:
        from circuitpy import generation

        def completed(
            effort: str,
            blocking: int,
            kind: str = "pcb_trace_error",
            attempt: int = 1,
        ) -> dict:
            circuit_sha = "a" * 64
            scan_sha = "b" * 64
            return {
                "effort": effort,
                "status": "completed",
                "circuitPath": (
                    f"main_attempts/attempt-{attempt}-{circuit_sha}.circuit.json"
                ),
                "circuitSha256": circuit_sha,
                "preExportScanPath": (
                    "main_attempts/"
                    f"attempt-{attempt}-{scan_sha}.pre-export-scan.json"
                ),
                "preExportScanSha256": scan_sha,
                "blocking": blocking,
                "routingBlocking": blocking,
                "blockingKinds": ({kind: blocking} if blocking else {}),
            }

        cases = {
            "selected-worse": {
                "autorouterEffort": "5x",
                "attempts": 2,
                "blockingByAttempt": [0, 2],
                "attemptEvidence": [
                    completed("default", 0),
                    completed("5x", 2, attempt=2),
                ],
            },
            "ignored-better": {
                "autorouterEffort": "default",
                "attempts": 2,
                "blockingByAttempt": [2, 0],
                "attemptEvidence": [
                    completed("default", 2),
                    completed("5x", 0, attempt=2),
                ],
            },
            "failed-primary": {
                "autorouterEffort": "5x",
                "attempts": 2,
                "blockingByAttempt": [0],
                "attemptEvidence": [
                    {"effort": "default", "status": "failed"},
                    completed("5x", 0, attempt=2),
                ],
            },
            "three-attempts": {
                "autorouterEffort": "default",
                "attempts": 3,
                "blockingByAttempt": [0, 0, 0],
                "attemptEvidence": [
                    completed("default", 0),
                    completed("5x", 0, attempt=2),
                    completed("10x", 0, attempt=3),
                ],
            },
            "routing-count-lie": {
                "autorouterEffort": "default",
                "attempts": 1,
                "blockingByAttempt": [1],
                "attemptEvidence": [
                    {
                        **completed("default", 1),
                        "routingBlocking": 0,
                    }
                ],
            },
            "failed-with-artifact": {
                "autorouterEffort": "default",
                "attempts": 2,
                "blockingByAttempt": [1],
                "attemptEvidence": [
                    completed("default", 1),
                    {
                        "effort": "5x",
                        "status": "failed",
                        "circuitSha256": "b" * 64,
                    },
                ],
            },
            "retry-without-routing-primary": {
                "autorouterEffort": "default",
                "attempts": 2,
                "blockingByAttempt": [1],
                "attemptEvidence": [
                    {
                        **completed("default", 1, "board_exceeds_envelope"),
                        "routingBlocking": 0,
                    },
                    {"effort": "5x", "status": "failed"},
                ],
            },
        }
        for label, build in cases.items():
            with self.subTest(label=label):
                self.assertIsNotNone(generation.routing_attempt_evidence_error(build))

        authored = {
            "autorouterEffort": "10x",
            "attempts": 1,
            "blockingByAttempt": [0],
            "attemptEvidence": [completed("10x", 0)],
        }
        self.assertIsNone(generation.routing_attempt_evidence_error(authored))

    def test_every_completed_attempt_is_retained_and_independently_checked(self) -> None:
        import hashlib
        import json
        import tempfile

        from circuitpy import generation

        def make_fixture(root: Path, *, retry: bool = True):
            boards = root / "boards"
            boards.mkdir()
            selected = boards / "main.circuit.json"
            staged = root / "staged"
            selected.write_text(
                json.dumps(
                    [
                        {
                            "type": "pcb_trace_error",
                            "pcb_trace_error_id": "primary_error",
                            "message": "primary route is blocked",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            primary_warnings = generation._pre_export_scan(
                selected, _product(), PROFILE
            )
            primary_warning = primary_warnings[0]
            primary = generation._routing_attempt_evidence(
                attempt_index=1,
                effort="default",
                warnings=primary_warnings,
                circuit_json_path=selected,
                staged_dir=staged,
                stem="main",
            )
            records = [primary]
            blockers = [1]
            selected_effort = "default"
            final_warnings = [primary_warning]
            if retry:
                selected.write_text(
                    "[]",
                    encoding="utf-8",
                )
                retry_warnings = generation._pre_export_scan(
                    selected, _product(), PROFILE
                )
                alternate = generation._routing_attempt_evidence(
                    attempt_index=2,
                    effort="5x",
                    warnings=retry_warnings,
                    circuit_json_path=selected,
                    staged_dir=staged,
                    stem="main",
                )
                records.append(alternate)
                blockers.append(0)
                selected_effort = "5x"
                final_warnings = []
            generation._publish_routing_attempt_evidence(staged, boards, "main")
            build = {
                "autorouterEffort": selected_effort,
                "attempts": len(records),
                "blockingByAttempt": blockers,
                "attemptEvidence": records,
            }
            return boards, selected, build, primary_warning, final_warnings

        with tempfile.TemporaryDirectory(prefix="retained-attempts-") as tmp:
            boards, selected, build, _warning, final_warnings = make_fixture(
                Path(tmp)
            )
            self.assertIsNone(
                generation.routing_attempt_evidence_error(
                    build,
                    circuit_json_path=selected,
                    final_warnings=final_warnings,
                    fab_ready=True,
                    product=_product(),
                    profile=PROFILE,
                )
            )
            retained = sorted((boards / "main_attempts").iterdir())
            self.assertEqual(len(retained), 4)
            self.assertEqual(
                {path.suffix for path in retained}, {".json"}
            )

        cases = (
            "missing-unselected-circuit",
            "mutated-unselected-scan",
            "path-traversal",
            "fabricated-histogram",
            "self-consistent-fabricated-scan",
            "noncanonical-scan",
            "selected-main-drift",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(
                prefix=f"retained-{case}-"
            ) as tmp:
                boards, selected, build, _warning, final_warnings = make_fixture(
                    Path(tmp)
                )
                primary = build["attemptEvidence"][0]
                if case == "missing-unselected-circuit":
                    (boards / primary["circuitPath"]).unlink()
                elif case == "mutated-unselected-scan":
                    with (boards / primary["preExportScanPath"]).open("ab") as handle:
                        handle.write(b"\n")
                elif case == "path-traversal":
                    primary["circuitPath"] = "../outside.circuit.json"
                elif case == "fabricated-histogram":
                    primary["blocking"] = 2
                    primary["routingBlocking"] = 2
                    primary["blockingKinds"] = {"pcb_trace_error": 2}
                    build["blockingByAttempt"][0] = 2
                elif case == "self-consistent-fabricated-scan":
                    old_scan = boards / primary["preExportScanPath"]
                    payload = json.loads(old_scan.read_text(encoding="utf-8"))
                    fabricated_circuit = b"[]"
                    circuit_digest = hashlib.sha256(fabricated_circuit).hexdigest()
                    circuit_relative = generation._attempt_relative_path(
                        "main", 1, circuit_digest, "circuit.json"
                    )
                    (boards / circuit_relative).write_bytes(fabricated_circuit)
                    primary["circuitPath"] = circuit_relative
                    primary["circuitSha256"] = circuit_digest
                    payload["circuitSha256"] = circuit_digest
                    payload["warnings"] = [
                        {
                            "part": "TR_FAKE",
                            "kind": "pcb_trace_error",
                            "detail": "fabricated but schema-shaped",
                            "severity": "error",
                        }
                    ]
                    fabricated = generation._canonical_json(payload).encode("utf-8")
                    digest = hashlib.sha256(fabricated).hexdigest()
                    relative = generation._attempt_relative_path(
                        "main", 1, digest, "pre-export-scan.json"
                    )
                    (boards / relative).write_bytes(fabricated)
                    primary["preExportScanPath"] = relative
                    primary["preExportScanSha256"] = digest
                elif case == "noncanonical-scan":
                    old_scan = boards / primary["preExportScanPath"]
                    payload = json.loads(old_scan.read_text(encoding="utf-8"))
                    noncanonical = json.dumps(payload, indent=2).encode("utf-8")
                    digest = hashlib.sha256(noncanonical).hexdigest()
                    relative = generation._attempt_relative_path(
                        "main", 1, digest, "pre-export-scan.json"
                    )
                    target = boards / relative
                    target.write_bytes(noncanonical)
                    primary["preExportScanPath"] = relative
                    primary["preExportScanSha256"] = digest
                elif case == "selected-main-drift":
                    selected.write_text(
                        '[{"type":"pcb_board"}]', encoding="utf-8"
                    )
                error = generation.routing_attempt_evidence_error(
                    build,
                    circuit_json_path=selected,
                    final_warnings=final_warnings,
                    fab_ready=True,
                    product=_product(),
                    profile=PROFILE,
                )
                self.assertIsNotNone(error, case)

    def test_attempt_scan_is_deduped_and_sorted_before_selection(self) -> None:
        from circuitpy import generation

        first = {
            "part": "U2",
            "kind": "z_warning",
            "detail": "later",
            "severity": "warning",
        }
        second = {
            "part": "U1",
            "kind": "a_error",
            "detail": "earlier",
            "severity": "error",
        }
        weaker_duplicate = dict(second, severity="warning")

        self.assertEqual(
            generation._canonical_attempt_scan(
                [first, weaker_duplicate, second, first]
            ),
            [second, first],
        )

    def test_attempt_publication_prunes_stale_files_and_rolls_back(self) -> None:
        import tempfile
        from unittest import mock

        from circuitpy import generation
        from circuitpy.errors import ExportError

        with tempfile.TemporaryDirectory(prefix="attempt-publish-") as tmp:
            root = Path(tmp)
            boards = root / "boards"
            boards.mkdir()
            first = root / "first"
            first.mkdir()
            (first / "attempt-1-a.circuit.json").write_text(
                "first", encoding="utf-8"
            )
            generation._publish_routing_attempt_evidence(first, boards, "main")
            target = boards / "main_attempts"
            (target / "stale-unreferenced.json").write_text(
                "stale", encoding="utf-8"
            )

            second = root / "second"
            second.mkdir()
            (second / "attempt-1-b.circuit.json").write_text(
                "second", encoding="utf-8"
            )
            generation._publish_routing_attempt_evidence(second, boards, "main")
            self.assertEqual(
                {path.name for path in target.iterdir()},
                {"attempt-1-b.circuit.json"},
            )

            prior = {
                path.name: path.read_bytes() for path in target.iterdir()
            }
            third = root / "third"
            third.mkdir()
            (third / "attempt-1-c.circuit.json").write_text(
                "third", encoding="utf-8"
            )
            real_replace = generation.os.replace

            def fail_final_swap(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    source_path.name.startswith(".main_attempts.staged-")
                    and destination_path == target
                ):
                    raise OSError("synthetic final rename failure")
                return real_replace(source, destination)

            with mock.patch.object(
                generation.os, "replace", side_effect=fail_final_swap
            ):
                with self.assertRaises(ExportError):
                    generation._publish_routing_attempt_evidence(
                        third, boards, "main"
                    )
            self.assertEqual(
                {path.name: path.read_bytes() for path in target.iterdir()},
                prior,
            )

    def test_board_evidence_transaction_restores_prior_set_on_late_failure(self) -> None:
        import tempfile
        from unittest import mock

        from circuitpy import generation
        from circuitpy.errors import ExportError

        for failure in ("sidecar-write", "sidecar", "final-ir"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(
                prefix=f"board-evidence-{failure}-"
            ) as tmp:
                root = Path(tmp)
                boards = root / "boards"
                boards.mkdir()
                sidecar = boards / "main.board.json"
                output = boards / "main.circuit.json"
                sidecar.write_bytes(b"prior-sidecar")
                output.write_bytes(b"prior-circuit")
                prior_attempts = boards / "main_attempts"
                prior_attempts.mkdir()
                (prior_attempts / "prior.json").write_bytes(b"prior-attempt")
                prior_bytes = {
                    "sidecar": sidecar.read_bytes(),
                    "circuit": output.read_bytes(),
                    "attempt": (prior_attempts / "prior.json").read_bytes(),
                }

                staged_attempts = root / "staged-attempts"
                staged_attempts.mkdir()
                (staged_attempts / "next.json").write_bytes(b"next-attempt")
                built_circuit = root / "built.circuit.json"
                built_circuit.write_bytes(b"next-circuit")
                real_replace = generation.os.replace
                real_write_bytes = generation.Path.write_bytes

                def fail_write(path, data):
                    path = Path(path)
                    if (
                        failure == "sidecar-write"
                        and path.name.startswith(".main.board.json.staged-")
                    ):
                        raise OSError("synthetic staged sidecar write failure")
                    return real_write_bytes(path, data)

                def fail_target(source, destination):
                    source_path = Path(source)
                    destination_path = Path(destination)
                    if (
                        failure == "sidecar"
                        and source_path.name.startswith(".main.board.json.staged-")
                        and destination_path == sidecar
                    ):
                        raise OSError("synthetic sidecar publish failure")
                    if (
                        failure == "final-ir"
                        and source_path.name.startswith(".main.circuit.json.staged-")
                        and destination_path == output
                    ):
                        raise OSError("synthetic final IR publish failure")
                    return real_replace(source, destination)

                with (
                    mock.patch.object(
                        generation.Path,
                        "write_bytes",
                        autospec=True,
                        side_effect=fail_write,
                    ),
                    mock.patch.object(
                        generation.os, "replace", side_effect=fail_target
                    ),
                ):
                    with self.assertRaises(ExportError):
                        generation._publish_board_evidence_transaction(
                            staged_attempt_dir=staged_attempts,
                            boards_dir=boards,
                            stem="main",
                            sidecar_path=sidecar,
                            sidecar_bytes=b"next-sidecar",
                            built_circuit_json=built_circuit,
                            output_path=output,
                        )

                self.assertEqual(sidecar.read_bytes(), prior_bytes["sidecar"])
                self.assertEqual(output.read_bytes(), prior_bytes["circuit"])
                self.assertEqual(
                    {path.name: path.read_bytes() for path in prior_attempts.iterdir()},
                    {"prior.json": prior_bytes["attempt"]},
                )

    def test_selected_scan_must_survive_final_validation_and_fab_state(self) -> None:
        import json
        import tempfile

        from circuitpy import generation

        with tempfile.TemporaryDirectory(prefix="selected-scan-ledger-") as tmp:
            root = Path(tmp)
            boards = root / "boards"
            boards.mkdir()
            selected = boards / "main.circuit.json"
            selected.write_text(
                json.dumps(
                    [
                        {
                            "type": "pcb_trace_error",
                            "pcb_trace_error_id": "selected_error",
                            "message": "must survive into final validation",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            scan_warnings = generation._pre_export_scan(
                selected, _product(), PROFILE
            )
            warning = scan_warnings[0]
            staged = root / "staged"
            record = generation._routing_attempt_evidence(
                attempt_index=1,
                effort="default",
                warnings=scan_warnings,
                circuit_json_path=selected,
                staged_dir=staged,
                stem="main",
            )
            generation._publish_routing_attempt_evidence(staged, boards, "main")
            build = {
                "autorouterEffort": "default",
                "attempts": 1,
                "blockingByAttempt": [1],
                "attemptEvidence": [record],
            }
            self.assertEqual(
                generation.routing_attempt_evidence_error(
                    build,
                    circuit_json_path=selected,
                    final_warnings=[],
                    fab_ready=False,
                    product=_product(),
                    profile=PROFILE,
                ),
                "final validation omits a selected pre-export scan finding",
            )
            self.assertEqual(
                generation.routing_attempt_evidence_error(
                    build,
                    circuit_json_path=selected,
                    final_warnings=[warning],
                    fab_ready=True,
                    product=_product(),
                    profile=PROFILE,
                ),
                "fab.ready contradicts blocking selected pre-export evidence",
            )
