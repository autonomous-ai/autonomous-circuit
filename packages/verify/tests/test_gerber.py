"""The gerber reader and the packet-vs-design reconciliation.

Two kinds of test here. The parser tests build gerber text by hand so the
expected geometry is known exactly. The reconciliation tests build a *correct*
packet, prove it is clean, then mutate one thing — delete a layer, scale the
coordinates, drop a drill, cover a pad in mask — and require the check to
catch it. A gate that has never been shown failing is a gate nobody should
trust.
"""

from __future__ import annotations

import copy
import zipfile
from pathlib import Path

import fixtures
import pytest

from verifylib import gerber, gerber_truth
from verifylib.model import Board

# ---------------------------------------------------------------------------
# Gerber text builders.
# ---------------------------------------------------------------------------

_HEADER = """%FSLAX46Y46*%
%MOMM*%
%LPD*%
G01*
"""


def _coord(value: float) -> str:
    return f"{round(value * 1_000_000):d}"


def gerber_text(
    *,
    apertures: dict[int, str],
    flashes: list[tuple[int, float, float]] = (),
    draws: list[tuple[int, float, float, float, float]] = (),
    regions: list[list[tuple[float, float]]] = (),
    file_function: str | None = None,
) -> str:
    lines = []
    if file_function:
        lines.append(f"%TF.FileFunction,{file_function}*%")
    lines.append(_HEADER.rstrip("\n"))
    for code, definition in sorted(apertures.items()):
        lines.append(f"%ADD{code}{definition}*%")
    for code, x, y in flashes:
        lines.append(f"D{code}*")
        lines.append(f"X{_coord(x)}Y{_coord(y)}D03*")
    for code, x0, y0, x1, y1 in draws:
        lines.append(f"D{code}*")
        lines.append(f"X{_coord(x0)}Y{_coord(y0)}D02*")
        lines.append(f"X{_coord(x1)}Y{_coord(y1)}D01*")
    for contour in regions:
        if not contour:
            continue
        first_x, first_y = contour[0]
        # Real RS-274X moves to the contour start before entering region mode.
        # Doing the move inside G36 makes the parser correctly retain the
        # previous graphics position as part of the filled contour.
        lines.append(f"X{_coord(first_x)}Y{_coord(first_y)}D02*")
        lines.append("G36*")
        for x, y in contour[1:]:
            lines.append(f"X{_coord(x)}Y{_coord(y)}D01*")
        if contour[-1] != contour[0]:
            lines.append(f"X{_coord(first_x)}Y{_coord(first_y)}D01*")
        lines.append("G37*")
    lines.append("M02*")
    return "\n".join(lines) + "\n"


def excellon_text(
    tools: dict[int, tuple[float, bool]],
    hits: list[tuple[int, float, float]],
    slots: list[tuple[int, float, float, float, float]] = (),
) -> str:
    lines = ["M48", "FMAT,2", "METRIC"]
    for code, (diameter, plated) in sorted(tools.items()):
        kind = "Plated,PTH,ComponentDrill" if plated else "NonPlated,NPTH,ComponentDrill"
        lines.append(f"; #@! TA.AperFunction,{kind}")
        lines.append(f"T{code}C{diameter:.3f}")
    lines += ["%", "G90", "G05"]
    for code in sorted(tools):
        own_hits = [h for h in hits if h[0] == code]
        own_slots = [s for s in slots if s[0] == code]
        if not own_hits and not own_slots:
            continue
        lines.append(f"T{code}")
        for _, x, y in own_hits:
            lines.append(f"X{x}Y{y}")
        for _, x0, y0, x1, y1 in own_slots:
            lines.append(f"X{x0}Y{y0}G85X{x1}Y{y1}")
    lines.append("M30")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Parser.
# ---------------------------------------------------------------------------


def test_apertures_and_flashes_round_trip():
    text = gerber_text(
        apertures={10: "C,0.5", 11: "R,1.0X2.0", 12: "O,1.2X1.8"},
        flashes=[(10, 5.0, -5.0), (11, 6.0, -5.0)],
    )
    layer = gerber.parse_gerber(text)
    assert layer.apertures[10].size == (0.5, 0.5)
    assert layer.apertures[11].size == (1.0, 2.0)
    assert layer.apertures[12].size == (1.2, 1.8)
    assert [(f.x, f.y) for f in layer.flashes] == [(5.0, -5.0), (6.0, -5.0)]


def test_draw_width_comes_from_the_aperture():
    text = gerber_text(apertures={10: "C,0.15"}, draws=[(10, 0, 0, 10, 0)])
    layer = gerber.parse_gerber(text)
    assert layer.min_draw_width == pytest.approx(0.15)
    assert layer.draws[0].length == pytest.approx(10.0)


def test_regions_are_read_not_dropped():
    """A copper pour is plotted as a region. Skipping G36 would make a poured
    board look emptier than it is — hydrate-coaster has 39 of them."""
    text = gerber_text(
        apertures={10: "C,0.1"},
        regions=[[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]],
    )
    layer = gerber.parse_gerber(text)
    assert len(layer.regions) == 1
    assert layer.regions[0].bounds.width == pytest.approx(10.0)
    assert not layer.unsupported


def test_arcs_are_reported_as_unsupported_rather_than_dropped_silently():
    text = gerber_text(apertures={10: "C,0.1"}, draws=[(10, 0, 0, 1, 0)])
    text = text.replace("M02*", "G03*\nM02*")
    layer = gerber.parse_gerber(text)
    assert any("circular" in u for u in layer.unsupported)


def test_inch_units_are_refused():
    text = gerber_text(apertures={10: "C,0.1"}).replace("%MOMM*%", "%MOIN*%")
    with pytest.raises(gerber.GerberError):
        gerber.parse_gerber(text)


def test_coordinate_format_is_honoured():
    """A 4.6 file read as 3.5 is off by a factor of ten — the exact failure a
    packet check exists to catch, so the parser must not paper over it."""
    text = gerber_text(apertures={10: "C,0.1"}, flashes=[(10, 12.345678, 0)])
    layer = gerber.parse_gerber(text)
    assert layer.flashes[0].x == pytest.approx(12.345678)


def test_excellon_tools_carry_plating():
    text = excellon_text({1: (0.3, True), 2: (2.2, False)}, [(1, 5.0, -5.0), (2, 1.0, -1.0)])
    drill = gerber.parse_excellon(text)
    assert drill.tools[1].plated is True
    assert drill.tools[2].plated is False
    assert len(drill.hits) == 2


def test_a_routed_slot_reports_its_centre_and_extent():
    """Reading a G85 slot as a round hole at its first endpoint misplaced four
    holes on every example board and produced four phantom missing drills."""
    text = excellon_text({2: (0.8, True)}, [], slots=[(2, 95.675, -127.194, 95.675, -127.994)])
    drill = gerber.parse_excellon(text)
    hit = drill.hits[0]
    assert hit.is_slot
    assert hit.center == pytest.approx((95.675, -127.594))
    assert hit.size == pytest.approx((0.8, 1.6))


def test_layer_roles_are_recognised_by_name_and_by_extension():
    assert gerber.role_of("board-F_Cu.gtl") == "copper_top"
    assert gerber.role_of("something.gbl") == "copper_bottom"
    assert gerber.role_of("board-Edge_Cuts.gm1") == "outline"
    assert gerber.role_of("board.drl") == "drill"
    assert gerber.role_of("readme.pdf") is None


# ---------------------------------------------------------------------------
# Reconciliation. Build a correct packet, then break one thing at a time.
# ---------------------------------------------------------------------------

BOARD_W, BOARD_H = 40.0, 30.0
OFFSET_X, OFFSET_Y = 100.0, -100.0
PADS = [(-5.0, 0.0), (5.0, 0.0)]
VIA = (0.0, 5.0)


def _design() -> Board:
    elements = [fixtures.board(BOARD_W, BOARD_H)]
    elements += fixtures.component(
        "R1", index=1, x=0, y=0,
        pads=[(dx, dy, 0.6, 0.6) for dx, dy in PADS],
        courtyard=(11.0, 1.6),
    )
    elements.append(
        {
            "type": "pcb_via",
            "pcb_via_id": "pcb_via_0",
            "x": VIA[0],
            "y": VIA[1],
            "hole_diameter": 0.3,
            "outer_diameter": 0.6,
            "layers": ["top", "bottom"],
        }
    )
    return Board(elements)


def _outline_draws() -> list[tuple[int, float, float, float, float]]:
    x0, y0 = OFFSET_X - BOARD_W / 2, OFFSET_Y - BOARD_H / 2
    x1, y1 = OFFSET_X + BOARD_W / 2, OFFSET_Y + BOARD_H / 2
    return [
        (10, x0, y0, x1, y0),
        (10, x1, y0, x1, y1),
        (10, x1, y1, x0, y1),
        (10, x0, y1, x0, y0),
    ]


def _members(**overrides) -> dict[str, str]:
    pad_flashes = [(10, x + OFFSET_X, y + OFFSET_Y) for x, y in PADS]
    via_flash = [(11, VIA[0] + OFFSET_X, VIA[1] + OFFSET_Y)]
    members = {
        "board-F_Cu.gtl": gerber_text(
            apertures={10: "R,0.6X0.6", 11: "C,0.6"},
            flashes=pad_flashes + via_flash,
            draws=[(12, OFFSET_X - 5, OFFSET_Y, OFFSET_X + 5, OFFSET_Y)],
        ).replace("%ADD11C,0.6*%", "%ADD11C,0.6*%\n%ADD12C,0.2*%"),
        "board-B_Cu.gbl": gerber_text(apertures={10: "C,0.6"}, flashes=via_flash),
        "board-F_Mask.gts": gerber_text(apertures={10: "R,0.7X0.7"}, flashes=pad_flashes),
        "board-B_Mask.gbs": gerber_text(apertures={10: "C,0.1"}),
        "board-F_Silkscreen.gto": gerber_text(
            apertures={10: "C,0.2"},
            draws=[(10, OFFSET_X - 8, OFFSET_Y + 3, OFFSET_X + 8, OFFSET_Y + 3)],
        ),
        "board-F_Paste.gtp": gerber_text(apertures={10: "R,0.6X0.6"}, flashes=pad_flashes),
        "board-Edge_Cuts.gm1": gerber_text(apertures={10: "C,0.1"}, draws=_outline_draws()),
        "board.drl": excellon_text(
            {1: (0.3, True)}, [(1, VIA[0] + OFFSET_X, VIA[1] + OFFSET_Y)]
        ),
    }
    members.update(overrides)
    return members


def _zip(tmp_path: Path, members: dict[str, str]) -> str:
    path = tmp_path / "gerbers.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            if text is None:
                continue
            archive.writestr(name, text)
    return str(path)


def kinds(result, severity: str | None = None) -> set[str]:
    return {
        f["kind"]
        for f in result.findings
        if severity is None or f["severity"] == severity
    }


def test_a_correct_packet_reconciles_clean(tmp_path):
    result = gerber_truth.check(_design(), _zip(tmp_path, _members()))
    assert result.blocking == 0, [f for f in result.findings if f["severity"] == "error"]


def test_the_transform_is_solved_from_the_outline(tmp_path):
    result = gerber_truth.check(_design(), _zip(tmp_path, _members()))
    note = " ".join(result.notes)
    assert "+100.000" in note and "-100.000" in note and "1.0000" in note


def test_a_missing_copper_layer_blocks(tmp_path):
    members = _members()
    del members["board-B_Cu.gbl"]
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_missing_layer" in kinds(result, "error")


def test_a_missing_drill_file_blocks(tmp_path):
    members = _members()
    del members["board.drl"]
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_missing_layer" in kinds(result, "error")


def test_a_dropped_drill_hit_blocks(tmp_path):
    members = _members(**{"board.drl": excellon_text({1: (0.3, True)}, [])})
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_drill_empty" in kinds(result, "error") or "gerber_drill_missing" in kinds(
        result, "error"
    )


def test_a_wrong_drill_diameter_blocks(tmp_path):
    members = _members(
        **{
            "board.drl": excellon_text(
                {1: (0.9, True)}, [(1, VIA[0] + OFFSET_X, VIA[1] + OFFSET_Y)]
            )
        }
    )
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_drill_size_mismatch" in kinds(result, "error")


def test_a_plating_flip_blocks(tmp_path):
    members = _members(
        **{
            "board.drl": excellon_text(
                {1: (0.3, False)}, [(1, VIA[0] + OFFSET_X, VIA[1] + OFFSET_Y)]
            )
        }
    )
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_drill_plating_mismatch" in kinds(result, "error")


def test_a_units_scale_error_blocks_and_names_the_factor(tmp_path):
    """The classic export bug: a 4.6-format file emitted as 4.5. Everything
    upstream is clean; only the shipped file is wrong."""
    x0, y0 = OFFSET_X - BOARD_W / 2, OFFSET_Y - BOARD_H / 2
    shrunk = [
        (10, x0, y0, x0 + BOARD_W / 10, y0),
        (10, x0 + BOARD_W / 10, y0, x0 + BOARD_W / 10, y0 + BOARD_H / 10),
        (10, x0 + BOARD_W / 10, y0 + BOARD_H / 10, x0, y0 + BOARD_H / 10),
        (10, x0, y0 + BOARD_H / 10, x0, y0),
    ]
    members = _members(
        **{
            "board-Edge_Cuts.gm1": gerber_text(apertures={10: "C,0.1"}, draws=shrunk)
        }
    )
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_scale_mismatch" in kinds(result, "error")
    detail = next(
        f["detail"] for f in result.findings if f["kind"] == "gerber_scale_mismatch"
    )
    assert "0.1000" in detail


def test_an_outline_that_is_the_wrong_size_blocks(tmp_path):
    x0, y0 = OFFSET_X - BOARD_W / 2, OFFSET_Y - BOARD_H / 2
    wrong = [
        (10, x0, y0, x0 + BOARD_W, y0),
        (10, x0 + BOARD_W, y0, x0 + BOARD_W, y0 + BOARD_H - 2.0),
        (10, x0 + BOARD_W, y0 + BOARD_H - 2.0, x0, y0 + BOARD_H - 2.0),
        (10, x0, y0 + BOARD_H - 2.0, x0, y0),
    ]
    members = _members(
        **{"board-Edge_Cuts.gm1": gerber_text(apertures={10: "C,0.1"}, draws=wrong)}
    )
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_outline_mismatch" in kinds(result, "error")


def test_a_dropped_footprint_blocks(tmp_path):
    members = _members(
        **{
            "board-F_Cu.gtl": gerber_text(
                apertures={10: "C,0.6"},
                flashes=[(10, VIA[0] + OFFSET_X, VIA[1] + OFFSET_Y)],
            )
        }
    )
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_pad_missing" in kinds(result, "error")


def test_a_pad_with_no_mask_opening_blocks(tmp_path):
    members = _members(**{"board-F_Mask.gts": gerber_text(apertures={10: "C,0.1"})})
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_pad_masked_over" in kinds(result, "error")


def test_an_explicit_mask_covered_routing_node_needs_copper_but_not_mask_or_paste(
    tmp_path,
):
    design = _design()
    hidden_pad = next(
        element
        for element in design.elements
        if element.get("pcb_smtpad_id") == "pcb_smtpad_1_0"
    )
    hidden_pad["is_covered_with_solder_mask"] = True
    design = Board(design.elements)

    visible_x, visible_y = PADS[1]
    visible_flash = [(10, visible_x + OFFSET_X, visible_y + OFFSET_Y)]
    members = _members(
        **{
            "board-F_Mask.gts": gerber_text(
                apertures={10: "R,0.7X0.7"}, flashes=visible_flash
            ),
            "board-F_Paste.gtp": gerber_text(
                apertures={10: "R,0.6X0.6"}, flashes=visible_flash
            ),
        }
    )
    result = gerber_truth.check(design, _zip(tmp_path, members))
    assert "gerber_pad_masked_over" not in kinds(result)
    assert "gerber_pad_no_paste" not in kinds(result)

    members["board-F_Cu.gtl"] = gerber_text(
        apertures={10: "R,0.6X0.6"}, flashes=visible_flash
    )
    missing_copper = gerber_truth.check(design, _zip(tmp_path, members))
    assert "gerber_pad_missing" in kinds(missing_copper, "error")


def test_a_pad_with_no_paste_warns_on_an_assembly_order(tmp_path):
    members = _members(**{"board-F_Paste.gtp": gerber_text(apertures={10: "C,0.1"})})
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_pad_no_paste" in kinds(result, "warning")
    bare = gerber_truth.check(_design(), _zip(tmp_path, members), assembly=False)
    assert "gerber_pad_no_paste" not in kinds(bare)


def test_a_sub_floor_conductor_in_the_plot_blocks(tmp_path):
    members = _members()
    members["board-F_Cu.gtl"] = members["board-F_Cu.gtl"].replace(
        "%ADD12C,0.2*%", "%ADD12C,0.05*%"
    )
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_trace_width" in kinds(result, "error")


def test_mask_slivers_are_caught(tmp_path):
    """Two mask openings 0.05mm apart. Only the mask apertures know this — the
    design has two ordinary pads."""
    close = [(10, OFFSET_X, OFFSET_Y), (10, OFFSET_X + 0.75, OFFSET_Y)]
    members = _members(
        **{"board-F_Mask.gts": gerber_text(apertures={10: "R,0.7X0.7"}, flashes=close)}
    )
    result = gerber_truth.check(
        _close_mask_pad_design(same_part=False), _zip(tmp_path, members)
    )
    assert "gerber_mask_sliver" in kinds(result, "warning")


def _close_mask_pad_design(*, same_part: bool) -> Board:
    elements = [fixtures.board(BOARD_W, BOARD_H)]
    if same_part:
        elements += fixtures.component(
            "U1", index=1, x=0, y=0,
            pads=[(0.0, 0.0, 0.6, 0.6), (0.75, 0.0, 0.6, 0.6)],
            courtyard=(2.0, 1.6),
            manufacturer_part_number="TEST-TWO-PAD",
        )
    else:
        elements += fixtures.component(
            "R1", index=1, x=0, y=0,
            pads=[(0.0, 0.0, 0.6, 0.6)],
            courtyard=(0.7, 1.0),
        )
        elements += fixtures.component(
            "R2", index=2, x=0.75, y=0,
            pads=[(0.0, 0.0, 0.6, 0.6)],
            courtyard=(0.7, 1.0),
        )
    return Board(elements)


def _approval_for(
    board: Board,
    component_name: str,
    *,
    min_web_mm: float = 0.04,
) -> tuple[gerber_truth.ReviewedMaskSliverFootprint, ...]:
    component = board.by_name[component_name]
    assert component.manufacturer_part_number
    assert component.lcsc
    return (
        gerber_truth.ReviewedMaskSliverFootprint(
            manufacturer_part_number=component.manufacturer_part_number,
            supplier_part_number=component.lcsc,
            footprint_sha256=gerber_truth._footprint_signature(component),
            min_web_mm=min_web_mm,
        ),
    )


def _close_mask_members() -> dict[str, str]:
    close = [(10, OFFSET_X, OFFSET_Y), (10, OFFSET_X + 0.75, OFFSET_Y)]
    return _members(
        **{
            "board-F_Mask.gts": gerber_text(
                apertures={10: "R,0.7X0.7"}, flashes=close
            )
        }
    )


def test_reviewed_same_footprint_sliver_is_advisory(tmp_path):
    board = _close_mask_pad_design(same_part=True)
    result = gerber_truth.check(
        board,
        _zip(tmp_path, _close_mask_members()),
        reviewed_mask_sliver_footprints=_approval_for(board, "U1"),
    )
    assert "gerber_mask_sliver_in_footprint" in kinds(result, "info")
    assert "gerber_mask_sliver" not in kinds(result)


def test_review_contract_is_revoked_by_any_pad_geometry_edit():
    original = _close_mask_pad_design(same_part=True)
    contract = _approval_for(original, "U1")
    elements = copy.deepcopy(original.elements)
    pad = next(element for element in elements if element.get("type") == "pcb_smtpad")
    pad["width"] = float(pad["width"]) + 0.01
    changed = Board(elements)
    assert gerber_truth._reviewed_mask_contract(changed.by_name["U1"], contract) is None


def test_review_contract_cannot_waive_a_web_below_its_own_floor(tmp_path):
    board = _close_mask_pad_design(same_part=True)
    result = gerber_truth.check(
        board,
        _zip(tmp_path, _close_mask_members()),
        reviewed_mask_sliver_footprints=_approval_for(board, "U1", min_web_mm=0.06),
    )
    assert "gerber_mask_sliver_unreviewed_footprint" in kinds(result, "error")


def test_unreviewed_same_refdes_sliver_fails_closed(tmp_path):
    result = gerber_truth.check(
        _close_mask_pad_design(same_part=True),
        _zip(tmp_path, _close_mask_members()),
    )
    assert "gerber_mask_sliver_unreviewed_footprint" in kinds(result, "error")
    assert "gerber_mask_sliver_in_footprint" not in kinds(result)


def test_cross_part_sliver_remains_a_fabrication_finding(tmp_path):
    result = gerber_truth.check(
        _close_mask_pad_design(same_part=False),
        _zip(tmp_path, _close_mask_members()),
    )
    assert "gerber_mask_sliver" in kinds(result, "warning")
    assert "gerber_mask_sliver_in_footprint" not in kinds(result)


@pytest.mark.parametrize("side", ["top", "bottom"])
def test_coincident_opposite_side_pad_cannot_steal_mask_opening(tmp_path, side):
    opposite = "bottom" if side == "top" else "top"
    elements = [fixtures.board(BOARD_W, BOARD_H)]
    elements += fixtures.component(
        "U1", index=1, x=0, y=0, layer=side,
        pads=[(0.0, 0.0, 0.6, 0.6), (0.75, 0.0, 0.6, 0.6)],
        manufacturer_part_number=f"TEST-{side.upper()}-TWO-PAD",
    )
    elements += fixtures.component(
        "R9", index=9, x=0, y=0, layer=opposite,
        pads=[(0.0, 0.0, 0.6, 0.6), (0.75, 0.0, 0.6, 0.6)],
        manufacturer_part_number=f"TEST-{opposite.upper()}-TWO-PAD",
    )
    board = Board(elements)
    role = "board-F_Mask.gts" if side == "top" else "board-B_Mask.gbs"
    members = _members(
        **{
            role: gerber_text(
                apertures={10: "R,0.7X0.7"},
                flashes=[(10, OFFSET_X, OFFSET_Y), (10, OFFSET_X + 0.75, OFFSET_Y)],
            )
        }
    )
    result = gerber_truth.check(
        board,
        _zip(tmp_path, members),
        reviewed_mask_sliver_footprints=_approval_for(board, "U1"),
    )
    info = [f for f in result.findings if f["kind"] == "gerber_mask_sliver_in_footprint"]
    assert len(info) == 1
    assert "U1/" in info[0]["detail"]
    assert "gerber_mask_sliver_ownership_unknown" not in kinds(result)


def test_concave_custom_pad_is_owned_by_its_polygon_not_its_bounds(tmp_path):
    elements = [fixtures.board(BOARD_W, BOARD_H)]
    elements += fixtures.component(
        "J1", index=1, x=0, y=0, pads=[],
        manufacturer_part_number="TEST-CONCAVE",
    )
    concave = [
        {"x": -0.35, "y": -0.35}, {"x": 0.35, "y": -0.35},
        {"x": 0.35, "y": -0.10}, {"x": -0.10, "y": -0.10},
        {"x": -0.10, "y": 0.35}, {"x": -0.35, "y": 0.35},
    ]
    second = [
        {"x": 0.40, "y": -0.35}, {"x": 1.00, "y": -0.35},
        {"x": 1.00, "y": -0.10}, {"x": 0.40, "y": -0.10},
    ]
    for index, points in enumerate((concave, second)):
        elements.append(
            {
                "type": "pcb_smtpad",
                "pcb_smtpad_id": f"pcb_smtpad_1_custom_{index}",
                "pcb_component_id": "pcb_component_1",
                "layer": "top",
                "shape": "polygon",
                "points": points,
            }
        )
    # Its centre lies in the concavity but its copper does not overlap J1.
    elements += fixtures.component(
        "R9", index=9, x=0.15, y=0.15,
        pads=[(0.0, 0.0, 0.1, 0.1)],
    )
    board = Board(elements)
    regions = [
        [(x + OFFSET_X, y + OFFSET_Y) for x, y in ((p["x"], p["y"]) for p in points)]
        for points in (concave, second)
    ]
    members = _members(
        **{
            "board-F_Mask.gts": gerber_text(
                apertures={10: "C,0.1"}, regions=regions
            )
        }
    )
    result = gerber_truth.check(
        board,
        _zip(tmp_path, members),
        reviewed_mask_sliver_footprints=_approval_for(board, "J1"),
    )
    assert "gerber_mask_sliver_in_footprint" in kinds(result, "info")
    assert "gerber_mask_sliver_ownership_unknown" not in kinds(result)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unowned_or_ambiguous_mask_opening_fails_closed(tmp_path, ambiguous):
    elements = [fixtures.board(BOARD_W, BOARD_H)]
    if ambiguous:
        elements += fixtures.component(
            "R1", index=1, x=0, y=0, pads=[(0, 0, 0.6, 0.6)]
        )
        elements += fixtures.component(
            "R2", index=2, x=0, y=0, pads=[(0, 0, 0.6, 0.6)]
        )
        elements += fixtures.component(
            "R3", index=3, x=0.75, y=0, pads=[(0, 0, 0.6, 0.6)]
        )
        close = [(10, OFFSET_X, OFFSET_Y), (10, OFFSET_X + 0.75, OFFSET_Y)]
    else:
        elements += fixtures.component(
            "R1", index=1, x=-5, y=0, pads=[(0, 0, 0.6, 0.6)]
        )
        close = [(10, OFFSET_X + 4, OFFSET_Y), (10, OFFSET_X + 4.75, OFFSET_Y)]
    members = _members(
        **{
            "board-F_Mask.gts": gerber_text(
                apertures={10: "R,0.7X0.7"}, flashes=close
            )
        }
    )
    result = gerber_truth.check(Board(elements), _zip(tmp_path, members))
    assert "gerber_mask_sliver_ownership_unknown" in kinds(result, "error")


def _protected_starter_j1_board() -> Board:
    """The exact C165948 pad geometry from the public starter regression."""
    elements = [
        fixtures.board(BOARD_W, BOARD_H),
        {
            "type": "source_component",
            "source_component_id": "source_component_0",
            "name": "J1",
            "ftype": "simple_connector",
            "manufacturer_part_number": "TYPE-C-31-M-12",
            "supplier_part_numbers": {"jlcpcb": ["C165948"]},
        },
        {
            "type": "pcb_component",
            "pcb_component_id": "pcb_component_0",
            "source_component_id": "source_component_0",
            "center": {"x": 0.0, "y": -11.72504355},
            "width": 9.8502216,
            "height": 6.4981709,
            "layer": "top",
            "rotation": 0,
        },
    ]
    for index, x in enumerate(
        (-1.75006, -1.249934, -0.750062, -0.249936, 0.249936, 0.750062, 1.24968, 1.75006)
    ):
        elements.append(
            {
                "type": "pcb_smtpad",
                "pcb_smtpad_id": f"pcb_smtpad_{index}",
                "pcb_component_id": "pcb_component_0",
                "layer": "top",
                "shape": "rect",
                "x": x,
                "y": -9.1259568,
                "width": 0.2999994,
                "height": 1.2999974,
            }
        )
    polygons = (
        [(-2.8999688, -9.775892), (-2.8999688, -8.4758692), (-3.1999682, -8.4758692),
         (-3.1999682, -8.4760216), (-3.4999422, -8.4760216), (-3.4999422, -9.7760444),
         (-3.1999428, -9.7760444), (-3.1999428, -9.775892)],
        [(2.8999942, -8.4758692), (2.8999942, -9.7758412), (3.1999936, -9.7758412),
         (3.5000184, -9.7758412), (3.5000184, -8.4758692), (3.200019, -8.4758692)],
        [(2.7001724, -9.7758412), (2.7001724, -8.4758692), (2.400173, -8.4758692),
         (2.1001482, -8.4758692), (2.1001482, -9.7758412), (2.4001476, -9.7758412)],
        [(-2.0999704, -9.7759936), (-2.0999704, -8.4760216), (-2.3999952, -8.4760216),
         (-2.6999438, -8.476047), (-2.6999438, -9.776019), (-2.399919, -9.776019)],
    )
    for index, points in enumerate(polygons, start=8):
        elements.append(
            {
                "type": "pcb_smtpad",
                "pcb_smtpad_id": f"pcb_smtpad_{index}",
                "pcb_component_id": "pcb_component_0",
                "layer": "top",
                "shape": "polygon",
                "points": [{"x": x, "y": y} for x, y in points],
            }
        )
    for index, (x, y, height) in enumerate(
        ((4.325112, -14.0741308, 1.7999964), (4.325112, -9.8943068, 1.999996),
         (-4.325112, -9.8943068, 1.999996), (-4.325112, -14.0741308, 1.7999964))
    ):
        elements.append(
            {
                "type": "pcb_plated_hole",
                "pcb_plated_hole_id": f"pcb_plated_hole_{index}",
                "pcb_component_id": "pcb_component_0",
                "x": x,
                "y": y,
                "outer_width": 1.1999976,
                "outer_height": height,
                "ccw_rotation": 0,
            }
        )
    hidden = fixtures.component(
        "N3", index=3, x=3.2, y=-7.9,
        pads=[(0.0, 0.0, 0.8, 0.8)],
        manufacturer_part_number="MASKED_COPPER_NODE",
    )
    for element in hidden:
        if element.get("type") == "pcb_smtpad":
            element["is_covered_with_solder_mask"] = True
    return Board(elements + hidden)


def test_protected_starter_uses_exact_reviewed_j1_not_nearby_masked_n3(tmp_path):
    board = _protected_starter_j1_board()
    assert gerber_truth._footprint_signature(board.by_name["J1"]) == (
        "4ad8b311766fcc32b796d0fe740acd2075f1de9f0e89b5186824fbae88ed690f"
    )
    members = _members(
        **{
            "board-F_Mask.gts": gerber_text(
                apertures={10: "R,0.600024X1.299972"},
                flashes=[
                    (10, OFFSET_X + 3.157147, OFFSET_Y - 9.033001),
                    (10, OFFSET_X + 2.443019, OFFSET_Y - 9.218711),
                ],
            )
        }
    )
    result = gerber_truth.check(board, _zip(tmp_path, members))
    info = [f for f in result.findings if f["kind"] == "gerber_mask_sliver_in_footprint"]
    assert len(info) == 1
    assert "TYPE-C-31-M-12, C165948" in info[0]["detail"]
    assert "gerber_mask_sliver" not in kinds(result)
    assert "gerber_mask_sliver_ownership_unknown" not in kinds(result)
    assert "gerber_mask_sliver_unreviewed_footprint" not in kinds(result)


def test_silk_printed_over_a_pad_is_caught(tmp_path):
    members = _members(
        **{
            "board-F_Silkscreen.gto": gerber_text(
                apertures={10: "C,0.2"},
                draws=[(10, OFFSET_X - 6, OFFSET_Y, OFFSET_X - 4, OFFSET_Y)],
            )
        }
    )
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_silk_over_pad" in kinds(result, "warning")


def test_silk_cleared_by_a_later_negative_pad_flash_is_not_reported(tmp_path):
    """KiCad's --subtract-soldermask output retains the positive text strokes
    and then erases the pad areas with LPC flashes. The composite image, not
    the pre-subtraction stroke list, is what the fab prints."""
    silk = gerber_text(
        apertures={10: "C,0.2", 11: "R,0.7X0.7"},
        draws=[(10, OFFSET_X - 6, OFFSET_Y, OFFSET_X - 4, OFFSET_Y)],
    ).replace(
        "M02*",
        f"%LPC*%\nD11*\nX{_coord(OFFSET_X - 5)}Y{_coord(OFFSET_Y)}D03*\n"
        "%LPD*%\nM02*",
    )
    parsed = gerber.parse_gerber(silk)
    assert parsed.draws[0].polarity == "dark"
    assert parsed.flashes[0].polarity == "clear"
    assert parsed.flashes[0].sequence > parsed.draws[0].sequence

    members = _members(**{"board-F_Silkscreen.gto": silk})
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_silk_over_pad" not in kinds(result, "warning")


def test_an_unreadable_member_is_an_error_not_a_silent_skip(tmp_path):
    members = _members(**{"board-F_Cu.gtl": "%FSLAX46Y46*%\n%MOIN*%\nM02*\n"})
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_unreadable" in kinds(result, "error")


def test_a_missing_zip_is_a_finding_not_a_crash(tmp_path):
    result = gerber_truth.check(_design(), str(tmp_path / "nope.zip"))
    assert "gerber_unreadable" in kinds(result, "error")


def _design_without_holes() -> Board:
    """An all-SMD board: no vias, no mounting holes, nothing to drill."""
    elements = [fixtures.board(BOARD_W, BOARD_H)]
    elements += fixtures.component(
        "R1", index=1, x=0, y=0,
        pads=[(dx, dy, 0.6, 0.6) for dx, dy in PADS],
        courtyard=(11.0, 1.6),
    )
    return Board(elements)


def test_an_all_smd_board_with_an_empty_drill_file_is_fine(tmp_path):
    """A board with nothing to drill should ship an empty drill file.

    The check used to fire its own message back at itself — "the drill file has
    no hits, but the design has 0 holes" — and because the kind blocks, a
    hole-less board could never be fab-ready. Any all-SMD single-layer design
    hit it.
    """
    design = _design_without_holes()
    assert not design.vias and not design.holes, "fixture must have nothing to drill"
    members = _members(**{"board.drl": excellon_text({}, [])})
    result = gerber_truth.check(design, _zip(tmp_path, members))
    assert "gerber_drill_empty" not in kinds(result, "error")


def test_a_board_that_does_have_holes_still_needs_them_drilled(tmp_path):
    """The other side of the line: the exemption must not swallow the real bug."""
    members = _members(**{"board.drl": excellon_text({1: (0.3, True)}, [])})
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_drill_empty" in kinds(result, "error") or "gerber_drill_missing" in kinds(
        result, "error"
    )
