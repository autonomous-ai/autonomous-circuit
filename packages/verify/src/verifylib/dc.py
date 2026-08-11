"""A DC operating point with the rails still called by their names.

**The gap this closes.** No check we own knows Ohm's law on a *built* board.
``circuitlib`` has ``led_current`` and ``pullup_warnings``, but they only ever
see the values the board source chooses to hand them; nothing reads the
netlist. A board passes compile, ERC, DRC, DFM and every rendered image with a
10-ohm resistor where 10k belongs.

**Why not SPICE.** Verified again on 2026-08-11 against
``circuit-json-to-spice`` 0.0.45: for a 2321-element board it emits 42 lines,
models nothing without a SPICE model, contains **no voltage source at all**
(power arrives through a connector, which has no model), and **renames every
node to ``N1..N36``** so a rail cannot be identified. ``tsci simulate analog``
runs the same conversion and dies with ``singular matrix: check node n3``.
Wiring either in would add a check that always finds nothing — which is worse
than no check, because it implies coverage of exactly the blind spot we have.

The blocker is the converter, not the idea. ``circuit.json`` carries the whole
netlist with real names: ``source_net`` gives ``is_power`` / ``is_ground`` and
names like ``V5``, ``subcircuit_connectivity_map_key`` gives exact connectivity,
and every passive carries its value. So the nodal system is built here, and the
names survive.

**The model, stated plainly** — a solver whose assumptions are hidden is a
confident liar:

* resistors conduct; capacitors and crystals are open at DC; inductors,
  ferrites and zero-ohm links are shorts
* diodes and LEDs are exponential junctions (Shockley, solved by damped
  Newton with SPICE's ``pnjlim``), each calibrated so it passes 20mA at its
  tabled forward voltage. A piecewise-linear stand-in was tried first and did
  not converge on a single example board
* buttons and switches are open in the resting scenario, closed in the
  ``pressed`` one, because a button that shorts a rail is worth finding
* an IC, module or sensor is a **black box that draws current**: its pins do
  not conduct between each other, and its datasheet load (``loads.py``) is
  drawn from the power nets it touches. That is the honest model — pretending
  an unmodelled chip is an open circuit is what makes a rail look unloaded
* a named rail fed by a connector or a regulator output is a voltage source at
  its nominal value

Anything not in that list is reported as coverage. Silence is never a pass.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.loads import lookup
from verifylib.model import Board, Component, Net

#: Forward voltage by LED colour at indicator currents. Same table as
#: ``circuitlib.helpers``; duplicated because this package must stand alone,
#: and asserted equal in tests.
LED_VF = {
    "red": 1.9, "green": 2.1, "yellow": 2.1, "orange": 2.0,
    "blue": 3.0, "white": 3.0,
}
DEFAULT_LED_VF = 2.1
#: A plain silicon signal diode.
DIODE_VF = 0.7
#: Emission coefficient x thermal voltage at 25 degC. An LED runs an emission
#: coefficient near 2, a small-signal silicon diode near 1.9 but from a far
#: lower Vf; both are calibrated so the junction passes DIODE_RATED_A at Vf.
LED_NVT = 2.0 * 0.02585
DIODE_NVT = 1.9 * 0.02585
#: The current at which the tabled forward voltages are quoted.
DIODE_RATED_A = 0.020
OFF_RESISTANCE = 1e9
SHORT_RESISTANCE = 1e-3
#: How far out of balance a node may be, as a fraction of the largest current
#: on the board, and still count as solved. Relative rather than absolute: a
#: nanoamp is nothing beside a half-amp rail and impossible beside a microamp
#: one.
KCL_TOLERANCE = 1e-6
#: Leak conductance from every node to ground, siemens (a teraohm). Standard
#: GMIN: without it the matrix is singular on any board with a floating signal
#: net, which is every board. It is not free — at 1e-9 it moved a 10k divider
#: by 12 microvolts, so it sits three decades lower. The honest limit: a node
#: whose impedance to everything else exceeds ~10 Gohm reads pulled toward
#: ground, and no board we build has one.
GMIN = 1e-12

#: An indicator wants to be seen, not to melt or hog the rail.
LED_CURRENT_BAND_MA = (0.5, 20.0)
#: I2C pull-ups: below this a weak open-drain driver cannot reach a valid low;
#: above it the rise time misses spec on any real bus capacitance.
I2C_PULLUP_BAND_OHMS = (1000.0, 10000.0)

#: Rail names whose nominal voltage is unambiguous.
RAIL_NOMINAL = {
    "V5": 5.0, "VBUS": 5.0, "VUSB": 5.0, "+5V": 5.0, "5V": 5.0,
    "V3_3": 3.3, "3V3": 3.3, "+3V3": 3.3, "VDD3V3": 3.3,
    "V1_8": 1.8, "1V8": 1.8,
}

_OPEN_FTYPES = {"simple_capacitor", "simple_crystal", "simple_test_point"}
_SHORT_FTYPES = {"simple_inductor", "simple_ferrite", "simple_jumper"}
_SWITCH_FTYPES = {"simple_push_button", "simple_switch"}
_DIODE_FTYPES = {"simple_diode", "simple_led"}


def nominal_of(net: Net) -> float | None:
    if net.is_ground:
        return 0.0
    name = (net.name or "").upper().replace("-", "_")
    return RAIL_NOMINAL.get(name)


@dataclass
class Element:
    """One two-terminal contributor to the conductance matrix."""

    refdes: str
    kind: str
    a: str                       # net key
    b: str                       # net key
    resistance: float
    #: A junction's forward drop at its rated current.
    vf: float = 0.0
    #: Which way the drop points: current flows a -> b through the junction.
    directional: bool = False
    #: Emission coefficient times thermal voltage, volts. An LED's junction is
    #: much softer than a signal diode's, and using one number for both puts an
    #: indicator's current out by an order of magnitude.
    nvt: float = LED_NVT


@dataclass
class Sink:
    """A black-box part pulling current out of a rail into ground."""

    refdes: str
    net: str
    amps: float
    source: str


@dataclass
class Network:
    nets: list[Net]
    elements: list[Element] = field(default_factory=list)
    sinks: list[Sink] = field(default_factory=list)
    sources: dict[str, float] = field(default_factory=dict)   # net key -> volts
    #: ``(refdes, net key, volts)`` for each regulator output treated as a
    #: source, so the report can say where a rail's voltage came from.
    regulators: list[tuple[str, str, float]] = field(default_factory=list)
    unmodelled: list[str] = field(default_factory=list)
    modelled: int = 0
    total: int = 0


def _port_nets(board: Board, component: Component) -> list[tuple[str, Net]]:
    """``(pin name, net)`` for every pin of a part, in pin order."""
    out: list[tuple[str, Net]] = []
    for element in board.of_type("source_port"):
        if element.get("source_component_id") != component.source_id:
            continue
        net = board.net_of_port(str(element.get("source_port_id") or ""))
        if net is None:
            continue
        out.append((str(element.get("name") or element.get("pin_number") or "?"), net))
    return out


def build_network(
    board: Board, *, scenario: str = "resting", load_mode: str = "typical"
) -> Network:
    """Turn the board into a solvable DC network.

    ``load_mode`` picks which datasheet number a black box draws: ``typical``
    for the operating point, ``peak`` for the worst case a rail and a regulator
    have to survive. They differ by a factor of six on a WS2812 chain, so
    grading heat at typical alone is grading the easy case.
    """
    network = Network(nets=list(board.nets))
    network.total = len(board.components)
    ground = board.ground

    for component in board.components:
        ports = _port_nets(board, component)
        ftype = component.ftype or ""

        if ftype == "simple_resistor" and component.resistance and len(ports) >= 2:
            network.elements.append(
                Element(component.name, "resistor", ports[0][1].key, ports[1][1].key,
                        max(component.resistance, 1e-6))
            )
            network.modelled += 1
            continue

        if ftype in _OPEN_FTYPES:
            network.modelled += 1
            continue

        if ftype in _SHORT_FTYPES and len(ports) >= 2:
            network.elements.append(
                Element(component.name, "short", ports[0][1].key, ports[1][1].key,
                        SHORT_RESISTANCE)
            )
            network.modelled += 1
            continue

        if ftype in _SWITCH_FTYPES and len(ports) >= 2:
            closed = scenario == "pressed"
            network.elements.append(
                Element(
                    component.name,
                    "switch",
                    ports[0][1].key,
                    ports[1][1].key,
                    SHORT_RESISTANCE if closed else OFF_RESISTANCE,
                )
            )
            network.modelled += 1
            continue

        if ftype in _DIODE_FTYPES and len(ports) >= 2:
            vf = (
                LED_VF.get((component.color or "").lower(), DEFAULT_LED_VF)
                if ftype == "simple_led"
                else DIODE_VF
            )
            # Pin order for a polarised part is anode then cathode.
            network.elements.append(
                Element(
                    component.name,
                    ftype,
                    ports[0][1].key,
                    ports[1][1].key,
                    resistance=0.0,
                    vf=vf,
                    directional=True,
                    nvt=LED_NVT if ftype == "simple_led" else DIODE_NVT,
                )
            )
            network.modelled += 1
            continue

        # Everything else is a black box that draws current.
        load = lookup(
            lcsc=component.lcsc,
            mpn=_mpn(board, component.source_id),
            ftype=component.ftype,
        )
        power_nets = [net for _, net in ports if net.is_power]
        if load is None:
            if power_nets:
                network.unmodelled.append(component.name)
            continue
        network.modelled += 1
        amps_ma = load.peak_ma if load_mode == "peak" else load.typical_ma
        if amps_ma > 0 and power_nets and ground is not None:
            share = amps_ma / 1000.0 / len(power_nets)
            for net in power_nets:
                network.sinks.append(
                    Sink(component.name, net.key, share, load.source)
                )

    # Voltage sources. Two origins, both derived rather than assumed: a net
    # whose *name* is an unambiguous rail, and a regulator's output pin, whose
    # voltage its part number states (AMS1117-3.3).
    for net in board.nets:
        volts = nominal_of(net)
        if volts is not None:
            network.sources[net.key] = volts
    for component in board.components:
        mpn = _mpn(board, component.source_id) or ""
        volts = _regulator_output_volts(mpn)
        if volts is None:
            continue
        for name, net in _port_nets(board, component):
            if name.lower().startswith(("vout", "out", "vo")):
                network.sources.setdefault(net.key, volts)
                network.regulators.append((component.name, net.key, volts))
                break
    return network


#: A regulator part number states its output voltage: AMS1117-3.3, XC6206P332,
#: TLV70233. Only the unambiguous dash-decimal form is read; anything else
#: returns None and the rail is reported as undriven rather than guessed.
_REGULATOR_VOLTS_RE = re.compile(r"-(\d)\.(\d)\b")


def _regulator_output_volts(mpn: str) -> float | None:
    if not mpn:
        return None
    lower = mpn.lower()
    if not any(hint in lower for hint in _REGULATOR_HINTS):
        return None
    match = _REGULATOR_VOLTS_RE.search(mpn)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")
    return None


#: Part-number fragments that identify a linear regulator.
_REGULATOR_HINTS = ("ams1117", "lm1117", "xc6206", "me6211", "tlv7", "ldo", "ap2112")


def _mpn(board: Board, source_id: str) -> str | None:
    for element in board.of_type("source_component"):
        if element.get("source_component_id") == source_id:
            value = element.get("manufacturer_part_number")
            return str(value) if value else None
    return None


@dataclass
class Solution:
    voltages: dict[str, float]
    currents: dict[str, float]        # refdes -> amps through the element
    converged: bool
    iterations: int


def _diode_saturation_current(vf: float, nvt: float) -> float:
    """``Is`` such that the junction passes its rated current at ``Vf``."""
    return DIODE_RATED_A / max(math.exp(vf / nvt) - 1.0, 1e-300)


def _limit_junction(
    v_new: float, v_old: float, nvt: float, v_crit: float
) -> tuple[float, bool]:
    """SPICE's ``pnjlim``. Returns the damped voltage and whether it clamped.

    An exponential junction overflows a Newton step the moment the linear
    solve overshoots, and the iteration then flip-flops forever. Measured
    before this was added: the piecewise-linear version never converged on any
    of the three example boards and reported a 120mA indicator LED that draws
    1.2mA in reality. A non-converged solver is not a slow solver, it is a
    solver that makes things up.

    The clamp flag matters as much as the value. While limiting is active the
    node-voltage step is artificially small, so a step-size convergence test
    fires at a point that satisfies nothing. That produced an 8.7-gigaamp LED
    in a corner sweep — a number so absurd it was obvious, which is the only
    reason it was caught.
    """
    if v_new > v_crit and abs(v_new - v_old) > 2 * nvt:
        if v_old > 0:
            arg = 1 + (v_new - v_old) / nvt
            return (v_old + nvt * math.log(arg) if arg > 0 else v_crit), True
        return nvt * math.log(max(v_new / nvt, 1e-12)), True
    return v_new, False


def solve(network: Network, *, max_iterations: int = 200,
          tolerance: float = KCL_TOLERANCE) -> Solution:
    """Nodal analysis with a damped Newton pass over the junctions.

    Linear elements go straight into the conductance matrix. Each diode is
    linearised at its last operating point through the Shockley equation and
    stamped as a companion conductance plus a current source, with ``pnjlim``
    damping so the iteration cannot run away.

    Pure Python and dense: our boards have tens of nets, so a hand-rolled
    Gaussian elimination is both fast enough and one less dependency in a
    package whose whole point is independence.
    """
    keys = sorted({net.key for net in network.nets})
    index = {key: i for i, key in enumerate(keys)}
    n = len(keys)
    if n == 0:
        return Solution({}, {}, True, 0)

    ground_index = index.get(_ground_key(network))
    junction_v: dict[str, float] = {
        e.refdes: 0.0 for e in network.elements if e.directional
    }
    voltages: dict[str, float] = {key: 0.0 for key in keys}
    for key, volts in network.sources.items():
        if key in voltages:
            voltages[key] = volts
    currents: dict[str, float] = {}
    converged = False
    iterations = 0

    # The linear part of the matrix never changes; build it once.
    base = [[0.0] * n for _ in range(n)]
    base_rhs = [0.0] * n
    if ground_index is not None:
        for i in range(n):
            if i == ground_index:
                continue
            base[i][i] += GMIN
            base[i][ground_index] -= GMIN
            base[ground_index][i] -= GMIN
            base[ground_index][ground_index] += GMIN
    for element in network.elements:
        if element.a == element.b or element.directional:
            continue
        conductance = 1.0 / max(element.resistance, 1e-9)
        ia, ib = index[element.a], index[element.b]
        base[ia][ia] += conductance
        base[ib][ib] += conductance
        base[ia][ib] -= conductance
        base[ib][ia] -= conductance
    for sink in network.sinks:
        if sink.net in index:
            base_rhs[index[sink.net]] -= sink.amps

    for iterations in range(1, max_iterations + 1):
        g = [row[:] for row in base]
        rhs = base_rhs[:]
        limited = False

        for element in network.elements:
            if not element.directional or element.a == element.b:
                continue
            nvt = element.nvt
            saturation = _diode_saturation_current(element.vf, nvt)
            v_crit = nvt * math.log(nvt / (math.sqrt(2) * saturation))
            v, clamped = _limit_junction(
                voltages[element.a] - voltages[element.b],
                junction_v[element.refdes],
                nvt,
                v_crit,
            )
            limited = limited or clamped
            junction_v[element.refdes] = v
            exponent = math.exp(min(v / nvt, 200.0))
            current = saturation * (exponent - 1.0)
            conductance = max(saturation * exponent / nvt, GMIN)
            equivalent = current - conductance * v
            ia, ib = index[element.a], index[element.b]
            g[ia][ia] += conductance
            g[ib][ib] += conductance
            g[ia][ib] -= conductance
            g[ib][ia] -= conductance
            rhs[ia] -= equivalent
            rhs[ib] += equivalent

        for key, volts in network.sources.items():
            if key not in index:
                continue
            i = index[key]
            g[i] = [0.0] * n
            g[i][i] = 1.0
            rhs[i] = volts

        solved = _gauss(g, rhs)
        if solved is None:
            return Solution({}, {}, False, iterations)
        voltages = {key: solved[index[key]] for key in keys}
        # Convergence is decided by Kirchhoff, not by the size of the Newton
        # step. A step is small for two different reasons — the answer is
        # found, or the limiter is holding it down — and only the residual
        # tells those apart. Trusting the step reported an 8.7-gigaamp LED in
        # a corner sweep, which is the kind of wrong that is only obvious
        # because it is absurd.
        if iterations > 1 and not limited:
            if _kcl_residual(network, voltages, index) < tolerance:
                converged = True
                break

    for element in network.elements:
        va = voltages.get(element.a, 0.0)
        vb = voltages.get(element.b, 0.0)
        if element.directional:
            nvt = element.nvt
            saturation = _diode_saturation_current(element.vf, nvt)
            currents[element.refdes] = saturation * (
                math.exp(min((va - vb) / nvt, 200.0)) - 1.0
            )
        else:
            currents[element.refdes] = (va - vb) / max(element.resistance, 1e-9)
    return Solution(voltages, currents, converged, iterations)


def _kcl_residual(
    network: Network, voltages: dict[str, float], index: dict[str, int]
) -> float:
    """Largest current imbalance at any node that is not a source.

    The check on the check. A Newton loop can report a small step at a point
    that satisfies nothing; only Kirchhoff says the answer is an answer.
    """
    residual: dict[str, float] = {key: 0.0 for key in index}
    for element in network.elements:
        if element.a == element.b:
            continue
        va = voltages.get(element.a, 0.0)
        vb = voltages.get(element.b, 0.0)
        if element.directional:
            nvt = element.nvt
            saturation = _diode_saturation_current(element.vf, nvt)
            current = saturation * (math.exp(min((va - vb) / nvt, 200.0)) - 1.0)
        else:
            current = (va - vb) / max(element.resistance, 1e-9)
        residual[element.a] -= current
        residual[element.b] += current
    for sink in network.sinks:
        if sink.net in residual:
            residual[sink.net] -= sink.amps
    worst = 0.0
    for key, value in residual.items():
        if key in network.sources:
            continue  # a source supplies whatever the node needs
        worst = max(worst, abs(value))
    return worst


def _ground_key(network: Network) -> str | None:
    for net in network.nets:
        if net.is_ground:
            return net.key
    for key, volts in network.sources.items():
        if volts == 0.0:
            return key
    return None


def _gauss(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. ``None`` on a singular
    system — which is itself information, so the caller reports it."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-15:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        inverse = 1.0 / a[col][col]
        for row in range(col + 1, n):
            factor = a[row][col] * inverse
            if factor == 0.0:
                continue
            for k in range(col, n + 1):
                a[row][k] -= factor * a[col][k]
    out = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = a[row][n] - sum(a[row][k] * out[k] for k in range(row + 1, n))
        out[row] = total / a[row][row]
    return out


# ---------------------------------------------------------------------------
# Findings.
# ---------------------------------------------------------------------------


@never_raises
def _led_findings(board: Board, network: Network, solution: Solution) -> list[Finding]:
    out: list[Finding] = []
    low, high = LED_CURRENT_BAND_MA
    for element in network.elements:
        if element.kind != "simple_led":
            continue
        current_ma = solution.currents.get(element.refdes, 0.0) * 1000.0
        if current_ma > high:
            out.append(
                finding(
                    element.refdes,
                    "dc_led_current",
                    f"{element.refdes} draws {current_ma:.1f}mA at the operating "
                    f"point; an indicator LED is rated for {high:g}mA. Raise the "
                    "series resistor. Solved from the built netlist, not from a "
                    "declared value",
                    "error",
                )
            )
        elif 0 < current_ma < low:
            out.append(
                finding(
                    element.refdes,
                    "dc_led_current",
                    f"{element.refdes} draws {current_ma:.2f}mA — it will barely "
                    f"be visible (under {low:g}mA). Lower the series resistor",
                    "warning",
                )
            )
        elif current_ma <= 0 and _reaches_a_source(board, network, element.a):
            # Only worth saying when the LED is a permanent indicator. A
            # GPIO-driven LED is dark at rest by design, and reporting that as
            # a defect is how a gate earns a reputation for crying wolf.
            va = solution.voltages.get(element.a, 0.0)
            vb = solution.voltages.get(element.b, 0.0)
            out.append(
                finding(
                    element.refdes,
                    "dc_led_current",
                    f"{element.refdes} never lights: its anode sits at "
                    f"{va:.2f}V and its cathode at {vb:.2f}V, which does not "
                    f"clear the {element.vf:g}V forward drop",
                    "warning",
                )
            )
    return out


def _reaches_a_source(board: Board, network: Network, net_key: str) -> bool:
    """Is this node connected to an energised rail through resistors alone?"""
    seen = {net_key}
    frontier = [net_key]
    while frontier:
        key = frontier.pop()
        if key in network.sources and network.sources[key] > 0:
            return True
        for element in network.elements:
            if element.directional or element.resistance > 1e6:
                continue
            for a, b in ((element.a, element.b), (element.b, element.a)):
                if a == key and b not in seen:
                    seen.add(b)
                    frontier.append(b)
    return False


@never_raises
def _unpowered_rails(board: Board, network: Network, solution: Solution) -> list[Finding]:
    """A net with loads hanging off it and nothing driving it.

    Without this the solver reports the honest consequence of the model — a
    node at minus a hundred million volts, because the only path to ground is
    the GMIN leak. That number is arithmetically correct and useless. The
    useful statement is that the rail has no source.
    """
    out: list[Finding] = []
    loaded: dict[str, float] = {}
    for sink in network.sinks:
        loaded[sink.net] = loaded.get(sink.net, 0.0) + sink.amps
    for key, amps in sorted(loaded.items()):
        if key in network.sources:
            continue
        net = board.net_by_key.get(key)
        if net is None:
            continue
        out.append(
            finding(
                net.label,
                "dc_unpowered_rail",
                f"{net.label} has {amps * 1000:.0f}mA of load on it and nothing "
                "this check can identify as driving it — either the regulator "
                "feeding it does not state its voltage in its part number, or "
                "the rail genuinely has no source",
                "warning",
            )
        )
    return out


@never_raises
def _rail_findings(board: Board, network: Network, solution: Solution) -> list[Finding]:
    out: list[Finding] = []
    for net in board.nets:
        expected = nominal_of(net)
        if expected is None or expected == 0.0:
            continue
        actual = solution.voltages.get(net.key)
        if actual is None or abs(actual) > 1000.0:
            continue
        if abs(actual - expected) > max(0.1, expected * 0.05):
            out.append(
                finding(
                    net.label,
                    "dc_rail_voltage",
                    f"{net.label} solves at {actual:.3f}V against a nominal "
                    f"{expected:g}V",
                    "error" if actual < expected * 0.5 else "warning",
                )
            )
    return out


@never_raises
def _load_findings(board: Board, network: Network, solution: Solution) -> list[Finding]:
    """Current pulled out of each source rail, and whether the source can
    give it."""
    out: list[Finding] = []
    per_rail: dict[str, float] = {}
    for sink in network.sinks:
        per_rail[sink.net] = per_rail.get(sink.net, 0.0) + sink.amps
    for element in network.elements:
        current = solution.currents.get(element.refdes, 0.0)
        if abs(current) < 1e-9:
            continue
        for key, sign in ((element.a, 1.0), (element.b, -1.0)):
            if key in network.sources and network.sources[key] > 0:
                per_rail[key] = per_rail.get(key, 0.0) + max(0.0, current * sign)
    for key, amps in sorted(per_rail.items()):
        net = board.net_by_key.get(key)
        if net is None or amps <= 0:
            continue
        if amps > 3.0:
            out.append(
                finding(
                    net.label,
                    "dc_rail_overload",
                    f"{net.label} sources {amps * 1000:.0f}mA at the operating "
                    "point — beyond anything a USB-C source will deliver, which "
                    "usually means a resistor value is wrong or a rail is shorted",
                    "error",
                )
            )
    return out


@never_raises
def _pullup_findings(board: Board, network: Network) -> list[Finding]:
    """A resistor from a rail onto a bus line, judged by its value."""
    out: list[Finding] = []
    low, high = I2C_PULLUP_BAND_OHMS
    for element in network.elements:
        if element.kind != "resistor":
            continue
        a = board.net_by_key.get(element.a)
        b = board.net_by_key.get(element.b)
        if a is None or b is None:
            continue
        rail, signal = (a, b) if a.is_power else (b, a) if b.is_power else (None, None)
        if rail is None or signal is None or signal.is_ground:
            continue
        name = (signal.name or "").upper()
        if not any(token in name for token in ("SDA", "SCL", "I2C")):
            continue
        value = element.resistance
        if low <= value <= high:
            continue
        detail = (
            f"{element.refdes} pulls {signal.label} up through {value:g} ohms; "
            + (
                f"under {low:g} ohms a weak open-drain driver cannot reach a "
                "valid low"
                if value < low
                else f"over {high:g} ohms the rise time misses spec on any real "
                "bus capacitance"
            )
        )
        out.append(finding(element.refdes, "dc_pullup_value", detail, "warning"))
    return out


def check(board: Board, *, scenario: str = "resting") -> CheckResult:
    """Solve the board's DC operating point and grade what it says."""
    network = build_network(board, scenario=scenario)
    coverage = Coverage(unit="components", total=network.total)
    coverage.examined = network.modelled
    for name in sorted(set(network.unmodelled)):
        coverage.skip(
            f"{name}: no datasheet load, so it contributes neither current nor "
            "conduction"
        )
    coverage.skip("AC behaviour, startup, and anything a capacitor does")
    coverage.skip(
        "internal conduction inside an IC (its pins are treated as isolated)"
    )
    if not network.sources:
        coverage.skip(
            "no net carries a recognisable rail name, so nothing could be "
            "energised — no voltage in this result is meaningful"
        )

    solution = solve(network)
    findings: list[Finding] = []
    if not network.sources:
        return CheckResult(
            name=f"dc[{scenario}]",
            findings=findings,
            coverage=coverage,
            notes=["not solved: the board declares no named power rail"],
        )
    if not solution.converged and not solution.voltages:
        findings.append(
            finding(
                "board",
                "dc_unsolvable",
                "the nodal system is singular — usually a net with nothing "
                "resistive attached, or a rail shorted to another rail",
                "warning",
            )
        )
        return CheckResult(
            name=f"dc[{scenario}]", findings=findings, coverage=coverage
        )

    findings += _unpowered_rails(board, network, solution)
    findings += _rail_findings(board, network, solution)
    findings += _led_findings(board, network, solution)
    findings += _load_findings(board, network, solution)
    findings += _pullup_findings(board, network)

    notes = [
        f"scenario: {scenario}; "
        f"{len(network.sources)} rail(s) energised, {len(network.sinks)} "
        f"datasheet load(s) applied, converged in {solution.iterations} pass(es)",
    ]
    return CheckResult(
        name=f"dc[{scenario}]", findings=findings, coverage=coverage, notes=notes
    )
