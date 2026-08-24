"""``product.json`` resolution + the safety-envelope pre-flight.

Contract §1: malformed project shape (missing/bad ``product.json``, broken
``parts.json``) is a :class:`~circuitpy.errors.ProjectShapeError`; a safety
refusal is a :class:`~circuitpy.errors.SpecValidationError` raised **at spec
time**, before any toolchain process runs.

The safety envelope (blocking and non-negotiable):

* **no mains, ever** — low-voltage DC ≤ 24 V only;
* **battery power only via the sealed validated charge/protect block** —
  raw charger-IC references outside ``blocks/`` are refused;
* **radio only as certified modules** — bare-die RF is refused.

The scan is a pattern pass over the board's source graph (the same files the
fingerprint folds). It is deliberately conservative and text-level: a comment
discussing mains trips it too — that is the intended bias, matching the
golden-block rule that deterministic checks cannot judge electrical intent.
The blocks are the real safety mechanism; this scan refuses the obvious.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from circuitpy.errors import ProjectShapeError, SpecValidationError

POWER_KINDS = ("usb-c-5v", "battery-lipo-sealed-block", "external-dc-lv")
MAX_DC_VOLTAGE = 24.0

_SCANNABLE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js"}

_MAINS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmains\b", re.I), "mains reference"),
    (re.compile(r"\b\d{2,3}\s*V\s*AC\b", re.I), "AC line voltage"),
    (re.compile(r"\b\d{2,3}\s*VAC\b", re.I), "AC line voltage"),
    (re.compile(r"\bline[\s_-]?voltage\b", re.I), "line-voltage reference"),
    (re.compile(r"\bAC[\s_-]?(?:line|live|neutral)\b"), "AC live/neutral net"),
    (re.compile(r"\b(?:triac|optotriac)\b", re.I), "mains-class component"),
)

_RAW_RF_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bbare[\s_-]?die\b", re.I), "bare-die RF"),
    (
        re.compile(r"\bantenna[\s_-]?(?:trace|matching|tuner)\b", re.I),
        "antenna/matching-network design",
    ),
    (re.compile(r"\bbalun\b", re.I), "RF balun"),
    (re.compile(r"\bRF[\s_-]?front[\s_-]?end\b", re.I), "RF front end"),
)

# Raw battery charge/protect ICs — allowed ONLY inside blocks/ (the sealed
# validated block carries them; hand-rolled charging circuits are refused).
#: The last three entries came from `circuitlib.safety.CHARGER_PATTERNS`, which
#: screens prose and had them while this table — written for source, where a
#: part is named by its part number — did not. Two tables meant to describe one
#: envelope had drifted apart, and the drift only mattered once this table
#: started reading descriptions too.
#:
#: **The cell-format hole, closed 2026-08-24.** "An 18650 charger board" was in
#: neither table and passed. It could not be closed by adding the bare format,
#: because that also refuses "a gauge for an 18650 pack, using the sealed
#: block" — a monitor, which the envelope permits. So the rule names a cell
#: format **and** a charging role, and the role is an *allowlist*:
#: `charger(s)`, or `charge`/`charging`/`recharging` followed by a word that
#: makes the board the thing doing the charging (`circuit`, `dock`,
#: `controller`, …).
#:
#: Allowlist and not a list of monitoring phrases to exclude, deliberately.
#: An exclusion list refuses every phrase nobody thought of, and the phrases
#: nobody thinks of here are monitors — `charge state`, `state of charge`,
#: `charge level`, `charging status`. Built this way they pass **by
#: construction**, and the gate can only refuse wording it names out loud.
#: `rechargeable` is an adjective on the battery, not a role, and does not
#: match. Verified against all 31 product descriptions and 32 board sources in
#: the corpus: nothing that passes today starts failing.
_CELL_FORMAT = (
    r"(?:18650|21700|26650|14500|16340|18350|10440|20700|26800|lifepo4)"
)
_CHARGING_ROLE = (
    r"(?:(?:re)?chargers?\b"
    r"|(?:re)?charg(?:e|ing)[\s-]+"
    r"(?:ic|circuit|controller|board|module|dock|cradle|station|bay|pcb|shield))"
)
#: Either order, within one sentence — `[^.]` stops the pair pairing across a
#: full stop, so "…an 18650. A charger for the lamp" is two asks, not one.
_CELL_CHARGER_RE = (
    rf"\b(?:{_CELL_FORMAT}\b[^.]{{0,40}}?{_CHARGING_ROLE}"
    rf"|{_CHARGING_ROLE}[^.]{{0,40}}?\b{_CELL_FORMAT}\b)"
)

_RAW_BATTERY_IC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bTP4056\b", re.I), "raw charger IC TP4056"),
    (re.compile(r"\bTP5100\b", re.I), "raw charger IC TP5100"),
    (re.compile(r"\bBQ2[45]\d{2}\b", re.I), "raw charger IC (BQ24xx/BQ25xx)"),
    (re.compile(r"\bMCP7383\d\b", re.I), "raw charger IC (MCP7383x)"),
    (re.compile(r"\bDW01\b", re.I), "raw protection IC DW01"),
    (re.compile(r"\bFS8205\b", re.I), "raw protection FET FS8205"),
    (re.compile(r"\bli-?(?:po|ion)\s+charg", re.I), "lithium charging"),
    (re.compile(_CELL_CHARGER_RE, re.I),
     "lithium cell charging (a cell format named with a charging role)"),
)

# A quoted voltage prop ("48V") or voltage={48} above the DC ceiling.
_VOLTAGE_LITERAL_RE = re.compile(r"""['"](\d+(?:\.\d+)?)\s*V['"]""")
_VOLTAGE_PROP_RE = re.compile(r"""voltage\s*=\s*\{?\s*(\d+(?:\.\d+)?)\s*\}?""")


@dataclass(frozen=True)
class ResolvedProduct:
    name: str
    description: str
    power: str
    envelope_mm: tuple[float, float] | None
    layers: int
    fab: str
    assembly: bool
    path: Path


def load_product(project_root: Path) -> ResolvedProduct:
    """Load + validate ``<project>/product.json`` (the bible)."""
    path = project_root / "product.json"
    if not path.is_file():
        raise ProjectShapeError(
            f"no product.json at {project_root} — a circuit project root must "
            "contain the product definition"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectShapeError(f"product.json unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectShapeError(
            f"product.json must be a JSON object (got {type(raw).__name__})"
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProjectShapeError("product.json must define a non-empty 'name'")
    power = raw.get("power")
    if power not in POWER_KINDS:
        raise ProjectShapeError(
            f"product.json 'power' must be one of {', '.join(POWER_KINDS)} "
            f"(got {power!r})"
        )
    envelope_raw = raw.get("envelopeMm")
    envelope: tuple[float, float] | None = None
    if envelope_raw is not None:
        if (
            not isinstance(envelope_raw, (list, tuple))
            or len(envelope_raw) != 2
            or not all(isinstance(v, (int, float)) and v > 0 for v in envelope_raw)
        ):
            raise ProjectShapeError(
                f"product.json 'envelopeMm' must be [width, height] in mm "
                f"(got {envelope_raw!r})"
            )
        envelope = (float(envelope_raw[0]), float(envelope_raw[1]))
    layers_raw = raw.get("layers", 2)
    if not isinstance(layers_raw, int) or layers_raw < 1:
        raise ProjectShapeError(
            f"product.json 'layers' must be a positive integer (got {layers_raw!r})"
        )
    return ResolvedProduct(
        name=name.strip(),
        description=str(raw.get("description") or ""),
        power=str(power),
        envelope_mm=envelope,
        layers=layers_raw,
        fab=str(raw.get("fab") or "jlcpcb"),
        assembly=bool(raw.get("assembly", False)),
        path=path,
    )


def load_parts(project_root: Path) -> dict[str, dict]:
    """Load ``<project>/parts.json`` (the locked BOM identities, owned wholly
    by parts-book). Absent → ``{}``; present-but-broken → ProjectShapeError
    (a silent empty lock would skip part_drift silently)."""
    path = project_root / "parts.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectShapeError(f"parts.json unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectShapeError(
            f"parts.json must be a JSON object (got {type(raw).__name__})"
        )
    # Two shapes, because two tracks wrote to this file with different ideas
    # about it and only one of them was ever read.
    #
    # `parts-book` writes what its contract prints — a wrapper carrying
    # `version`/`generator`/`updated`/`summary` and a **`parts` array**, each
    # entry keyed by its own `id` (skills/parts-book/SKILL.md, "The record
    # written per part"). This loader wanted a flat `{id: entry}` map, so on
    # every board that has a lock it collected the one dict-valued key it
    # recognised — `summary` — and returned a lock of size 1. Measured
    # 2026-08-16 on hydrate-coaster, the only board that pins its parts:
    # `load_parts` returned `{"summary": {...}}` for 19 locked parts.
    #
    # Nothing raised. The BOM's Footprint column went out blank for every part
    # the local catalog mirror does not carry (9 of 18 lines on harness-puck),
    # `part_drift` compared against nothing, and the cost estimate had no
    # prices to add up. A lock that is silently empty is worse than no lock:
    # the checks that exist to catch a part changing under you all pass.
    #
    # Both shapes are read now, and the flat one stays supported because it is
    # what the tests and any hand-written lock use.
    listed = raw.get("parts")
    parts: dict[str, dict] = {}
    if isinstance(listed, list):
        for entry in listed:
            if not isinstance(entry, dict):
                continue
            part_id = str(entry.get("id") or entry.get("lcsc") or "").strip()
            if part_id:
                parts[part_id] = entry
        return parts
    for part_id, entry in raw.items():
        if isinstance(entry, dict):
            parts[str(part_id)] = entry
    return parts


#: A negator sitting immediately before a match exonerates it. This applies to
#: the description scan and **not** to the file scan, because the two are
#: different languages: prose says "USB only, no mains anywhere" all the time
#: and TSX almost never does.
#:
#: Deliberately narrow — adjacency only. The asymmetry decides it: a false
#: refusal is an annoyance, a false pass is a fire. "no mains" is exonerated
#: and "switches mains, no problem" is not, even though that leaves "never
#: touches mains" refused. Allowing intervening words would also exonerate
#: "no problem, switches mains", which is the sentence this exists to catch.
_NEGATOR_RE = re.compile(
    r"\b(?:no|not|never|without|avoid|avoids|excludes?|free\s+of|zero)"
    r"\b[\s,\-]*$",
    re.I,
)


def _negated(text: str, start: int) -> bool:
    return _NEGATOR_RE.search(text[max(0, start - 24):start]) is not None


def screen_description(product: ResolvedProduct) -> None:
    """Screen the **ask**, not only the source it turned into.

    `preflight_safety` scans the board's source graph, and that is the half
    that was wired in. The other half was not: `product.json`'s description is
    the natural-language request the whole project was built from, and nothing
    read it. A dangerous intent that compiles to innocent-looking source walked
    straight through — "a smart switch for a 230V lamp" is a spec violation
    before a single line of TSX exists, and it is the sentence a person
    actually writes.

    Same patterns as the file scan, so the envelope cannot say two different
    things depending on which half caught you, and a refusal reads the same way
    either way.
    """
    text = (product.description or "").strip()
    if not text:
        return
    for patterns, reason in (
        (_MAINS_PATTERNS, "no mains, ever (\u226424V DC only)"),
        (_RAW_RF_PATTERNS,
         "radio only as certified modules, never bare-die RF"),
        (_RAW_BATTERY_IC_PATTERNS,
         "battery power only via the sealed validated charge/protect block"),
    ):
        for pattern, label in patterns:
            for match in pattern.finditer(text):
                if _negated(text, match.start()):
                    continue
                _refuse("product.json", text, match, f"{label} — {reason}")


def preflight_safety(
    source_files: Iterable[Path], project_root: Path, product: ResolvedProduct
) -> None:
    """Scan the board's source graph; raise SpecValidationError on the first
    envelope violation. ``source_files`` is the fingerprint's file set —
    entry + local imports + blocks. The product's own description is screened
    first, because the ask can break the envelope before any source does."""
    screen_description(product)
    root = project_root.resolve()
    for file_path in source_files:
        path = Path(file_path)
        if path.suffix not in _SCANNABLE_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        in_blocks = rel.startswith("blocks/")

        for pattern, label in _MAINS_PATTERNS:
            match = pattern.search(text)
            if match:
                _refuse(rel, text, match, f"{label} — no mains, ever (≤24V DC only)")
        for pattern, label in _RAW_RF_PATTERNS:
            match = pattern.search(text)
            if match:
                _refuse(
                    rel,
                    text,
                    match,
                    f"{label} — radio only as certified modules, never bare-die RF",
                )
        if not in_blocks:
            for pattern, label in _RAW_BATTERY_IC_PATTERNS:
                match = pattern.search(text)
                if match:
                    _refuse(
                        rel,
                        text,
                        match,
                        f"{label} — battery power only via the sealed validated "
                        "charge/protect block",
                    )
        for pattern in (_VOLTAGE_LITERAL_RE, _VOLTAGE_PROP_RE):
            for match in pattern.finditer(text):
                try:
                    volts = float(match.group(1))
                except ValueError:
                    continue
                if volts > MAX_DC_VOLTAGE:
                    _refuse(
                        rel,
                        text,
                        match,
                        f"{volts:g}V exceeds the {MAX_DC_VOLTAGE:g}V DC ceiling",
                    )


def _refuse(rel_path: str, text: str, match: re.Match[str], reason: str) -> None:
    line = text.count("\n", 0, match.start()) + 1
    raise SpecValidationError(
        f"safety_envelope: {reason} ({rel_path}:{line}: {match.group(0)!r})"
    )
