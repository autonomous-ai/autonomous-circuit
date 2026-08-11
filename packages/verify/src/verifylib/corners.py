"""Does it still work when every part is at the edge of its tolerance?

**The gap this closes.** Every number the pipeline reasons about is nominal.
Real resistors are +/-1%, ceramics +/-10% before DC-bias derating, an LDO's
output moves +/-5% over line, load and temperature, and an LED's forward
voltage varies across a reel. Nothing we own asks whether the design still
works at the corners — only whether it works at the centre.

**Why it is nearly free.** The exchange rate makes compute worthless against a
two-week fab cycle, and a thousand solves of a hundred-net board is a fraction
of a second. This is the cheapest coverage in the package: the DC solver
already exists, so a corner sweep is the same solve with different numbers.

**How the corners are chosen.** Two passes, because neither alone is enough:

* the **deterministic corners** — every combination of tolerance *kinds* at
  their two extremes, 2^5 = 32 solves. Exhaustive is cheaper than clever here,
  and it is the only way to reach the mixed corner that actually bites: rail
  high with forward voltage low pushes an LED hardest, and neither an
  all-low nor an all-high sweep contains both.
* a **Monte-Carlo sweep** with a fixed seed, which catches the quantities that
  are not monotone and gives a distribution rather than a bound.

The verdict that matters is not "the worst case is X". It is **"this passes at
nominal and fails at a corner"** — a defect that a nominal-only check reports
as clean and a fab cycle then reveals.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from verifylib import dc
from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board
from verifylib.rules import TOLERANCE

#: Enough samples to see a 1-in-200 corner without the run being noticeable.
DEFAULT_TRIALS = 500
#: Fixed so a corner finding is reproducible. A verification result that
#: changes between runs is not a result.
SEED = 20260811


@dataclass
class Span:
    """The range a quantity covered across the corners.

    Both ends are kept, not just the one furthest from nominal. An LED that is
    already too bright at nominal has its *low* end pulled by the sweep, and
    reporting that as "the worst corner" would say the design got better.
    """

    nominal: float
    low: float
    low_corner: str
    high: float
    high_corner: str

    def update(self, value: float, label: str) -> None:
        if value < self.low:
            self.low, self.low_corner = value, label
        if value > self.high:
            self.high, self.high_corner = value, label

    def violation(self, floor: float, ceiling: float) -> tuple[float, str] | None:
        """The end that leaves the band, worst first."""
        if self.high > ceiling:
            return self.high, self.high_corner
        if self.low < floor:
            return self.low, self.low_corner
        return None


@dataclass
class Sweep:
    trials: int
    led_current_ma: dict[str, Span] = field(default_factory=dict)
    rail_volts: dict[str, tuple[float, float]] = field(default_factory=dict)
    non_convergent: int = 0


def _perturb(network: dc.Network, factor_for) -> dc.Network:
    """A copy of the network with every tolerance-bearing value moved."""
    clone = dc.Network(
        nets=network.nets,
        elements=[
            dc.Element(
                e.refdes,
                e.kind,
                e.a,
                e.b,
                e.resistance
                * (factor_for("resistor", e.refdes) if e.kind == "resistor" else 1.0),
                vf=e.vf * (factor_for("led_vf", e.refdes) if e.directional else 1.0),
                directional=e.directional,
                nvt=e.nvt,
            )
            for e in network.elements
        ],
        sinks=list(network.sinks),
        sources={
            key: volts * factor_for("rail", key)
            for key, volts in network.sources.items()
        },
        regulators=list(network.regulators),
        unmodelled=list(network.unmodelled),
        modelled=network.modelled,
        total=network.total,
    )
    return clone


def sweep(
    board: Board, *, trials: int = DEFAULT_TRIALS, scenario: str = "resting"
) -> Sweep:
    """Solve the board at nominal, at every deterministic tolerance corner,
    and at ``trials`` random ones."""
    network = dc.build_network(board, scenario=scenario)
    nominal = dc.solve(network)
    result = Sweep(trials=trials)

    led_names = [e.refdes for e in network.elements if e.kind == "simple_led"]
    for name in led_names:
        current = nominal.currents.get(name, 0.0) * 1000.0
        result.led_current_ma[name] = Span(
            current, current, "nominal", current, "nominal"
        )
    for key, volts in network.sources.items():
        solved = nominal.voltages.get(key, volts)
        result.rail_volts[key] = (solved, solved)

    def record(solution: dc.Solution, label: str) -> None:
        if not solution.converged:
            result.non_convergent += 1
            return
        for name in led_names:
            result.led_current_ma[name].update(
                solution.currents.get(name, 0.0) * 1000.0, label
            )
        for key in result.rail_volts:
            low, high = result.rail_volts[key]
            value = solution.voltages.get(key, low)
            result.rail_volts[key] = (min(low, value), max(high, value))

    # Every combination of tolerance *kinds* at their two extremes. Five kinds
    # is 32 solves, so exhaustive is cheaper than clever. Sweeping only
    # all-low and all-high misses the corner that actually bites: rail high
    # with forward voltage low pushes an LED hardest, and neither extreme has
    # both. Measured — a 170-ohm indicator that clears 20mA only in that mixed
    # corner was invisible until this replaced the two-extreme sweep.
    kinds = sorted(TOLERANCE)
    for mask in range(2 ** len(kinds)):
        directions = {
            kind: (1.0 if (mask >> i) & 1 else -1.0) for i, kind in enumerate(kinds)
        }
        label = ", ".join(
            f"{kind} {'high' if directions[kind] > 0 else 'low'}" for kind in kinds
        )

        def factor_for(kind: str, _name: str, directions=directions) -> float:
            return 1.0 + directions.get(kind, 0.0) * TOLERANCE.get(kind, 0.0)

        record(dc.solve(_perturb(network, factor_for)), label)

    rng = random.Random(SEED)
    for trial in range(trials):
        draws: dict[tuple[str, str], float] = {}

        def factor_for(kind: str, name: str, draws=draws, rng=rng) -> float:
            key = (kind, name)
            if key not in draws:
                spread = TOLERANCE.get(kind, 0.0)
                draws[key] = 1.0 + rng.uniform(-spread, spread)
            return draws[key]

        record(dc.solve(_perturb(network, factor_for)), f"random corner #{trial + 1}")
    return result


@never_raises
def _led_corner_findings(result: Sweep) -> list[Finding]:
    out: list[Finding] = []
    low, high = dc.LED_CURRENT_BAND_MA
    for name, span in sorted(result.led_current_ma.items()):
        if not low <= span.nominal <= high:
            continue  # the nominal check already said so; do not say it twice
        breach = span.violation(low, high)
        if breach is not None:
            value, corner = breach
            out.append(
                finding(
                    name,
                    "corner_led_current",
                    f"{name} is fine at nominal ({span.nominal:.2f}mA) but "
                    f"reaches {value:.2f}mA at a corner ({corner}), outside the "
                    f"{low:g}-{high:g}mA band. A nominal-only check calls this "
                    "board clean",
                    "warning",
                )
            )
        elif span.nominal > 0:
            out.append(
                finding(
                    name,
                    "corner_led_current",
                    f"{name} holds {span.nominal:.2f}mA nominal and spans "
                    f"{span.low:.2f}-{span.high:.2f}mA across the corners — "
                    f"inside the {low:g}-{high:g}mA band with margin",
                    "info",
                )
            )
    return out


@never_raises
def _rail_corner_findings(board: Board, result: Sweep) -> list[Finding]:
    out: list[Finding] = []
    for key, (low, high) in sorted(result.rail_volts.items()):
        net = board.net_by_key.get(key)
        nominal = dc.nominal_of(net) if net else None
        if net is None or nominal is None or nominal <= 0:
            continue
        spread = max(abs(high - nominal), abs(nominal - low))
        if spread / nominal > 0.10:
            out.append(
                finding(
                    net.label,
                    "corner_rail_voltage",
                    f"{net.label} swings {low:.3f}V to {high:.3f}V across the "
                    f"corners, {spread / nominal * 100:.0f}% around its "
                    f"{nominal:g}V nominal",
                    "warning",
                )
            )
    return out


def check(
    board: Board, *, trials: int = DEFAULT_TRIALS, scenario: str = "resting"
) -> CheckResult:
    result = sweep(board, trials=trials, scenario=scenario)
    solves = trials + 2 ** len(TOLERANCE)
    coverage = Coverage(unit="corner solves", examined=solves, total=solves)
    coverage.skip(
        "temperature: the solve is at 25 degC, so nothing here reflects a hot "
        "or cold board"
    )
    coverage.skip(
        "MLCC DC-bias derating, which loses more capacitance than the "
        "datasheet tolerance does"
    )
    coverage.skip("anything an unmodelled IC does differently at a corner")
    if result.non_convergent:
        coverage.skip(
            f"{result.non_convergent} corner(s) did not converge and were "
            "discarded"
        )

    findings: list[Finding] = []
    findings += _led_corner_findings(result)
    findings += _rail_corner_findings(board, result)

    spread = ", ".join(
        f"{kind} +/-{value * 100:g}%" for kind, value in sorted(TOLERANCE.items())
    )
    return CheckResult(
        name=f"corners[{scenario}]",
        findings=findings,
        coverage=coverage,
        notes=[
            f"{2 ** len(TOLERANCE)} deterministic corners (every tolerance "
            f"kind at both extremes) plus {trials} random ones, seed {SEED}",
            f"tolerances applied: {spread}",
        ],
    )
