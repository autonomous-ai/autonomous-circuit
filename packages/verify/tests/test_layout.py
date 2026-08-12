"""Electrical-layout checks: reference plane, critical routes, ESD placement."""

from __future__ import annotations

import fixtures

from verifylib import layout
from verifylib.model import Board


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        finding["kind"]
        for finding in result.findings
        if severity is None or finding["severity"] == severity
    }


def _named_pins(elements: list[dict], component_index: int, names: list[str]) -> None:
    source_id = f"source_component_{component_index}"
    ports = [
        element
        for element in elements
        if element.get("type") == "source_port"
        and element.get("source_component_id") == source_id
    ]
    for port, name in zip(ports, names):
        port["name"] = name


def _via(index: int, net_name: str, x: float, y: float) -> dict:
    return {
        "type": "pcb_via",
        "pcb_via_id": f"pcb_via_{index}",
        "x": x,
        "y": y,
        "hole_diameter": 0.3,
        "outer_diameter": 0.6,
        "layers": ["top", "bottom"],
        "subcircuit_connectivity_map_key": f"conn_{net_name}",
    }


def _usb_board(*, esd_y: float = 3.0, dp_vias: int = 0, ground_plane: bool = False) -> Board:
    elements = [fixtures.board(50, 40)]
    elements += [
        fixtures.net(0, "GND", is_ground=True),
        fixtures.net(1, "USB_DP"),
        fixtures.net(2, "USB_DM"),
    ]
    elements += fixtures.component(
        "J1",
        index=1,
        x=0,
        y=0,
        ftype="simple_connector",
        manufacturer_part_number="TYPE-C-31-M-12",
        pads=[(-1.5, 0, 0.4, 1), (-0.5, 0, 0.4, 1), (0.5, 0, 0.4, 1)],
    )
    _named_pins(elements, 1, ["DP1", "DM1", "GND1"])
    fixtures.connect(elements, "J1", 0, "USB_DP")
    fixtures.connect(elements, "J1", 1, "USB_DM")
    fixtures.connect(elements, "J1", 2, "GND")

    elements += fixtures.component(
        "U1",
        index=2,
        x=0,
        y=esd_y,
        ftype="simple_chip",
        manufacturer_part_number="USBLC6-2SC6",
        pads=[(-1, 0, 0.4, 0.5), (0, 0, 0.4, 0.5), (1, 0, 0.4, 0.5)],
    )
    _named_pins(elements, 2, ["IO1", "IO2", "GND"])
    fixtures.connect(elements, "U1", 0, "USB_DP")
    fixtures.connect(elements, "U1", 1, "USB_DM")
    fixtures.connect(elements, "U1", 2, "GND")

    elements += fixtures.component(
        "U2",
        index=3,
        x=0,
        y=15,
        ftype="simple_chip",
        manufacturer_part_number="RP2040",
        pads=[(-1, 0, 0.4, 0.5), (1, 0, 0.4, 0.5)],
    )
    _named_pins(elements, 3, ["USB_DP", "USB_DM"])
    fixtures.connect(elements, "U2", 0, "USB_DP")
    fixtures.connect(elements, "U2", 1, "USB_DM")
    elements += [
        fixtures.trace_on("dp", 1, [(0, 0), (0, 15)]),
        fixtures.trace_on("dm", 2, [(1, 0), (1, 15)]),
        fixtures.trace_on("gnd", 0, [(-8, 0), (-8, 15)]),
    ]
    elements += [_via(i, "USB_DP", 0, 2 + i) for i in range(dp_vias)]
    if ground_plane:
        elements.append(
            {
                "type": "pcb_copper_pour",
                "pcb_copper_pour_id": "gnd_plane",
                "source_net_id": "source_net_0",
                "layer": "bottom",
                "shape": "brep",
            }
        )
    return Board(elements)


def test_a_fast_board_without_ground_plane_is_named_with_measured_return_path():
    result = layout.check(_usb_board())
    assert "layout_reference_plane_missing" in kinds(result, "warning")
    detail = next(
        item["detail"]
        for item in result.findings
        if item["kind"] == "layout_reference_plane_missing"
    )
    assert "15.0mm routed GND tree" in detail


def test_a_ground_pour_tied_to_gnd_satisfies_the_reference_check():
    result = layout.check(_usb_board(ground_plane=True))
    assert "layout_reference_plane_missing" not in kinds(result)


def test_usb_data_with_many_layer_changes_is_caught():
    result = layout.check(_usb_board(dp_vias=4, ground_plane=True))
    assert "layout_usb_layer_changes" in kinds(result, "warning")
    detail = next(
        item["detail"]
        for item in result.findings
        if item["kind"] == "layout_usb_layer_changes"
    )
    assert "4 via(s)" in detail and "15.00mm" in detail


def test_esd_protection_far_behind_the_connector_is_caught():
    result = layout.check(_usb_board(esd_y=12, ground_plane=True))
    assert "layout_esd_distant" in kinds(result, "warning")
    assert "12.0mm" in next(
        item["detail"] for item in result.findings if item["kind"] == "layout_esd_distant"
    )


def test_a_close_esd_part_is_clean():
    result = layout.check(_usb_board(esd_y=3, ground_plane=True))
    assert "layout_esd_distant" not in kinds(result)


def _crystal_board(*, length: float, vias: int) -> Board:
    elements = [fixtures.board(30, 20)]
    elements += [fixtures.net(0, "GND", is_ground=True), fixtures.net(1, "XIN")]
    elements += fixtures.component("Y1", index=1, x=-5, y=0, ftype="simple_crystal")
    _named_pins(elements, 1, ["XIN", "GND"])
    fixtures.connect(elements, "Y1", 0, "XIN")
    fixtures.connect(elements, "Y1", 1, "GND")
    elements += fixtures.component(
        "U1", index=2, x=5, y=0, ftype="simple_chip", manufacturer_part_number="RP2040"
    )
    _named_pins(elements, 2, ["XIN", "GND"])
    fixtures.connect(elements, "U1", 0, "XIN")
    fixtures.connect(elements, "U1", 1, "GND")
    elements.append(fixtures.trace_on("xin", 1, [(0, 0), (length, 0)]))
    elements += [_via(i, "XIN", float(i), 0) for i in range(vias)]
    return Board(elements)


def test_a_long_via_bearing_crystal_loop_is_caught():
    result = layout.check(_crystal_board(length=12, vias=2))
    assert "layout_crystal_route" in kinds(result, "warning")
    detail = next(
        item["detail"] for item in result.findings if item["kind"] == "layout_crystal_route"
    )
    assert "12.00mm" in detail and "2 via(s)" in detail


def test_a_short_single_layer_crystal_loop_passes_its_route_budget():
    result = layout.check(_crystal_board(length=5, vias=0))
    assert "layout_crystal_route" not in kinds(result)


def test_qspi_clock_is_identified_from_pin_names_even_on_an_unnamed_net():
    elements = [fixtures.board(50, 30), fixtures.net(1, "FLASH_INTERNAL")]
    elements += fixtures.component(
        "U3", index=3, x=0, y=0, ftype="simple_chip", manufacturer_part_number="RP2040"
    )
    _named_pins(elements, 3, ["QSPI_SCLK", "GND"])
    fixtures.connect(elements, "U3", 0, "FLASH_INTERNAL")
    elements += fixtures.component(
        "U4", index=4, x=20, y=0, ftype="simple_chip", manufacturer_part_number="W25Q128JVSIQ"
    )
    _named_pins(elements, 4, ["CLK", "GND"])
    fixtures.connect(elements, "U4", 0, "FLASH_INTERNAL")
    elements.append(fixtures.trace_on("clock", 1, [(0, 0), (30, 0)]))
    elements += [_via(0, "FLASH_INTERNAL", 10, 0), _via(1, "FLASH_INTERNAL", 20, 0)]
    result = layout.check(Board(elements))
    assert "layout_qspi_clock_route" in kinds(result, "warning")


def test_coverage_says_impedance_and_emi_are_not_inferred():
    result = layout.check(_usb_board())
    blind = " ".join(result.coverage.blind)
    assert "impedance" in blind and "EMI" in blind


def _explicit_width_board(*, requested: float, actual: float) -> Board:
    source_id = "source_trace_signal"
    return Board(
        [
            fixtures.board(20, 10),
            {
                "type": "source_trace",
                "source_trace_id": source_id,
                "name": "TR_CONTROL",
                "connected_source_port_ids": ["source_port_a", "source_port_b"],
                "min_trace_thickness": requested,
                "subcircuit_connectivity_map_key": "conn_control",
            },
            {
                "type": "pcb_trace",
                "pcb_trace_id": "pcb_trace_control",
                "source_trace_id": source_id,
                "connection_name": source_id,
                "route": [
                    {
                        "route_type": "wire",
                        "x": -4,
                        "y": 0,
                        "width": actual,
                        "layer": "top",
                    },
                    {
                        "route_type": "wire",
                        "x": 4,
                        "y": 0,
                        "width": actual,
                        "layer": "top",
                    },
                ],
            },
        ]
    )


def test_an_explicit_source_minimum_cannot_silently_shrink_to_the_board_floor():
    result = layout.check(_explicit_width_board(requested=0.25, actual=0.15))

    assert "layout_trace_below_requested" in kinds(result, "error")
    detail = next(
        item["detail"]
        for item in result.findings
        if item["kind"] == "layout_trace_below_requested"
    )
    assert "0.15mm" in detail and "0.25mm" in detail


def test_copper_at_the_explicit_source_minimum_passes():
    result = layout.check(_explicit_width_board(requested=0.25, actual=0.25))

    assert "layout_trace_below_requested" not in kinds(result)
