"""Stage 3b: holding the exported schematic to the design's own netlist.

Every case here is a defect that was measured on a real export, not a shape
that looked risky. The three that cost the most time:

* the drawn pin's identity lives in its **name**, not its number, and the two
  disagree on `Device:crystal_4pin_right`;
* KiCad multiplies millimetres by 10000 to reach internal units, and dividing
  by 0.0001 instead is off by a whole unit on real coordinates — enough that a
  correct label lands 100nm from its pin and connects nothing;
* three symbols placed on one point cannot be repaired with labels at all, and
  a pass that tries loops forever.
"""

from __future__ import annotations

from pathlib import Path

from circuitpy.kicad_schematic import (
    LibPin,
    Placement,
    _library_pins,
    _parse,
    _pin_position,
    _placements,
    _to_iu,
    normalize_schematic_truth,
    source_netlist,
    symbol_pin_sources,
)

# ---------------------------------------------------------------------------
# The design's own netlist.
# ---------------------------------------------------------------------------


def _switch_design() -> list[dict]:
    """One 4-pad button wired diagonally, exactly as `sw-tact` emits it."""
    return [
        {
            "type": "source_component",
            "source_component_id": "sc1",
            "name": "SW10",
            "ftype": "simple_push_button",
            "internally_connected_source_port_ids": [
                ["source_port_2", "source_port_3"],
                ["source_port_4", "source_port_5"],
            ],
        },
        *(
            {
                "type": "source_port",
                "source_port_id": f"source_port_{i}",
                "source_component_id": "sc1",
                "pin_number": i - 1,
                "name": f"pin{i - 1}",
            }
            for i in range(2, 6)
        ),
        {"type": "source_net", "source_net_id": "n_col", "name": "K00"},
        {"type": "source_net", "source_net_id": "n_row", "name": "ROW0"},
        {
            "type": "source_trace",
            "connected_source_port_ids": ["source_port_2"],
            "connected_source_net_ids": ["n_col"],
        },
        {
            "type": "source_trace",
            "connected_source_port_ids": ["source_port_5"],
            "connected_source_net_ids": ["n_row"],
        },
    ]


def test_declared_groups_fold_the_button_without_shorting_it() -> None:
    truth = source_netlist(_switch_design())
    assert truth.groups["SW10"] == [["1", "2"], ["3", "4"]]
    # Both terminals exist and they are NOT the same net: the whole point.
    assert truth.pin_net[("SW10", "1")] == truth.pin_net[("SW10", "2")]
    assert truth.pin_net[("SW10", "3")] == truth.pin_net[("SW10", "4")]
    assert truth.pin_net[("SW10", "1")] != truth.pin_net[("SW10", "3")]


def test_a_group_entry_is_a_port_id_not_a_pin_number() -> None:
    """`source_port_2` means pin 1. Reading its trailing digit ties 2 to 3 —
    which shorts the switch inside our own model of the design."""
    truth = source_netlist(_switch_design())
    assert truth.pin_net[("SW10", "2")] != truth.pin_net[("SW10", "3")]


def test_folded_symbol_maps_drawn_pins_to_declared_groups() -> None:
    truth = source_netlist(_switch_design())
    place = Placement("SW10", "Device:push_button_normally_open_momentary_right",
                      61.025, 45.5, 0.0, ("1", "2", "3", "4"))
    drawn = [LibPin("1", "1", -7.05, -0.75, 0.0),
             LibPin("2", "2", 7.05, -0.75, 180.0)]
    mapping, reason = symbol_pin_sources(place, drawn, truth)
    assert reason is None
    # Not {1:[1], 2:[2]} — pin 2 of the symbol is pads 3 and 4 of the part.
    assert mapping == {"1": ["1", "2"], "2": ["3", "4"]}


# ---------------------------------------------------------------------------
# The drawn pin's name is its identity.
# ---------------------------------------------------------------------------


def _crystal_design() -> list[dict]:
    ports = {1: "XIN", 2: "GND", 3: "XOUT", 4: "GND"}
    elements: list[dict] = [
        {"type": "source_component", "source_component_id": "y", "name": "Y1"},
        {"type": "source_net", "source_net_id": "n_gnd", "name": "GND"},
        {"type": "source_net", "source_net_id": "n_xin", "name": "XIN"},
        {"type": "source_net", "source_net_id": "n_xout", "name": "XOUT"},
    ]
    for pin, net in ports.items():
        elements.append({
            "type": "source_port", "source_port_id": f"y{pin}",
            "source_component_id": "y", "pin_number": pin, "name": f"pin{pin}",
        })
        elements.append({
            "type": "source_trace", "connected_source_port_ids": [f"y{pin}"],
            "connected_source_net_ids": [f"n_{net.lower()}"],
        })
    return elements


def test_crystal_pin_name_beats_pin_number() -> None:
    """`Device:crystal_4pin_right` runs num 1..4 against name 4,2,1,3. Drawn
    pin 1 is the part's pin 4 — a ground leg — and calling it pin 1 puts XIN's
    name on ground, which is what the first version of this pass did."""
    truth = source_netlist(_crystal_design())
    place = Placement("Y1", "Device:crystal_4pin_right", 424.83125, 255.5, 0.0,
                      ("1", "2", "3", "4"))
    drawn = [LibPin("1", "4", 0.0, 10.65, 270.0),
             LibPin("2", "2", -0.3, -10.65, 90.0),
             LibPin("3", "1", -8.1, -0.15, 0.0),
             LibPin("4", "3", 8.1, -0.15, 180.0)]
    mapping, reason = symbol_pin_sources(place, drawn, truth)
    assert reason is None
    assert mapping == {"1": ["4"], "2": ["2"], "3": ["1"], "4": ["3"]}
    assert truth.pin_net[("Y1", mapping["3"][0])] == \
        truth.pin_net[("Y1", "1")] != truth.pin_net[("Y1", "4")]


def test_ordinary_part_is_unaffected_by_the_name_rule() -> None:
    truth = source_netlist(_crystal_design())
    place = Placement("Y1", "Device:whatever", 0.0, 0.0, 0.0, ())
    drawn = [LibPin(str(i), str(i), 0.0, 0.0, 0.0) for i in (1, 2, 3, 4)]
    mapping, reason = symbol_pin_sources(place, drawn, truth)
    assert reason is None
    assert mapping == {"1": ["1"], "2": ["2"], "3": ["3"], "4": ["4"]}


# ---------------------------------------------------------------------------
# KiCad's grid.
# ---------------------------------------------------------------------------


def test_internal_units_multiply_the_way_kicad_multiplies() -> None:
    """424.83124999999995mm is 4248312.5 IU multiplied and 4248312.499999999
    IU divided. KiCad multiplies, so 4248313 is the pin's real home; the other
    answer put three of harness-puck's four crystal labels 100nm off."""
    assert _to_iu(424.83124999999995) == 4248313
    assert _to_iu(34.831249999999955) == 348312
    assert _to_iu(-73.97500000000002) == -739750
    assert _to_iu(645.5) == 6455000


def test_pin_position_snaps_before_it_adds() -> None:
    """harness-puck's J1: KiCad reads the symbol at 348312 IU and pin 1 at
    348312 - 127000 = 221312. Adding in float and snapping afterwards gives
    221313, and a stub there bonds to nothing."""
    place = Placement("J1", "Device:J_TYPE-C-31-M-12",
                      34.831249999999955, 375.5, 0.0, ())
    x, y = _pin_position(place, LibPin("1", "EH2", -12.7, 8.89, 0.0))
    assert _to_iu(x) == 221312
    assert _to_iu(y) == 3666100


def test_pin_position_rotates_on_the_grid() -> None:
    place = Placement("U1", "Device:x", 100.0, 200.0, 90.0, ())
    pin = LibPin("1", "1", 2.54, 1.27, 0.0)
    assert _pin_position(place, pin) == (98.73, 197.46)


# ---------------------------------------------------------------------------
# End to end, against a netlist reader we control.
# ---------------------------------------------------------------------------

_SHEET = """(kicad_sch
  (version 20250114)
  (lib_symbols
    (symbol "Device:R"
      (symbol "Device:R_1_1"
        (pin passive line
          (at -3.81 0 0)
          (length 1.27)
          (name "1"
            (effects
              (font
                (size 1.27 1.27)
              )
            )
          )
          (number "1"
            (effects
              (font
                (size 1.27 1.27)
              )
            )
          )
        )
        (pin passive line
          (at 3.81 0 180)
          (length 1.27)
          (name "2"
            (effects
              (font
                (size 1.27 1.27)
              )
            )
          )
          (number "2"
            (effects
              (font
                (size 1.27 1.27)
              )
            )
          )
        )
      )
    )
  )
  (symbol
    (lib_id "Device:R")
    (at 50 50 0)
    (property "Reference" "R1"
      (id 0)
      (at 50 46 0)
    )
  )
  (symbol
    (lib_id "Device:R")
    (at 80 50 0)
    (property "Reference" "R2"
      (id 0)
      (at 80 46 0)
    )
  )
)
"""


def _two_resistor_design() -> list[dict]:
    elements: list[dict] = [
        {"type": "source_net", "source_net_id": "n_sig", "name": "SIG"},
    ]
    for ref, cid in (("R1", "a"), ("R2", "b")):
        elements.append({"type": "source_component",
                         "source_component_id": cid, "name": ref})
        for pin in (1, 2):
            elements.append({"type": "source_port",
                             "source_port_id": f"{cid}{pin}",
                             "source_component_id": cid,
                             "pin_number": pin, "name": f"pin{pin}"})
    # R1.2 and R2.1 are one net that the drawing never draws.
    elements.append({"type": "source_trace",
                     "connected_source_port_ids": ["a2", "b1"],
                     "connected_source_net_ids": ["n_sig"]})
    return elements


def _reader_from(text_holder: dict[str, str]):
    """A netlist reader that answers from whatever the file currently says.

    Deliberately dumb: a pin is on a net only when a global label with that
    net's name sits on the pin's own point. That is the property the repair
    depends on, so a test that mocked it away would prove nothing.
    """

    def read(path: Path) -> dict[tuple[str, str], str]:
        import re as _re

        text = path.read_text(encoding="utf-8")
        text_holder["text"] = text
        labels: dict[tuple[float, float], str] = {}
        for match in _re.finditer(
            r'\(global_label "([^"]+)"\s*\n\s*\(shape \w+\)\s*\n\s*'
            r"\(at ([0-9.eE+-]+) ([0-9.eE+-]+)",
            text,
        ):
            labels[(round(float(match.group(2)), 4),
                    round(float(match.group(3)), 4))] = match.group(1)
        tree = _parse(text)
        library = _library_pins(tree[0])
        out: dict[tuple[str, str], str] = {}
        for place in _placements(tree[0]):
            for pin in library.get(place.lib_id, []):
                x, y = _pin_position(place, pin)
                name = labels.get((round(x, 4), round(y, 4)))
                out[(place.ref, pin.number)] = (
                    name or f"unconnected-({place.ref}-Pad{pin.number})"
                )
        return out

    return read


def test_a_net_the_drawing_never_drew_is_repaired_and_stays_repaired(
    tmp_path: Path,
) -> None:
    path = tmp_path / "board.kicad_sch"
    path.write_text(_SHEET, encoding="utf-8")
    holder: dict[str, str] = {}
    result = normalize_schematic_truth(
        path, _two_resistor_design(), read_netlist=_reader_from(holder)
    )
    assert result.measured
    assert result.wrong_before == 2      # R1.2 and R2.1, both floating
    assert result.wrong_after == 0
    assert 'global_label "SIG"' in path.read_text(encoding="utf-8")

    # Idempotent: a second pass on the repaired file must not touch it. A pass
    # that keeps editing is a pass that makes every build's artifact differ.
    before = path.read_bytes()
    again = normalize_schematic_truth(
        path, _two_resistor_design(), read_netlist=_reader_from(holder)
    )
    assert path.read_bytes() == before
    assert again.wrong_before == 0 and not again.changed


def test_a_dead_netlist_reader_leaves_the_file_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    """The pass must never cost a build. A reader that raises is a note."""
    path = tmp_path / "board.kicad_sch"
    path.write_text(_SHEET, encoding="utf-8")
    before = path.read_bytes()

    def boom(_path: Path) -> dict[tuple[str, str], str]:
        raise RuntimeError("kicad-cli went away")

    result = normalize_schematic_truth(path, _two_resistor_design(),
                                       read_netlist=boom)
    assert path.read_bytes() == before
    assert result.notes and "kicad-cli went away" in result.notes[0]


# ---------------------------------------------------------------------------
# Symbols stacked on one point.
# ---------------------------------------------------------------------------

_STACKED = _SHEET.replace("(at 80 50 0)", "(at 50 50 0)")


def _two_nets_design() -> list[dict]:
    elements: list[dict] = [
        {"type": "source_net", "source_net_id": "n_a", "name": "SWCLK"},
        {"type": "source_net", "source_net_id": "n_b", "name": "SWD"},
    ]
    for ref, cid, net in (("R1", "a", "n_a"), ("R2", "b", "n_b")):
        elements.append({"type": "source_component",
                         "source_component_id": cid, "name": ref})
        for pin in (1, 2):
            elements.append({"type": "source_port",
                             "source_port_id": f"{cid}{pin}",
                             "source_component_id": cid,
                             "pin_number": pin, "name": f"pin{pin}"})
        elements.append({"type": "source_trace",
                         "connected_source_port_ids": [f"{cid}1", f"{cid}2"],
                         "connected_source_net_ids": [net]})
    return elements


def test_two_symbols_on_one_point_are_pulled_apart(tmp_path: Path) -> None:
    """hydrate-coaster's TP1/TP2/TP3 all sat at (660.125, 496.5541), so the
    drawing tied SWCLK, SWD and GND into one node. No label can fix that — two
    pins on one coordinate are one node whatever is written there — so the
    symbols move."""
    path = tmp_path / "board.kicad_sch"
    path.write_text(_STACKED, encoding="utf-8")
    holder: dict[str, str] = {}
    result = normalize_schematic_truth(
        path, _two_nets_design(), read_netlist=_reader_from(holder)
    )
    assert result.symbols_unstacked == 2   # both pins of the pair collided
    tree = _parse(path.read_text(encoding="utf-8"))
    library = _library_pins(tree[0])
    points = [
        _pin_position(place, pin)
        for place in _placements(tree[0])
        for pin in library.get(place.lib_id, [])
    ]
    assert len(points) == len(set(points))
    assert result.wrong_after == 0
