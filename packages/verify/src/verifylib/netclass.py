"""Net classes: does each net's copper match the job it has to do?

**The gap this closes.** The router uses one trace width for everything.
Measured on all three example boards: **every one of the 1443 / 1052 / 3100
routed segments is 0.15 mm** — V5, GND, V3_3 and a button signal are all the
same copper. By IPC-2221B a 0.15 mm external trace on 1 oz carries about
**0.49 A** at a 10 degC rise. An eight-LED WS2812 chain alone asks for 0.48 A
at full white, before the MCU and the radio.

That is the failure mode that never shows up as a defect: the board works on
the bench, the rail droops under load, and the trace runs hot forever. No DRC
mentions it, because 0.15 mm is a perfectly legal width — it is only wrong for
*this net*.

Altium expresses this as net classes plus a width rule. KiCad expresses it as a
custom rule with a ``track_width`` constraint scoped by ``A.NetClass``. We have
neither, so this module computes the requirement directly: what is attached to
the net, therefore how much current it carries, therefore how wide it has to be.

**What this cannot see.** Any part missing from ``loads.py`` (reported as
coverage, never silently zeroed), off-board loads through a connector, inrush,
and the real thermal environment — IPC-2221 assumes still air with no adjacent
plane, which makes it conservative rather than optimistic. Copper pours are not
measured, so a net poured rather than routed reads as its thinnest trace.
"""

from __future__ import annotations

import math
import re

from verifylib.findings import CheckResult, Coverage, Finding, finding, never_raises
from verifylib.loads import lookup
from verifylib.model import Board, Net
from verifylib.rules import (
    DEFAULT_DELTA_T_C,
    JLCPCB_2LAYER,
    ipc2221_current_a,
    ipc2221_width_mm,
)

#: Net names whose voltage is unambiguous. Anything else is reported unknown
#: rather than guessed — a wrong rail voltage produces a wrong LED current and
#: a confidently wrong finding is worse than an absent one.
_RAIL_VOLTS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"^(v?bus|v_?bus|vusb)$", re.I), 5.0),
    (re.compile(r"^\+?5v?$|^v_?5$|^v5v?$", re.I), 5.0),
    (re.compile(r"^\+?3v?3$|^v_?3_?3$|^3_?3v$", re.I), 3.3),
    (re.compile(r"^\+?1v?8$|^v_?1_?8$", re.I), 1.8),
    (re.compile(r"^gnd$|^agnd$|^dgnd$|^vss$|^ground$", re.I), 0.0),
)

#: A regulator passes its whole output current through its input. One level of
#: propagation covers our topology (USB 5V -> LDO -> 3.3V). It is exact for a
#: linear regulator and *understates* a buck's input current at low duty, which
#: is the safe direction but is stated in coverage rather than hidden.
_REGULATOR_HINTS = ("ams1117", "ldo", "lm1117", "xc6206", "me6211", "tlv7")
_VIN_HINTS = ("vin", "in", "vi")
_VOUT_HINTS = ("vout", "out", "vo")

#: USB 2.0 intra-pair skew budget. 150ps at ~6.7ps/mm on FR-4 microstrip.
USB_SKEW_BUDGET_MM = 3.8

#: Differential-pair coupling (EE review 2026-08-15, finding 3). Skew is the
#: length story; this is the geometry story: D+/D- must *travel together* —
#: parallel, constant gap, over the reference plane. The router has no
#: net-class concept, so it cannot be made to comply yet (the v2 route
#: stage's job); until then every board reports how much of the pair actually
#: runs as a pair. "Together" = centrelines within this many trace-widths;
#: below the fraction floor the pair is reported. Judgment thresholds, not a
#: spec — a routed pair sits at 2-3x width, a diverging one at tens.
DIFF_PAIR_GAP_WIDTHS = 4.0
DIFF_PAIR_MIN_COUPLED = 0.7
#: Sampling step along the P leg, mm. Fine enough that a gap spike shorter
#: than a pad cannot hide between samples.
_COUPLING_STEP_MM = 0.5

#: A plated via's barrel is a thin tube, not a slab. Cross-section for a 0.3mm
#: drill with JLCPCB's 25um plating: pi * 0.3 * 0.025 = 0.0236 mm^2. Fed through
#: IPC-2221's *internal* constant, because a barrel is surrounded by laminate.
VIA_PLATING_MM = 0.025


def rail_voltage(net: Net) -> float | None:
    name = (net.name or "").strip()
    if not name:
        return None
    for pattern, volts in _RAIL_VOLTS:
        if pattern.match(name):
            return volts
    if net.is_ground:
        return 0.0
    return None


def via_current_a(drill_mm: float, *, delta_t_c: float = DEFAULT_DELTA_T_C) -> float:
    """How much a single plated via carries, by IPC-2221 on its barrel area."""
    area_mm2 = math.pi * drill_mm * VIA_PLATING_MM
    equivalent_width_mm = area_mm2 / (JLCPCB_2LAYER.copper_oz * 0.0348)
    return ipc2221_current_a(
        equivalent_width_mm, delta_t_c=delta_t_c, layer="internal"
    )


class _Loads:
    """Per-net peak current, with the unknowns kept as unknowns."""

    def __init__(self, board: Board):
        self.board = board
        self.per_net: dict[str, float] = {}
        self.unknown: dict[str, list[str]] = {}
        self.contributors: dict[str, list[str]] = {}
        self._tally()
        self._propagate_regulators()

    def _tally(self) -> None:
        for component in self.board.components:
            load = lookup(
                lcsc=component.lcsc,
                mpn=self._mpn(component.source_id),
                ftype=component.ftype,
            )
            nets = self._nets_of(component)
            if load is None:
                for net in nets:
                    if net.is_power:
                        self.unknown.setdefault(net.key, []).append(component.name)
                continue
            if load.peak_ma <= 0:
                continue
            # A part loads the power nets it touches, not its signal nets.
            power_nets = [n for n in nets if n.is_power]
            for net in power_nets:
                self.per_net[net.key] = self.per_net.get(net.key, 0.0) + load.peak_ma
                self.contributors.setdefault(net.key, []).append(
                    f"{component.name} {load.peak_ma:g}mA"
                )

    def _propagate_regulators(self) -> None:
        for component in self.board.components:
            haystack = " ".join(
                filter(None, [self._mpn(component.source_id) or "", component.name])
            ).lower()
            if not any(hint in haystack for hint in _REGULATOR_HINTS):
                continue
            vin = self._net_on_port(component.source_id, _VIN_HINTS)
            vout = self._net_on_port(component.source_id, _VOUT_HINTS)
            if vin is None or vout is None or vin.key == vout.key:
                continue
            downstream = self.per_net.get(vout.key, 0.0)
            if downstream <= 0:
                continue
            self.per_net[vin.key] = self.per_net.get(vin.key, 0.0) + downstream
            self.contributors.setdefault(vin.key, []).append(
                f"{downstream:g}mA drawn through {component.name} from {vout.label}"
            )

    def _mpn(self, source_id: str) -> str | None:
        for element in self.board.of_type("source_component"):
            if element.get("source_component_id") == source_id:
                value = element.get("manufacturer_part_number")
                return str(value) if value else None
        return None

    def _nets_of(self, component) -> list[Net]:
        out: list[Net] = []
        for element in self.board.of_type("source_port"):
            if element.get("source_component_id") != component.source_id:
                continue
            net = self.board.net_of_port(str(element.get("source_port_id") or ""))
            if net is not None and net not in out:
                out.append(net)
        return out

    def _net_on_port(self, source_id: str, hints: tuple[str, ...]) -> Net | None:
        best: tuple[int, Net] | None = None
        for element in self.board.of_type("source_port"):
            if element.get("source_component_id") != source_id:
                continue
            name = str(element.get("name") or "").lower()
            for rank, hint in enumerate(hints):
                if name.startswith(hint):
                    net = self.board.net_of_port(str(element.get("source_port_id") or ""))
                    if net is not None and (best is None or rank < best[0]):
                        best = (rank, net)
                    break
        return best[1] if best else None


@never_raises
def _width_findings(board: Board, loads: _Loads) -> list[Finding]:
    out: list[Finding] = []
    for net in board.nets:
        current_ma = loads.per_net.get(net.key, 0.0)
        why_source = "; ".join(loads.contributors.get(net.key, [])[:4])
        if net.is_ground:
            # Ground is the return for every rail, so it carries the largest
            # current on the board rather than a load of its own.
            heaviest = max(loads.per_net.items(), key=lambda kv: kv[1], default=None)
            if heaviest is not None and heaviest[1] > current_ma:
                current_ma = heaviest[1]
                heaviest_net = board.net_by_key.get(heaviest[0])
                why_source = (
                    "the return path for every rail; the heaviest is "
                    f"{heaviest_net.label if heaviest_net else 'a rail'} at "
                    f"{current_ma:.0f}mA"
                )
        if current_ma <= 0:
            continue
        traces = board.traces_on(net)
        widths = [t.min_width for t in traces if t.min_width]
        if not widths:
            continue
        measured = min(widths)
        current_a = current_ma / 1000.0
        required = ipc2221_width_mm(current_a, copper_oz=JLCPCB_2LAYER.copper_oz)
        if measured >= required - 1e-9:
            continue
        carries_a = ipc2221_current_a(measured, copper_oz=JLCPCB_2LAYER.copper_oz)
        ratio = carries_a / current_a if current_a else 1.0
        why = why_source or "attached loads"
        detail = (
            f"{net.label} is routed at {measured:.3f}mm, which carries "
            f"{carries_a * 1000:.0f}mA at a {DEFAULT_DELTA_T_C:g}degC rise "
            f"(IPC-2221B, 1oz external). It has to carry {current_ma:.0f}mA "
            f"({why}), which needs {required:.3f}mm"
        )
        out.append(
            finding(
                net.label,
                "netclass_trace_width",
                detail,
                "error" if ratio < 0.7 else "warning",
            )
        )
    return out


@never_raises
def _uniform_width(board: Board) -> list[Finding]:
    """No net-class differentiation at all is worth saying once, plainly."""
    widths = {
        round(segment.width, 4)
        for trace in board.traces
        for segment in trace.segments
        if segment.width > 0
    }
    if len(widths) != 1:
        return []
    only = next(iter(widths))
    power = [n for n in board.nets if n.is_power or n.is_ground]
    if not power:
        return []
    carries = ipc2221_current_a(only, copper_oz=JLCPCB_2LAYER.copper_oz)
    names = ", ".join(sorted(n.label for n in power)[:6])
    return [
        finding(
            "board",
            "netclass_uniform_width",
            f"every routed segment on this board is {only:g}mm — the same copper "
            f"for {names} as for a button signal. No net class distinguishes "
            f"power from signal, so each rail carries whatever the router's "
            f"default happens to be ({carries * 1000:.0f}mA at "
            f"{DEFAULT_DELTA_T_C:g}degC by IPC-2221B)",
            "info",
        )
    ]


@never_raises
def _via_bottlenecks(board: Board, loads: _Loads) -> list[Finding]:
    out: list[Finding] = []
    by_position = {(round(v.x, 3), round(v.y, 3)): v for v in board.vias}
    for net in board.nets:
        current_ma = loads.per_net.get(net.key, 0.0)
        if net.is_ground:
            current_ma = max(loads.per_net.values(), default=0.0)
        if current_ma <= 0:
            continue
        # Vias belonging to this net: a via sitting on one of its trace routes.
        touching: list[float] = []
        for trace in board.traces_on(net):
            for segment in trace.segments:
                for (vx, vy), via in by_position.items():
                    if (
                        math.hypot(segment.x1 - vx, segment.y1 - vy) < 0.05
                        or math.hypot(segment.x0 - vx, segment.y0 - vy) < 0.05
                    ):
                        touching.append(via.diameter)
        if not touching:
            continue
        smallest = min(touching)
        capacity_a = via_current_a(smallest)
        if capacity_a * 1000 >= current_ma:
            continue
        out.append(
            finding(
                net.label,
                "netclass_via_current",
                f"{net.label} carries {current_ma:.0f}mA and crosses layers "
                f"through {len(touching)} via(s); a {smallest:g}mm-drill via "
                f"barrel carries about {capacity_a * 1000:.0f}mA "
                f"(IPC-2221B on a {VIA_PLATING_MM * 1000:.0f}um plated barrel). "
                "If the current passes through a single one it is over — "
                "stitch several in parallel",
                "warning",
            )
        )
    return out


def _diff_pairs(board: Board) -> list[tuple[Net, Net]]:
    """Every named P/N pair on the board (…DP/…DM, …D+/…D-)."""
    pairs: list[tuple[Net, Net]] = []
    by_name = {n.name: n for n in board.nets if n.name}
    for name, net in sorted(by_name.items()):
        upper = name.upper()
        for suffix_p, suffix_n in (("_DP", "_DM"), ("DP", "DM"), ("D+", "D-")):
            if not upper.endswith(suffix_p):
                continue
            partner_name = name[: len(name) - len(suffix_p)] + suffix_n
            partner = by_name.get(partner_name) or by_name.get(partner_name.lower())
            if partner is not None:
                pairs.append((net, partner))
            break
    return pairs


@never_raises
def _diff_pair_skew(board: Board) -> list[Finding]:
    """USB 2.0 intra-pair skew, from routed length. Length arithmetic, not
    simulation — the same thing KiCad's ``skew`` constraint does."""
    out: list[Finding] = []
    for net, partner in _diff_pairs(board):
        length_p = sum(t.length for t in board.traces_on(net))
        length_n = sum(t.length for t in board.traces_on(partner))
        if length_p <= 0 or length_n <= 0:
            continue
        skew = abs(length_p - length_n)
        if skew <= USB_SKEW_BUDGET_MM:
            continue
        out.append(
            finding(
                f"{net.label},{partner.label}",
                "netclass_pair_skew",
                f"{net.label} routes {length_p:.2f}mm and {partner.label} "
                f"{length_n:.2f}mm — {skew:.2f}mm of intra-pair skew against "
                f"a {USB_SKEW_BUDGET_MM:g}mm budget (USB 2.0, ~150ps on FR-4). "
                "Length-match the pair or shorten the longer leg",
                "warning",
            )
        )
    return out


def _seg_distance(px: float, py: float, seg) -> float:
    """Distance from a point to a finite segment's centreline."""
    dx = seg.x1 - seg.x0
    dy = seg.y1 - seg.y0
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-12:
        return math.hypot(px - seg.x0, py - seg.y0)
    t = ((px - seg.x0) * dx + (py - seg.y0) * dy) / seg2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (seg.x0 + t * dx), py - (seg.y0 + t * dy))


@never_raises
def _diff_pair_coupling(board: Board) -> list[Finding]:
    """How much of the pair actually runs as a pair (EE finding 3, report-only).

    Samples the P leg every ``_COUPLING_STEP_MM`` and asks how far the nearest
    N copper is; "coupled" means within ``DIFF_PAIR_GAP_WIDTHS`` trace-widths.
    Report-level on purpose: the router cannot route net classes yet, so this
    cannot gate — it makes the gap visible on every board until the v2 route
    stage can fix it.
    """
    out: list[Finding] = []
    for net, partner in _diff_pairs(board):
        segs_p = [s for t in board.traces_on(net) for s in t.segments]
        segs_n = [s for t in board.traces_on(partner) for s in t.segments]
        if not segs_p or not segs_n:
            continue
        widths = [s.width for s in segs_p if s.width > 0]
        width = min(widths) if widths else 0.2
        budget = DIFF_PAIR_GAP_WIDTHS * width
        total = 0.0
        coupled = 0.0
        worst = 0.0
        for seg in segs_p:
            length = seg.length
            if length <= 0:
                continue
            steps = max(1, int(length / _COUPLING_STEP_MM))
            piece = length / steps
            for i in range(steps):
                t = (i + 0.5) / steps
                px = seg.x0 + (seg.x1 - seg.x0) * t
                py = seg.y0 + (seg.y1 - seg.y0) * t
                gap = min(_seg_distance(px, py, s) for s in segs_n)
                total += piece
                if gap <= budget:
                    coupled += piece
                elif gap > worst:
                    worst = gap
        if total <= 0:
            continue
        fraction = coupled / total
        if fraction >= DIFF_PAIR_MIN_COUPLED:
            continue
        out.append(
            finding(
                f"{net.label},{partner.label}",
                "netclass_pair_coupling",
                f"{net.label}/{partner.label} run within "
                f"{budget:.2f}mm of each other ({DIFF_PAIR_GAP_WIDTHS:g}x the "
                f"{width:g}mm trace) for only {fraction:.0%} of the run — a "
                f"differential pair should travel together (widest gap "
                f"{worst:.2f}mm). The router cannot hold a pair yet, so this "
                "is reported, not fixed: review the pair before ordering",
                "warning",
            )
        )
    return out


def check(board: Board) -> CheckResult:
    loads = _Loads(board)
    # Signal nets have no current worth sizing for, so the honest denominator
    # is the power and ground nets, not every net on the board.
    carrying = [n for n in board.nets if n.is_power or n.is_ground]
    coverage = Coverage(unit="power and ground nets", total=len(carrying))
    coverage.examined = sum(
        1 for n in carrying if loads.per_net.get(n.key, 0.0) > 0 or n.is_ground
    )
    for key, names in sorted(loads.unknown.items()):
        net = board.net_by_key.get(key)
        coverage.skip(
            f"{net.label if net else key}: no datasheet load for "
            f"{', '.join(sorted(set(names))[:4])} — its current is not in the total"
        )
    coverage.skip("copper pours (a poured net reads as its thinnest routed trace)")
    coverage.skip("off-board loads through a connector")

    findings: list[Finding] = []
    findings += _width_findings(board, loads)
    findings += _via_bottlenecks(board, loads)
    findings += _diff_pair_skew(board)
    findings += _diff_pair_coupling(board)
    findings += _uniform_width(board)

    notes = [
        "IPC-2221B, 1oz external copper, "
        f"{DEFAULT_DELTA_T_C:g}degC rise — conservative: it assumes still air "
        "and no adjacent plane",
        "regulator input current is propagated one level, exact for an LDO and "
        "an understatement for a buck",
    ]
    return CheckResult(
        name="netclass", findings=findings, coverage=coverage, notes=notes
    )
