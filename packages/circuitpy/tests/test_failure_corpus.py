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


def hole(x: float, y: float, diameter: float, *, plated: bool = False,
         annular: float = 0.3) -> dict:
    """A drill. ``annular`` is the plated pad's ring width — the copper the
    fab *requires* around the barrel. It matters to every hole-clearance
    fixture: copper closer to the drill than the ring is copper *under the
    pad*, which is a short or a connection, never a drill-clearance defect."""
    if plated:
        return {
            "type": "pcb_plated_hole", "pcb_plated_hole_id": f"ph_{x}_{y}",
            "x": x, "y": y, "hole_diameter": diameter,
            "outer_diameter": diameter + 2 * annular,
        }
    return {
        "type": "pcb_hole", "pcb_hole_id": f"h_{x}_{y}",
        "x": x, "y": y, "hole_diameter": diameter,
    }


def net(net_id: str, *, name: str = "GND") -> dict:
    return {
        "type": "source_net", "source_net_id": net_id, "name": name,
        "subcircuit_connectivity_map_key": f"conn_{net_id}",
    }


def hole_on_net(x: float, y: float, diameter: float, net_id: str, *,
                annular: float = 0.2, hole_h: float | None = None) -> list[dict]:
    """A plated hole wired to a net, with the three elements that carry the
    connection: ``pcb_plated_hole`` -> ``pcb_port`` -> ``source_port``."""
    tag = f"{x}_{y}"
    return [
        {
            "type": "pcb_plated_hole", "pcb_plated_hole_id": f"ph_{tag}",
            "pcb_port_id": f"pp_{tag}", "x": x, "y": y,
            "shape": "pill" if hole_h else "circle",
            "hole_width": diameter, "hole_height": hole_h or diameter,
            "outer_width": diameter + 2 * annular,
            "outer_height": (hole_h or diameter) + 2 * annular,
        },
        {
            "type": "pcb_port", "pcb_port_id": f"pp_{tag}",
            "source_port_id": f"sp_{tag}", "x": x, "y": y,
        },
        {
            "type": "source_port", "source_port_id": f"sp_{tag}", "name": "EH1",
            "subcircuit_connectivity_map_key": f"conn_{net_id}",
        },
    ]


def track(points: list[tuple[float, float]], *, width: float = 0.2,
          trace_id: str = "t1", net_id: str | None = None) -> dict:
    out = {
        "type": "pcb_trace", "pcb_trace_id": trace_id,
        "route": [
            {"route_type": "wire", "x": x, "y": y, "width": width, "layer": "top"}
            for x, y in points
        ],
    }
    if net_id:
        out["connection_name"] = net_id
    return out


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


def pad_on_net(x: float, y: float, net_id: str, *, w: float, h: float,
               shape: str = "rect", rotation: float = 0.0) -> list[dict]:
    """An SMD pad wired to a net, with its ``ccw_rotation`` carried through.

    The net matters to every hole-clearance fixture for the same reason it
    does to the check: copper on the drill's own net is the connection, not a
    violation, so a fixture with no nets tests a different rule than the one
    that fires on a board.
    """
    tag = f"{x}_{y}".replace("-", "m")
    return [
        {
            "type": "pcb_smtpad", "pcb_smtpad_id": f"p_{tag}",
            "pcb_port_id": f"pp_{tag}", "shape": shape, "layer": "top",
            "x": x, "y": y, "width": w, "height": h, "ccw_rotation": rotation,
        },
        {
            "type": "pcb_port", "pcb_port_id": f"pp_{tag}",
            "source_port_id": f"sp_{tag}", "x": x, "y": y,
        },
        {
            "type": "source_port", "source_port_id": f"sp_{tag}", "name": "VCC",
            "subcircuit_connectivity_map_key": f"conn_{net_id}",
        },
    ]


def via_on_net(x: float, y: float, net_id: str, *, hole_d: float = 0.3,
               outer_d: float = 0.6) -> dict:
    out = via(hole_d=hole_d, outer_d=outer_d, x=x, y=y)
    out["subcircuit_connectivity_map_key"] = f"conn_{net_id}"
    return out


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
            "and this one was wrong in the dangerous direction. "
            "(Fixture corrected 2026-08-11: it originally gave the hole a "
            "0.3mm annular ring, which put the 0.25mm 'violating' track "
            "*underneath the pad*. Copper under a pad is a short or a "
            "connection, not a drill clearance, so the fixture was asserting "
            "the wrong defect. The ring is now JLC's 0.2mm minimum and the "
            "track is genuinely outside the pad.)"
        ),
        elements=[
            board(),
            hole(0.0, 0.0, 0.9, plated=True, annular=0.2),
            # 0.25mm of clearance: 0.80mm distance - 0.45mm radius - 0.1mm
            # half-width. Outside the 0.65mm pad. Legal beside an NPTH,
            # illegal beside a plated one.
            track([(-5.0, 0.80), (5.0, 0.80)]),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            hole(0.0, 0.0, 0.9, plated=True, annular=0.2),
            # 0.40mm — clear of both the 0.28mm floor and the warn threshold.
            track([(-5.0, 0.95), (5.0, 0.95)]),
        ],
        near_miss_why="0.40mm from a plated hole is comfortably legal.",
    ),
    Defect(
        id="hole-rule-applied-to-the-hole-s-own-pad",
        found="2026-08-11 — harness-puck and hydrate-coaster, the last "
              "blocking finding on both",
        story=(
            "The check measured every piece of copper against every drill and "
            "knew nothing about nets or pads, so it flagged J1's own "
            "shell-to-GND tie as 0.006mm from J1's own GND drill. That number "
            "is the annular ring. JLCPCB's capabilities page publishes 'PTH "
            "annular ring >= 0.20mm' beside 'PTH to Track 0.28mm', and both "
            "can be true only if the track figure measures copper arriving "
            "from outside rather than the pad the barrel is plated into. The "
            "measurement settles it: 28 segments across the three example "
            "boards came within 0.28mm of a shell drill from outside its pad, "
            "and 25 of them measured 0.2000mm to four decimals — that is not "
            "geometry, it is where a connection crosses its own pad boundary, "
            "counted 25 times. A net-blind rule makes every plated hole "
            "unconnectable, and KiCad's hole-clearance DRC (on at 0.2mm, and "
            "firing on NPTH holes on the same board) reported nothing here."
        ),
        elements=[
            board(),
            net("n_sig", name="SIG"),
            # A *different* net at the same distance is still a defect: 0.05mm
            # of copper beside a plated drill that has nothing to do with it.
            *hole_on_net(0.0, 0.0, 0.8, "n_gnd", hole_h=1.6),
            track([(-5.0, 0.45), (5.0, 0.45)], width=0.15, net_id="n_sig"),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            net("n_gnd", name="GND"),
            # The identical geometry on the hole's *own* net: the connection,
            # not a violation.
            *hole_on_net(0.0, 0.0, 0.8, "n_gnd", hole_h=1.6),
            track([(-5.0, 0.45), (5.0, 0.45)], width=0.15, net_id="n_gnd"),
        ],
        near_miss_why=(
            "same-net copper merges with the pad the fab already requires at "
            "0.20mm from this drill; it adds no copper the annular ring did "
            "not, and a drill that wanders into it can neither short nor open "
            "a net that is already this one."
        ),
    ),
    Defect(
        id="footprint-iou-graded-through-a-rotation",
        found="2026-08-11 — harness-puck, eight blocking findings on eight "
              "identical capacitors",
        story=(
            "The supplier-footprint IoU band called eight 0402 capacitors a "
            "footprint mismatch at 0.4739 and 0.4916, under the 0.5 error "
            "floor. Thirteen more capacitors on the same board — same part "
            "number C1525, same `footprint=\"0402\"` — scored 0.7249. The "
            "only difference was rotation: the eight sit tangentially around "
            "an LED ring at multiples of 22.5 degrees. A bench of one part at "
            "six angles settles it — 0.7249 at 0, 90, 180 and 270; 0.4739 at "
            "22.5; 0.4215 at 45. The metric survives a quarter turn and "
            "collapses off-axis, so below 0.5 at 22.5 degrees says nothing "
            "about the land pattern. Blocking on it would have made any "
            "circular layout, angled connector or diagonal-edge part "
            "permanently un-orderable. Orthogonal parts are still graded in "
            "full; off-axis ones keep the measurement at info."
        ),
        elements=[
            board(),
            # Both orthogonal placements still block at the same number: a
            # quarter turn does not buy a part an exemption.
            {"type": "source_component", "source_component_id": "sc1", "name": "C1"},
            {"type": "pcb_component", "pcb_component_id": "pc1",
             "source_component_id": "sc1", "rotation": 0},
            {"type": "supplier_footprint_mismatch_warning",
             "source_component_id": "sc1",
             "message": "C1 footprint \"0402\" does not match supplier "
                        "footprint jlcpcb:C1525 (copper IoU 0.4739).",
             "footprint_copper_intersection_over_union": 0.4739},
            {"type": "source_component", "source_component_id": "sc3", "name": "C3"},
            {"type": "pcb_component", "pcb_component_id": "pc3",
             "source_component_id": "sc3", "rotation": 90},
            {"type": "supplier_footprint_mismatch_warning",
             "source_component_id": "sc3",
             "message": "C3 footprint \"0402\" does not match supplier "
                        "footprint jlcpcb:C1525 (copper IoU 0.4739).",
             "footprint_copper_intersection_over_union": 0.4739},
        ],
        expect_kind="supplier_footprint_mismatch_warning",
        near_miss=[
            board(),
            {"type": "source_component", "source_component_id": "sc2", "name": "C2"},
            # The identical number on a part turned 22.5 degrees.
            {"type": "pcb_component", "pcb_component_id": "pc2",
             "source_component_id": "sc2", "rotation": 22.5},
            {"type": "supplier_footprint_mismatch_warning",
             "source_component_id": "sc2",
             "message": "C2 footprint \"0402\" does not match supplier "
                        "footprint jlcpcb:C1525 (copper IoU 0.4739).",
             "footprint_copper_intersection_over_union": 0.4739},
        ],
        near_miss_why=(
            "at 22.5 degrees the IoU is the angle, not the land pattern — "
            "measured 0.4739 for a part that scores 0.7249 at 0, 90, 180 and "
            "270. Same number, same part, and the orthogonal ones above still "
            "block."
        ),
    ),
    Defect(
        id="slot-drill-measured-as-a-circle",
        found="2026-08-11 — reading the hole rule against the USB-C footprint",
        story=(
            "Every hole was modelled as a circle of radius hole_width/2. The "
            "USB-C shell drills are 0.8 x 1.6mm pills, so copper sitting off "
            "the *end* of one measured against a 0.4mm circle instead of a "
            "1.6mm slot — a false negative of up to half the slot's length. "
            "The check was strict where it was easy and blind where the "
            "geometry was interesting, which is the worst combination: it "
            "spent its credibility on the pad it should have ignored and said "
            "nothing about the 0.4mm of slot it could not see."
        ),
        elements=[
            board(),
            net("n_sig", name="SIG"),
            # 0.9mm above the hole centre. A circle model calls that
            # 0.9 - 0.4 - 0.075 = 0.425mm and passes it; the real slot ends at
            # y = 0.4, so the true gap is 0.9 - 0.4 - 0.4 - 0.075 = 0.025mm.
            *hole_on_net(0.0, 0.0, 0.8, "n_gnd", hole_h=1.6),
            track([(-5.0, 0.9), (5.0, 0.9)], width=0.15, net_id="n_sig"),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            net("n_sig", name="SIG"),
            # 1.3mm above centre: 1.3 - 0.4 - 0.4 - 0.075 = 0.425mm clear of
            # the real slot. Must stay clean, or the slot model has simply
            # become a bigger circle.
            *hole_on_net(0.0, 0.0, 0.8, "n_gnd", hole_h=1.6),
            track([(-5.0, 1.3), (5.0, 1.3)], width=0.15, net_id="n_sig"),
        ],
        near_miss_why=(
            "0.425mm from the end of the slot is legal — modelling the slot "
            "must not turn into inflating it."
        ),
    ),
    Defect(
        id="a-pad-measured-unrotated",
        found="2026-08-16 — terminal-keyboard, the last blocking finding on "
              "the last of the three boards",
        story=(
            "The hole-clearance check modelled every pad as the stadium "
            "inscribed in its width and height and dropped the pad's own "
            "`ccw_rotation`. That does not blur a shape, it moves one. U4 is a "
            "W25Q128 in SOIC-8: eight 2.25 x 0.63mm pads at ccw_rotation 90, "
            "so the copper is 2.25mm *tall*. Swung back onto the x-axis it "
            "becomes 2.25mm *wide* and reaches 0.81mm toward a via that is "
            "nowhere near it — 0.130mm against a 0.20mm floor, on a board "
            "where the real gap is 0.506mm. KiCad's own hole-clearance DRC, on "
            "at 0.2mm on the same packet, reported nothing. The 1.27mm pad "
            "pitch settles it without any tool: eight pads 2.25mm wide at "
            "1.27mm centres would overlap each other, so the unrotated reading "
            "cannot describe a real footprint. The same blindness runs the "
            "other way — a pad whose long axis genuinely points at a drill was "
            "measured as if it pointed away, which is a missed defect and "
            "costs two weeks. Both directions are pinned here."
        ),
        elements=[
            board(),
            net("n_gnd", name="GND"),
            net("n_vcc", name="V3_3"),
            # The dangerous direction. The pad's real copper runs *toward* the
            # via: spine (0,-0.81)-(0,0.81) with a 0.315mm radius, so a drill
            # 1.405mm above the pad centre clears the copper by 0.595 - 0.315
            # - 0.15 = 0.130mm, under the 0.20mm via floor. The unrotated
            # model lays the same pad along x and reports 0.940mm — clean.
            *pad_on_net(0.0, 0.0, "n_vcc", w=2.25, h=0.63,
                        shape="rotated_pill", rotation=90.0),
            via_on_net(0.0, 1.405, "n_gnd"),
        ],
        expect_kind="dfm_hole_clearance",
        near_miss=[
            board(),
            net("n_gnd", name="GND"),
            net("n_vcc", name="V3_3"),
            # terminal-keyboard's own geometry, moved to the origin: U4 pin 8
            # and pcb_via_200, 0.971mm to the side and 0.573mm up. Real gap
            # 0.506mm; the unrotated model called it 0.130mm and blocked the
            # board.
            *pad_on_net(0.0, 0.0, "n_vcc", w=2.25, h=0.63,
                        shape="rotated_pill", rotation=90.0),
            via_on_net(-0.971, 0.5729, "n_gnd"),
        ],
        near_miss_why=(
            "0.506mm from a pad that is 0.63mm wide, not 2.25mm wide. A model "
            "that reports 0.130mm here is measuring copper that is not on the "
            "board, and it blocked a finished design for a day."
        ),
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
    """Every stage-4a verdict on this geometry.

    The IoU bander was outside this harness until 2026-08-11, so the corpus
    could not hold a footprint finding at all — which is how eight identical
    capacitors came to block a board over their rotation with no fixture
    standing in the way.
    """
    return (
        checks.dfm_warnings(elements, _product(envelope), PROFILE)
        + checks.iou_warnings(elements, PROFILE)
    )


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

    def test_the_second_substrate_can_also_ask_for_a_retry(self) -> None:
        """KiCad's verdict is one kind carrying every DRC type in its text.

        Measured 2026-08-11: an rp2040-core board came back with five blocking
        findings, *all five* of them `drc_violation`, and the escalation never
        fired — the kind was not in the routing set, and the type tag that
        would have said "route it differently" was inside the message. A
        routing failure was being reported and nothing was listening.
        """
        from circuitpy import generation

        blockers = generation._routing_blockers([
            {"severity": "error", "kind": "drc_violation",
             "detail": "[clearance] Clearance violation ( clearance 0.0900 mm; "
                       "actual 0.0778 mm)"},
            {"severity": "error", "kind": "drc_violation",
             "detail": "[shorting_items] Items shorting two nets"},
            {"severity": "error", "kind": "drc_violation",
             "detail": "[hole_clearance] Hole clearance violation"},
            # A via the router dropped in a pad is routing; a part off the
            # board is not, even though both arrive as pcb_placement_error.
            {"severity": "error", "kind": "pcb_placement_error",
             "detail": "Via at (-26.55mm, -28.14mm) is inside SMD pad C8.pin2"},
            {"severity": "error", "kind": "pcb_placement_error",
             "detail": "U3 is outside the board outline"},
            # KiCad findings a harder route cannot touch.
            {"severity": "error", "kind": "drc_violation",
             "detail": "[lib_footprint_mismatch] Footprint differs from library"},
            {"severity": "error", "kind": "drc_violation",
             "detail": "[duplicate_footprints] Duplicate footprint"},
        ])
        self.assertEqual(
            [b["detail"].split(" ")[0] for b in blockers],
            ["[clearance]", "[shorting_items]", "[hole_clearance]", "Via"],
        )

    def test_a_declared_effort_is_a_floor_the_retry_climbs_from(self) -> None:
        # The escalation used to refuse any board that declared an effort, on
        # the reading that the author's choice wins. Every board in the product
        # fleet declares one — the skill tells engineers to — so the retry was
        # unreachable for exactly the boards that needed it, and engineers
        # rebuilt at a higher effort by hand instead. A declaration is a floor.
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
        # Replaced, not stacked: one prop, the new value.
        self.assertTrue(generation._set_autorouter_effort(board, "10x"))
        self.assertEqual(board.read_text().count("autorouterEffortLevel"), 1)
        self.assertIn('autorouterEffortLevel="10x"', board.read_text())

        # The ladder climbs one rung and stops at the top. 100x is not a rung:
        # measured at 28 minutes with no verdict, which is a hang, not a retry.
        self.assertEqual(generation.next_effort(None), "1x")
        self.assertEqual(generation.next_effort("default"), "1x")
        self.assertEqual(generation.next_effort("5x"), "10x")
        self.assertIsNone(generation.next_effort("10x"))
        self.assertIsNone(generation.next_effort("100x"))
        # A level we cannot read is not guessed at.
        self.assertIsNone(generation.next_effort("turbo"))

        # An author who turned routing off meant it.
        off = tmp / "off.tsx"
        off.write_text('<board routingDisabled={true}>', encoding="utf-8")
        self.assertFalse(generation._set_autorouter_effort(off, "5x"))
        self.assertNotIn("autorouterEffortLevel", off.read_text())
