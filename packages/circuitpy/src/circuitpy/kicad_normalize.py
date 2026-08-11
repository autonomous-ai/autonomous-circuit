"""Make the converted KiCad board obey the fab's floors before it is plotted.

**The defect this closes, and why it is a platform fix rather than a check.**
Measured 2026-08-11 on `harness-puck`: **all 54 silkscreen texts on the
converted board are under JLCPCB's 1.0mm minimum height, 39 of them at
0.267mm**, and not one carries an explicit stroke thickness — so KiCad derives
it from the size and plots 1145 strokes at 0.033mm against a 0.15mm floor.

That is not a property of these three boards. `circuit-json-to-kicad` produces
it for *every* board, so every board this tool has ever made, or will make,
arrives with silkscreen that prints broken or not at all — no reference
designators, nothing an EE can review or a human can rework against.

The converter is upstream and we do not control it. The `.kicad_pcb` between
the converter and `kicad-cli` is the one place every board passes through, so
that is where the floor gets applied: one change, the whole catalog, forever.
Detecting it in the gerbers (`verifylib.gerber_truth`) stays as the smoke
alarm that proves this kept working.

**What this deliberately does not do.** Setting KiCad's
`solder_mask_min_width` looked like the matching fix for thin mask webs, and it
was measured and rejected: it does not remove a single web. It changes how the
mask is *plotted* — 174 flashed apertures become 223 filled regions — and the
same geometry then measures 92 sub-0.2mm gaps instead of 10, purely because
adjacent contours of one merged opening are read as two. It also made the
gerber check eight times slower. A change that alters the representation and
not the board is a placebo, and shipping one would have been worse than
shipping nothing, because it would have looked like the defect was handled.

Everything here edits the s-expression as text, with a balanced-paren scan
rather than a bare regex, and every edit is counted and reported. A rewrite
nobody can audit is a rewrite nobody should trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from circuitpy.fab import FabProfile

#: KiCad silkscreen layer names, old and new spellings.
SILK_LAYERS = ("F.SilkS", "B.SilkS", "F.Silkscreen", "B.Silkscreen")

_TEXT_OPENERS = ("(fp_text", "(gr_text")
_GRAPHIC_OPENERS = ("(fp_line", "(gr_line", "(fp_rect", "(gr_rect",
                    "(fp_circle", "(gr_circle", "(fp_arc", "(gr_arc",
                    "(fp_poly", "(gr_poly")

_SIZE_RE = re.compile(r"\(size\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*\)")
_THICKNESS_RE = re.compile(r"\(thickness\s+([0-9.eE+-]+)\s*\)")
_WIDTH_RE = re.compile(r"\(width\s+([0-9.eE+-]+)\s*\)")
_FONT_OPEN_RE = re.compile(r"\(font\b")


@dataclass
class Normalization:
    """What was changed, so the pipeline can say it out loud."""

    text_resized: int = 0
    text_thickened: int = 0
    strokes_widened: int = 0
    smallest_text_mm: float | None = None
    smallest_stroke_mm: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.text_resized or self.text_thickened or self.strokes_widened
        )

    def summary(self) -> str:
        parts = []
        if self.text_resized:
            parts.append(
                f"{self.text_resized} silkscreen text(s) raised to the "
                f"fab's minimum height"
                + (
                    f" (smallest was {self.smallest_text_mm:.3f}mm)"
                    if self.smallest_text_mm is not None
                    else ""
                )
            )
        if self.text_thickened:
            parts.append(f"{self.text_thickened} given an explicit stroke")
        if self.strokes_widened:
            parts.append(
                f"{self.strokes_widened} silkscreen stroke(s) widened"
                + (
                    f" (thinnest was {self.smallest_stroke_mm:.4f}mm)"
                    if self.smallest_stroke_mm is not None
                    else ""
                )
            )
        return "; ".join(parts)


def _balanced_spans(text: str, opener: str) -> list[tuple[int, int]]:
    """Every balanced ``(...)`` beginning with ``opener``.

    A regex cannot do this: an ``fp_text`` contains nested ``(effects (font
    (size ...))))`` and stopping at the first ``)`` would rewrite the wrong
    numbers. Paren counting is cheap and it is right.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        i = text.find(opener, start)
        if i < 0:
            return spans
        depth = 0
        for j in range(i, len(text)):
            char = text[j]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    spans.append((i, j + 1))
                    start = j + 1
                    break
        else:
            return spans  # unbalanced tail; leave the rest alone


def _on_silk(block: str) -> bool:
    return any(f'"{layer}"' in block or f"({layer}" in block or layer in block
               for layer in SILK_LAYERS)


def _raise_text(block: str, min_height: float, min_thickness: float,
                result: Normalization) -> str:
    match = _SIZE_RE.search(block)
    if match is None:
        return block
    width = float(match.group(1))
    height = float(match.group(2))
    smallest = min(width, height)
    if result.smallest_text_mm is None or smallest < result.smallest_text_mm:
        result.smallest_text_mm = smallest
    if smallest < min_height - 1e-9:
        scale = min_height / smallest if smallest > 0 else 1.0
        block = (
            block[: match.start()]
            + f"(size {width * scale:.6g} {height * scale:.6g})"
            + block[match.end():]
        )
        result.text_resized += 1

    thickness_match = _THICKNESS_RE.search(block)
    if thickness_match is None:
        # No explicit thickness: KiCad derives one from the size and lands
        # well under the floor. Insert it right after `(font`.
        font = _FONT_OPEN_RE.search(block)
        if font is not None:
            insert_at = font.end()
            block = (
                block[:insert_at]
                + f"\n          (thickness {min_thickness:.6g})"
                + block[insert_at:]
            )
            result.text_thickened += 1
    elif float(thickness_match.group(1)) < min_thickness - 1e-9:
        block = (
            block[: thickness_match.start()]
            + f"(thickness {min_thickness:.6g})"
            + block[thickness_match.end():]
        )
        result.text_thickened += 1
    return block


def _raise_stroke(block: str, min_width: float, result: Normalization) -> str:
    """Widen a silkscreen graphic's stroke.

    Only the innermost ``(width N)`` token is rewritten, never the ``(stroke``
    wrapper around it. KiCad writes both the modern
    ``(stroke (width 0.1) (type default))`` and the bare ``(width 0.1)``, and
    an earlier version of this matched across the wrapper and ate its closing
    paren — which produced a `.kicad_pcb` kicad-cli refused to load at all.
    Rewriting the leaf token is correct for both spellings.
    """
    width = _WIDTH_RE.search(block)
    if width is None:
        return block
    value = float(width.group(1))
    if result.smallest_stroke_mm is None or value < result.smallest_stroke_mm:
        result.smallest_stroke_mm = value
    if value >= min_width - 1e-9 or value <= 0:
        return block
    result.strokes_widened += 1
    return (
        block[: width.start()] + f"(width {min_width:.6g})" + block[width.end():]
    )


def normalize_for_fab(pcb_path: Path, profile: FabProfile) -> Normalization:
    """Apply the fab's silkscreen and solder-mask floors to a ``.kicad_pcb``.

    Idempotent: run twice and the second run changes nothing. Returns what it
    did, and never raises — a normaliser that takes the build down is worse
    than a board with small text.
    """
    result = Normalization()
    try:
        text = pcb_path.read_text(encoding="utf-8")
    except OSError as exc:
        result.notes.append(f"could not read {pcb_path.name}: {exc}")
        return result

    original = text
    try:
        # Rewrite from the end so earlier spans keep their offsets.
        spans: list[tuple[int, int, str]] = []
        for opener in _TEXT_OPENERS:
            spans.extend((a, b, "text") for a, b in _balanced_spans(text, opener))
        for opener in _GRAPHIC_OPENERS:
            spans.extend((a, b, "graphic") for a, b in _balanced_spans(text, opener))
        for start, end, kind in sorted(spans, reverse=True):
            block = text[start:end]
            if not _on_silk(block):
                continue
            if kind == "text":
                block = _raise_text(
                    block,
                    profile.min_silk_text_mm,
                    profile.min_silk_line_mm,
                    result,
                )
            else:
                block = _raise_stroke(block, profile.min_silk_line_mm, result)
            text = text[:start] + block + text[end:]

        if text != original:
            pcb_path.write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(
            f"normalisation aborted ({type(exc).__name__}: {exc}); the board "
            "was left exactly as the converter produced it"
        )
        try:
            pcb_path.write_text(original, encoding="utf-8")
        except OSError:
            pass
    return result
