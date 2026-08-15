"""Small synthetic boards, built by hand so a test can state exactly what the
answer should be. Real boards are covered by the instance fixtures; these exist
for the cases a real board does not contain — a deliberate short, a via in a
pad, copper hanging off the edge."""

from __future__ import annotations

from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    DesignRules,
    Drill,
    Keepout,
    Net,
    Pad,
    Plane,
    Point,
    RoutingProblem,
    RoutingSolution,
    Trace,
    Via,
)

RULES = DesignRules()  # plain defaults: no circuitpy needed to build a problem


def pad(pad_id: str, net: str | None, x: float, y: float, *, w=1.0, h=1.0,
        layer=TOP, kind="smd", rotation=0.0, component="U1") -> Pad:
    return Pad(
        id=pad_id,
        net=net,
        center=Point(x, y),
        width_mm=w,
        height_mm=h,
        layers=(TOP, BOTTOM) if kind == "plated_hole" else (layer,),
        kind=kind,
        component=component,
        port_id=f"port_{pad_id}",
        rotation_deg=rotation,
    )


def two_pad_board(
    *,
    gap_mm: float = 10.0,
    rules: DesignRules | None = None,
    extra_pads: tuple[Pad, ...] = (),
    extra_nets: tuple[Net, ...] = (),
    keepouts: tuple[Keepout, ...] = (),
    drills: tuple[Drill, ...] = (),
    planes: tuple[Plane, ...] = (),
) -> RoutingProblem:
    """A 20 x 20mm board with one two-pad net ``N1`` running left to right."""
    a = pad("p1", "N1", -gap_mm / 2, 0.0)
    b = pad("p2", "N1", gap_mm / 2, 0.0)
    net = Net(
        id="N1", name="SIG", net_class="signal", pads=("p1", "p2"),
        min_width_mm=0.2, source_net_id="source_net_1",
    )
    return RoutingProblem(
        id="synthetic",
        board=Board(width_mm=20.0, height_mm=20.0),
        rules=rules or RULES,
        pads=(a, b) + extra_pads,
        drills=drills,
        keepouts=keepouts,
        planes=planes,
        nets=(net,) + extra_nets,
    )


def straight_trace(problem: RoutingProblem, *, width=0.2, layer=TOP,
                   net="N1", router="test") -> RoutingSolution:
    pads = {p.id: p for p in problem.pads if p.net == net}
    ordered = sorted(pads.values(), key=lambda p: p.center.x)
    return RoutingSolution(
        router=router,
        traces=(
            Trace(
                id="t0", net=net, layer=layer,
                points=(ordered[0].center, ordered[-1].center),
                width_mm=width,
            ),
        ),
        complete=True,
    )
