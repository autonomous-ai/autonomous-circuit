"""Will anything on this board cook?

**The gap this closes.** ``circuitlib.helpers.regulator_thermal`` is good
arithmetic and it only ever runs on numbers the board source hands it — so a
board the agent wrote by hand, or a regulator whose load changed after the
source was written, is never asked the question. And nothing at all asks it of
a *resistor*: a 0402 is rated for about 62.5 mW, and a current-limiting or
sense resistor that quietly exceeds that discolours, drifts and eventually
opens. No DRC on earth mentions either.

Heat is the failure that survives every gate: the board is manufacturable, the
netlist is right, the packet is correct, and the part runs at 140 degC until it
does not. There is no way to find it before fabrication except arithmetic.

**How the current is known.** From the DC operating point (:mod:`verifylib.dc`)
for anything the solver reaches, and from the datasheet loads
(:mod:`verifylib.loads`) for the black boxes. Where neither knows, the part is
reported as coverage rather than assumed cool.

**What this cannot see.** Airflow, enclosure, board-to-board stacking, the
actual copper area attached to a thermal pad (a pour lives only in the gerbers,
as a region contour, and its area is not resolved here), and duty cycle — a
part that dissipates a watt for a millisecond is fine, and this treats every
load as continuous, which is the safe direction.
"""

from __future__ import annotations

from verifylib import dc
from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.loads import lookup
from verifylib.model import Board, Component
from verifylib.rules import AMBIENT_MAX_C, AMBIENT_NOMINAL_C

#: Chip-package power ratings in watts, keyed by the imperial size the land
#: pattern implies. Conservative end of what the commodity JLCPCB Basic parts
#: are rated for; a premium 0402 may be 100mW but we do not stock by rating.
CHIP_POWER_W = {
    "0201": 0.05,
    "0402": 0.0625,
    "0603": 0.10,
    "0805": 0.125,
    "1206": 0.25,
    "1210": 0.50,
    "2010": 0.75,
    "2512": 1.00,
}

#: Land-pattern bounding size (mm) for each chip package, used to infer the
#: package from geometry — circuit-json carries no footprint name. Matched to
#: the nearest entry, and only when the match is close enough to be sure.
CHIP_LAND_MM = {
    "0201": (0.9, 0.5),
    "0402": (1.55, 0.65),
    "0603": (2.2, 0.95),
    "0805": (2.85, 1.3),
    "1206": (4.0, 1.65),
    "1210": (4.0, 2.6),
    "2010": (5.6, 2.7),
    "2512": (7.2, 3.4),
}
#: How far a land pattern may sit from a tabled one and still be called that
#: package. 0402 and 0603 are 0.65mm apart on the long axis, so 0.35mm keeps
#: them distinct.
LAND_MATCH_MM = 0.35

#: Junction-to-ambient thermal resistance, degC/W, on a 2-layer board with
#: modest copper. Same table as ``circuitlib.helpers``; duplicated because this
#: package stands alone, and the tests assert the two agree.
THETA_JA_C_PER_W = {
    "SOT-223": 62.0,
    "SOT-23": 250.0,
    "SOT-89": 140.0,
    "TO-252": 92.0,
    "TO-263": 70.0,
    "SOIC-8": 120.0,
}
#: Bounding size (mm) of each power package's land pattern.
POWER_LAND_MM = {
    "SOT-23": (2.9, 2.4),
    "SOT-89": (4.6, 4.2),
    "SOT-223": (8.4, 5.7),
    "TO-252": (10.3, 6.2),
    "TO-263": (14.0, 10.4),
    "SOIC-8": (6.0, 5.2),
}
POWER_LAND_MATCH_MM = 1.2

MAX_JUNCTION_C = 125.0
#: Below this fraction of the package rating a part is fine; above it, warned.
DERATE_WARN = 0.7


def infer_package(component: Component, table: dict[str, tuple[float, float]],
                  tolerance: float) -> str | None:
    """Name the package from its land pattern, or ``None`` when unsure.

    ``None`` is the honest answer and the caller must treat it as one — a
    guessed package produces a guessed thermal resistance, and a confident
    wrong temperature is worse than no temperature.
    """
    if component.width <= 0 or component.height <= 0:
        return None
    long_axis = max(component.width, component.height)
    short_axis = min(component.width, component.height)
    best: tuple[float, str] | None = None
    for name, (tw, th) in table.items():
        error = max(abs(long_axis - tw), abs(short_axis - th))
        if best is None or error < best[0]:
            best = (error, name)
    if best is None or best[0] > tolerance:
        return None
    return best[1]


@never_raises
def _resistor_power(
    board: Board, network: dc.Network, solution: dc.Solution, unknown: list[str]
) -> list[Finding]:
    out: list[Finding] = []
    for element in network.elements:
        if element.kind != "resistor":
            continue
        component = board.by_name.get(element.refdes)
        if component is None:
            continue
        package = infer_package(component, CHIP_LAND_MM, LAND_MATCH_MM)
        if package is None:
            unknown.append(f"{element.refdes} (land pattern matches no chip size)")
            continue
        rating = CHIP_POWER_W[package]
        current = abs(solution.currents.get(element.refdes, 0.0))
        watts = current * current * element.resistance
        if watts >= rating:
            out.append(
                finding(
                    element.refdes,
                    "thermal_resistor_power",
                    f"{element.refdes} dissipates {watts * 1000:.0f}mW at the "
                    f"operating point; its land pattern is a {package}, rated "
                    f"{rating * 1000:g}mW. Use a larger package or split it",
                    "error",
                )
            )
        elif watts > rating * DERATE_WARN:
            out.append(
                finding(
                    element.refdes,
                    "thermal_resistor_power",
                    f"{element.refdes} dissipates {watts * 1000:.0f}mW against a "
                    f"{package}'s {rating * 1000:g}mW rating "
                    f"({watts / rating * 100:.0f}% of it). Chip resistors drift "
                    "and discolour long before they fail",
                    "warning",
                )
            )
    return out


@never_raises
def _regulator_heat(
    board: Board, network: dc.Network, solution: dc.Solution, unknown: list[str]
) -> list[Finding]:
    """``P = (Vin - Vout) x I``, then ``Tj = Tambient + P x theta_JA``.

    Arithmetic, not simulation, and it catches the single most common power
    mistake on a hobby board: an AMS1117 asked to drop 5V to 3.3V at half an
    amp. Unlike the circuitlib helper this reads the *built* board — the
    voltages come from the solve and the current from what is actually on the
    output rail.
    """
    out: list[Finding] = []
    for refdes, out_key, out_volts in network.regulators:
        component = board.by_name.get(refdes)
        if component is None:
            continue
        in_volts = None
        for name, net in _ports_of(board, component):
            if name.lower().startswith(("vin", "in", "vi")):
                in_volts = solution.voltages.get(net.key)
                break
        if in_volts is None or in_volts <= out_volts:
            continue
        current = sum(s.amps for s in network.sinks if s.net == out_key)
        # Anything drawing from the output rail through a resistive path too.
        for element in network.elements:
            if out_key in (element.a, element.b):
                flow = solution.currents.get(element.refdes, 0.0)
                if element.a == out_key and flow > 0:
                    current += flow
        if current <= 0:
            unknown.append(f"{refdes} (nothing identifiable draws from its output)")
            continue
        package = infer_package(component, POWER_LAND_MM, POWER_LAND_MATCH_MM)
        if package is None:
            unknown.append(f"{refdes} (land pattern matches no power package)")
            continue
        theta = THETA_JA_C_PER_W[package]
        watts = (in_volts - out_volts) * current
        junction = AMBIENT_MAX_C + watts * theta
        headroom = MAX_JUNCTION_C - junction
        if junction >= MAX_JUNCTION_C:
            out.append(
                finding(
                    refdes,
                    "thermal_regulator",
                    f"{refdes} drops {in_volts - out_volts:.2f}V at "
                    f"{current * 1000:.0f}mA — {watts:.2f}W into a {package} "
                    f"({theta:g} degC/W), reaching about {junction:.0f} degC "
                    f"junction from a {AMBIENT_MAX_C:g} degC ambient. The limit "
                    "is 125. Pour copper on the tab, drop the current, or use a "
                    "switching regulator",
                    "error",
                )
            )
        elif headroom < 30:
            out.append(
                finding(
                    refdes,
                    "thermal_regulator",
                    f"{refdes} dissipates {watts:.2f}W and reaches about "
                    f"{junction:.0f} degC junction — {headroom:.0f} degC from "
                    f"the 125 degC limit, at a {AMBIENT_MAX_C:g} degC ambient",
                    "warning",
                )
            )
        else:
            out.append(
                finding(
                    refdes,
                    "thermal_regulator",
                    f"{refdes} dissipates {watts:.2f}W at {current * 1000:.0f}mA "
                    f"and sits about {junction:.0f} degC junction, "
                    f"{headroom:.0f} degC inside the limit",
                    "info",
                )
            )
    return out


def _ports_of(board: Board, component: Component):
    for element in board.of_type("source_port"):
        if element.get("source_component_id") != component.source_id:
            continue
        net = board.net_of_port(str(element.get("source_port_id") or ""))
        if net is not None:
            yield str(element.get("name") or ""), net


@never_raises
def _hot_black_boxes(board: Board, unknown: list[str]) -> list[Finding]:
    """A part whose datasheet peak load is large enough to matter thermally,
    listed so the number is on the record even where the package is not."""
    out: list[Finding] = []
    for component in board.components:
        load = lookup(
            lcsc=component.lcsc,
            mpn=_mpn(board, component.source_id),
            ftype=component.ftype,
        )
        if load is None or load.peak_ma < 250:
            continue
        out.append(
            finding(
                component.name,
                "thermal_hot_part",
                f"{component.name} peaks at {load.peak_ma:g}mA ({load.source}). "
                "Its own dissipation depends on an internal drop this check "
                "cannot see — worth a copper pour under it regardless",
                "info",
            )
        )
    return out


def _mpn(board: Board, source_id: str) -> str | None:
    for element in board.of_type("source_component"):
        if element.get("source_component_id") == source_id:
            value = element.get("manufacturer_part_number")
            return str(value) if value else None
    return None


def check(board: Board, *, scenario: str = "resting") -> CheckResult:
    # Heat is a worst-case question, so the loads are the datasheet *peaks*,
    # not the typicals the operating point uses. They differ by six times on a
    # WS2812 chain, and grading a regulator at typical is grading the easy case.
    network = dc.build_network(board, scenario=scenario, load_mode="peak")
    solution = dc.solve(network)
    unknown: list[str] = []

    findings: list[Finding] = []
    if solution.voltages:
        findings += _resistor_power(board, network, solution, unknown)
        findings += _regulator_heat(board, network, solution, unknown)
    findings += _hot_black_boxes(board, unknown)

    resistors = [e for e in network.elements if e.kind == "resistor"]
    coverage = Coverage(
        unit="dissipating parts",
        total=len(resistors) + len(network.regulators),
        examined=len(resistors) + len(network.regulators) - len(unknown),
    )
    for item in sorted(set(unknown)):
        coverage.skip(item)
    if not solution.voltages:
        coverage.skip(
            "the board did not solve, so no current is known and nothing was "
            "graded"
        )
    coverage.skip(
        "copper area on a thermal pad — a pour exists only in the gerbers, as a "
        "region contour, and its area is not resolved"
    )
    coverage.skip("airflow, enclosure and duty cycle; every load is treated as continuous")

    return CheckResult(
        name="thermal",
        findings=findings,
        coverage=coverage,
        notes=[
            "loads are datasheet peaks, not typicals — heat is a worst-case "
            "question",
            f"junction temperatures assume a {AMBIENT_MAX_C:g} degC ambient "
            f"(a desk object in a warm room), not the {AMBIENT_NOMINAL_C:g} degC "
            "a datasheet quotes",
            "packages are inferred from the land pattern, because circuit-json "
            "carries no footprint name; an unrecognised one is reported rather "
            "than guessed",
        ],
    )
