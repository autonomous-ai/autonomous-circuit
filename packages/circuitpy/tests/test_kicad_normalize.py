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
