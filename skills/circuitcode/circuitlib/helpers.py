"""Helpers over the tables — call these instead of retyping numbers.

Every helper is keyword-only and raises ``ValueError`` on an impossible ask, so
a bad parameter fails pointing at the spec rather than at the toolchain five
stages deep.
"""

from __future__ import annotations

from dataclasses import dataclass

from circuitlib import tables
from circuitlib.blocks import BLOCKS, CAPABILITY_INDEX, block_for, missing_requirements

MM_PER_MIL = 0.0254
#: IPC-2221 external-layer constants for I = k * dT^0.44 * A^0.725 (A in mil^2).
_IPC_K_EXTERNAL = 0.048
_IPC_K_INTERNAL = 0.024
_OZ_THICKNESS_MIL = 1.378


def trace_width_for(
    *,
    current_a: float,
    delta_t_c: float = 10.0,
    layer: str = "external",
    copper_oz: int = tables.COPPER_OZ,
) -> float:
    """Minimum trace width in mm for a current, via IPC-2221.

    Never returns below the fab's blocking floor — a computed hair-width trace
    is not manufacturable even when it is thermally fine.
    """
    if current_a <= 0:
        raise ValueError(f"current_a must be positive (got {current_a})")
    if delta_t_c <= 0:
        raise ValueError(f"delta_t_c must be positive (got {delta_t_c})")
    if layer not in ("external", "internal"):
        raise ValueError(f"layer must be 'external' or 'internal' (got {layer!r})")
    if copper_oz <= 0:
        raise ValueError(f"copper_oz must be positive (got {copper_oz})")

    k = _IPC_K_EXTERNAL if layer == "external" else _IPC_K_INTERNAL
    area_mil2 = (current_a / (k * delta_t_c**0.44)) ** (1 / 0.725)
    width_mm = (area_mil2 / (_OZ_THICKNESS_MIL * copper_oz)) * MM_PER_MIL
    return max(round(width_mm, 3), tables.MIN_TRACE_WIDTH_MM)


def clearance_for(*, volts: float) -> float:
    """Minimum conductor spacing in mm for a working voltage (IPC-2221 B4,
    external uncoated, the conservative row). The envelope caps input voltage
    long before these numbers get interesting — this exists so a helper, not a
    guess, answers the question."""
    if volts < 0:
        raise ValueError(f"volts must be non-negative (got {volts})")
    if volts <= 15:
        return tables.MIN_TRACE_SPACING_MM
    if volts <= 30:
        return 0.25
    if volts <= 100:
        return 0.60
    raise ValueError(
        f"{volts:g}V is outside the low-voltage envelope "
        f"(max {tables.MAX_DC_INPUT_V:g}V) — see circuitlib.safety"
    )


def decoupling_for(*, power_pins: int, rails: int = 1) -> dict[str, int]:
    """The decoupling a board owes: one local cap per IC power pin, one bulk
    cap per rail."""
    if power_pins < 0 or rails < 0:
        raise ValueError("power_pins and rails must be non-negative")
    return {tables.DECOUPLE_PER_POWER_PIN: power_pins, tables.BULK_PER_RAIL: rails}


@dataclass(frozen=True)
class BoardPlan:
    """What the planner proposes before a line of TSX is written."""

    block_ids: tuple[str, ...]
    unmet: tuple[str, ...]
    unavailable: tuple[str, ...]
    current_ma: float
    est_parts_cost_usd: float

    @property
    def buildable(self) -> bool:
        return not self.unmet and not self.unavailable


def board_plan(*, capabilities: list[str]) -> BoardPlan:
    """Turn a capability list into a concrete block set.

    Capabilities we have no block for come back in ``unavailable`` — that is the
    honest answer, and the SKILL.md's rule is to report it, never to fill the
    hole by inventing a circuit.
    """
    chosen: list[str] = []
    unavailable: list[str] = []
    for cap in capabilities:
        block = block_for(cap)
        if block is None:
            unavailable.append(cap)
        elif block.id not in chosen:
            chosen.append(block.id)

    # A block whose requirements nothing satisfies pulls in its provider.
    for _ in range(len(BLOCKS)):
        unmet = missing_requirements(chosen)
        if not unmet:
            break
        grew = False
        for net in unmet:
            for bid, block in BLOCKS.items():
                if net in block.provides and bid not in chosen:
                    chosen.append(bid)
                    grew = True
                    break
        if not grew:
            break

    current = sum(BLOCKS[b].current_draw_ma for b in chosen)
    cost = sum(len(BLOCKS[b].parts) for b in chosen) * 0.6  # rough, marked estimate
    return BoardPlan(
        block_ids=tuple(chosen),
        unmet=tuple(missing_requirements(chosen)),
        unavailable=tuple(unavailable),
        current_ma=current,
        est_parts_cost_usd=round(cost, 2),
    )


def power_budget(*, source: str, current_ma: float) -> list[dict[str, str]]:
    """Warn when the draw outruns what the source/regulator can give."""
    limits_ma = {"usb-c-5v": 1500.0, "external-dc-lv": 1000.0,
                 "battery-lipo-sealed-block": 500.0}
    limit = limits_ma.get(source)
    if limit is None:
        return [{
            "part": "board", "kind": "power_budget", "severity": "warning",
            "detail": f"unknown power source {source!r}; cannot budget current",
        }]
    if current_ma > limit:
        return [{
            "part": "board", "kind": "power_budget", "severity": "warning",
            "detail": (f"estimated {current_ma:.0f}mA exceeds the {limit:.0f}mA "
                       f"budget for {source}"),
        }]
    return []


#: Junction-to-ambient thermal resistance, °C/W, for the packages our linear
#: regulators come in. Datasheet figures on a 2-layer board with modest copper —
#: the number degrades badly with less pour, which is the point of the warning.
THETA_JA_C_PER_W = {
    "SOT-223": 62.0,
    "SOT-23": 250.0,
    "SOT-89": 140.0,
    "TO-252": 92.0,   # DPAK
    "TO-263": 70.0,   # D2PAK
}
MAX_JUNCTION_C = 125.0
AMBIENT_C = 25.0


def regulator_thermal(
    *, vin: float, vout: float, current_a: float, package: str = "SOT-223",
    ambient_c: float = AMBIENT_C,
) -> dict[str, object]:
    """Will the linear regulator cook?

    A dropped volt times a drawn amp is heat, and no DRC on earth mentions it.
    This is arithmetic, not simulation: P = (Vin - Vout) x I, then
    Tj = Tambient + P x theta_JA. It catches the single most common
    power mistake on hobby boards — an AMS1117 asked to do 500mA from 5V.
    """
    if vin < vout:
        raise ValueError(f"vin {vin}V is below vout {vout}V")
    if current_a < 0:
        raise ValueError(f"current_a must be non-negative (got {current_a})")
    theta = THETA_JA_C_PER_W.get(package.upper().replace("_", "-"))
    if theta is None:
        raise ValueError(
            f"no thermal data for package {package!r} "
            f"(have: {', '.join(sorted(THETA_JA_C_PER_W))})"
        )
    watts = (vin - vout) * current_a
    rise = watts * theta
    junction = ambient_c + rise
    headroom = MAX_JUNCTION_C - junction
    if junction >= MAX_JUNCTION_C:
        verdict, severity = "over-temperature", "error"
    elif headroom < 30:
        verdict, severity = "marginal", "warning"
    else:
        verdict, severity = "ok", "info"
    return {
        "watts": round(watts, 3),
        "junction_c": round(junction, 1),
        "headroom_c": round(headroom, 1),
        "theta_ja": theta,
        "verdict": verdict,
        "severity": severity,
    }


def regulator_thermal_warnings(
    *, refdes: str = "U2", vin: float = 5.0, vout: float = 3.3,
    current_a: float, package: str = "SOT-223",
) -> list[dict[str, str]]:
    """The thermal verdict as contract-shaped warnings. Never raises."""
    try:
        result = regulator_thermal(
            vin=vin, vout=vout, current_a=current_a, package=package
        )
    except ValueError as exc:
        return [{"part": refdes, "kind": "check_failed", "severity": "warning",
                 "detail": f"regulator_thermal: {exc}"}]
    if result["verdict"] == "ok":
        return []
    return [{
        "part": refdes,
        "kind": "power_budget",
        "severity": str(result["severity"]),
        "detail": (
            f"{package} linear regulator dissipating {result['watts']}W at "
            f"{current_a * 1000:.0f}mA reaches an estimated "
            f"{result['junction_c']}degC junction "
            f"({result['headroom_c']}degC from the 125degC limit) — "
            "add copper pour on the tab, drop to a lower current, or use a buck"
        ),
    }]


def estimate_cost(
    *, unique_parts: int, extended_parts: int, joints: int, assembled: bool = True,
    quantity: int = 5,
) -> dict[str, float]:
    """JLCPCB economy estimate. Parts cost itself is not modelled — it comes
    from the parts lock, not from a table."""
    if quantity > tables.ECONOMY_MAX_ASSEMBLED and assembled:
        raise ValueError(
            f"economy assembly caps at {tables.ECONOMY_MAX_ASSEMBLED} pieces "
            f"(asked {quantity})"
        )
    pcb = tables.PCB_5X_2LAYER_USD
    if not assembled:
        return {"pcb": pcb, "assembly": 0.0, "fees": 0.0, "total_ex_parts": pcb}
    assembly = tables.ASSEMBLY_SETUP_USD + tables.STENCIL_USD
    assembly += joints * quantity * tables.SMT_JOINT_USD
    fees = extended_parts * tables.EXTENDED_PART_FEE_USD
    return {
        "pcb": round(pcb, 2),
        "assembly": round(assembly, 2),
        "fees": round(fees, 2),
        "total_ex_parts": round(pcb + assembly + fees, 2),
    }


def validate_board_law(
    *,
    block_ids: list[str] | None = None,
    power_source: str | None = None,
    board_mm: tuple[float, float] | None = None,
    envelope_mm: tuple[float, float] | None = None,
    thickness_mm: float | None = None,
    mounting_holes: int | None = None,
) -> list[dict[str, str]]:
    """Soft craft law: contract-shaped warnings the board source declares about
    itself, before the pipeline's hard checks ever run.

    Never raises — a validator that explodes is a validator that gets deleted.
    """
    out: list[dict[str, str]] = []
    try:
        ids = list(block_ids or [])
        for net in missing_requirements(ids):
            out.append({
                "part": "board", "kind": "functional", "severity": "warning",
                "detail": f"net {net} is required by a block but nothing provides it",
            })
        if ids.count("i2c-bus") > 1:
            out.append({
                "part": "i2c-bus", "kind": "functional", "severity": "warning",
                "detail": "more than one i2c-bus block halves the pull-up resistance",
            })
        if "usb-c-power" in ids and "usb-c-data" in ids:
            out.append({
                "part": "usb-c-data", "kind": "functional", "severity": "warning",
                "detail": "usb-c-data is a superset of usb-c-power; place only one",
            })
        if power_source:
            out.extend(power_budget(
                source=power_source,
                current_ma=sum(BLOCKS[b].current_draw_ma for b in ids if b in BLOCKS),
            ))
        if board_mm:
            w, h = board_mm
            if min(w, h) < tables.MIN_BOARD_EDGE_MM:
                out.append({
                    "part": "board", "kind": "functional", "severity": "warning",
                    "detail": (f"board {w:g}x{h:g}mm is below the "
                               f"{tables.MIN_BOARD_EDGE_MM:g}mm fab minimum"),
                })
            if envelope_mm and (w > envelope_mm[0] or h > envelope_mm[1]):
                out.append({
                    "part": "board", "kind": "functional", "severity": "warning",
                    "detail": (f"board {w:g}x{h:g}mm exceeds the declared envelope "
                               f"{envelope_mm[0]:g}x{envelope_mm[1]:g}mm"),
                })
        if thickness_mm is not None and abs(thickness_mm - tables.BOARD_THICKNESS_MM) > 1e-6:
            out.append({
                "part": "board", "kind": "functional", "severity": "warning",
                "detail": (f"thickness {thickness_mm:g}mm is not the standard "
                           f"{tables.BOARD_THICKNESS_MM:g}mm — set it explicitly "
                           "or the toolchain default (1.4mm) ships"),
            })
        if mounting_holes is not None and mounting_holes < 2:
            out.append({
                "part": "board", "kind": "functional", "severity": "warning",
                "detail": ("fewer than two mounting holes — the enclosure has "
                           "nothing to hold"),
            })
    except Exception as exc:  # pragma: no cover - defensive, mirrors the pipeline
        out.append({
            "part": "board", "kind": "check_failed", "severity": "warning",
            "detail": f"validate_board_law raised: {exc}",
        })
    return out


def fab_profile(vendor: str = "jlcpcb") -> dict[str, object]:
    """The manufacturable window a composer must design inside."""
    if vendor != "jlcpcb":
        raise ValueError(f"no fab profile for {vendor!r} (v1 ships jlcpcb only)")
    return {
        "vendor": vendor,
        "layers": tables.DEFAULT_LAYERS,
        "thickness_mm": tables.BOARD_THICKNESS_MM,
        "min_trace_mm": tables.MIN_TRACE_WIDTH_MM,
        "min_spacing_mm": tables.MIN_TRACE_SPACING_MM,
        "min_via_drill_mm": tables.MIN_VIA_DRILL_MM,
        "min_via_annular_mm": tables.MIN_VIA_ANNULAR_MM,
        "min_pth_drill_mm": tables.MIN_PTH_DRILL_MM,
        "min_pth_annular_mm": tables.MIN_PTH_ANNULAR_MM,
        "min_copper_to_edge_mm": tables.MIN_COPPER_TO_EDGE_MM,
        "cost_band_assembled_5x_usd": tables.COST_BAND_ASSEMBLED_5X_USD,
        "lead_time_days": tables.LEAD_TIME_DAYS,
    }


__all__ = [
    "trace_width_for", "clearance_for", "decoupling_for", "board_plan",
    "power_budget", "estimate_cost", "validate_board_law", "fab_profile",
    "BoardPlan", "CAPABILITY_INDEX",
]
