"""Crystal net geometry: will the router route this board at all?

**The gap this closes.** tscircuit puts a hard ceiling
(``DEFAULT_CRYSTAL_MAX_TRACE_LENGTH_MM``, 10mm) on every connection to a
crystal net. Break it and the autorouter does not fail that net — it **skips
the whole board**, and every trace comes back missing. The error it reports is
``pcb_autorouting_error`` naming the crystal, which is almost never the part
that broke the rule.

That combination is the worst shape a failure can have for a one-shot build:
the board is unroutable, the message points at the wrong component, and nothing
in the verdict says *how far over* anything is. Measured on the three example
boards, it cost three separate manual debugging sessions and three different
local patches to a block that had already been fixed upstream.

**What this check does instead.** It measures each declared connection on a
crystal net from pad to pad and says which one is too long and by how much:

    C15.pin1 -> U3.XIN is 11.78mm, 1.78mm over the 10mm ceiling

That is a finding an agent can act on inside the build loop without a human.

**Two measurements, because there are two ways to break the rule.**

*Placement* — the parts are simply too far apart. Measured pad to pad off
``source_trace`` (the declared connection) and the pad each end lands on. Both
exist before routing, which matters: when the rule is broken this way there are
no routed traces to measure at all.

*Routing* — the parts are close enough but the copper is not. The router
detours around obstacles and drops through vias, and a via costs a full board
thickness each way. Found on the first real board this check ever ran on:
``Y1.pin1 -> U3.XIN`` measured 7.94mm pad to pad and passed, while the copper
the router actually laid was **12.71mm** — 9.51mm of planar detour plus two
vias at 1.6mm. Two vias alone spend 3.2mm, a third of the whole budget, without
crossing a single millimetre of board.

Straight-line distance is a *lower bound*, so the placement measurement can
never catch the routing case. Both run; the worse one wins.

**The margin warning.** A connection under
``CRYSTAL_LENGTH_MARGIN_MM`` of slack passes today and breaks on any nudge.
harness-puck shipped at 0.12mm of margin with a source comment admitting that
another 0.5mm re-broke routing. Silence there would be a lie.

**What this cannot see.** A crystal whose pins were never placed (reported as
coverage). Oscillators and resonators, which tscircuit does not put under the
same constant. And on a board that never declared a thickness, the via depth is
unknown, so the routed length falls back to the planar figure and understates —
said out loud in coverage rather than left to read as measured.
"""

from __future__ import annotations

import math

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.model import Board
from verifylib.rules import CRYSTAL_LENGTH_MARGIN_MM, CRYSTAL_MAX_TRACE_LENGTH_MM

#: The ftype tscircuit gives the part the ceiling applies to.
CRYSTAL_FTYPE = "simple_crystal"


def _crystal_components(board: Board) -> list:
    return [c for c in board.components if c.ftype == CRYSTAL_FTYPE]


def _ports_by_component(board: Board) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for port in board.of_type("source_port"):
        component_id = str(port.get("source_component_id") or "")
        port_id = str(port.get("source_port_id") or "")
        if component_id and port_id:
            out.setdefault(component_id, []).append(port_id)
    return out


def _port_labels(board: Board) -> dict[str, str]:
    """``source_port_id -> "U3.XIN"`` — the name a human reads on a schematic."""
    names = {
        str(e.get("source_component_id")): str(e.get("name") or "?")
        for e in board.of_type("source_component")
    }
    out: dict[str, str] = {}
    for port in board.of_type("source_port"):
        port_id = str(port.get("source_port_id") or "")
        component = names.get(str(port.get("source_component_id") or ""), "?")
        pin = port.get("name") or port.get("pin_number") or "?"
        if port_id:
            out[port_id] = f"{component}.{pin}"
    return out


def _connections(board: Board, net_keys: set[str]) -> list[tuple[str, str, str]]:
    """``(trace name, port a, port b)`` for every two-ended declared connection
    on one of ``net_keys``. One-ended traces are a pin tied straight to a named
    net (ground, a rail) and carry no second endpoint to measure against."""
    out: list[tuple[str, str, str]] = []
    for trace in board.of_type("source_trace"):
        if str(trace.get("subcircuit_connectivity_map_key") or "") not in net_keys:
            continue
        ports = [str(p) for p in (trace.get("connected_source_port_ids") or [])]
        if len(ports) != 2:
            continue
        out.append((str(trace.get("name") or "trace"), ports[0], ports[1]))
    return out


def _routed_findings(
    board: Board, crystal, net_keys: set[str], coverage: Coverage
) -> list[Finding]:
    """The copper the router actually laid, vias included.

    Placement can be inside the ceiling while the copper is not: the router
    detours around obstacles, and each via spends a full board thickness. This
    runs only when there is routed copper to measure — on a board the router
    refused, there is none, and the pad-to-pad measurement is the whole answer.
    """
    findings: list[Finding] = []
    thickness = board.thickness_mm
    for net_key in sorted(net_keys):
        net = board.net_by_key.get(net_key)
        if net is None:
            continue
        # A crystal net is usually unnamed, and `net:tivity_net13` tells nobody
        # which part to move. The pins on it do.
        pins = ", ".join(f"{c}.{p}" for c, p in net.pins) or net.label
        who = f"{net.name} ({pins})" if net.name else f"the net joining {pins}"
        for trace in board.traces_on(net):
            if not trace.segments:
                continue
            length = trace.copper_length(thickness)
            if length <= CRYSTAL_MAX_TRACE_LENGTH_MM:
                continue
            if thickness and trace.via_count:
                breakdown = (
                    f" ({trace.length:.2f}mm across the board plus "
                    f"{trace.via_count} via{'s' if trace.via_count > 1 else ''} "
                    f"through it at {thickness:g}mm each — the vias alone spend "
                    f"{trace.via_count * thickness:.2f}mm)"
                )
                advice = (
                    "Keep this net on one layer: a via costs a full board "
                    "thickness each way and there is no budget for it here"
                )
            else:
                breakdown = ""
                advice = (
                    "Shorten the detour — move the parts closer or clear what "
                    "the router is routing around"
                )
            findings.append(
                finding(
                    crystal.name,
                    "crystal_net_routed_long",
                    f"{who} is routed as {length:.2f}mm of copper"
                    f"{breakdown}, {length - CRYSTAL_MAX_TRACE_LENGTH_MM:.2f}mm "
                    f"over the {CRYSTAL_MAX_TRACE_LENGTH_MM:g}mm ceiling. The "
                    f"parts are placed close enough; the copper between them is "
                    f"not. The board still routes and still ships — this is an "
                    f"oscillator running on a longer antenna than its design "
                    f"guide asks for, not a board that cannot be built. "
                    f"{advice}",
                    "warning",
                )
            )
    if board.traces and not thickness:
        coverage.skip(
            "board thickness is not declared, so via depth could not be added "
            "to any routed length — those figures understate"
        )
    return findings


@never_raises
def _length_findings(board: Board, coverage: Coverage) -> list[Finding]:
    crystals = _crystal_components(board)
    ports_of = _ports_by_component(board)
    labels = _port_labels(board)
    ground_keys = {n.key for n in board.nets if n.is_ground}

    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    for crystal in crystals:
        net_keys = {
            net.key
            for port_id in ports_of.get(crystal.source_id, [])
            if (net := board.net_of_port(port_id)) and net.key not in ground_keys
        }
        if not net_keys:
            coverage.skip(
                f"{crystal.name}: no non-ground net on it — nothing to measure"
            )
            continue

        measured = 0
        for trace_name, port_a, port_b in _connections(board, net_keys):
            pad_a = board.pad_of_source_port(port_a)
            pad_b = board.pad_of_source_port(port_b)
            label_a = labels.get(port_a, port_a)
            label_b = labels.get(port_b, port_b)
            if pad_a is None or pad_b is None:
                coverage.skip(
                    f"{trace_name} ({label_a} -> {label_b}): an end has no placed "
                    "pad, so its length is unknown"
                )
                continue

            key = tuple(sorted((port_a, port_b)))
            if key in seen:
                continue
            seen.add(key)
            measured += 1

            distance = math.dist((pad_a.x, pad_a.y), (pad_b.x, pad_b.y))
            slack = CRYSTAL_MAX_TRACE_LENGTH_MM - distance
            span = f"{label_a} -> {label_b}"

            if slack < 0:
                findings.append(
                    finding(
                        crystal.name,
                        "crystal_net_too_long",
                        f"{span} is {distance:.2f}mm pad to pad, "
                        f"{abs(slack):.2f}mm over the "
                        f"{CRYSTAL_MAX_TRACE_LENGTH_MM:g}mm ceiling on a crystal "
                        f"net. The router will skip autorouting for the WHOLE "
                        f"board and blame {crystal.name}; the part to move is "
                        f"{label_a.split('.')[0]}. Keep the crystal cluster "
                        f"(crystal, load caps and any series resistor) together "
                        f"beside the oscillator pins",
                        "error",
                    )
                )
            elif slack < CRYSTAL_LENGTH_MARGIN_MM:
                findings.append(
                    finding(
                        crystal.name,
                        "crystal_net_tight",
                        f"{span} is {distance:.2f}mm pad to pad, only "
                        f"{slack:.2f}mm inside the "
                        f"{CRYSTAL_MAX_TRACE_LENGTH_MM:g}mm ceiling. It routes "
                        f"today and stops routing the whole board on any nudge "
                        f"— move {label_a.split('.')[0]} closer to "
                        f"{label_b.split('.')[0]} while there is room",
                        "warning",
                    )
                )

        findings += _routed_findings(board, crystal, net_keys, coverage)

        if measured:
            coverage.examined += 1
        else:
            coverage.skip(
                f"{crystal.name}: no two-ended connection with pads on both "
                "ends — its geometry was not measured"
            )

    return findings


def check(board: Board) -> CheckResult:
    crystals = _crystal_components(board)
    coverage = Coverage(unit="crystals", total=len(crystals))
    coverage.skip("oscillators and resonators, which the router does not "
                  "put under this ceiling")
    if crystals and not board.traces:
        coverage.skip(
            "no routed copper on this board, so every figure is the pad-to-pad "
            "straight line — a lower bound on what the router would lay"
        )

    findings = _length_findings(board, coverage)

    notes = [
        f"ceiling {CRYSTAL_MAX_TRACE_LENGTH_MM:g}mm mirrors tscircuit's "
        "DEFAULT_CRYSTAL_MAX_TRACE_LENGTH_MM; breaking it skips autorouting "
        "for the whole board, not just the offending net",
        f"a connection with under {CRYSTAL_LENGTH_MARGIN_MM:g}mm of slack is "
        "reported as tight rather than passed",
        "measured twice: pad to pad (works before routing, and on a board the "
        "router refused) and over the routed copper with via depth added "
        "(catches a detour that placement alone cannot show)",
    ]
    if not crystals:
        notes.append("no crystal on this board — nothing to check")

    return CheckResult(
        name="crystal", findings=findings, coverage=coverage, notes=notes
    )
