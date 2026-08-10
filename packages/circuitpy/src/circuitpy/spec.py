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
_RAW_BATTERY_IC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bTP4056\b", re.I), "raw charger IC TP4056"),
    (re.compile(r"\bTP5100\b", re.I), "raw charger IC TP5100"),
    (re.compile(r"\bBQ2[45]\d{2}\b", re.I), "raw charger IC (BQ24xx/BQ25xx)"),
    (re.compile(r"\bMCP7383\d\b", re.I), "raw charger IC (MCP7383x)"),
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
            f"parts.json must be a JSON object of part id -> entry "
            f"(got {type(raw).__name__})"
        )
    parts: dict[str, dict] = {}
    for part_id, entry in raw.items():
        if isinstance(entry, dict):
            parts[str(part_id)] = entry
    return parts


def preflight_safety(
    source_files: Iterable[Path], project_root: Path, product: ResolvedProduct
) -> None:
    """Scan the board's source graph; raise SpecValidationError on the first
    envelope violation. ``source_files`` is the fingerprint's file set —
    entry + local imports + blocks."""
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
