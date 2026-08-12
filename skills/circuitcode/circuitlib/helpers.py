"""Helpers over the tables — call these instead of retyping numbers.

Every helper is keyword-only and raises ``ValueError`` on an impossible ask, so
a bad parameter fails pointing at the spec rather than at the toolchain five
stages deep.
"""

from __future__ import annotations

from dataclasses import dataclass

from circuitlib import tables
from circuitlib.blocks import (
    BLOCKS,
    CAPABILITY_INDEX,
    block_for,
    missing_requirements,
    peak_ma_for_rail,
    total_peak_ma,
    unexposed_nets,
)

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
    """What the planner proposes before a line of TSX is written.

    Three of the fields below exist because a defect kept being *detected* on
    a finished board when it could have been *refused* here:

    * ``power_trace_width_mm`` — the router uses one width for everything, so
      a rail ends up on signal copper. The number the planner already knows
      (peak current) decides it, by IPC-2221, before anything is routed.
    * ``regulator`` — a linear regulator asked to drop 1.7V at 600mA cooks.
      That is arithmetic on numbers the plan already contains, so a plan that
      would cook a part is not buildable rather than merely warned about.
    * ``must_expose`` — an MCU whose SWD pins reach nothing cannot be
      programmed after assembly. Every block is individually fine; only the
      plan can see it.
    """

    block_ids: tuple[str, ...]
    unmet: tuple[str, ...]
    unavailable: tuple[str, ...]
    current_ma: float
    est_parts_cost_usd: float
    peak_current_ma: float = 0.0
    #: Preferred width for ordinary board-level signals. Fine-pitch package
    #: escapes and impedance-controlled interfaces declare their own short or
    #: calculated widths; this value never raises the board-wide minimum.
    signal_trace_width_mm: float = tables.PREFERRED_SIGNAL_TRACE_WIDTH_MM
    power_trace_width_mm: float = 0.0
    power_trunk_width_mm: float = 0.0
    power_neckdown_width_mm: float = tables.POWER_NECKDOWN_WIDTH_MM
    power_neckdown_max_length_mm: float = tables.POWER_NECKDOWN_MAX_LENGTH_MM
    preferred_clearance_mm: float = tables.PREFERRED_CLEARANCE_MM
    # Two solved, stitched faces are the ordinary two-layer-board contract.
    # A one-face plane remains an explicit board choice, but must not be the
    # generator's silent default: it lengthens return paths and makes
    # opposite-side components depend on routed GND spokes.
    ground_plane_layers: tuple[str, ...] = ("top", "bottom")
    ground_fanout_max_length_mm: float = tables.GROUND_FANOUT_MAX_LENGTH_MM
    ground_stitching_pitch_mm: float = tables.GROUND_STITCHING_PITCH_MM
    must_expose: tuple[str, ...] = ()
    regulator: dict[str, object] | None = None
    #: Protected-source contract selected by the block plan. Physical peak is
    #: never replaced by a firmware number; ``operational_load_ma`` applies
    #: only to the normal-load/trip comparison.
    source_budget: dict[str, object] | None = None

    @property
    def overheats(self) -> bool:
        # Planning is where we still have the freedom to choose a sound power
        # architecture. A regulator with less than 30degC junction headroom at
        # the declared hot ambient is already a rejected plan, not a warning
        # to discover after a complete layout. The artifact verifier keeps the
        # warning severity useful for an existing board; the generator must
        # not deliberately create that board in the first place.
        return bool(
            self.regulator
            and self.regulator.get("severity") in {"warning", "error"}
        )

    @property
    def buildable(self) -> bool:
        source_over_budget = bool(
            self.source_budget
            and self.source_budget.get("severity") == "error"
        )
        return (
            not self.unmet
            and not self.unavailable
            and not self.must_expose
            and not self.overheats
            and not source_over_budget
        )


def board_plan(
    *,
    capabilities: list[str],
    counts: dict[str, int] | None = None,
    power_source: str = "usb-c-5v",
    supply_rail_overrides: dict[str, str] | None = None,
    firmware_load_caps_ma: dict[str, float] | None = None,
    exposed_nets: list[str] | None = None,
) -> BoardPlan:
    """Turn a capability list into a concrete block set, sized and budgeted.

    Capabilities we have no block for come back in ``unavailable`` — that is the
    honest answer, and the SKILL.md's rule is to report it, never to fill the
    hole by inventing a circuit.

    ``counts`` gives unit counts for parametric blocks (``{"ws2812-chain": 8}``);
    without one a parametric block is counted as a single unit, which
    understates its peak. ``firmware_load_caps_ma`` may replace one chosen
    block family's physical peak only for the source's *normal-operation*
    comparison. The physical number remains in the plan for copper, fault and
    product-policy generation; an omitted cap can therefore make a high-peak
    USB plan honestly unbuildable. ``exposed_nets`` names signals that the
    board composition really brings to a connector or probe. An MCU plan with
    unresolved SWD obligations remains unbuildable rather than merely carrying
    an advisory tuple that callers can ignore.
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

    chosen = _collapse_supersets(chosen)

    current = sum(BLOCKS[b].current_draw_ma for b in chosen)
    peak = total_peak_ma(chosen, counts)
    cost = sum(len(BLOCKS[b].parts) for b in chosen) * 0.6  # rough, marked estimate

    width = trace_width_for(current_a=peak / 1000.0) if peak > 0 else 0.0
    trunk_width = max(width, tables.POWER_TRUNK_MIN_MM) if peak > 0 else 0.0

    regulator: dict[str, object] | None = None
    regulator_block = BLOCKS.get("ldo-3v3")
    regulator_rail = (
        regulator_block.regulator_output_rail if regulator_block else ""
    )
    regulator_load_ma = peak_ma_for_rail(
        chosen,
        regulator_rail,
        counts,
        supply_rail_overrides,
    ) if regulator_rail else 0.0
    if "ldo-3v3" in chosen and regulator_load_ma > 0:
        # Only loads supplied by the regulator's output heat it.  A V5 pixel
        # ring still counts in the source/copper budget above, but does not
        # magically flow through the 3V3 LDO.
        regulator = regulator_thermal(
            vin=tables.USB_VBUS_V if hasattr(tables, "USB_VBUS_V") else 5.0,
            vout=3.3,
            current_a=regulator_load_ma / 1000.0,
            package="SOT-223",
            ambient_c=AMBIENT_HOT_C,
        )
        regulator["refdes"] = "U2"
        regulator["output_rail"] = regulator_rail
        regulator["load_ma"] = round(regulator_load_ma, 1)

    source_budget: dict[str, object] | None = None
    source_blocks = [
        BLOCKS[block_id]
        for block_id in chosen
        if BLOCKS[block_id].source_operational_limit_ma > 0
    ]
    if source_blocks and power_source == "usb-c-5v":
        source = source_blocks[0]
        caps = dict(firmware_load_caps_ma or {})
        operational = peak
        normalized_caps: dict[str, float] = {}
        limited_loads: dict[str, dict[str, float | int]] = {}
        for block_id, raw_cap in caps.items():
            if block_id not in chosen:
                raise ValueError(
                    f"firmware current cap names unselected block {block_id!r}"
                )
            if isinstance(raw_cap, bool) or not isinstance(raw_cap, (int, float)):
                raise ValueError(
                    f"firmware current cap for {block_id!r} must be a number"
                )
            cap = float(raw_cap)
            units = (counts or {}).get(
                block_id,
                1 if BLOCKS[block_id].unit_prop else 0,
            )
            physical = BLOCKS[block_id].peak_ma(units)
            if cap < 0 or cap > physical + 1e-9:
                raise ValueError(
                    f"firmware current cap for {block_id!r} must be between "
                    f"0 and its {physical:g}mA physical peak (got {cap:g})"
                )
            operational += cap - physical
            normalized_caps[block_id] = cap
            limited_loads[block_id] = {
                "count": max(units, 0),
                "per_device_physical_peak_ma": (
                    BLOCKS[block_id].peak_per_unit_ma or physical
                ),
                "physical_peak_ma": physical,
                "operational_max_ma": cap,
            }

        limit = source.source_operational_limit_ma
        fixed_operational_load = peak - sum(
            float(load["physical_peak_ma"])
            for load in limited_loads.values()
        )
        source_budget = {
            "block_id": source.id,
            "physical_peak_ma": round(peak, 1),
            "operational_load_ma": round(operational, 1),
            "fixed_operational_load_ma": round(fixed_operational_load, 1),
            "operational_limit_ma": limit,
            "source_current_max_ma": source.source_current_max_ma,
            "firmware_load_caps_ma": dict(sorted(normalized_caps.items())),
            "firmware_limited_loads": dict(sorted(limited_loads.items())),
            "severity": "error" if operational > limit + 1e-9 else "info",
        }

    return BoardPlan(
        block_ids=tuple(chosen),
        unmet=tuple(missing_requirements(chosen)),
        unavailable=tuple(unavailable),
        current_ma=current,
        est_parts_cost_usd=round(cost, 2),
        peak_current_ma=round(peak, 1),
        power_trace_width_mm=width,
        power_trunk_width_mm=trunk_width,
        must_expose=tuple(unexposed_nets(chosen, exposed_nets)),
        regulator=regulator,
        source_budget=source_budget,
    )


def usb_power_budget_for_plan(
    plan: BoardPlan,
    *,
    firmware_load_matches: dict[str, str | list[str]] | None = None,
) -> dict[str, object]:
    """Compile a USB ``product.json.powerBudget`` from a buildable plan.

    The planner owns the arithmetic and the protected-entry block owns the
    limiter identity. The caller supplies only the product's actual refdes
    pattern for each firmware-limited block family; that cannot be inferred
    from a block id after a board allocates its global designators.
    """
    budget = plan.source_budget
    if not isinstance(budget, dict):
        raise ValueError("plan has no protected USB source budget")
    if budget.get("severity") == "error":
        raise ValueError(
            "plan exceeds its protected USB operational limit; choose lower "
            "firmware caps before generating product.json"
        )
    if not plan.buildable:
        raise ValueError(
            "plan is not buildable; resolve unmet capabilities, unavailable "
            "blocks, debug/test-point exposure, and regulator heat before "
            "generating product.json"
        )
    source_id = str(budget.get("block_id") or "")
    source = BLOCKS.get(source_id)
    contract = source.usb_source_contract if source is not None else None
    if contract is None:
        raise ValueError(f"plan source block {source_id!r} has no USB contract")

    limited = budget.get("firmware_limited_loads")
    if not isinstance(limited, dict):
        limited = {}
    matches = dict(firmware_load_matches or {})
    missing = sorted(set(limited) - set(matches))
    extra = sorted(set(matches) - set(limited))
    if missing:
        raise ValueError(
            "firmware-limited block(s) need product refdes match patterns: "
            + ", ".join(missing)
        )
    if extra:
        raise ValueError(
            "firmware load match names block(s) not capped by this plan: "
            + ", ".join(extra)
        )

    product_loads: list[dict[str, object]] = []
    for block_id in sorted(limited):
        load = limited[block_id]
        if not isinstance(load, dict):
            raise ValueError(f"invalid source budget for {block_id!r}")
        raw_match = matches[block_id]
        patterns = [raw_match] if isinstance(raw_match, str) else list(raw_match)
        if not patterns or not all(
            isinstance(pattern, str) and pattern.strip() for pattern in patterns
        ):
            raise ValueError(
                f"firmware load match for {block_id!r} must contain non-empty strings"
            )
        product_loads.append(
            {
                "match": [pattern.strip() for pattern in patterns],
                "perDevicePhysicalPeakMa": float(
                    load["per_device_physical_peak_ma"]
                ),
                "aggregateOperationalMaxMa": float(load["operational_max_ma"]),
            }
        )

    return {
        "usb": {
            "rawVbusNet": contract.raw_net,
            "protectedVbusNet": contract.protected_net,
            "rawAttachCapacitanceMaxUf": contract.raw_attach_capacitance_max_uf,
            "sourceCurrentMaxMa": contract.source_current_max_ma,
            "fixedOperationalLoadMa": float(
                budget.get("fixed_operational_load_ma") or 0.0
            ),
            "currentLimiter": {
                "ref": contract.limiter_ref,
                "lcsc": contract.limiter_lcsc,
                "inputPin": contract.input_pin,
                "outputPin": contract.output_pin,
                "settingPin": contract.setting_pin,
                "settingResistor": {
                    "ref": contract.setting_resistor_ref,
                    "lcsc": contract.setting_resistor_lcsc,
                    "resistanceOhms": contract.setting_resistance_ohms,
                    "returnNet": contract.setting_return_net,
                },
                "minTripMa": contract.min_trip_ma,
                "maxTripMa": contract.max_trip_ma,
            },
            "firmwareLimitedLoads": product_loads,
        }
    }


#: block id -> the block that subsumes it. When both land in a plan, only the
#: superset survives: they place the same refdes block (J1/R1/R2/U1/C1), so a
#: board carrying both is two USB-C entries wired to the same designators.
#: Found 2026-08-11 — `board_plan(["mcu", "button", "power-usb"])` returned
#: usb-c-power *and* usb-c-data, which is the one composition the matrix marks
#: illegal. The planner was able to emit a board outside the tested space,
#: which is exactly the thing closure-under-composition forbids.
BLOCK_SUPERSETS: dict[str, str] = {
    "usb-c-power": "usb-c-data",
}


def _collapse_supersets(chosen: list[str]) -> list[str]:
    """Drop any block a chosen superset already provides."""
    present = set(chosen)
    return [
        bid for bid in chosen
        if BLOCK_SUPERSETS.get(bid) not in present
    ]


def power_budget(*, source: str, current_ma: float) -> list[dict[str, str]]:
    """Warn when the draw outruns what the source/regulator can give."""
    # Keep this legacy single-number helper aligned with the block planner's
    # protected USB entry.  An unadvertised USB-C sink is a 500mA source, not
    # the historical 1.5A guess.  ``board_plan`` applies the stricter 400.6mA
    # normal-operation/trip contract and preserves physical peak separately.
    limits_ma = {
        "usb-c-5v": BLOCKS["usb-power-entry"].source_current_max_ma,
        "external-dc-lv": 1000.0,
        "battery-lipo-sealed-block": 500.0,
    }
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
    # Conservative generic board-level value. A lower number is valid only
    # when an exact regulator profile also proves its required thermal land.
    "SOT-223": 110.0,
    "SOT-23": 250.0,
    "SOT-89": 140.0,
    "TO-252": 92.0,   # DPAK
    "TO-263": 70.0,   # D2PAK
}
MAX_JUNCTION_C = 125.0
AMBIENT_C = 25.0
#: What a desk object actually sits in. A datasheet quotes 25 degC; a puck in a
#: warm room with an enclosure round it does not, and planning at 25 is
#: planning for a laboratory.
AMBIENT_HOT_C = 45.0


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


#: Typical forward voltage by LED colour, at the few-mA currents we run.
LED_VF = {"red": 1.9, "green": 2.1, "yellow": 2.1, "orange": 2.0,
          "blue": 3.0, "white": 3.0}
#: An indicator wants to be seen, not to melt or to hog the rail.
LED_CURRENT_BAND_MA = (0.5, 20.0)
#: I2C pull-ups: below this the bus sinks too hard for a weak driver; above
#: it the rise time misses spec on any real capacitance.
I2C_PULLUP_BAND_OHMS = (1000, 10000)


def led_current(*, rail_v: float, resistance_ohms: float,
                color: str = "green") -> dict[str, object]:
    """Ohm's law on an indicator: I = (Vrail - Vf) / R.

    This is the check that catches the mistake no DRC can see. A board with a
    10-ohm series resistor passes compile, ERC, DRC, DFM and every image
    review, and then draws 130mA through a part rated for 20 and dies.
    """
    if resistance_ohms <= 0:
        raise ValueError(f"resistance must be positive (got {resistance_ohms})")
    vf = LED_VF.get(color.lower(), 2.1)
    if rail_v <= vf:
        return {"current_ma": 0.0, "verdict": "no-conduction", "vf": vf,
                "severity": "warning"}
    current_ma = (rail_v - vf) / resistance_ohms * 1000.0
    low, high = LED_CURRENT_BAND_MA
    if current_ma > high:
        verdict, severity = "over-current", "error"
    elif current_ma < low:
        verdict, severity = "too-dim", "warning"
    else:
        verdict, severity = "ok", "info"
    return {"current_ma": round(current_ma, 2), "vf": vf,
            "verdict": verdict, "severity": severity}


def led_warnings(*, refdes: str, rail_v: float, resistance_ohms: float,
                 color: str = "green") -> list[dict[str, str]]:
    """The LED verdict as contract-shaped warnings. Never raises."""
    try:
        result = led_current(rail_v=rail_v, resistance_ohms=resistance_ohms,
                             color=color)
    except ValueError as exc:
        return [{"part": refdes, "kind": "check_failed", "severity": "warning",
                 "detail": f"led_current: {exc}"}]
    if result["verdict"] == "ok":
        return []
    detail = {
        "over-current": (
            f"{result['current_ma']}mA through {refdes} exceeds the "
            f"{LED_CURRENT_BAND_MA[1]:g}mA an indicator LED is rated for — "
            "raise the series resistor"
        ),
        "too-dim": (
            f"{result['current_ma']}mA through {refdes} will barely be "
            "visible — lower the series resistor"
        ),
        "no-conduction": (
            f"{refdes} will never light: a {result['vf']}V forward drop "
            f"needs more than a {rail_v}V rail"
        ),
    }[str(result["verdict"])]
    return [{"part": refdes, "kind": "functional",
             "severity": str(result["severity"]), "detail": detail}]


def pullup_warnings(*, refdes: str, resistance_ohms: float,
                    bus: str = "I2C") -> list[dict[str, str]]:
    """Pull-up value sanity for a shared bus."""
    low, high = I2C_PULLUP_BAND_OHMS
    if low <= resistance_ohms <= high:
        return []
    if resistance_ohms < low:
        detail = (
            f"{refdes} at {resistance_ohms:g}ohm pulls the {bus} bus too hard "
            f"(under {low}ohm); a weak open-drain driver may not reach a valid low"
        )
    else:
        detail = (
            f"{refdes} at {resistance_ohms:g}ohm is a weak {bus} pull-up "
            f"(over {high}ohm); rise time will miss spec on any real bus "
            "capacitance"
        )
    return [{"part": refdes, "kind": "functional", "severity": "warning",
             "detail": detail}]


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
    counts: dict[str, int] | None = None,
    supply_rail_overrides: dict[str, str] | None = None,
    exposed_nets: list[str] | None = None,
    power_trace_width_mm: float | None = None,
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

        # -- Three things only the *plan* can see. Each one used to be found
        # -- on a finished board, or on the gerbers, or not at all.
        if ids:
            peak = total_peak_ma(ids, counts)

            # 1. A rail on signal copper. The router uses one width for
            #    everything, so the plan has to state the number.
            if peak > 0:
                required = trace_width_for(current_a=peak / 1000.0)
                if power_trace_width_mm is None:
                    out.append({
                        "part": "board", "kind": "power_trace_width",
                        "severity": "info",
                        "detail": (
                            f"peak rail current is {peak:.0f}mA, so every power "
                            f"and ground net needs at least {required:.3f}mm of "
                            "copper (IPC-2221B, 1oz external, 10degC rise). The "
                            "router's default is 0.15mm and applies to every net "
                            "equally — set the width on the power traces"
                        ),
                    })
                elif power_trace_width_mm < required - 1e-9:
                    out.append({
                        "part": "board", "kind": "power_trace_width",
                        "severity": "error",
                        "detail": (
                            f"power nets are declared at "
                            f"{power_trace_width_mm:.3f}mm but the plan draws "
                            f"{peak:.0f}mA peak, which needs {required:.3f}mm"
                        ),
                    })

            # 2. A linear regulator asked to drop more heat than its package
            #    can shed. Arithmetic on numbers the plan already has.
            regulator_block = BLOCKS.get("ldo-3v3")
            regulator_rail = (
                regulator_block.regulator_output_rail
                if regulator_block else ""
            )
            regulator_load_ma = peak_ma_for_rail(
                ids,
                regulator_rail,
                counts,
                supply_rail_overrides,
            ) if regulator_rail else 0.0
            if "ldo-3v3" in ids and regulator_load_ma > 0:
                verdict = regulator_thermal(
                    vin=5.0, vout=3.3, current_a=regulator_load_ma / 1000.0,
                    package="SOT-223", ambient_c=AMBIENT_HOT_C,
                )
                if verdict["verdict"] != "ok":
                    out.append({
                        "part": "U2", "kind": "regulator_thermal",
                        "severity": str(verdict["severity"]),
                        "detail": (
                            f"the {regulator_rail} rail draws "
                            f"{regulator_load_ma:.0f}mA peak through a SOT-223 "
                            f"linear regulator dropping 1.7V: {verdict['watts']}W, "
                            f"about {verdict['junction_c']}degC junction at a "
                            f"{AMBIENT_HOT_C:g}degC ambient "
                            f"({verdict['headroom_c']}degC from the 125degC "
                            "limit). Use a buck, split the rail, or cut the load"
                        ),
                    })

            # 3. A board nobody can program. Every block is individually fine.
            for net in unexposed_nets(ids, exposed_nets):
                out.append({
                    "part": "board", "kind": "debug_unreachable",
                    "severity": "error",
                    "detail": (
                        f"{net} is owned by a block on this board and reaches no "
                        "connector, header or test point, so the board cannot be "
                        f"programmed or halted once it is assembled. Add a "
                        f"<testpoint> on {net} (copper only — it costs nothing "
                        "and the BOM gate exempts it)"
                    ),
                })

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
