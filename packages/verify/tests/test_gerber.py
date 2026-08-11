"""The gerber reader and the packet-vs-design reconciliation.

Two kinds of test here. The parser tests build gerber text by hand so the
expected geometry is known exactly. The reconciliation tests build a *correct*
packet, prove it is clean, then mutate one thing — delete a layer, scale the
coordinates, drop a drill, cover a pad in mask — and require the check to
catch it. A gate that has never been shown failing is a gate nobody should
trust.
"""

from __future__ import annotations

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
        lines.append("G36*")
        first = True
        for x, y in contour:
            op = "D02" if first else "D01"
            lines.append(f"X{_coord(x)}Y{_coord(y)}{op}*")
            first = False
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
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_mask_sliver" in kinds(result, "warning")


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


def test_an_unreadable_member_is_an_error_not_a_silent_skip(tmp_path):
    members = _members(**{"board-F_Cu.gtl": "%FSLAX46Y46*%\n%MOIN*%\nM02*\n"})
    result = gerber_truth.check(_design(), _zip(tmp_path, members))
    assert "gerber_unreadable" in kinds(result, "error")


def test_a_missing_zip_is_a_finding_not_a_crash(tmp_path):
    result = gerber_truth.check(_design(), str(tmp_path / "nope.zip"))
    assert "gerber_unreadable" in kinds(result, "error")
