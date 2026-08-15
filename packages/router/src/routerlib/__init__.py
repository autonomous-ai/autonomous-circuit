"""routerlib — the contract, the benchmark and the scorer for PCB autorouting.

The one-line version::

    def route(problem: RoutingProblem, budget: Budget) -> RoutingSolution

Everything else in this package exists to make that line measurable: adapters
so a real board becomes a problem and a solution becomes a board again, a
benchmark of instances stripped out of boards we have built, and a scorer that
judges completeness, then legality, then quality, then cost — in that order,
because that is the order in which a defect costs money.

See ``packages/router/README.md`` for the contract in prose.
"""

from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    Budget,
    BudgetMeter,
    DesignRules,
    Drill,
    Keepout,
    Net,
    Pad,
    Plane,
    Point,
    Router,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
    empty_solution,
)

__version__ = "0.1.0"

__all__ = [
    "BOTTOM",
    "Board",
    "Budget",
    "BudgetMeter",
    "DesignRules",
    "Drill",
    "Keepout",
    "Net",
    "Pad",
    "Plane",
    "Point",
    "Router",
    "RoutingProblem",
    "RoutingSolution",
    "TOP",
    "Trace",
    "Via",
    "__version__",
    "empty_solution",
]
