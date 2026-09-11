"""DSN out, SES in: the file both ways, on a board small enough to read."""

from __future__ import annotations

import re

from routerlib import specctra
from routerlib.model import (
    BOTTOM,
    TOP,
    Board,
    DesignRules,
    Drill,
    Keepout,
    Net,
    Pad,
    Point,
    RoutingProblem,
)


def _problem() -> RoutingProblem:
    rules = DesignRules(signal_trace_mm=0.2, power_trace_mm=0.5, target_clearance_mm=0.15)
    pads = (
        Pad(id="p1", net="n_sig", center=Point(-5, 0), width_mm=1, height_mm=0.6, layers=(TOP,)),
        Pad(id="p2", net="n_sig", center=Point(5, 0), width_mm=1, height_mm=0.6, layers=(TOP,), rotation_deg=90),
        Pad(id="p3", net="n_gnd", center=Point(-5, 5), width_mm=1.2, height_mm=1.2, layers=(TOP, BOTTOM),
            kind="plated_hole", shape="circle"),
        Pad(id="p4", net="n_gnd", center=Point(5, 5), width_mm=1.2, height_mm=1.2, layers=(TOP, BOTTOM),
            kind="plated_hole", shape="circle"),
        Pad(id="p5", net=None, center=Point(0, -5), width_mm=1, height_mm=1, layers=(BOTTOM,)),
    )
    nets = (
        Net(id="n_sig", name="SIG", net_class="signal", pads=("p1", "p2"), min_width_mm=0.2),
        Net(id="n_gnd", name="GND", net_class="ground", pads=("p3", "p4"), min_width_mm=0.5),
        Net(id="n_lone", name="LONE", net_class="signal", pads=("p5",), min_width_mm=0.2),
    )
    return RoutingProblem(
        id="tiny",
        board=Board(width_mm=20, height_mm=20),
        rules=rules,
        pads=pads,
        drills=(Drill(id="mount", center=Point(8, -8), width_mm=2.2, height_mm=2.2, plated=False),),
        keepouts=(Keepout(id="k1", center=Point(0, 8), width_mm=4, height_mm=1),),
        nets=nets,
    )


def test_the_design_names_layers_outline_pads_nets_and_classes():
    dsn = specctra.write_dsn(_problem())
    assert "(layer F.Cu (type signal)" in dsn and "(layer B.Cu (type signal)" in dsn
    assert "(boundary (path pcb 0 -10000 -10000 10000 -10000 10000 10000 -10000 10000 -10000 -10000))" in dsn
    # every pad is its own one-pin component at the origin, pin at the pad
    assert "(component IMG_p1 (place p1 0 0 front 0))" in dsn
    assert "(image IMG_p1 (pin PS0 1 -5000 0))" in dsn
    # a bottom-only pad has a B.Cu shape and no F.Cu one
    ps_bottom = re.search(r"\(image IMG_p5 \(pin (PS\d+)", dsn).group(1)
    stack = re.search(rf"\(padstack {ps_bottom} (.*?)\(attach off\)\)", dsn).group(1)
    assert "B.Cu" in stack and "F.Cu" not in stack
    # a through-hole pad is on both
    ps_th = re.search(r"\(image IMG_p3 \(pin (PS\d+)", dsn).group(1)
    stack = re.search(rf"\(padstack {ps_th} (.*?)\(attach off\)\)", dsn).group(1)
    assert "(circle F.Cu 1200)" in stack and "(circle B.Cu 1200)" in stack
    # pins are bare identifiers (Freerouting reads a quoted one up to the quote)
    assert "(net n_sig (pins p1-1 p2-1))" in dsn
    assert "(net n_gnd (pins p3-1 p4-1))" in dsn
    assert "n_lone" not in dsn.split("(network")[1].split("(class")[0]
    # rails get the power width, signals the signal width, both the clearance
    assert re.search(r"\(class power n_gnd .*\(rule \(width 500\) \(clearance 150\)\)", dsn)
    assert re.search(r"\(class default n_sig .*\(rule \(width 200\) \(clearance 150\)\)", dsn)
    # obstacles: the keep-out rectangle and the mounting hole with its clearance
    assert "(keepout k1 (polygon signal 0" in dsn
    assert "(keepout mount (circle signal" in dsn
    assert "(via Via[0-1]_600:300_um)" in dsn


def test_the_design_is_deterministic():
    assert specctra.write_dsn(_problem()) == specctra.write_dsn(_problem())


_SES = """(session tiny
  (base_design tiny)
  (placement (resolution um 10))
  (routes
    (resolution um 10)
    (parser (host_cad "Freerouting"))
    (library_out
      (padstack "Via[0-1]_600:300_um" (shape (circle F.Cu 6000 0 0)) (shape (circle B.Cu 6000 0 0)) (attach off))
    )
    (network_out
      (net n_sig
        (wire (path F.Cu 2000 -50000 0 -20000 0 -20000 10000) (type route))
        (via "Via[0-1]_600:300_um" -20000 10000)
        (wire (path B.Cu 2000 -20000 10000 50000 0) (type route))
      )
      (net n_gnd
        (wire (path F.Cu 5000 -50000 50000 50000 50000) (type route))
      )
      (net n_unknown
        (wire (path F.Cu 2000 0 0 1000 1000) (type route))
      )
    )
  )
)
"""


def test_the_session_comes_back_as_traces_and_vias_in_millimetres():
    problem = _problem()
    sol = specctra.read_ses(_SES, problem)
    assert sol.router == "freerouting"
    by_net = {}
    for t in sol.traces:
        by_net.setdefault(t.net, []).append(t)
    # resolution um 10: 50000 units = 5000 um = 5 mm
    sig_top = next(t for t in by_net["n_sig"] if t.layer == TOP)
    assert sig_top.points[0] == Point(-5, 0) and sig_top.points[-1] == Point(-2, 1)
    assert abs(sig_top.width_mm - 0.2) < 1e-9
    sig_bottom = next(t for t in by_net["n_sig"] if t.layer == BOTTOM)
    assert sig_bottom.points[-1] == Point(5, 0)
    assert abs(by_net["n_gnd"][0].width_mm - 0.5) < 1e-9
    assert len(sol.vias) == 1
    via = sol.vias[0]
    assert (via.net, via.center, via.pad_mm, via.drill_mm) == ("n_sig", Point(-2, 1), 0.6, 0.3)
    # a net the problem does not know is dropped, not invented
    assert "n_unknown" not in by_net
    assert specctra.routed_net_ids(sol) == {"n_sig", "n_gnd"}


def test_the_session_reader_survives_an_empty_network():
    sol = specctra.read_ses("(session x (routes (resolution um 10) (network_out)))", _problem())
    assert sol.traces == () and sol.vias == ()
