"""Quality: the part of a routed board a human EE grades and DRC cannot.

**Why this package exists.** On 2026-08-16 the routers reached zero DRC errors
on eleven real boards. On 2026-08-15 a human EE looked at the same copper and
said it was *"better than before, still quite tangled."* Both statements are
true and the gap between them is the whole problem: **we score legality and a
human scores quality.** Nothing we cannot measure can be optimised, so every
algorithm in ``packages/router/algorithms`` is currently maximising *legal and
short* and will plateau exactly there.

Six metrics, ranked by what an EE actually complains about:

===============================  =============================================
:mod:`~routerlib.quality.loop_area`   area enclosed between a trace and its return
:mod:`~routerlib.quality.reference`   does the return path disappear underneath
:mod:`~routerlib.quality.diffpair`    coupled, constant-gap, over ground
:mod:`~routerlib.quality.power`       path resistance, and tree vs daisy chain
:mod:`~routerlib.quality.detour`      detour ratio, crossings, bends
:mod:`~routerlib.quality.vias`        count, on high-speed nets, dangling
===============================  =============================================

Every one is a pure function of ``(RoutingProblem, RoutingSolution)``,
deterministic, and cheap enough that all sixteen benchmark instances score in
seconds.

**Three rules this package will not break.**

*These are scores, not gates.* No function here returns a severity or a
pass/fail. ``fab.ready`` remains zero error-severity findings plus independently
verified gerbers, and adding a soft metric to a hard gate would destroy the one
property that makes the gate worth having.

*Every score carries its ruler.* :class:`~routerlib.quality.report.QualityRuler`
hashes the sampling step, the reference window, the coupling window and the
copper constants. Change any and the numbers move without the boards moving —
that is a **new baseline**, never an improvement. The same discipline
``routerlib.scoring.Ruler`` already enforces for legality.

*No invented thresholds.* Where a real number exists it is cited: USB 2.0's
3.8mm intra-pair skew budget, IPC-2221B's 1oz copper thickness, JLCPCB's 25um
barrel plating. Where none exists — loop area, detour ratio, gap constancy —
the raw number is reported and boards are ranked against each other. A
threshold we made up would be a pass mark that means nothing, which is worse
than no pass mark at all.

    from routerlib.quality import measure
    report = measure(problem, solution)
    print(report.ruler_line())
    print(report.summary()["detourRatio"])

## What the first calibration found, 2026-08-16

269 copper sets scored in 84 seconds — 14 families x 16 instances from the
tournament, 3 families re-run against the corrected pad model, and the shipped
autorouter's own copper out of 13 built boards. Ruler ``b7029fbf41df``; results
in ``benchmarks/tournament/quality-2026-08-16.json``, rebuilt by
``python3.12 -m routerlib.quality table``.

**The comparison has to be on the nets both sides connected.** Our routers leave
nets unrouted, so they lay less copper, enclose less loop area and drill fewer
vias — a router that routes nothing wins every absolute column. ``render
--mode paired`` restricts to the intersection and prints both connected counts
beside it. Every number below is from that restriction, over the 12 boards
where the incumbent's placement matches the benchmark instance byte for byte.

**Four metrics separate the incumbent from every family; two do not.**

======================  ==================================================
detour ratio            **discriminates, decisively.** 15 of 16 families
                        route the same nets shorter than the incumbent:
                        ``maze-astar`` 1.07 against 1.32. And it separates
                        in both directions — ``topological-graph``, the
                        family with the *least* copper, is the worst at
                        1.62. It is not a proxy for "routed less"
bends                   **discriminates**, and hardest: 595 against 2139
                        for ``maze-astar`` on the same nets. Rank
                        correlation with detour ratio is only +0.13, so it
                        is a second axis and not the same one
vias                    **discriminates**: 182 against 360. Already in
                        ``routerlib.scoring``; kept here for the
                        breakdown, not the total
dangling vias           **discriminates, against us.** The incumbent leaves
                        zero on all 13 boards; our families leave up to 36.
                        The only column where it beats every family
                        outright
loop area (absolute)    **confounded.** Rank-correlates +0.92 with the
                        number of nets connected. On the common-net set it
                        still favours us (5357 against 6832) but the whole
                        difference is length: see the next row
mean return distance    **does not discriminate.** 5 boards better, 6
                        worse, median ratio 1.03 against the incumbent —
                        and the same against our worst family. Loop area
                        is length times this, and this does not move, so
                        loop area is detour in different units
referenced fraction     **does not discriminate** either: 0.10 to 0.19 for
                        every router on every plane-less board, median
                        ratio 1.02
======================  ==================================================

**Nothing was cut, and here is why that is not a dodge.** The two
non-discriminating metrics are the two the brief ranked first, and the reason
they are flat is worth more than the metrics would have been: **on a 2-layer
board with no pour, the return path is set by the board template and no router
can move it.** The proof is in the fixtures — the same boards with a synthesised
ground plane (``hydrate-coaster-plane``, ``terminal-keyboard-plane``) drop from
a 2.4-32mm mean return to **0.8-1.6mm** and rise from 5-19% referenced to
**62-100%**, and there the families spread over a 1.8x range. So the metric
works; it is measuring a decision nobody has made yet.

Two consequences, and the second is the important one:

* **Do not put loop area or referenced fraction in a router's objective.**
  Optimising a number the router cannot move is worse than not measuring it.
  They are board-template diagnostics and the report labels them as such.
* **Reference *continuity* survives where reference *coverage* does not.**
  ``gap_crossings`` separates cleanly (8 boards better, 1 worse), because how
  often the return disappears *is* a routing choice even when how much of it
  exists is not.

**And the headline, which is not the one the brief expected.** The question was
whether these metrics rank the incumbent above us, the quantitative version of
*"still quite tangled"*. **They rank it below us.** On the same nets, on every
matched board, our copper is shorter, bendier-free and carries fewer vias than
the shipped autorouter's. That is not a contradiction of the EE: the board he
reviewed was routed by the incumbent — ``CIRCUIT_ROUTER`` is off by default —
so "still quite tangled" was said *about the incumbent's copper*, and the
metrics agree with him about exactly that board.

What it does mean is that **tangle is no longer the reason to prefer the
incumbent.** Completeness is, and it is the whole gap
(``docs/architecture/routing.md``, *What is still worse*). And inside our own
families it reorders things: ``pathfinder-negotiated``, the portfolio's default
lead, is our **worst** family on detour (1.29) and the only one bendier than the
incumbent (2349 against 2217), while ``maze-astar`` — already the strongest on
legality since the pad model was corrected — is the tidiest by a wide margin.
That is a portfolio question, not a quality-metrics question, and it is left
where it belongs.
"""

from __future__ import annotations

from routerlib.quality.common import (
    REFERENCE_MM,
    SAMPLE_STEP_MM,
    GroundField,
    ground_net_ids,
)
from routerlib.quality.report import (
    COVERAGE_GAPS,
    QUALITY_VERSION,
    QualityReport,
    QualityRuler,
    measure,
    ruler_for,
)

__all__ = [
    "COVERAGE_GAPS",
    "GroundField",
    "QUALITY_VERSION",
    "QualityReport",
    "QualityRuler",
    "REFERENCE_MM",
    "SAMPLE_STEP_MM",
    "ground_net_ids",
    "measure",
    "ruler_for",
]
