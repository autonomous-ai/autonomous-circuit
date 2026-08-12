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

**Why pads and not traces.** When the rule is broken there are no routed traces
to measure — that is the whole failure mode. So this works off
``source_trace`` (the declared connection, which exists before routing) and the
pad each end lands on. It is geometry, available at compile time.

**The margin warning.** A connection under
``CRYSTAL_LENGTH_MARGIN_MM`` of slack passes today and breaks on any nudge.
harness-puck shipped at 0.12mm of margin with a source comment admitting that
another 0.5mm re-broke routing. Silence there would be a lie.

**What this cannot see.** The router's real path, which is longer than the
straight line between pads — so every number here is a *lower* bound and the
check is conservative in the reporting direction, not the missing one. It also
cannot see a crystal whose pins were never placed (reported as coverage), and
it does not know about oscillators or resonators, which tscircuit does not put
under the same constant.
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
    coverage.skip(
        "the routed path, which is longer than the pad-to-pad straight line — "
        "every distance here is a lower bound"
    )
    coverage.skip("oscillators and resonators, which the router does not "
                  "put under this ceiling")

    findings = _length_findings(board, coverage)

    notes = [
        f"ceiling {CRYSTAL_MAX_TRACE_LENGTH_MM:g}mm mirrors tscircuit's "
        "DEFAULT_CRYSTAL_MAX_TRACE_LENGTH_MM; breaking it skips autorouting "
        "for the whole board, not just the offending net",
        f"a connection with under {CRYSTAL_LENGTH_MARGIN_MM:g}mm of slack is "
        "reported as tight rather than passed",
    ]
    if not crystals:
        notes.append("no crystal on this board — nothing to check")

    return CheckResult(
        name="crystal", findings=findings, coverage=coverage, notes=notes
    )
