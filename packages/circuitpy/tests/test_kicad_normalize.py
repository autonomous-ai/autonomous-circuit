"""Stage 3a: holding the converted board to the fab's silkscreen floors.

This is a platform fix, not a check: it runs on every board, so it has to be
exactly right and it has to be idempotent. The one thing worse than silkscreen
that will not print is a rewriter that corrupts the board — an earlier draft
matched across a `(stroke (width N))` wrapper, ate its closing paren, and
produced a file kicad-cli refused to load at all. That case is pinned below.
"""

from __future__ import annotations

from pathlib import Path

from circuitpy.fab import get_profile
from circuitpy.kicad_normalize import normalize_for_fab

PROFILE = get_profile("jlcpcb")


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "board.kicad_pcb"
    path.write_text(f'(kicad_pcb\n  (setup\n  )\n{body}\n)\n', encoding="utf-8")
    return path


SMALL_TEXT = """  (footprint "R_0402"
    (fp_text
      reference
      "R1"
      (at 0 -1.22 0)
      (layer F.SilkS)
      (effects
        (font
          (size 0.26666666666666666 0.26666666666666666)
        )
      )
    )
  )"""

THIN_MODERN_STROKE = """  (fp_line
    (start -4.4 1.25)
    (end -4.4 -0.61)
    (stroke
      (width 0.1)
      (type default)
    )
    (layer F.SilkS)
  )"""

THIN_LEGACY_STROKE = """  (gr_line
    (start 0 0)
    (end 1 0)
    (width 0.05)
    (layer "F.Silkscreen")
  )"""

COPPER_LINE = """  (gr_line
    (start 0 0)
    (end 1 0)
    (width 0.05)
    (layer "F.Cu")
  )"""

MASKED_NODE = """  (footprint
    "tscircuit:chip"
    (layer F.Cu)
    (property "Reference" "N1"
      (at 0 -3 0)
      (layer F.SilkS)
      (hide yes)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "MASKED_COPPER_NODE"
      (at 0 3 0)
      (layer F.Fab)
      (hide yes)
      (effects (font (size 1.27 1.27)))
    )
    (pad "1" smd circle
      (at 0 0 0)
      (size 0.25 0.25)
      (layers F.Cu F.Paste F.Mask)
      (net 1 "SWCLK")
    )
  )"""

CLIPPED_REFERENCE = """  (footprint "fixture:R_0402"
    (layer F.Cu)
    (at 10 10)
    (fp_text reference "R123"
      (at 0 0)
      (layer F.SilkS)
      (effects (font (size 1 1) (thickness 0.15)))
    )
    (pad "1" smd rect
      (at 0.9 0)
      (size 1.2 1.4)
      (layers F.Cu F.Paste F.Mask)
    )
  )
  (gr_rect
    (start 5 5)
    (end 15 15)
    (stroke (width 0.1) (type default))
    (fill none)
    (layer Edge.Cuts)
  )"""


def _balanced(text: str) -> bool:
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def test_text_below_the_floor_is_raised_to_it(tmp_path):
    path = _write(tmp_path, SMALL_TEXT)
    result = normalize_for_fab(path, PROFILE)
    assert result.text_resized == 1
    text = path.read_text()
    assert "(size 1 1)" in text
    assert result.smallest_text_mm is not None
    assert round(result.smallest_text_mm, 3) == 0.267


def test_text_with_no_thickness_gets_one_at_the_floor(tmp_path):
    """KiCad derives thickness from size when it is absent, and lands at
    0.033mm — a fifth of what JLCPCB will print."""
    path = _write(tmp_path, SMALL_TEXT)
    result = normalize_for_fab(path, PROFILE)
    assert result.text_thickened == 1
    assert "(thickness 0.15)" in path.read_text()


def test_a_modern_stroke_wrapper_survives_being_widened(tmp_path):
    """The regression: rewriting across `(stroke (width N))` ate the closing
    paren and produced a board kicad-cli could not load."""
    path = _write(tmp_path, THIN_MODERN_STROKE)
    result = normalize_for_fab(path, PROFILE)
    text = path.read_text()
    assert result.strokes_widened == 1
    assert "(width 0.15)" in text
    assert "(type default)" in text
    assert _balanced(text), "the rewritten s-expression must still balance"


def test_a_legacy_bare_width_is_widened_too(tmp_path):
    path = _write(tmp_path, THIN_LEGACY_STROKE)
    assert normalize_for_fab(path, PROFILE).strokes_widened == 1
    assert "(width 0.15)" in path.read_text()


def test_nothing_outside_the_silkscreen_layers_is_touched(tmp_path):
    path = _write(tmp_path, COPPER_LINE)
    before = path.read_text()
    result = normalize_for_fab(path, PROFILE)
    assert not result.changed
    assert path.read_text() == before


def test_masked_copper_node_stays_covered_and_out_of_assembly(tmp_path):
    """The converter loses all three source semantics; the shared boundary
    restores them by component identity, never by one board's coordinate."""
    path = _write(tmp_path, MASKED_NODE)
    result = normalize_for_fab(path, PROFILE)
    text = path.read_text()
    assert result.hidden_nodes_normalized == 1
    assert result.hidden_node_pads_covered == 1
    assert result.hidden_node_assembly_exclusions == 1
    assert "(layers F.Cu)" in text
    assert "F.Paste" not in text
    assert "F.Mask" not in text
    assert "(attr smd exclude_from_pos_files exclude_from_bom)" in text
    assert '(property "Reference" "N1"' in text
    assert "(hide yes)" in text
    assert _balanced(text)


def test_bottom_masked_node_keeps_only_bottom_copper(tmp_path):
    path = _write(
        tmp_path,
        MASKED_NODE.replace("(layer F.Cu)", "(layer B.Cu)", 1)
        .replace("(layers F.Cu F.Paste F.Mask)", "(layers B.Cu B.Paste B.Mask)"),
    )
    normalize_for_fab(path, PROFILE)
    text = path.read_text()
    assert "(layers B.Cu)" in text
    assert "B.Paste" not in text
    assert "B.Mask" not in text


def test_an_n_prefixed_real_component_without_the_marker_is_not_rewritten(tmp_path):
    path = _write(tmp_path, MASKED_NODE.replace("MASKED_COPPER_NODE", "REAL_PART"))
    before = path.read_text()
    result = normalize_for_fab(path, PROFILE)
    assert result.hidden_nodes_normalized == 0
    assert path.read_text() == before


def test_malformed_hidden_node_with_multiple_attrs_is_left_whole(tmp_path):
    malformed = MASKED_NODE.replace(
        '    (pad "1" smd circle',
        "    (attr smd)\n    (attr through_hole)\n    (pad \"1\" smd circle",
    )
    path = _write(tmp_path, malformed)
    before = path.read_text()
    result = normalize_for_fab(path, PROFILE)
    assert result.hidden_nodes_normalized == 0
    assert result.hidden_node_pads_covered == 0
    assert result.hidden_node_assembly_exclusions == 0
    assert result.notes and "multiple KiCad attr blocks" in result.notes[0]
    assert path.read_text() == before


def test_it_is_idempotent(tmp_path):
    path = _write(tmp_path, SMALL_TEXT + "\n" + THIN_MODERN_STROKE)
    first = normalize_for_fab(path, PROFILE)
    after_first = path.read_text()
    second = normalize_for_fab(path, PROFILE)
    assert first.changed and not second.changed
    assert path.read_text() == after_first


def test_text_already_at_the_floor_is_left_alone(tmp_path):
    body = SMALL_TEXT.replace(
        "(size 0.26666666666666666 0.26666666666666666)", "(size 1.2 1.2)"
    ).replace("(font", "(font\n          (thickness 0.2)")
    path = _write(tmp_path, body)
    result = normalize_for_fab(path, PROFILE)
    assert result.text_resized == 0
    assert result.text_thickened == 0


def test_the_solder_mask_setting_is_not_written(tmp_path):
    """Measured and rejected: `solder_mask_min_width` removes no web. It turns
    174 flashed apertures into 223 filled regions, so the same geometry then
    reads as 92 sub-0.2mm gaps instead of 10, and the gerber check runs eight
    times slower. A change that alters the representation and not the board is
    a placebo."""
    path = _write(tmp_path, SMALL_TEXT)
    normalize_for_fab(path, PROFILE)
    assert "solder_mask_min_width" not in path.read_text()


def test_a_missing_file_is_a_note_not_an_exception(tmp_path):
    result = normalize_for_fab(tmp_path / "nope.kicad_pcb", PROFILE)
    assert not result.changed
    assert result.notes and "could not read" in result.notes[0]


def test_a_corrupt_board_is_left_exactly_as_it_was(tmp_path):
    path = tmp_path / "board.kicad_pcb"
    path.write_text("(kicad_pcb (fp_text (layer F.SilkS) (effects (font (size",
                    encoding="utf-8")
    before = path.read_text()
    normalize_for_fab(path, PROFILE)
    assert path.read_text() == before


def test_reference_is_relocated_before_mask_subtraction_can_clip_it(tmp_path):
    """The measured defect: R123 lost 29.9% of its strokes even though the
    subtracted Gerber was solder-safe.  The complete label moves instead."""
    path = _write(tmp_path, CLIPPED_REFERENCE)
    result = normalize_for_fab(path, PROFILE)
    text = path.read_text()
    assert result.references_relocated == 1
    assert result.unreadable_references == []
    assert '(fp_text reference "R123"\n      (at 0 0)' not in text
    assert "(size 1 1)" in text
    assert "(thickness 0.15)" in text

    after_first = text
    second = normalize_for_fab(path, PROFILE)
    assert second.references_relocated == 0
    assert second.unreadable_references == []
    assert path.read_text() == after_first


def test_impossible_populated_reference_fails_closed_without_shrinking(tmp_path):
    body = CLIPPED_REFERENCE.replace("(start 5 5)", "(start 9 9)").replace(
        "(end 15 15)", "(end 11 11)"
    )
    path = _write(tmp_path, body)
    before = path.read_text()
    result = normalize_for_fab(path, PROFILE)
    assert result.references_relocated == 0
    assert result.unreadable_references == ["R123"]
    assert result.unreadable_findings(PROFILE) == [
        {
            "part": "R123",
            "kind": "silkscreen_refdes_unreadable",
            "detail": (
                "R123 cannot fit a complete 1mm reference designator inside "
                "the board and clear of same-face solder-mask openings; mask "
                "subtraction would leave a clipped/partial label"
            ),
            "severity": "error",
        }
    ]
    assert "(size 1 1)" in path.read_text()
    assert path.read_text() == before


def test_bottom_rotated_reference_uses_the_same_deterministic_gate(tmp_path):
    body = (
        CLIPPED_REFERENCE.replace("(layer F.Cu)", "(layer B.Cu)", 1)
        .replace("(at 10 10)", "(at 10 10 90)", 1)
        .replace("(layer F.SilkS)", "(layer B.SilkS)", 1)
        .replace("(layers F.Cu F.Paste F.Mask)", "(layers B.Cu B.Paste B.Mask)")
    )
    path = _write(tmp_path, body)
    first = normalize_for_fab(path, PROFILE)
    assert first.references_relocated == 1
    assert first.unreadable_references == []
    after_first = path.read_text()
    second = normalize_for_fab(path, PROFILE)
    assert second.references_relocated == 0
    assert path.read_text() == after_first


def test_explicit_board_graphic_text_is_not_repositioned(tmp_path):
    custom = CLIPPED_REFERENCE.replace(
        '(fp_text reference "R123"\n      (at 0 0)\n      (layer F.SilkS)\n'
        '      (effects (font (size 1 1) (thickness 0.15)))\n    )',
        '(gr_text "CUSTOM" (at 10 10) (layer F.SilkS) '
        '(effects (font (size 1 1) (thickness 0.15))))',
    )
    path = _write(tmp_path, custom)
    normalize_for_fab(path, PROFILE)
    assert '(gr_text "CUSTOM" (at 10 10)' in path.read_text()


def test_explicitly_hidden_reference_is_a_machine_readable_omission(tmp_path):
    hidden = CLIPPED_REFERENCE.replace(
        "(layer F.SilkS)", "(layer F.SilkS)\n      hide", 1
    )
    path = _write(tmp_path, hidden)
    result = normalize_for_fab(path, PROFILE)
    assert result.references_relocated == 0
    assert result.unreadable_references == []
    assert '(fp_text reference "R123"\n      (at 0 0)' in path.read_text()
