"""Product-layout intent is measured against the compiled board."""

from __future__ import annotations

import math

import fixtures

from verifylib import intent
from verifylib.model import Board


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        item["kind"]
        for item in result.findings
        if severity is None or item["severity"] == severity
    }


def _base_board() -> Board:
    pcb_board = fixtures.board(105, 55)
    pcb_board["min_trace_to_pad_edge_clearance"] = 0.15
    pcb_board["min_via_edge_to_pad_edge_clearance"] = 0.15
    elements = [pcb_board]
    elements += [fixtures.net(0, "GND", is_ground=True), fixtures.net(1, "V5", is_power=True)]
    elements += fixtures.component("SW10", index=1, x=-10, y=0, layer="top")
    elements += fixtures.component("D1", index=2, x=-5, y=0, layer="top")
    elements += fixtures.component(
        "J1", index=3, x=0, y=-26.5, width=10, height=1, layer="bottom", ftype="simple_connector"
    )
    elements += fixtures.component("U1", index=4, x=0, y=0, layer="bottom", ftype="simple_chip")
    elements += [
        {
            "type": "pcb_copper_pour",
            "pcb_copper_pour_id": "gnd_top",
            "source_net_id": "source_net_0",
            "subcircuit_connectivity_map_key": "conn_GND",
            "layer": "top",
        },
        {
            "type": "pcb_copper_pour",
            "pcb_copper_pour_id": "gnd_bottom",
            "source_net_id": "source_net_0",
            "subcircuit_connectivity_map_key": "conn_GND",
            "layer": "bottom",
        },
    ]
    for index in range(30):
        elements.append(
            {
                "type": "pcb_via",
                "pcb_via_id": f"gnd_via_{index}",
                "x": -45 + (index % 10) * 10,
                "y": -20 + (index // 10) * 10,
                "hole_diameter": 0.3,
                "outer_diameter": 0.6,
                "subcircuit_connectivity_map_key": "conn_GND",
            }
        )
    trace = fixtures.trace_on("v5", 1, [(0, 0), (1, 0), (9, 0), (10, 0)], width=0.8)
    trace["route"][0]["width"] = 0.2
    trace["route"][-2]["width"] = 0.8
    trace["route"][-1]["width"] = 0.2
    elements.append(trace)
    return Board(elements)


POLICY = {
    "boardSizeMm": [105, 55],
    "minCopperClearanceMm": 0.15,
    "componentSides": [
        {"match": ["SW[1-5][0-9]", "D*"], "side": "top"},
        {"match": "*", "side": "bottom"},
    ],
    "edgeConnectors": [
        {
            "ref": "J1",
            "edge": "bottom",
            "alignment": "center",
            "edgeToleranceMm": 1.0,
            "centerToleranceMm": 0.5,
        }
    ],
    "groundPlanes": {
        "layers": ["top", "bottom"],
        "maxRoutedLengthMm": 20,
        "maxFanoutLengthMm": 2,
        "stitchingPitchMm": 10,
    },
    "netClasses": [
        {
            "name": "POWER",
            "nets": ["V5"],
            "minTrunkWidthMm": 0.6,
            "minNeckdownWidthMm": 0.2,
            "maxNeckdownLengthMm": 2,
            "minViaOuterDiameterMm": 0.8,
            "minViaHoleDiameterMm": 0.5,
        }
    ],
}


def test_a_board_that_matches_every_declared_policy_is_clean():
    result = intent.check(_base_board(), POLICY)
    assert result.findings == []


def _decoupling_board(*, capacitor_x: float = 1.5, local_edge: bool = True) -> Board:
    elements = [fixtures.board(20, 15)]
    elements += [
        fixtures.net(0, "GND", is_ground=True),
        fixtures.net(1, "V3_3", is_power=True),
    ]
    elements += fixtures.component(
        "U1",
        index=1,
        x=0,
        y=0,
        width=2,
        height=2,
        ftype="simple_chip",
        pads=[(0.5, 0, 0.4, 0.4), (-0.5, 0, 0.4, 0.4)],
    )
    elements += fixtures.component(
        "C1",
        index=2,
        x=capacitor_x,
        y=0,
        width=1,
        height=0.8,
        ftype="simple_capacitor",
        capacitance=1e-7,
        pads=[(-0.3, 0, 0.4, 0.4), (0.3, 0, 0.4, 0.4)],
    )
    fixtures.connect(elements, "U1", 0, "V3_3")
    fixtures.connect(elements, "C1", 0, "V3_3")
    fixtures.connect(elements, "C1", 1, "GND")
    supply = next(
        element
        for element in elements
        if element.get("source_port_id") == "source_port_1_0"
    )
    supply["requires_power"] = True
    if local_edge:
        elements.append(
            {
                "type": "source_trace",
                "source_trace_id": "source_trace_u1_c1",
                "name": "TR_U1_VDD_C1",
                "connected_source_port_ids": [
                    "source_port_1_0",
                    "source_port_2_0",
                ],
                "connected_source_net_ids": [],
                "subcircuit_connectivity_map_key": "conn_V3_3",
            }
        )
    return Board(elements)


DECOUPLING_POLICY = {"decoupling": {"maxDistanceMm": 2.0}}


def test_declared_decoupling_requires_a_nearby_authored_local_loop():
    result = intent.check(_decoupling_board(), DECOUPLING_POLICY)
    assert result.findings == []


def test_decoupling_rejects_a_distant_cap_even_on_authored_topology():
    result = intent.check(
        _decoupling_board(capacitor_x=5.0), DECOUPLING_POLICY
    )
    issue = next(
        item
        for item in result.findings
        if item["kind"] == "layout_intent_decoupling_distance"
    )
    assert issue["part"] == "U1.pin1"
    assert "3.80mm" in issue["detail"]


def test_decoupling_rejects_mst_only_topology_even_when_the_cap_is_close():
    result = intent.check(
        _decoupling_board(local_edge=False), DECOUPLING_POLICY
    )
    assert "layout_intent_decoupling_topology" in kinds(result, "error")


def test_decoupling_rejects_a_missing_or_dnp_capacitor():
    board = _decoupling_board()
    pcb_cap = next(
        element
        for element in board.elements
        if element.get("pcb_component_id") == "pcb_component_2"
    )
    pcb_cap["do_not_place"] = True
    result = intent.check(Board(board.elements), DECOUPLING_POLICY)
    assert "layout_intent_decoupling_missing" in kinds(result, "error")


def test_decoupling_fails_closed_when_authored_pad_geometry_is_missing():
    board = _decoupling_board()
    elements = [
        element
        for element in board.elements
        if element.get("pcb_smtpad_id") != "pcb_smtpad_2_0"
    ]
    result = intent.check(Board(elements), DECOUPLING_POLICY)
    assert "layout_intent_decoupling_geometry" in kinds(result, "error")


def test_decoupling_exclusion_is_an_explicit_ref_pattern_not_a_hidden_heuristic():
    board = _decoupling_board()
    board.elements[:] = [
        element
        for element in board.elements
        if element.get("source_component_id") != "source_component_2"
        and element.get("pcb_component_id") != "pcb_component_2"
        and element.get("source_trace_id") != "source_trace_u1_c1"
    ]
    result = intent.check(
        Board(board.elements),
        {"decoupling": {"maxDistanceMm": 2.0, "exclude": "U1"}},
    )
    assert result.findings == []


def test_decoupling_override_is_a_ref_scoped_vendor_bound():
    board = _decoupling_board(capacitor_x=5.0)
    result = intent.check(
        board,
        {
            "decoupling": {
                "maxDistanceMm": 2.0,
                "overrides": [
                    {
                        "match": "U1",
                        "maxDistanceMm": 4.0,
                        "source": "vendor-reference",
                    }
                ],
            }
        },
    )
    assert result.findings == []


def test_decoupling_overlapping_overrides_use_the_strictest_bound():
    result = intent.check(
        _decoupling_board(capacitor_x=5.0),
        {
            "decoupling": {
                "maxDistanceMm": 2.0,
                "overrides": [
                    {
                        "match": "U*",
                        "maxDistanceMm": 4.0,
                        "source": "family-reference",
                    },
                    {
                        "match": "U1",
                        "maxDistanceMm": 3.0,
                        "source": "device-reference",
                    },
                ],
            }
        },
    )
    issue = next(
        item
        for item in result.findings
        if item["kind"] == "layout_intent_decoupling_distance"
    )
    assert "at most 3mm" in issue["detail"]


def test_decoupling_override_typo_and_exclusion_conflict_fail_closed():
    unmatched = intent.check(
        _decoupling_board(),
        {
            "decoupling": {
                "maxDistanceMm": 2.0,
                "overrides": [
                    {
                        "match": "U404",
                        "maxDistanceMm": 5.0,
                        "source": "vendor-reference",
                    }
                ],
            }
        },
    )
    assert "layout_intent_decoupling_override_unmatched" in kinds(unmatched, "error")

    conflict = intent.check(
        _decoupling_board(),
        {
            "decoupling": {
                "maxDistanceMm": 2.0,
                "exclude": "U1",
                "overrides": [
                    {
                        "match": "U1",
                        "maxDistanceMm": 5.0,
                        "source": "vendor-reference",
                    }
                ],
            }
        },
    )
    assert "layout_intent_decoupling_policy_conflict" in kinds(conflict, "error")

    invalid = intent.check(
        _decoupling_board(),
        {
            "decoupling": {
                "maxDistanceMm": 2.0,
                "overrides": [{"match": "U1", "maxDistanceMm": 5.0}],
            }
        },
    )
    assert "layout_intent_decoupling_override_invalid" in kinds(invalid, "error")


def test_exact_board_size_is_not_confused_with_a_maximum_envelope():
    board = _base_board()
    board.outline = fixtures_board = Board([fixtures.board(112, 90)]).outline
    assert fixtures_board is not None
    assert "layout_intent_board_size" in kinds(intent.check(board, POLICY), "error")


def test_clearance_contract_requires_both_authoring_tolerances():
    board = _base_board()
    board.of_type("pcb_board")[0]["min_via_edge_to_pad_edge_clearance"] = 0.1
    result = intent.check(board, POLICY)
    assert "layout_intent_clearance_contract" in kinds(result, "error")


def test_first_matching_side_rule_supports_a_front_population_and_bottom_default():
    board = _base_board()
    board.by_name["U1"].layer = "top"
    result = intent.check(board, POLICY)
    assert "layout_intent_component_side" in kinds(result, "error")
    assert not any(item["part"] == "SW10" for item in result.findings)


def test_component_zone_center_boundary_passes_and_wrong_zone_is_localized():
    board = Board([fixtures.board(20, 20)] + fixtures.component("U1", index=1, x=5, y=0))
    policy = {
        "componentZones": [
            {
                "match": "U*",
                "containment": "center",
                "shape": {"kind": "circle", "center": [0, 0], "radiusMm": 5},
            }
        ]
    }
    assert intent.check(board, policy).findings == []
    policy["componentZones"][0]["shape"]["radiusMm"] = 4.99
    findings = intent.check(board, policy).findings
    assert [item["kind"] for item in findings] == ["layout_intent_component_zone"]
    assert findings[0]["part"] == "U1"


def test_component_zone_courtyard_uses_the_rotated_outline_at_its_boundary():
    half_extent = 3 / math.sqrt(2)
    elements = [fixtures.board(20, 20)]
    elements += fixtures.component(
        "D10",
        index=1,
        x=0,
        y=0,
        width=2,
        height=4,
        courtyard=(2, 4),
        courtyard_rotation_deg=45,
    )
    board = Board(elements)
    policy = {
        "componentZones": [
            {
                "match": ["D1[0-7]", "C4[0-7]"],
                "containment": "courtyard",
                "shape": {
                    "kind": "rect",
                    "center": [0, 0],
                    "widthMm": 2 * half_extent,
                    "heightMm": 2 * half_extent,
                },
            }
        ]
    }
    assert intent.check(board, policy).findings == []
    policy["componentZones"][0]["shape"]["widthMm"] -= 0.001
    assert "layout_intent_component_zone" in kinds(intent.check(board, policy), "error")


def test_component_zone_annulus_rejects_a_courtyard_crossing_the_inner_void():
    elements = [fixtures.board(20, 20)]
    elements += fixtures.component(
        "U1", index=1, x=0, y=0, width=6, height=1, courtyard=(6, 1)
    )
    policy = {
        "componentZones": [
            {
                "match": "U1",
                "containment": "courtyard",
                "shape": {
                    "kind": "annulus",
                    "center": [0, 0],
                    "innerRadiusMm": 1,
                    "outerRadiusMm": 4,
                },
            }
        ]
    }
    # All four vertices are outside the 1mm inner radius, but the filled
    # courtyard crosses the center. Vertex-only annulus checks miss this.
    assert "layout_intent_component_zone" in kinds(
        intent.check(Board(elements), policy), "error"
    )


def test_component_zone_rule_that_matches_no_populated_hardware_fails_closed():
    board = Board([fixtures.board(20, 20)] + fixtures.component("U1", index=1, x=0, y=0))
    policy = {
        "componentZones": [
            {
                "match": "D1[0-7]",
                "containment": "center",
                "shape": {"kind": "circle", "center": [0, 0], "radiusMm": 5},
            }
        ]
    }
    findings = intent.check(board, policy).findings
    assert [item["kind"] for item in findings] == [
        "layout_intent_component_zone_unmatched"
    ]


def test_connector_must_reach_the_declared_edge_and_centerline():
    board = _base_board()
    board.by_name["J1"].center = (12, -20)
    result = intent.check(board, POLICY)
    assert {
        "layout_intent_connector_edge",
        "layout_intent_connector_alignment",
    } <= kinds(result, "error")


def test_connector_edge_uses_the_cable_mating_datum_before_the_body_box():
    board = _base_board()
    elements = list(board.elements)
    pcb = next(
        item
        for item in elements
        if item.get("type") == "pcb_component"
        and item.get("source_component_id") == "source_component_3"
    )
    # The connector body is deliberately well inside the board, while its
    # authored cable-mating point sits just outside the requested bottom edge.
    # This is the normal geometry for a receptacle overhanging the outline.
    pcb["center"] = {"x": 8.0, "y": -23.0}
    pcb["cable_insertion_center"] = {"x": 0.0, "y": -27.55}
    result = intent.check(Board(elements), POLICY)
    connector_findings = [
        item
        for item in result.findings
        if item["kind"].startswith("layout_intent_connector_")
    ]
    assert connector_findings == []


def test_connector_edge_falls_back_to_body_when_no_mating_datum_exists():
    board = _base_board()
    board.by_name["J1"].center = (0, -20)
    result = intent.check(board, POLICY)
    issue = next(
        item
        for item in result.findings
        if item["kind"] == "layout_intent_connector_edge"
    )
    assert "body" in issue["detail"]


def test_required_ground_layers_and_routed_ground_budget_are_enforced():
    board = _base_board()
    board.elements[:] = [
        item
        for item in board.elements
        if not (item.get("type") == "pcb_copper_pour" and item.get("layer") == "top")
    ]
    # The Board index is immutable after construction, rebuild it after editing.
    elements = list(board.elements)
    elements.append(fixtures.trace_on("gnd-long", 0, [(0, 0), (30, 0)]))
    result = intent.check(Board(elements), POLICY)
    assert {
        "layout_intent_ground_plane_missing",
        "layout_intent_ground_route_length",
    } <= kinds(result, "error")


def test_plane_fanout_length_is_measured_per_drop_not_assumed_from_its_name():
    board = _base_board()
    elements = list(board.elements)
    elements += [
        {
            "type": "source_trace",
            "source_trace_id": "source_trace_ground_drop",
            "name": "TR_U1_GND",
            "connected_source_net_ids": ["source_net_0"],
        },
        {
            "type": "pcb_trace",
            "pcb_trace_id": "fanout:source_trace_ground_drop",
            "source_trace_id": "source_trace_ground_drop",
            "connection_name": "source_trace_ground_drop",
            "route": [
                {
                    "route_type": "wire",
                    "x": 0,
                    "y": 0,
                    "width": 0.2,
                    "layer": "bottom",
                },
                {
                    "route_type": "wire",
                    "x": 8,
                    "y": 0,
                    "width": 0.2,
                    "layer": "bottom",
                },
            ],
        },
    ]
    result = intent.check(Board(elements), POLICY)
    issue = next(
        item
        for item in result.findings
        if item["kind"] == "layout_intent_ground_fanout_length"
    )
    assert issue["part"] == "TR_U1_GND"
    assert "8.00mm" in issue["detail"]


def test_power_trunk_allows_short_endpoint_neckdowns_but_not_a_narrow_middle():
    board = _base_board()
    trace = board.of_type("pcb_trace")[0]
    trace["route"][1]["width"] = 0.2
    trace["route"][2]["width"] = 0.2
    result = intent.check(Board(board.elements), POLICY)
    assert "layout_intent_power_trunk" in kinds(result, "error")


def test_fixed_pcbpath_power_copper_cannot_bypass_the_netclass_gate():
    board = _base_board()
    elements = list(board.elements)
    fixed = next(item for item in elements if item.get("pcb_trace_id") == "v5")
    fixed.pop("connection_name")
    fixed["source_trace_id"] = "source_trace_fixed_v5"
    fixed["route"][1]["width"] = 0.2
    fixed["route"][2]["width"] = 0.2
    elements.append(
        {
            "type": "source_trace",
            "source_trace_id": "source_trace_fixed_v5",
            "name": "TR_V5_FIXED",
            "subcircuit_connectivity_map_key": "conn_V5",
            "connected_source_port_ids": ["source_port_a", "source_port_b"],
        }
    )
    result = intent.check(Board(elements), POLICY)
    assert "layout_intent_power_trunk" in kinds(result, "error")


def test_power_netclass_measures_via_copper_and_drill_not_only_trace_width():
    board = _base_board()
    elements = list(board.elements)
    elements.append(
        {
            "type": "pcb_via",
            "pcb_via_id": "v5_signal_sized_via",
            "pcb_trace_id": "v5",
            "x": 5,
            "y": 0,
            "outer_diameter": 0.6,
            "hole_diameter": 0.3,
            "layers": ["top", "bottom"],
        }
    )
    result = intent.check(Board(elements), POLICY)
    issue = next(
        item
        for item in result.findings
        if item["kind"] == "layout_intent_netclass_via"
    )
    assert issue["part"] == "V5"
    assert "0.8mm" in issue["detail"]
    assert "0.5mm" in issue["detail"]


def _brep_pour(
    pour_id: str,
    *,
    layer: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    source_net_id: str = "source_net_0",
    inner: list[tuple[float, float]] | None = None,
) -> dict:
    return {
        "type": "pcb_copper_pour",
        "pcb_copper_pour_id": pour_id,
        "source_net_id": source_net_id,
        "subcircuit_id": "subcircuit_0",
        "layer": layer,
        "shape": "brep",
        "brep_shape": {
            "outer_ring": {
                "vertices": [
                    {"x": x0, "y": y0},
                    {"x": x1, "y": y0},
                    {"x": x1, "y": y1},
                    {"x": x0, "y": y1},
                ]
            },
            "inner_rings": (
                [
                    {
                        "vertices": [
                            {"x": point[0], "y": point[1]} for point in inner
                        ]
                    }
                ]
                if inner
                else []
            ),
        },
    }


def _plane_fanout_fixture(*, x: float, include_top_bridge: bool = False) -> Board:
    elements = [fixtures.board(30, 20), fixtures.net(0, "GND", is_ground=True)]
    # fixtures.net uses this identity and source id; pin every added element to
    # the same subcircuit so the checker proves physical, not merely named, net
    # connectivity.
    elements[1]["subcircuit_id"] = "subcircuit_0"
    elements += [
        _brep_pour("gnd_main", layer="bottom", x0=-10, y0=-8, x1=10, y1=8),
        _brep_pour("gnd_island", layer="bottom", x0=12, y0=-1, x1=14, y1=1),
        {
            "type": "source_trace",
            "source_trace_id": "source_trace_gnd",
            "name": "TR_U1_GND",
            "connected_source_net_ids": ["source_net_0"],
            "subcircuit_id": "subcircuit_0",
            "subcircuit_connectivity_map_key": "conn_GND",
        },
        {
            "type": "pcb_trace",
            "pcb_trace_id": "fanout:source_trace_gnd",
            "source_trace_id": "source_trace_gnd",
            "subcircuit_id": "subcircuit_0",
            "route": [
                {"route_type": "wire", "x": x, "y": 2, "width": 0.2, "layer": "top"},
                {
                    "route_type": "via",
                    "x": x,
                    "y": 0,
                    "from_layer": "top",
                    "to_layer": "bottom",
                },
                {"route_type": "wire", "x": x, "y": 0, "width": 0.2, "layer": "bottom"},
            ],
        },
        {
            "type": "pcb_via",
            "pcb_via_id": "fanout_via",
            "pcb_trace_id": "fanout:source_trace_gnd",
            "x": x,
            "y": 0,
            "from_layer": "top",
            "to_layer": "bottom",
            "layers": ["top", "bottom"],
            "subcircuit_id": "subcircuit_0",
            "subcircuit_connectivity_map_key": "conn_GND",
        },
    ]
    if include_top_bridge:
        elements.append(
            _brep_pour("gnd_top_main", layer="top", x0=-10, y0=-8, x1=14, y1=8)
        )
    return Board(elements)


def test_plane_fanout_must_not_land_on_a_logically_named_but_isolated_island():
    result = intent.check(_plane_fanout_fixture(x=13), None)
    assert "pcb_plane_connectivity_error" in kinds(result, "error")
    assert "isolated" in next(
        item["detail"]
        for item in result.findings
        if item["kind"] == "pcb_plane_connectivity_error"
    )


def test_plane_fanout_accepts_the_dominant_island_or_a_via_stitched_bridge():
    assert intent.check(_plane_fanout_fixture(x=0), None).findings == []
    # The target bottom fragment is small, but the same via physically joins it
    # to the dominant top plane. This is connected copper, not a name-based
    # exemption.
    assert intent.check(
        _plane_fanout_fixture(x=13, include_top_bridge=True), None
    ).findings == []


def _same_layer_plane_contact_fixture(*, mode: str) -> Board:
    elements = [fixtures.board(30, 20), fixtures.net(0, "GND", is_ground=True)]
    elements[1]["subcircuit_id"] = "subcircuit_0"
    contact_x = 13.0 if mode == "fragmented" else 0.0
    if mode != "missing":
        elements.append(
            _brep_pour(
                "gnd_main" if mode != "wrong-net" else "wrong_net_main",
                layer="top",
                x0=-10,
                y0=-8,
                x1=10,
                y1=8,
                source_net_id=(
                    "source_net_0" if mode != "wrong-net" else "source_net_1"
                ),
            )
        )
    if mode == "fragmented":
        elements.append(
            _brep_pour(
                "gnd_island", layer="top", x0=12, y0=-1, x1=14, y1=1
            )
        )
    elements += [
        {
            "type": "source_port",
            "source_port_id": "source_port_same_layer_gnd",
            "subcircuit_id": "subcircuit_0",
            "subcircuit_connectivity_map_key": "conn_GND",
        },
        {
            "type": "pcb_port",
            "pcb_port_id": "pcb_port_same_layer_gnd",
            "source_port_id": "source_port_same_layer_gnd",
            "subcircuit_id": "subcircuit_0",
            "x": contact_x,
            "y": 0,
            "layers": ["top"],
        },
        {
            "type": "source_trace",
            "source_trace_id": "source_trace_same_layer_gnd",
            "name": "TR_U1_GND",
            "connected_source_net_ids": ["source_net_0"],
            "connected_source_port_ids": ["source_port_same_layer_gnd"],
            "subcircuit_id": "subcircuit_0",
            "subcircuit_connectivity_map_key": "conn_GND",
        },
        {
            "type": "pcb_trace",
            "pcb_trace_id": "fanout:source_trace_same_layer_gnd",
            "source_trace_id": "source_trace_same_layer_gnd",
            "subcircuit_id": "subcircuit_0",
            "trace_length": 0,
            "route": [
                {
                    "route_type": "wire",
                    "x": contact_x,
                    "y": 0,
                    "width": 0.2,
                    "layer": "top",
                    "is_inside_copper_pour": True,
                }
            ],
        },
    ]
    return Board(elements)


def test_same_layer_plane_marker_requires_real_connected_pour_material():
    assert intent.check(
        _same_layer_plane_contact_fixture(mode="connected"), None
    ).findings == []

    for mode in ("missing", "wrong-net", "fragmented"):
        result = intent.check(_same_layer_plane_contact_fixture(mode=mode), None)
        issue = next(
            item
            for item in result.findings
            if item["kind"] == "pcb_plane_connectivity_error"
        )
        if mode == "fragmented":
            assert "isolated" in issue["detail"]
        else:
            assert "no material pour island" in issue["detail"]


def test_same_layer_plane_marker_must_be_bound_to_its_source_pad():
    board = _same_layer_plane_contact_fixture(mode="connected")
    marker = next(
        element
        for element in board.of_type("pcb_trace")
        if str(element.get("pcb_trace_id") or "").startswith("fanout:")
    )["route"][0]
    marker["x"] = 1.0
    result = intent.check(Board(board.elements), None)
    issue = next(
        item
        for item in result.findings
        if item["kind"] == "pcb_plane_connectivity_error"
    )
    assert "unbound same-layer plane marker" in issue["detail"]


def test_different_net_pour_faces_are_checked_after_the_solver():
    elements = [fixtures.board(30, 20)]
    elements += [fixtures.net(0, "GND", is_ground=True), fixtures.net(1, "CAP_A")]
    elements += [
        _brep_pour("gnd", layer="top", x0=-10, y0=-8, x1=10, y1=8),
        _brep_pour(
            "electrode",
            layer="top",
            x0=-2,
            y0=-2,
            x1=2,
            y1=2,
            source_net_id="source_net_1",
        ),
    ]
    result = intent.check(Board(elements), None)
    assert "pcb_copper_pour_short_error" in kinds(result, "error")


def test_a_different_net_island_inside_a_real_pour_void_is_not_a_false_short():
    elements = [fixtures.board(30, 20)]
    elements += [fixtures.net(0, "GND", is_ground=True), fixtures.net(1, "CAP_A")]
    elements += [
        _brep_pour(
            "gnd",
            layer="top",
            x0=-10,
            y0=-8,
            x1=10,
            y1=8,
            inner=[(-3, -3), (3, -3), (3, 3), (-3, 3)],
        ),
        _brep_pour(
            "electrode",
            layer="top",
            x0=-2,
            y0=-2,
            x1=2,
            y1=2,
            source_net_id="source_net_1",
        ),
    ]
    assert intent.check(Board(elements), None).findings == []


def test_missing_policy_is_reported_as_coverage_not_a_false_pass():
    result = intent.check(_base_board(), None)
    assert result.findings == []
    assert result.coverage and "unknown" in " ".join(result.coverage.blind)
