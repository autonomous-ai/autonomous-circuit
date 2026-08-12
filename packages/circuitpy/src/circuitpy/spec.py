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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, cast

from circuitpy.errors import ProjectShapeError, SpecValidationError
from circuitpy.layout_intent import validate_layout
from circuitpy.power_intent import validate_power_budget

POWER_KINDS = ("usb-c-5v", "battery-lipo-sealed-block", "external-dc-lv")
ASSEMBLY_TIERS = ("economic", "standard")
AssemblyTier = Literal["economic", "standard"]
DESIGN_PROFILES = ("protected-usb-indicator-v1",)
PROTECTED_USB_INDICATOR_PLANNER_BLOCKS = (
    "ldo-3v3",
    "status-led",
    "usb-c-data",
    "usb-power-entry",
)
PROTECTED_USB_INDICATOR_BLOCKS = tuple(
    sorted((*PROTECTED_USB_INDICATOR_PLANNER_BLOCKS, "usb-c-power"))
)
PROTECTED_USB_INDICATOR_BOARD_MM = (46.9, 36.8)
PROTECTED_USB_INDICATOR_SOURCE_SHA256 = (
    "6da57f1055e2dd2d48cb0e5801dc9b25fa6dcac37e1441aeeea32623eb1dfbc9"
)
PROTECTED_USB_INDICATOR_PARTS = {
    "C1": ("C52923", True, "usb-c-data"),
    "C2": ("C19702", True, "ldo-3v3"),
    "C24": ("C1525", True, "usb-power-entry"),
    "C3": ("C19702", True, "ldo-3v3"),
    "J1": ("C165948", False, "usb-c-data"),
    "LED1": ("C2297", True, "status-led"),
    "R1": ("C25905", True, "usb-c-data"),
    "R2": ("C25905", True, "usb-c-data"),
    "R20": ("C11702", True, "status-led"),
    "R31": ("C32297", False, "usb-power-entry"),
    "R32": ("C25741", True, "usb-power-entry"),
    "R3": ("C25100", True, "usb-c-data"),
    "R4": ("C25100", True, "usb-c-data"),
    "U1": ("C2687116", False, "usb-c-data"),
    "U2": ("C500795", False, "ldo-3v3"),
    "U7": ("C55266", False, "usb-power-entry"),
}
_PROTECTED_PART_REQUIRED_FIELDS = {"lcsc", "basic", "description", "block"}
_PROTECTED_PART_OPTIONAL_FIELDS = {
    "mfr",
    "package",
    "stock",
    "unit_price_usd",
    "stock_checked",
    "datasheet_url",
    "source",
    "preferred",
    "override",
    "footprint_risk",
    "swapped_from",
}
MAX_DC_VOLTAGE = 24.0

_SCANNABLE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js"}

# Generated and hand-authored v1 locks are keyed by one concrete component
# reference, never by a part family, range, grouped selector, or metadata
# record. Keep this identical to circuitlib's fixed-ref grammar: refs such as
# R1, LED1, and RDM are valid; `R1/R2`, `C4-C11`, `summary`, and lowercase
# aliases are not. The later BOM gate can therefore compare exact identities
# without expanding a user-authored mini-language or silently dropping rows.
_EXACT_PART_REF_RE = re.compile(r"^[A-Z][A-Z0-9]*$")

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
    assembly_tier: AssemblyTier = "economic"
    layout: dict[str, Any] = field(default_factory=dict)
    power_budget: dict[str, Any] = field(default_factory=dict)
    design_profile: str | None = None
    design_profile_source_sha256: str | None = None
    schematic_policy: dict[str, Any] = field(default_factory=dict)


def _read_parts_lock(project_root: Path) -> dict[str, dict]:
    """Read the optional exact-ref parts lock without weakening bad JSON."""

    path = project_root / "parts.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectShapeError(f"parts.json unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectShapeError(
            f"parts.json must be a JSON object of exact ref -> entry "
            f"(got {type(raw).__name__})"
        )
    if isinstance(raw.get("parts"), list):
        raise ProjectShapeError(
            "parts.json uses the legacy {version, parts:[...]} shape; "
            "regenerate it as one exact uppercase component ref -> entry object"
        )
    parts: dict[str, dict] = {}
    for part_id, entry in raw.items():
        if not part_id:
            raise ProjectShapeError("parts.json contains an empty component ref")
        if not _EXACT_PART_REF_RE.fullmatch(part_id):
            raise ProjectShapeError(
                f"parts.json key {part_id!r} is not one exact uppercase component ref"
            )
        if not isinstance(entry, dict):
            raise ProjectShapeError(
                f"parts.json entry {part_id} must be an object "
                f"(got {type(entry).__name__})"
            )
        parts[part_id] = entry
    return parts


def _validate_schematic_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires product.json "
            "'schematicPolicy' to be an object"
        )
    expected = {"placement": "explicit", "flow": "left-to-right"}
    if value != expected:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 schematicPolicy must be exactly "
            "{'placement': 'explicit', 'flow': 'left-to-right'}"
        )
    return dict(value)


def _profile_net_classes(layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = layout.get("netClasses")
    if not isinstance(raw, list):
        return {}
    return {
        str(rule.get("name")): rule
        for rule in raw
        if isinstance(rule, dict) and isinstance(rule.get("name"), str)
    }


def _require_protected_usb_indicator_contracts(
    *,
    envelope_mm: tuple[float, float] | None,
    layers: int,
    fab: str,
    assembly: bool,
    assembly_tier: str,
    source_sha256: Any,
    raw_layout: Any,
    layout: dict[str, Any],
    raw_power_budget: Any,
    power_budget: dict[str, Any],
    schematic_policy: Any,
    parts_lock: dict[str, dict],
) -> dict[str, Any]:
    """Fail the safe starter profile before any toolchain process.

    Legacy products may omit intent.  Selecting this profile is different: it
    means the public generator promised a protected source, exact current
    budget, two-face ground policy, typed rail geometry and a deliberate
    schematic.  A missing member cannot silently degrade that promise.
    """

    if source_sha256 != PROTECTED_USB_INDICATOR_SOURCE_SHA256:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires the exact public generator "
            "designProfileSourceSha256"
        )
    if (
        envelope_mm != PROTECTED_USB_INDICATOR_BOARD_MM
        or layers != 2
        or fab != "jlcpcb"
        or not assembly
        or assembly_tier != "standard"
    ):
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires exact 46.9x36.8mm, "
            "2-layer, assembled JLCPCB standard product metadata"
        )
    if not isinstance(raw_layout, dict) or not raw_layout:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires a non-empty product.json layout"
        )
    if not isinstance(raw_power_budget, dict) or not raw_power_budget:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires a non-empty product.json powerBudget"
        )
    required_layout = {"boardSizeMm", "minCopperClearanceMm", "decoupling", "groundPlanes", "netClasses"}
    missing_layout = sorted(required_layout - set(layout))
    if missing_layout:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 layout is missing: "
            + ", ".join(missing_layout)
        )
    if tuple(float(value) for value in layout["boardSizeMm"]) != PROTECTED_USB_INDICATOR_BOARD_MM:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires exact layout.boardSizeMm "
            "[46.9, 36.8]"
        )
    if float(layout.get("boardSizeToleranceMm", float("nan"))) != 0.1:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires boardSizeToleranceMm=0.1"
        )
    if float(layout["minCopperClearanceMm"]) != 0.15:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires minCopperClearanceMm=0.15"
        )
    decoupling = layout["decoupling"]
    if (
        float(decoupling.get("maxDistanceMm", float("nan"))) != 2.0
        or decoupling.get("exclude") != ["U1"]
        or set(decoupling) != {"maxDistanceMm", "exclude"}
    ):
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires exact 2mm decoupling with U1 excluded"
        )
    ground = layout["groundPlanes"]
    if ground != {
        "layers": ["top", "bottom"],
        "maxRoutedLengthMm": 20.0,
        "maxFanoutLengthMm": 2.0,
        "stitchingPitchMm": 10.0,
    }:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires exact top/bottom ground "
            "planes, 20mm routed GND, 2mm fanouts and 10mm stitches"
        )
    if layout.get("componentSides") != [
        {"match": ["LED1", "R20"], "side": "bottom"},
        {"match": "*", "side": "top"},
    ]:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires the exact bottom indicator side policy"
        )
    if layout.get("edgeConnectors") != [
        {
            "ref": "J1",
            "edge": "bottom",
            "alignment": "center",
            "edgeToleranceMm": 2.0,
            "centerToleranceMm": 0.1,
        }
    ]:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires the exact J1 bottom-edge datum"
        )

    classes = _profile_net_classes(layout)
    raw_classes = layout.get("netClasses")
    if not isinstance(raw_classes, list) or len(classes) != len(raw_classes):
        raise ProjectShapeError(
            "protected-usb-indicator-v1 netClasses must have unique names"
        )
    required_classes = {
        "POWER": ({"V5", "V3_3"}, 0.8, 0.2, 2.0, True),
        "USB_ATTACH_POWER": ({"VBUS_RAW"}, 0.8, 0.2, 2.0, True),
        "CONTROL_SIGNAL": ({"USB_POWER_FAULT"}, 0.25, 0.15, 1.0, False),
    }
    if set(classes) != set(required_classes):
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires exactly POWER, "
            "USB_ATTACH_POWER and CONTROL_SIGNAL net classes"
        )
    for name, (nets, trunk, neck, max_neck, power_vias) in required_classes.items():
        rule = classes.get(name)
        if rule is None or set(rule.get("nets", [])) != nets:
            raise ProjectShapeError(
                f"protected-usb-indicator-v1 requires exact {name} net membership"
            )
        if (
            float(rule.get("minTrunkWidthMm", 0)) != trunk
            or float(rule.get("minNeckdownWidthMm", 0)) != neck
            or float(rule.get("maxNeckdownLengthMm", float("inf"))) != max_neck
        ):
            raise ProjectShapeError(
                f"protected-usb-indicator-v1 {name} geometry is weaker than its profile"
            )
        if power_vias and (
            float(rule.get("minViaOuterDiameterMm", 0)) != 0.8
            or float(rule.get("minViaHoleDiameterMm", 0)) != 0.5
        ):
            raise ProjectShapeError(
                f"protected-usb-indicator-v1 {name} requires 0.8/0.5mm power vias"
            )
        if not power_vias and (
            "minViaOuterDiameterMm" in rule or "minViaHoleDiameterMm" in rule
        ):
            raise ProjectShapeError(
                "protected-usb-indicator-v1 CONTROL_SIGNAL must not inflate signal vias"
            )

    exact_power_budget = {
        "usb": {
            "rawVbusNet": "VBUS_RAW",
            "protectedVbusNet": "V5",
            "rawAttachCapacitanceMaxUf": 10.0,
            "sourceCurrentMaxMa": 500.0,
            "fixedOperationalLoadMa": 13.0,
            "currentLimiter": {
                "ref": "U7",
                "lcsc": "C55266",
                "inputPin": "IN",
                "outputPin": "OUT",
                "settingPin": "ILIM",
                "settingResistor": {
                    "ref": "R31",
                    "lcsc": "C32297",
                    "resistanceOhms": 59000.0,
                    "returnNet": "GND",
                },
                "minTripMa": 400.6,
                "maxTripMa": 500.0,
            },
            "firmwareLimitedLoads": [],
        },
        "regulators": [
            {
                "profile": "ap7361c-33e-c500795-v1",
                "ref": "U2",
                "inputNet": "V5",
                "outputNet": "V3_3",
                "inputCapRef": "C2",
                "outputCapRef": "C3",
                "maxAmbientC": 60.0,
            }
        ],
    }
    if power_budget != exact_power_budget:
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires the exact VBUS_RAW -> U7 -> "
            "V5 -> AP7361C U2 -> V3_3 power contract"
        )
    if set(parts_lock) != set(PROTECTED_USB_INDICATOR_PARTS):
        raise ProjectShapeError(
            "protected-usb-indicator-v1 requires the exact generated parts.json references"
        )
    for ref, (lcsc, basic, block_id) in PROTECTED_USB_INDICATOR_PARTS.items():
        entry = parts_lock.get(ref)
        fields = set(entry) if isinstance(entry, dict) else set()
        if (
            not isinstance(entry, dict)
            or not _PROTECTED_PART_REQUIRED_FIELDS <= fields
            or not fields <= (
                _PROTECTED_PART_REQUIRED_FIELDS | _PROTECTED_PART_OPTIONAL_FIELDS
            )
            or entry.get("lcsc") != lcsc
            or entry.get("basic") is not basic
            or entry.get("block") != block_id
            or not isinstance(entry.get("description"), str)
            or not entry["description"].strip()
        ):
            raise ProjectShapeError(
                f"protected-usb-indicator-v1 parts.json entry {ref} does not "
                "match the planner-selected golden part identity"
            )
    return _validate_schematic_policy(schematic_policy)


def required_blocks_for_product(product: ResolvedProduct) -> tuple[str, ...]:
    """Golden entries a resolved machine profile must import and lock."""

    if product.design_profile == "protected-usb-indicator-v1":
        return PROTECTED_USB_INDICATOR_BLOCKS
    return ()


def validate_profile_source_identity(
    product: ResolvedProduct, source_sha256: str
) -> None:
    """Bind a machine profile to the generated board behavior, not its label.

    The narrow v1 profile owns exact schematic anchors, protected attachment
    trees, routing phases, pours and scoped via styles.  Any board-source edit
    therefore requires a new reviewed profile revision rather than silently
    retaining the old profile name and JSON contract.
    """

    if product.design_profile == "protected-usb-indicator-v1" and (
        source_sha256 != product.design_profile_source_sha256
        or source_sha256 != PROTECTED_USB_INDICATOR_SOURCE_SHA256
    ):
        raise ProjectShapeError(
            "protected-usb-indicator-v1 board source differs from its exact "
            "public generator profile"
        )


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
    assembly_tier_raw = raw.get("assemblyTier", "economic")
    if assembly_tier_raw not in ASSEMBLY_TIERS:
        raise ProjectShapeError(
            "product.json 'assemblyTier' must be one of "
            f"{', '.join(ASSEMBLY_TIERS)} (got {assembly_tier_raw!r})"
        )
    design_profile_raw = raw.get("designProfile")
    if design_profile_raw is not None and design_profile_raw not in DESIGN_PROFILES:
        raise ProjectShapeError(
            "product.json 'designProfile' must be one of "
            f"{', '.join(DESIGN_PROFILES)} (got {design_profile_raw!r})"
        )
    layout = validate_layout(raw.get("layout"))
    power_budget = validate_power_budget(raw.get("powerBudget"))
    if power_budget.get("usb") and power != "usb-c-5v":
        raise ProjectShapeError(
            "product.json 'powerBudget.usb' requires product power='usb-c-5v'"
        )
    schematic_policy: dict[str, Any] = {}
    if design_profile_raw == "protected-usb-indicator-v1":
        schematic_policy = _require_protected_usb_indicator_contracts(
            envelope_mm=envelope,
            layers=layers_raw,
            fab=str(raw.get("fab") or "jlcpcb"),
            assembly=bool(raw.get("assembly", False)),
            assembly_tier=str(assembly_tier_raw),
            source_sha256=raw.get("designProfileSourceSha256"),
            raw_layout=raw.get("layout"),
            layout=layout,
            raw_power_budget=raw.get("powerBudget"),
            power_budget=power_budget,
            schematic_policy=raw.get("schematicPolicy"),
            parts_lock=_read_parts_lock(project_root),
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
        assembly_tier=cast(AssemblyTier, assembly_tier_raw),
        layout=layout,
        power_budget=power_budget,
        design_profile=cast(str | None, design_profile_raw),
        design_profile_source_sha256=cast(
            str | None, raw.get("designProfileSourceSha256")
        ),
        schematic_policy=schematic_policy,
    )


def load_parts(project_root: Path) -> dict[str, dict]:
    """Load ``<project>/parts.json`` (the locked BOM identities, owned wholly
    by parts-book). Absent → ``{}``; present-but-broken → ProjectShapeError
    (a silent empty lock would skip part_drift silently)."""
    return _read_parts_lock(project_root)


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
