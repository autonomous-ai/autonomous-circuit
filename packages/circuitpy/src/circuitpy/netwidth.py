"""How wide a net can be, and how wide it is — the two numbers a human needs
before they touch a trace width.

EE review 2026-08-15, finding 4: *"the main power nets, 5V and 3V3, are
currently the same width as a signal, 0.2mm ... should be 0.5–1.0mm"*. The
board file can say so — ``<trace thickness="0.5mm">`` reaches the autorouter as
a per-net ``nominalTraceWidth`` it holds while it searches — and saying it
blindly scraps the board. Measured twice, on two different days:

* harness-puck, every rail declared at once: ``fab.ready`` **true → false**,
  0 blocking findings → **33**, every one of them inside the RP2040's fanout.
* terminal-keyboard, V3_3 alone declared at 0.5mm from the board file:
  0 blocking → **2**, one of them ``Track [V3_3] ... [clearance] 0.0501mm``
  against the 0.0900mm floor.

Neither is a router failure. It is arithmetic: a track leaving a QFN-56 pad at
0.400mm pitch with 0.200mm pads can be
``2 × (0.400 − 0.100 − 0.100) = 0.400mm`` wide and no wider, so a 0.5mm rail
through that fanout cannot exist at any effort, with any router.

So the tool's job is not to widen rails. It is to **hand the engineer the
ceiling before they choose**, per net:

``ceiling_mm``
    the widest track that can leave the net's own pads, measured against the
    *placement* — pads, plated holes, NPTH, keepouts, board edge. Traces, vias
    and pours are excluded on purpose: they are the router's output, and the
    question is what the placement permits, not what this route happened to do.

``narrowest_mm``
    what the net is actually routed at today, at its worst point, which is what
    ``dfm_power_trace_width`` reports and therefore what a fab sees.

``declared_mm``
    what the board file asked for, if anything.

The measurement is the one from ``work/railwidth/escape.py``, which is where
the numbers in ``docs/architecture/rail-width.md`` came from; this is that
script with a return value instead of a print, so the app can show it.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from circuitpy import diffpair, powerwidth

#: How far out of the pad the escape has to survive, in mm. Past the far edge
#: of the neighbouring pads the channel opens up, so a shorter probe flatters
#: the escape; 1.0mm clears a 0.85mm QFN pad end to end.
PROBE_MM = 1.0

#: Bearings tried per pad. 180 is one every two degrees, which is finer than
#: any pad pitch we ship.
DIRECTIONS = 180

#: The widest answer this asks for, in mm.
#:
#: It is a speed knob and an honesty knob at once. `_segment_limit`'s
#: bounding-box prefilter is sized from the cap (`cap / 2 + 1.0`), so the 50mm
#: the one-off script passed meant a 26mm reach and effectively no prefilter at
#: all: 11.3 seconds for two rails on terminal-keyboard, which is not a number
#: you can put behind a click. At 2.0mm the prefilter does its job and the same
#: two rails take a fraction of that.
#:
#: 2.0mm is past anything a board of ours would route — JLC's own sample rules
#: and every rail in `circuitlib` live under 1.0mm — and a pad that can escape
#: wider than the cap is reported **at** the cap with `ceiling_capped`, which
#: reads "at least this", never "exactly this".
CAP_MM = 2.0

#: Ceilings are reported to the micrometre. The escape is a measurement, and
#: printing it to fourteen decimals in a UI is how 0.4000000000000001 ends up
#: in front of an engineer.
_ROUND = 4


def escape_width(
    cx: float,
    cy: float,
    layer: str,
    net_key: str | None,
    obstacles: Sequence[Any],
    outline: Any,
    edge: float,
    cap: float = CAP_MM,
) -> tuple[float, float, str | None]:
    """The widest track that can leave ``(cx, cy)``, its bearing, and what held
    it there."""
    best = -math.inf
    bearing = 0.0
    limiter: str | None = None
    for i in range(DIRECTIONS):
        theta = math.pi * i / DIRECTIONS * 2
        seg = (cx, cy, cx + PROBE_MM * math.cos(theta), cy + PROBE_MM * math.sin(theta))
        allowed, who = powerwidth._segment_limit(
            seg, layer, net_key, obstacles, outline, edge, cap
        )
        if allowed > best:
            best, bearing, limiter = allowed, math.degrees(theta), who
    return best, bearing, limiter


def _placement_obstacles(board: Any, elements: Sequence[dict], profile: Any) -> list:
    """Everything the *placement* puts in a track's way.

    Not traces (a `capsule`), not vias, not pours — those are the router's
    output, and a ceiling measured against them would move every time the
    router did. Pour ids are net-derived strings rather than anything
    containing the word "pour", so they are collected from the elements; routed
    copper is filtered by *shape* for the same reason.
    """
    pour_labels = {
        str(e.get("pcb_copper_pour_id") or "")
        for e in elements
        if isinstance(e, dict) and e.get("type") == "pcb_copper_pour"
    }
    return [
        o
        for o in diffpair.collect_obstacles(board, profile, set())
        if o.kind != "capsule"
        and not o.label.startswith("pcb_via")
        and o.label not in pour_labels
    ]


def net_width_report(
    elements: Sequence[dict],
    profile: Any,
    *,
    nets: Sequence[str] | None = None,
    rails_only: bool = False,
) -> list[dict]:
    """One row per net, newest measurement first in the caller's order.

    ``nets`` selects by name (the net's own name, as the board file spells it);
    ``rails_only`` selects the power and ground nets `powerwidth` recognises.
    With neither, every net that has at least one SMD pad is measured — which
    on terminal-keyboard is 300+ pads and takes seconds, so a caller with a
    person waiting should ask for the net they are looking at.
    """
    board = diffpair._Board(elements)
    outline = diffpair._board_outline(board)
    edge = float(profile.min_edge_clearance_mm)
    placement = _placement_obstacles(board, elements, profile)

    rails = powerwidth._rail_source_nets(elements)
    wanted = {str(n) for n in nets} if nets else None

    components = {
        e["source_component_id"]: e.get("name")
        for e in elements
        if isinstance(e, dict) and e.get("type") == "source_component"
    }
    source_ports = {
        e["source_port_id"]: e
        for e in elements
        if isinstance(e, dict) and e.get("type") == "source_port"
    }
    routed = _narrowest_by_net(elements, board)
    declared = declared_widths(elements)

    rows: list[dict] = []
    for net in board.source_nets.values():
        name = str(net.get("name") or "")
        nid = str(net.get("source_net_id") or "")
        key = net.get("subcircuit_connectivity_map_key")
        if not name or not isinstance(key, str):
            continue
        if wanted is not None and name not in wanted:
            continue
        if rails_only and nid not in rails:
            continue

        pads: list[dict] = []
        for pad in board.by_type.get("pcb_smtpad", []):
            port_id = str(pad.get("pcb_port_id") or "")
            if board.net_key_of_pcb_port(port_id) != key:
                continue
            if pad.get("x") is None or pad.get("y") is None:
                # A polygon pad carries `points` and no centre — the USB-C
                # shell pads are the only ones we ship. Counted, not guessed.
                pads.append({"pad": str(pad.get("pcb_smtpad_id") or ""), "skipped": True})
                continue
            port = board.pcb_ports.get(port_id) or {}
            sp = source_ports.get(str(port.get("source_port_id") or "")) or {}
            width, bearing, limiter = escape_width(
                float(pad["x"]),
                float(pad["y"]),
                str(pad.get("layer") or "top"),
                key,
                placement,
                outline,
                edge,
            )
            pads.append(
                {
                    "pad": str(pad.get("pcb_smtpad_id") or ""),
                    "port": f"{components.get(sp.get('source_component_id'), '?')}."
                    f"{sp.get('name', '?')}",
                    "escape_mm": round(max(width, 0.0), _ROUND),
                    "capped": width >= CAP_MM - 1e-9,
                    "bearing_deg": round(bearing, 1),
                    "limiter": limiter,
                }
            )

        measured = [p for p in pads if "escape_mm" in p]
        if not measured:
            continue
        tightest = min(measured, key=lambda p: p["escape_mm"])
        rows.append(
            {
                "net": name,
                "rail": nid in rails,
                # The ceiling is the *tightest* pad, because a rail is only as
                # wide as its worst escape — the same rule
                # `dfm_power_trace_width` reports the board by.
                "ceiling_mm": tightest["escape_mm"],
                "ceiling_at": tightest["port"],
                # "at least this wide" — the measurement stopped at the cap
                # rather than finding a limit.
                "ceiling_capped": bool(tightest.get("capped")),
                "ceiling_limiter": tightest["limiter"],
                "pads": len(measured),
                "pads_skipped": len(pads) - len(measured),
                "narrowest_mm": routed.get(key),
                "declared_mm": declared.get(name),
            }
        )
    return rows


def _narrowest_by_net(elements: Sequence[dict], board: Any) -> dict[str, float]:
    """The thinnest copper each net is actually routed at, keyed by net key."""
    out: dict[str, float] = {}
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "pcb_trace":
            continue
        key = None
        for point in element.get("route") or []:
            port_id = point.get("start_pcb_port_id") or point.get("end_pcb_port_id")
            if port_id:
                key = board.net_key_of_pcb_port(str(port_id))
                if key:
                    break
        if not key:
            continue
        for point in element.get("route") or []:
            width = point.get("width")
            if width is None:
                continue
            try:
                value = float(width)
            except (TypeError, ValueError):
                continue
            out[key] = min(out.get(key, value), value)
    return {k: round(v, _ROUND) for k, v in out.items()}


def declared_widths(elements: Sequence[dict]) -> dict[str, float]:
    """What the board file asked for, per net name.

    `getSimpleRouteJsonFromCircuitJson` gives a net-shaped connection
    ``nominalTraceWidth = max(min_trace_thickness)`` over every trace joined to
    that net, so **one declaration anywhere on a net sets the whole net** and a
    partial marking is identical to a full one. The max is why this reports the
    largest declaration rather than the first.
    """
    nets_by_id = {
        str(e.get("source_net_id") or ""): str(e.get("name") or "")
        for e in elements
        if isinstance(e, dict) and e.get("type") == "source_net"
    }
    out: dict[str, float] = {}
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "source_trace":
            continue
        thickness = element.get("min_trace_thickness")
        if thickness is None:
            continue
        try:
            value = float(thickness)
        except (TypeError, ValueError):
            continue
        for net_id in element.get("connected_source_net_ids") or []:
            name = nets_by_id.get(str(net_id))
            if name:
                out[name] = max(out.get(name, value), value)
    return {k: round(v, _ROUND) for k, v in out.items()}
