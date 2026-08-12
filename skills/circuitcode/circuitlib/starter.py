"""Public protected-USB starter generation from planner-owned facts.

This is intentionally one narrow machine profile, not a bag of example
coordinates.  It resolves the block set with :func:`board_plan`, places the
measured boxes with :func:`place_board`, reads typed external copper datums
from the registry, and emits one authored/acyclic VBUS_RAW -> U7 -> V5 -> U2
-> V3_3 composition.  If any prerequisite changes, generation refuses or the
cold behavioral fixture fails; a stale template cannot quietly become the
default new project.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any

from circuitlib.blocks import BLOCKS, AttachmentPort, parts_lock_for_blocks
from circuitlib.helpers import BoardPlan, board_plan, usb_power_budget_for_plan
from circuitlib.layout import place_board, product_layout


DESIGN_PROFILE = "protected-usb-indicator-v1"
CAPABILITIES = ("power-usb", "indicator", "usb-data")
EXPECTED_BLOCKS = (
    "usb-power-entry",
    "status-led",
    "usb-c-data",
    "ldo-3v3",
)


@dataclass(frozen=True)
class StarterProject:
    """Fully resolved public starter inputs and generated files."""

    plan: BoardPlan
    placement: dict[str, object]
    product: dict[str, object]
    parts: dict[str, dict]
    board_source: str
    block_ids: tuple[str, ...]


def _round(value: float) -> float:
    return round(float(value), 8)


def _ts(value: float) -> str:
    rounded = _round(value)
    if rounded == int(rounded):
        return str(int(rounded))
    return repr(rounded)


def _port(
    placements: dict[str, tuple[float, float]], block_id: str, role: str
) -> tuple[AttachmentPort, tuple[float, float]]:
    port = BLOCKS[block_id].attachment(role)
    x, y = placements[block_id]
    return port, (_round(x + port.local_x_mm), _round(y + port.local_y_mm))


def _protected_ground_stitches(
    width: float,
    height: float,
    *,
    reserved: tuple[tuple[float, float], ...] = (),
    reserved_segments: tuple[
        tuple[tuple[float, float], tuple[float, float]], ...
    ] = (),
) -> tuple[tuple[float, float], ...]:
    """Placement-aware <=10mm stitch grid for this machine profile.

    The centre of the bottom band is reserved for the USB connector/pair, the
    upper band keeps clear of the two authored power corridors, and the outer
    columns stay inside the mounting-hole keepouts.  All gaps are <=10mm; the
    independent layout-intent check proves that on the compiled pours.
    """

    if width < 45 or height < 30:
        raise ValueError(
            "protected USB stitch policy requires at least a 45x30mm board"
        )
    bottom = round(-height / 2 + 4.65)
    top = round(height / 2 - 3.65)
    outer = round(width / 2 - 7.65)
    candidates = (
        (-17.0, float(bottom)), (-9.0, float(bottom)),
        (9.0, float(bottom)), (18.0, float(bottom)),
        (-20.0, -7.0), (-10.0, -7.0), (10.0, -7.0), (20.0, -7.0),
        (-20.0, 0.0), (-10.0, 0.0), (10.0, 0.0), (20.0, 0.0),
        (-20.0, 5.0), (-10.0, 5.0), (0.0, 5.0), (10.0, 5.0), (20.0, 5.0),
        (-outer, float(top)), (-9.0, float(top)), (0.0, float(top)),
        (9.0, float(top)), (outer, float(top)),
    )
    # A stitched-plane policy must follow the placed copper. The earlier
    # fixed grid happened to put a 0.6/0.3 GND via inside the moved AP7361
    # TP13 pad, causing a placement failure before routing. Reserve every
    # authored rail probe/transition with the same 0.15mm via-to-pad margin
    # used by the board contract; keep the remaining deterministic grid.
    def segment_distance(
        point: tuple[float, float],
        segment: tuple[tuple[float, float], tuple[float, float]],
    ) -> float:
        (x, y), ((x0, y0), (x1, y1)) = point, segment
        dx, dy = x1 - x0, y1 - y0
        length2 = dx * dx + dy * dy
        if length2 <= 1e-12:
            return math.dist(point, (x0, y0))
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length2))
        return math.dist(point, (x0 + t * dx, y0 + t * dy))

    def clears(point: tuple[float, float]) -> bool:
        # 0.6mm GND stitch versus a 1.2mm probe pad needs
        # .3 + .6 + .15 = 1.05mm centre separation. Keep 0.10mm construction
        # margin. Against an 0.8mm rail, .3 + .4 + .15 = .85mm.
        return (
            all(math.dist(point, keepout) >= 1.15 for keepout in reserved)
            and all(
                segment_distance(point, segment) >= 0.85
                for segment in reserved_segments
            )
        )

    stitches: list[tuple[float, float]] = []
    for candidate in candidates:
        # Moving a colliding stitch 2mm toward the nearby board edge retains
        # <=10mm pitch on this profile's grid while clearing the authored rail.
        alternatives = (
            candidate,
            (candidate[0], candidate[1] + 2.0),
            (candidate[0], candidate[1] - 2.0),
            (candidate[0] - 2.0, candidate[1]),
            (candidate[0] + 2.0, candidate[1]),
        )
        selected = next(
            (
                point for point in alternatives
                if abs(point[0]) <= width / 2 - 0.3
                and abs(point[1]) <= height / 2 - 0.3
                and clears(point)
            ),
            None,
        )
        if selected is None:
            raise ValueError(
                f"protected USB stitch at {candidate} has no safe placement"
            )
        stitches.append(selected)
    return tuple(stitches)


def _source(plan: BoardPlan, placement: dict[str, object]) -> str:
    width = float(placement["width_mm"])
    height = float(placement["height_mm"])
    placements = dict(placement["placements"])
    holes = list(placement["holes"])

    n15_port, n15 = _port(placements, "usb-c-data", "raw_vbus_boundary")
    c24_port, c24 = _port(placements, "usb-power-entry", "raw_input")
    u7_port, u7_out = _port(placements, "usb-power-entry", "protected_output")
    r32_port, r32 = _port(placements, "usb-power-entry", "fault_pullup")
    c2_port, c2 = _port(placements, "ldo-3v3", "input_cap")
    u2_port, u2_out = _port(placements, "ldo-3v3", "regulated_output")
    r20_port, r20 = _port(placements, "status-led", "rail_input")

    # Corridor construction is relative to typed copper endpoints.  These
    # are escape/lane distances, not global product coordinates.
    v5_start = (_round(u7_out[0] - 1.47999), _round(u7_out[1] - 1.30004))
    v5_via = (_round(v5_start[0] - 1.45), _round(v5_start[1] - 1.45))
    v5_end = (_round(c2[0] - 0.0675), v5_via[1])

    # The AP7361 VOUT pin sits inside the manufacturer's SOT-223 courtyard.
    # Reuse the exact cross-layer PowerTrunk construction proven by the golden
    # real-LDO bench: a 2mm vertical source neck reaches a start probe just
    # outside that courtyard, then a (-1.6,+.7) off-pad transition reaches the
    # standalone 0.8/0.5mm via. This relation is owned by the typed attachment
    # datum; it does not depend on a consumer board's global coordinates.
    v3_start = (u2_out[0], _round(u2_out[1] + 2.0))
    v3_via = (_round(v3_start[0] - 1.6), _round(v3_start[1] + 0.7))
    v3_end = (_round(r20[0] + 1.11), _round(r20[1] + 1.59))

    raw_node = (c24[0], _round(c24[1] - 1.4))
    fault_node = (_round(r32[0] - 1.47), r32[1])
    raw_mid_y = _round(n15[1] + 5.85)
    v5_attach_via = (_round(v5_end[0] + 0.328), _round(v5_end[1] + 4.05))
    v3_branch_via = (_round(v3_end[0] - 2.5), _round(v3_end[1] + 1.6))

    usb_x, usb_y = placements["usb-c-data"]
    entry_x, entry_y = placements["usb-power-entry"]
    ldo_x, ldo_y = placements["ldo-3v3"]
    status_x, status_y = placements["status-led"]
    region = {
        "minX": _round(usb_x - 10),
        "maxX": _round(usb_x + 10),
        "minY": _round(-height / 2),
        "maxY": _round(usb_y + 13),
    }
    stitches = _protected_ground_stitches(
        width,
        height,
        reserved=(v5_start, v5_via, v5_end, v3_start, v3_via, v3_end),
        reserved_segments=(
            (v5_start, v5_via), (v5_via, v5_end),
            (v3_start, v3_via), (v3_via, v3_end),
            (v3_end, v3_branch_via),
            (v3_branch_via, (fault_node[0], v3_branch_via[1])),
            ((fault_node[0], v3_branch_via[1]), fault_node),
        ),
    )
    stitches_ts = ",\n  ".join(
        f"{{ x: {_ts(x)}, y: {_ts(y)} }}" for x, y in stitches
    )
    holes_ts = "\n".join(
        f'    <MountingHole name="{hole["name"]}" diameter={{{_ts(float(hole["diameter_mm"]))}}} '
        f'pcbX={{{_ts(float(hole["pcbX"]))}}} pcbY={{{_ts(float(hole["pcbY"]))}}} />'
        for hole in holes
    )

    return f'''/**
 * Generated {DESIGN_PROFILE} starter.
 * dialect: tscircuit@0.0.2279 (pinned by the project toolchain)
 *
 * Planner blocks: {", ".join(plan.block_ids)}
 * Protected topology: VBUS_RAW -> U7 -> V5 -> U2 -> V3_3
 * Schematic policy: explicit, left-to-right block anchors.
 */

import {{ GndPlanes, MaskedCopperNode, MountingHole, PowerTrunk }} from "../blocks/glue"
import {{ UsbCData }} from "../blocks/usb-c-data/usb-c-data"
import {{ UsbPowerEntry }} from "../blocks/usb-power-entry/usb-power-entry"
import {{ Ldo3v3 }} from "../blocks/ldo-3v3/ldo-3v3"
import {{ StatusLed }} from "../blocks/status-led/status-led"

const GND_STITCHES = [
  {stitches_ts},
] as const

const USB_LOCAL_REGION = {{
  minX: {_ts(region["minX"])}, maxX: {_ts(region["maxX"])},
  minY: {_ts(region["minY"])}, maxY: {_ts(region["maxY"])},
}} as const

export default () => (
  <board width="{_ts(width)}mm" height="{_ts(height)}mm" thickness="1.6mm"
    minTraceWidth="0.15mm" minTraceToPadEdgeClearance="0.15mm"
    minViaEdgeToPadEdgeClearance="0.15mm"
    minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm">
    <autoroutingphase name="usb-connector-pair" phaseIndex={{0}} region={{USB_LOCAL_REGION}} />
    <autoroutingphase name="usb-series-pair" phaseIndex={{1}} region={{USB_LOCAL_REGION}} />
    <autoroutingphase name="usb-cc1" phaseIndex={{2}} region={{USB_LOCAL_REGION}} />
    <autoroutingphase name="usb-cc2" phaseIndex={{3}} region={{USB_LOCAL_REGION}} />
    <autoroutingphase name="usb-local-power" phaseIndex={{4}} region={{USB_LOCAL_REGION}} />
    <net name="VBUS_RAW" routingPhaseIndex={{5}} />
    <net name="V5" routingPhaseIndex={{6}} />
    <net name="V3_3" routingPhaseIndex={{7}} />
    <net name="USB_POWER_FAULT" routingPhaseIndex={{8}} />

    <UsbCData pcbX={{{_ts(usb_x)}}} pcbY={{{_ts(usb_y)}}} schX={{-12}} schY={{0}}
      vbusBoundaryRefs={{{{ right: "N3", left: "N4" }}}} vbusRailNodeRef="N15"
      vbusClampNodeRef="N16"
      pairRules={{{{ pcbTraceGapMm: 0.15, maxLengthSkewMm: 3.8, maxUncoupledLengthMm: 3 }}}}
      localRoutingPhaseIndex={{4}} dpConnectorRoutingPhaseIndex={{0}}
      dmConnectorRoutingPhaseIndex={{0}} connectorPairRoutingPhaseIndex={{0}}
      seriesPairRoutingPhaseIndex={{1}} cc1RoutingPhaseIndex={{2}}
      cc2RoutingPhaseIndex={{3}} powerRoutingPhaseIndex={{4}}
      criticalSignalWidthMm={{0.15}} signalTraceWidthMm={{0.25}} />
    <UsbPowerEntry pcbX={{{_ts(entry_x)}}} pcbY={{{_ts(entry_y)}}} schX={{-4}} schY={{0}}
      externalPowerTrunkPort="OUT" externalRawPowerTrunkPort="IN"
      externalFaultPullupPort="R32" signalTraceWidthMm={{0.25}}
      finePitchEscapeWidthMm={{0.15}} />
    <Ldo3v3 pcbX={{{_ts(ldo_x)}}} pcbY={{{_ts(ldo_y)}}} schX={{4}} schY={{0}}
      externalPowerTrunkPort="VOUT" externalInputPowerTrunkPort="VIN"
      railWidthMm={{0.8}} pinNeckdownWidthMm={{0.2}}
      maxPinNeckdownLengthMm={{2}} />
    <StatusLed layer="bottom" pcbX={{{_ts(status_x)}}} pcbY={{{_ts(status_y)}}}
      schX={{12}} schY={{0}} externalRailAttachmentPort="R"
      railTraceWidthMm={{0.2}} signalTraceWidthMm={{0.25}}
      maxRailNeckdownLengthMm={{2}} />

    <group pcbX={{0}} pcbY={{0}}>
{holes_ts}
    <GndPlanes layers={{["top", "bottom"]}} stitchingVias={{[...GND_STITCHES]}}
      viaOuterDiameterMm={{0.6}} viaHoleDiameterMm={{0.3}} />

    <PowerTrunk name="V5_MAIN" source="{u7_port.selector}" net="V5"
      sourcePoint={{{{ x: {_ts(u7_out[0])}, y: {_ts(u7_out[1])} }}}}
      start={{{{ x: {_ts(v5_start[0])}, y: {_ts(v5_start[1])} }}}}
      trunkVia={{{{ x: {_ts(v5_via[0])}, y: {_ts(v5_via[1])} }}}}
      end={{{{ x: {_ts(v5_end[0])}, y: {_ts(v5_end[1])} }}}}
      startTestpoint="TP11" endTestpoint="TP12"
      sourceLayer="top" trunkLayer="bottom" trunkWidthMm={{0.8}}
      neckdownWidthMm={{0.2}} maxNeckdownLengthMm={{2}}
      viaOuterDiameterMm={{0.8}} viaHoleDiameterMm={{0.5}} />

    <PowerTrunk name="V3V3_MAIN" source="{u2_port.selector}" net="V3_3"
      sourcePoint={{{{ x: {_ts(u2_out[0])}, y: {_ts(u2_out[1])} }}}}
      start={{{{ x: {_ts(v3_start[0])}, y: {_ts(v3_start[1])} }}}}
      trunkVia={{{{ x: {_ts(v3_via[0])}, y: {_ts(v3_via[1])} }}}}
      end={{{{ x: {_ts(v3_end[0])}, y: {_ts(v3_end[1])} }}}}
      startTestpoint="TP13" endTestpoint="TP14"
      sourceLayer="top" trunkLayer="bottom" trunkWidthMm={{0.8}}
      neckdownWidthMm={{0.2}} maxNeckdownLengthMm={{2}}
      viaOuterDiameterMm={{0.8}} viaHoleDiameterMm={{0.5}} />

    <MaskedCopperNode name="N20" layer="top" diameterMm={{0.8}}
      pcbX={{{_ts(fault_node[0])}}} pcbY={{{_ts(fault_node[1])}}} />
    <MaskedCopperNode name="N21" layer="top" diameterMm={{0.8}}
      pcbX={{{_ts(raw_node[0])}}} pcbY={{{_ts(raw_node[1])}}} />
    <trace name="TR_RAW_ATTACH_NECK" from=".N21 > .pin1" to="{c24_port.selector}"
      thickness="0.2mm" maxLength="2mm" pcbPathRelativeTo=".N21 > .pin1"
      pcbPath={{[{{ x: 0, y: 0 }}, {{ x: 0, y: {_ts(c24[1] - raw_node[1])} }}]}} />
    <trace name="TR_RAW_ATTACH_TRUNK" from="{n15_port.selector}" to=".N21 > .pin1"
      thickness="0.8mm" maxLength="24mm" pcbPathRelativeTo="{n15_port.selector}"
      pcbPath={{[
        {{ x: 0, y: 0 }},
        {{ x: 0, y: {_ts(raw_mid_y - n15[1])} }},
        {{ x: {_ts(raw_node[0] - n15[0])}, y: {_ts(raw_node[1] - n15[1])} }},
      ]}} />
    <group pcbStyle={{{{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}}}>
      <trace name="TR_V5_ATTACH_LDO" from=".TP12 > .pin1" to="{c2_port.selector}"
        thickness="0.8mm" maxLength="12mm" pcbPathRelativeTo=".TP12 > .pin1"
        pcbPath={{[
          {{ x: 0, y: 0 }},
          {{ x: {_ts(v5_attach_via[0] - v5_end[0])}, y: {_ts(v5_attach_via[1] - v5_end[1])} }},
          {{ x: {_ts(v5_attach_via[0] - v5_end[0])}, y: {_ts(v5_attach_via[1] - v5_end[1])}, via: true, fromLayer: "bottom", toLayer: "top" }},
          {{ x: {_ts(v5_attach_via[0] - v5_end[0])}, y: {_ts(v5_attach_via[1] - v5_end[1])} }},
          {{ x: {_ts(v5_attach_via[0] - v5_end[0])}, y: {_ts(c2[1] - v5_end[1] - 0.26)} }},
          {{ x: {_ts(c2[0] - v5_end[0])}, y: {_ts(c2[1] - v5_end[1])} }},
        ]}} />
    </group>
    <trace name="TR_V3_ATTACH_LED" from=".TP14 > .pin1" to="{r20_port.selector}"
      thickness="0.2mm" maxLength="2mm" pcbPathRelativeTo=".TP14 > .pin1"
      pcbPath={{[{{ x: 0, y: 0 }}, {{ x: {_ts(r20[0] - v3_end[0])}, y: {_ts(r20[1] - v3_end[1])} }}]}} />
    <group pcbStyle={{{{ viaPadDiameter: "0.8mm", viaHoleDiameter: "0.5mm" }}}}>
      <trace name="TR_V3_ATTACH_BRANCH" from=".TP14 > .pin1" to=".N20 > .pin1"
        thickness="0.8mm" maxLength="20mm" pcbPathRelativeTo=".TP14 > .pin1"
        pcbPath={{[
          {{ x: 0, y: 0 }},
          {{ x: {_ts(v3_branch_via[0] - v3_end[0])}, y: {_ts(v3_branch_via[1] - v3_end[1])} }},
          {{ x: {_ts(v3_branch_via[0] - v3_end[0])}, y: {_ts(v3_branch_via[1] - v3_end[1])}, via: true, fromLayer: "bottom", toLayer: "top" }},
          {{ x: {_ts(v3_branch_via[0] - v3_end[0])}, y: {_ts(v3_branch_via[1] - v3_end[1])} }},
          {{ x: {_ts(fault_node[0] - v3_end[0])}, y: {_ts(v3_branch_via[1] - v3_end[1])} }},
          {{ x: {_ts(fault_node[0] - v3_end[0])}, y: {_ts(fault_node[1] - v3_end[1])} }},
        ]}} />
    </group>
    <trace name="TR_V3_ATTACH_FAULT" from=".N20 > .pin1" to="{r32_port.selector}"
      thickness="0.2mm" maxLength="2mm" pcbPathRelativeTo=".N20 > .pin1"
      pcbPath={{[{{ x: 0, y: 0 }}, {{ x: {_ts(r32[0] - fault_node[0])}, y: 0 }}]}} />
    </group>
  </board>
)
'''


def protected_usb_indicator_starter(
    *,
    name: str = "new-board",
    description: str = "protected USB-powered board with a 3.3V status indicator",
) -> StarterProject:
    """Resolve and generate the public safe starter profile."""

    if not isinstance(name, str) or not name.strip():
        raise ValueError("starter name must be non-empty")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("starter description must be non-empty")
    plan = board_plan(capabilities=list(CAPABILITIES))
    if not plan.buildable or tuple(plan.block_ids) != EXPECTED_BLOCKS:
        raise ValueError(
            "protected starter planner closure changed: "
            f"blocks={plan.block_ids}, unmet={plan.unmet}, unavailable={plan.unavailable}"
        )
    placement = place_board(list(plan.block_ids))
    if placement["warnings"]:
        raise ValueError(
            f"protected starter placement is not clean: {placement['warnings']}"
        )
    layout = product_layout(
        board_size_mm=(
            float(placement["width_mm"]),
            float(placement["height_mm"]),
        ),
        component_sides=[
            {"match": ["LED1", "R20"], "side": "bottom"},
            {"match": "*", "side": "top"},
        ],
        edge_connectors=[
            {
                "ref": "J1",
                "edge": "bottom",
                "alignment": "center",
                "edgeToleranceMm": 2.0,
                "centerToleranceMm": 0.1,
            }
        ],
        max_ground_route_length_mm=20.0,
        max_ground_fanout_length_mm=plan.ground_fanout_max_length_mm,
        ground_stitching_pitch_mm=plan.ground_stitching_pitch_mm,
        min_copper_clearance_mm=plan.preferred_clearance_mm,
        decoupling_max_distance_mm=2.0,
        decoupling_exclude=("U1",),
        power_nets=("V5", "V3_3"),
        power_trunk_width_mm=plan.power_trunk_width_mm,
        power_neckdown_width_mm=plan.power_neckdown_width_mm,
        power_neckdown_max_length_mm=plan.power_neckdown_max_length_mm,
        usb_attach_power_nets=("VBUS_RAW",),
        control_signal_nets=("USB_POWER_FAULT",),
    )
    board_source = _source(plan, placement)
    parts = parts_lock_for_blocks(list(plan.block_ids))
    product: dict[str, Any] = {
        "name": name.strip(),
        "description": description.strip(),
        "power": "usb-c-5v",
        "envelopeMm": [placement["width_mm"], placement["height_mm"]],
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": True,
        "assemblyTier": "standard",
        "designProfile": DESIGN_PROFILE,
        "designProfileSourceSha256": hashlib.sha256(
            board_source.encode("utf-8")
        ).hexdigest(),
        "schematicPolicy": {
            "placement": "explicit",
            "flow": "left-to-right",
        },
        "layout": layout,
        "powerBudget": usb_power_budget_for_plan(plan),
    }
    return StarterProject(
        plan=plan,
        placement=placement,
        product=product,
        parts=parts,
        board_source=board_source,
        block_ids=tuple(plan.block_ids),
    )


__all__ = [
    "CAPABILITIES",
    "DESIGN_PROFILE",
    "EXPECTED_BLOCKS",
    "StarterProject",
    "protected_usb_indicator_starter",
]
