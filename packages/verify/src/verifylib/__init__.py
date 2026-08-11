"""``verifylib`` — the verification we did not have.

Standalone checks that see things the four existing detection sources cannot.
Every one of them consumes artifacts (``circuit.json``, gerbers), never TSX, so
they sit on the substrate-agnostic side of the pipeline and could be wired in
behind any authoring front end.

The package deliberately has **no dependency on ``circuitpy`` or on the skill
runtimes** — a second opinion computed with the first opinion's code is not a
second opinion. Findings come out in the pipeline's warning shape
(``{part, kind, detail, severity}``) so wiring is a call, not a translation.

See ``docs/verification/gap-analysis.md`` for what each module attacks and why,
and ``packages/verify/README.md`` for how to run them.
"""

from __future__ import annotations

from verifylib.findings import (  # noqa: F401
    CheckResult,
    Coverage,
    Finding,
    check_failed,
    dedupe,
    finding,
    never_raises,
)
from verifylib.model import Board, Component, Net, Poly, Rect, load  # noqa: F401

__all__ = [
    "Board",
    "CheckResult",
    "Component",
    "Coverage",
    "Finding",
    "Net",
    "Poly",
    "Rect",
    "check_failed",
    "dedupe",
    "finding",
    "load",
    "never_raises",
]
