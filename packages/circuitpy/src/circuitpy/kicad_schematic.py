"""Make the exported KiCad schematic tell the truth about the design.

**The defect this closes.** The first human EE review (2026-08-15) read all 50
terminal-keyboard keys as shorted, because the exported schematic drew them
that way. Ledger #29 answered with `internallyConnectedPins` on the block, and
that half worked: measured on the 2026-08-15 export, all 50 matrix keys now
render as a two-pin switch with pin 1 on its column net and pin 2 on its row
net, no wire across the contacts.

What the block fix could not reach is everything the *converter* drops. Measured
on the same file, by exporting KiCad's own netlist from the `.kicad_sch` and
comparing it against `circuit.json`:

* `J1`, the USB-C receptacle, has **no symbol at all**. The converter emits
  `(lib_id "Device:J_TYPE-C-31-M-12")` and never defines it, because
  `schematic_component.symbol_name` is null for that part. With no symbol, all
  16 of its pins land on the origin and KiCad reads **VBUS, GND, D+ and D- as
  one net**. A reviewer sees a shorted connector.
* `SW2`/`SW3` (BOOTSEL, RESET) each have **one terminal dangling**: the
  schematic router gave up on the R13->SW2 wire and emitted a net label on the
  resistor side only, so the button reads as dead. The same phantom as the 50
  keys, arrived at from the other direction.
* Twelve more pins — `C1.1`, `C14.1`, `C16.2`, `C17.1`, `C2.1`, `C3.1`,
  `R1.1`, `R20.1`, `TP3.1`, `U3.57`, `U4.8`, `Y1.2` — are simply absent from
  the drawn connectivity.

None of this was invisible. KiCad said so in the packet we already ship:
**398 ERC violations** (139 `lib_symbol_issues`, 18 `pin_not_connected`, 14
`unconnected_wire_endpoint`, 4 `wire_dangling`, 1 `label_dangling`) and **135
`net_conflict` schematic-parity findings**, every one of them filed at `info`
or `warning` so nothing ever blocked. The evidence was in the artifact; nobody
compared it against the design.

**Why the fix lives here.** `circuit-json-to-kicad` is upstream and we do not
own it, and the ftypes it has no symbol for are open-ended — the next board
will hit a different one. The `.kicad_sch` between the converter and the
reviewer is the one place every board passes through, exactly like
`kicad_normalize` for the `.kicad_pcb`.

**What it does, in order.**

1. Synthesise a symbol for every `lib_id` the file references and never
   defines, so no component can collapse onto a point. Pins come from the
   part's own `source_port` list, laid out on a rectangle at 2.54mm pitch.
2. Pull apart symbols the converter stacked on one point. Two pins at one
   coordinate are one node to KiCad whatever is written on them, so this is
   the one defect a label cannot repair and the reason a labelling pass alone
   never converges.
3. Ask kicad-cli for the netlist of the result and compare it, pin by pin,
   against the netlist implied by `circuit.json` (source traces plus the
   `internallyConnectedPins` groups). Every disagreement is named. The
   `wrong_before` figure is therefore measured **after** steps 1 and 2 — it
   counts what is left for the labels, not the damage the converter shipped.
4. Stamp a global label carrying the true net name on each pin KiCad reads
   wrongly — plus, when the net already exists in the drawing under another
   name, one anchor label on a pin it got right, so the two halves join
   instead of becoming two nets that share a shape.
5. Ask kicad-cli again and report **both** numbers. If the second measurement
   is not zero the remainder is listed, not rounded away: a label can add a
   connection but it cannot remove one, so a genuine drawn short survives this
   pass by construction and has to be reported as what it is.

**Measured on the three 2026-08-15 exports** (independent count: KiCad's own
netlist against a union-find over `circuit.json`, not this module's report):

| board | KiCad nets covering >1 design net | design nets drawn as >1 | pins the design connects that KiCad reads as unconnected |
|---|---|---|---|
| terminal-keyboard | 0 -> 0 | 2 -> 0 | 14 -> 0 |
| harness-puck | 0 -> 0 | 4 -> 0 | 15 -> 0 |
| hydrate-coaster | 1 -> 0 | 2 -> 0 | 15 -> 0 |

6-12 seconds a board, and byte-identical on a second run of all three.
(terminal-keyboard's `J1` reads as one 8-net short in KiCad and shows as 0 in
the "before" column only because a part with no symbol has no drawn pins to
attribute it to — step 1 is what makes it countable at all.)

Nothing here is inferred from how a symbol looks. The mapping from a KiCad
symbol pin to the source pins behind it is taken from the declared
`internally_connected_source_port_ids` groups when the symbol has been folded
(a 4-pad button on a 2-pin symbol), then from the pin's own **name**, and only
then from its number. When no rule applies the pin is reported unresolved and
left alone — guessing the pairing from geometry is the mistake this whole file
exists to undo.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

# ---------------------------------------------------------------------------
# A very small s-expression reader.
# ---------------------------------------------------------------------------
#
# Read-only: every edit below is text surgery with a balanced-paren scan, the
# same posture as kicad_normalize. The parser exists so we can *locate* things
# (symbol placements, library pins) without pretending a regex understands
# nesting.


def _tokenize(text: str) -> Iterable[tuple[str, str]]:
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char in "()":
            yield ("paren", char)
            i += 1
        elif char.isspace():
            i += 1
        elif char == '"':
            j = i + 1
            buf: list[str] = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    buf.append(text[j])
                    j += 1
            yield ("str", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            yield ("atom", text[i:j])
            i = j


def _parse(text: str) -> list:
    root: list = []
    stack: list[list] = [root]
    for kind, value in _tokenize(text):
        if kind == "paren" and value == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif kind == "paren":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append((kind, value))
    return root


def _head(node) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], tuple):
        return node[0][1]
    return None


def _val(token) -> str:
    return token[1] if isinstance(token, tuple) else ""


def _kids(node, name: str) -> list:
    return [c for c in node if isinstance(c, list) and _head(c) == name]


def _kid(node, name: str):
    found = _kids(node, name)
    return found[0] if found else None


def _num(token, default: float = 0.0) -> float:
    try:
        return float(_val(token))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# The truth: what circuit.json says is connected to what.
# ---------------------------------------------------------------------------


@dataclass
class SourceNetlist:
    """The design's own netlist, keyed by ``(refdes, source pin number)``."""

    #: (ref, pin) -> net id (an opaque, stable key)
    pin_net: dict[tuple[str, str], str] = field(default_factory=dict)
    #: net id -> the name the design gave it, when it named it at all
    net_name: dict[str, str] = field(default_factory=dict)
    #: ref -> declared internally-connected pin-number groups, in order
    groups: dict[str, list[list[str]]] = field(default_factory=dict)
    #: ref -> every source pin number it has, in numeric order
    pins: dict[str, list[str]] = field(default_factory=dict)
    #: (ref, pin) -> the port's own name (``VBUS``, ``SWCLK``, …)
    pin_name: dict[tuple[str, str], str] = field(default_factory=dict)

    def members(self, net: str) -> list[tuple[str, str]]:
        return sorted(k for k, v in self.pin_net.items() if v == net)


def source_netlist(elements: Sequence[dict]) -> SourceNetlist:
    """Build the design netlist from circuit.json.

    Union-find over `source_trace` (ports and nets), then the declared
    `internally_connected_source_port_ids` groups — a tact switch's pads 1+2
    are one terminal whether or not any trace says so, and a schematic that
    does not fold them is the defect this module exists for.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    comp_name: dict[str, str] = {}
    raw_groups: dict[str, list[list[str]]] = {}
    out = SourceNetlist()
    for element in elements:
        if element.get("type") == "source_component":
            cid = element.get("source_component_id")
            name = element.get("name")
            if isinstance(cid, str) and isinstance(name, str):
                comp_name[cid] = name
                for group in element.get("internally_connected_source_port_ids") or []:
                    if isinstance(group, list) and len(group) > 1:
                        raw_groups.setdefault(name, []).append([str(p) for p in group])

    port_key: dict[str, tuple[str, str]] = {}
    named_nets: dict[str, str] = {}
    for element in elements:
        kind = element.get("type")
        if kind == "source_port":
            pid = element.get("source_port_id")
            ref = comp_name.get(element.get("source_component_id", ""))
            if not isinstance(pid, str) or ref is None:
                continue
            pin = str(element.get("pin_number"))
            port_key[pid] = (ref, pin)
            out.pins.setdefault(ref, []).append(pin)
            label = element.get("name")
            if isinstance(label, str):
                out.pin_name[(ref, pin)] = label
            find(pid)
        elif kind == "source_net":
            nid = element.get("source_net_id")
            if isinstance(nid, str):
                name = element.get("name")
                if isinstance(name, str) and name:
                    named_nets[nid] = name
                find(nid)

    for element in elements:
        if element.get("type") != "source_trace":
            continue
        ids = [
            i
            for i in (list(element.get("connected_source_port_ids") or [])
                      + list(element.get("connected_source_net_ids") or []))
            if isinstance(i, str)
        ]
        for other in ids[1:]:
            union(ids[0], other)

    # The declared groups. `internally_connected_source_port_ids` is the
    # authority; the redundant same-net copper tie that used to stand in for it
    # is exactly what drew as a short.
    by_ref_pin: dict[str, dict[str, str]] = {}
    for pid, (ref, pin) in port_key.items():
        by_ref_pin.setdefault(ref, {})[pin] = pid
    for ref, groups in raw_groups.items():
        resolved: list[list[str]] = []
        for group_tokens in groups:
            pins = [_group_pin(token, ref, port_key, by_ref_pin)
                    for token in group_tokens]
            if any(p is None for p in pins):
                continue  # unreadable group: leave the part unfolded, not wrong
            resolved.append([p for p in pins if p])
            pids = [by_ref_pin.get(ref, {}).get(p) for p in pins if p]
            pids = [p for p in pids if p]
            for other in pids[1:]:
                union(pids[0], other)
        if resolved:
            out.groups[ref] = resolved

    for pid, key in port_key.items():
        root = find(pid)
        out.pin_net[key] = root
    for nid, name in named_nets.items():
        out.net_name.setdefault(find(nid), name)
    for ref in out.pins:
        out.pins[ref] = sorted(out.pins[ref], key=_pin_sort_key)
    return out


_PIN_TOKEN_RE = re.compile(r"(\d+)\s*$")


def _group_pin(
    token: str,
    ref: str,
    port_key: dict[str, tuple[str, str]],
    by_ref_pin: dict[str, dict[str, str]],
) -> str | None:
    """The pin number a group entry names, or ``None`` when it names nothing.

    Two spellings reach here and they mean different things, which is a real
    trap: tscircuit resolves the block author's `pin1` into a **port id** —
    `source_port_2` on the first switch of a keyboard — and reading its
    trailing integer as a pin number quietly ties pin 2 to pin 3, shorting the
    switch inside our own model of the design. So a port id is looked up, and
    only a token that is not a port id falls back to its trailing integer.
    """
    if token in port_key:
        owner, pin = port_key[token]
        return pin if owner == ref else None
    match = _PIN_TOKEN_RE.search(token or "")
    if match is None:
        return None
    pin = match.group(1)
    return pin if pin in by_ref_pin.get(ref, {}) else None


def _pin_sort_key(pin: str) -> tuple[int, object]:
    return (0, int(pin)) if str(pin).isdigit() else (1, str(pin))


# ---------------------------------------------------------------------------
# What the exported .kicad_sch actually draws.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LibPin:
    number: str
    name: str
    x: float
    y: float
    angle: float


@dataclass(frozen=True)
class Placement:
    ref: str
    lib_id: str
    x: float
    y: float
    rotation: float
    instance_pins: tuple[str, ...]


def _library_pins(tree: list) -> dict[str, list[LibPin]]:
    """Every pin of every symbol the file defines, in library coordinates."""
    out: dict[str, list[LibPin]] = {}
    lib = _kid(tree, "lib_symbols")
    if lib is None:
        return out
    for symbol in _kids(lib, "symbol"):
        if len(symbol) < 2:
            continue
        name = _val(symbol[1])
        pins: list[LibPin] = []
        for unit in _kids(symbol, "symbol"):
            for pin in _kids(unit, "pin"):
                at = _kid(pin, "at")
                number = _kid(pin, "number")
                pin_name = _kid(pin, "name")
                if at is None or number is None or len(at) < 3:
                    continue
                pins.append(
                    LibPin(
                        number=_val(number[1]),
                        name=_val(pin_name[1]) if pin_name and len(pin_name) > 1 else "",
                        x=_num(at[1]),
                        y=_num(at[2]),
                        angle=_num(at[3]) if len(at) > 3 else 0.0,
                    )
                )
        # A duplicated definition is the converter repeating itself; the pins
        # are identical, so first-wins is right and quiet.
        out.setdefault(name, pins)
    return out


def _placements(tree: list) -> list[Placement]:
    out: list[Placement] = []
    for symbol in _kids(tree, "symbol"):
        lib_id = _kid(symbol, "lib_id")
        at = _kid(symbol, "at")
        if lib_id is None or at is None or len(at) < 3:
            continue
        ref = ""
        for prop in _kids(symbol, "property"):
            if len(prop) > 2 and _val(prop[1]) == "Reference":
                ref = _val(prop[2])
        out.append(
            Placement(
                ref=ref,
                lib_id=_val(lib_id[1]),
                x=_num(at[1]),
                y=_num(at[2]),
                rotation=_num(at[3]) if len(at) > 3 else 0.0,
                instance_pins=tuple(
                    _val(p[1]) for p in _kids(symbol, "pin") if len(p) > 1
                ),
            )
        )
    return out


#: Eeschema's internal unit: the sheet is stored as integers of 0.0001mm, and
#: every coordinate in the file is snapped to one of them the moment KiCad
#: parses it. Two points are the same point only when they snap to the same
#: integer.
_SCH_IU_PER_MM = 10000.0
_SCH_IU_MM = 1.0 / _SCH_IU_PER_MM


def _kiround(value: float) -> int:
    """KiCad's own rounding: half away from zero, not Python's half-to-even."""
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _to_iu(mm: float) -> int:
    """Millimetres to internal units, the way KiCad does the arithmetic.

    ``mm * 10000`` and ``mm / 0.0001`` are not the same computation in binary
    floating point, and the difference is a whole unit exactly where it hurts.
    harness-puck's crystal sits at ``x=424.83124999999995``: multiplied, that
    is 4248312.5 and rounds **up** to 4248313, which is where KiCad puts the
    pin; divided, it is 4248312.499999999 and rounds down. Measured with the
    divide: Y1's own repair labels landed 100nm off three of its four pins and
    KiCad bonded none of them. KiCad multiplies, so this multiplies.
    """
    return _kiround(mm * _SCH_IU_PER_MM)


def _pin_position(place: Placement, pin: LibPin) -> tuple[float, float]:
    """A library pin's connection point on the sheet, on KiCad's own grid.

    KiCad rotates the library coordinate and then inverts Y, because library
    space is Y-up and the sheet is Y-down. The arithmetic is done the way
    eeschema does it — **snap first, then add** — because doing it in floating
    point and snapping at the end is off by one internal unit whenever the
    symbol's own position ends in a repeating binary fraction, and one unit is
    enough for KiCad to bond nothing.

    That is not hypothetical. harness-puck's `J1` sits at
    ``x=34.831249999999955``: KiCad reads the symbol at 348312 IU, puts pin 1
    at ``348312 - 127000 = 221312``, and the float route lands a repair stub at
    221313. Measured before this changed: **all 16 J1 pins stayed unconnected
    after the pass stamped 16 correct labels on them**, while the same code
    repaired terminal-keyboard's J1 completely — its ``x=-73.97500000000002``
    happens to snap the other way. A pass that silently no-ops on some boards
    and not others is worse than one that fails everywhere, because the
    difference reads as "that board was fine".
    """
    px, py = _to_iu(pin.x), _to_iu(pin.y)
    angle = round(place.rotation) % 360
    if angle == 0:
        rx, ry = px, py
    elif angle == 90:
        rx, ry = -py, px
    elif angle == 180:
        rx, ry = -px, -py
    elif angle == 270:
        rx, ry = py, -px
    else:
        # Nothing the converter emits lands here; snap the rotated float so an
        # unexpected angle degrades to the old behaviour rather than crashing.
        radians = math.radians(place.rotation)
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        rx = _to_iu(pin.x * cos_a - pin.y * sin_a)
        ry = _to_iu(pin.x * sin_a + pin.y * cos_a)
    x = _to_iu(place.x) + rx
    y = _to_iu(place.y) - ry
    return (round(x * _SCH_IU_MM, 6), round(y * _SCH_IU_MM, 6))


# ---------------------------------------------------------------------------
# Mapping a drawn pin back to the design.
# ---------------------------------------------------------------------------


def _named_source_pin(
    lib_pin: LibPin, ref: str, source_pins: Sequence[str], truth: SourceNetlist
) -> str | None:
    """The source pin a drawn pin's *name* refers to, or ``None``.

    The converter writes the part's own pin identity into the KiCad pin
    **name** and uses the pin **number** as the drawn index. On a numbered
    passive the two coincide (``num='2' name='2'``); on a part whose symbol
    orders its pins differently they do not, and the name is the one that is
    right. Measured over all three boards' exports (2026-08-15): the name
    resolves 239 of 239 pins, and disagrees with the number on 3 — every one
    of them `Device:crystal_4pin_right`, whose pins run ``num 1..4`` against
    ``name 4,2,1,3``.
    """
    name = lib_pin.name
    if name in source_pins:
        return name
    if name.startswith("pin") and name[3:] in source_pins:
        return name[3:]
    hits = [pin for pin in source_pins if truth.pin_name.get((ref, pin)) == name]
    return hits[0] if len(hits) == 1 else None


def symbol_pin_sources(
    place: Placement, drawn: Sequence[LibPin], truth: SourceNetlist
) -> tuple[dict[str, list[str]], str | None]:
    """Which source pins each drawn symbol pin stands for.

    Returns ``(mapping, reason_unresolved)``. Three rules, in this order:

    1. **A folded symbol.** The part declares internal groups and the symbol
       draws exactly one pin per group — a 4-pad tact switch on a two-pin
       switch symbol. Drawn pin *k* is group *k*. This has to be tried first:
       pin numbers 1 and 2 both exist on the part, so matching by number would
       silently put the wrong net on terminal 2. Checked against the converter:
       on SW10 this rule reproduces KiCad's own netlist exactly (pin 1 -> K00,
       pin 2 -> ROW0).
    2. **The drawn pin's own name.** The converter records the part's pin
       identity in the pin name; the number is only the drawn order. They
       agree on every ordinary part and disagree on `Device:crystal_4pin_right`
       (``num 1..4`` against ``name 4,2,1,3``), where the name is right — Y1's
       drawn pin 1 is the part's pin 4, which is a ground leg, not XIN.
       Accepted only when it maps every drawn pin onto a distinct source pin.
    3. **A plain symbol.** The drawn pin number is a source pin number. Kept as
       the fallback for a symbol whose names carry no identity at all.

    Anything else returns no mapping and a reason. Inferring a pairing from
    where the pins sit is precisely the error that produced the phantom short.
    """
    source_pins = truth.pins.get(place.ref) or []
    drawn_pins = [pin.number for pin in drawn]
    if not source_pins:
        # Not a part at all: the converter draws power rails and net flags as
        # one-pin symbols whose Reference is the net name (GND, V3_3, V5).
        # They carry no design pins, so there is nothing to be right or wrong
        # about, and calling them unresolved would bury the real findings.
        return {}, None

    groups = truth.groups.get(place.ref) or []
    if groups and len(drawn_pins) == len(groups) and len(drawn_pins) < len(source_pins):
        ordered = sorted(drawn_pins, key=_pin_sort_key)
        mapping = {number: list(group) for number, group in zip(ordered, groups)}
        covered = {p for group in groups for p in group}
        if covered == set(source_pins):
            return mapping, None
        return {}, (
            f"{place.ref}: declared groups cover {sorted(covered)} but the part "
            f"has pins {source_pins}"
        )

    by_name = {
        pin.number: _named_source_pin(pin, place.ref, source_pins, truth)
        for pin in drawn
    }
    resolved = [p for p in by_name.values() if p is not None]
    if len(resolved) == len(drawn_pins) and len(set(resolved)) == len(resolved):
        return {number: [pin] for number, pin in by_name.items()}, None  # type: ignore[misc]

    mapping = {}
    for number in drawn_pins:
        if (place.ref, number) in truth.pin_net:
            mapping[number] = [number]
    if len(mapping) == len(drawn_pins):
        return mapping, None
    missing = sorted(set(drawn_pins) - set(mapping))
    return {}, (
        f"{place.ref}: symbol draws {len(drawn_pins)} pin(s), the part has "
        f"{len(source_pins)}, and {missing} match no source pin"
    )


# ---------------------------------------------------------------------------
# Result type.
# ---------------------------------------------------------------------------


@dataclass
class SchematicTruth:
    """What the pass measured and what it changed. Both numbers, always."""

    pins_compared: int = 0
    wrong_before: int = 0
    wrong_after: int | None = None
    labels_added: int = 0
    wires_cut: int = 0
    orphan_labels_dropped: int = 0
    symbols_synthesised: int = 0
    symbols_unstacked: int = 0
    synthesised_ids: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    measured: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.labels_added or self.symbols_synthesised or self.wires_cut
            or self.orphan_labels_dropped or self.symbols_unstacked
        )

    def summary(self) -> str:
        if not self.measured:
            return "; ".join(self.notes) or "not measured"
        after = "not re-measured" if self.wrong_after is None else str(self.wrong_after)
        parts = [
            f"{self.wrong_before} of {self.pins_compared} pins disagreed with "
            f"the design before, {after} after"
        ]
        if self.symbols_synthesised:
            parts.append(
                f"{self.symbols_synthesised} missing symbol(s) drawn "
                f"({', '.join(self.synthesised_ids)})"
            )
        if self.symbols_unstacked:
            parts.append(
                f"{self.symbols_unstacked} stacked pin point(s) pulled apart"
            )
        if self.labels_added:
            parts.append(f"{self.labels_added} net label(s) stamped")
        if self.wires_cut:
            parts.append(
                f"{self.wires_cut} wire(s) cut where the drawing shorted two nets"
            )
        if self.orphan_labels_dropped:
            parts.append(
                f"{self.orphan_labels_dropped} label(s) removed from empty space"
            )
        if self.unresolved:
            parts.append(f"{len(self.unresolved)} pin group(s) left alone")
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# Editing the file.
# ---------------------------------------------------------------------------

_INDENT = "  "

#: How many measure/repair rounds the pass may take before it reports what is
#: left. Two is what terminal-keyboard needs; the third is headroom, not hope.
MAX_ROUNDS = 3

#: Marker the comparison puts in a reading when KiCad has merged design nets.
_SHARED_MARK = " (shared with "


def _balanced_span(text: str, opener: str, start: int = 0) -> tuple[int, int] | None:
    i = text.find(opener, start)
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return (i, j + 1)
    return None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


#: How far a repair stub reaches out of a bare pin, in mm. One 2.54mm pitch.
_STUB_MM = 2.54


def _outward(pin_angle: float) -> tuple[float, float]:
    """Unit vector pointing away from the body, in sheet coordinates.

    A KiCad pin's own angle points from its connection end *into* the symbol,
    and the sheet's Y runs opposite the library's, so the direction a wire
    leaves a pin is ``pin_angle + 180`` with the Y term negated. Checked
    against the converter's own wires: every stub this produces runs the same
    way the exporter's wire ran before it was cut.
    """
    angle = math.radians((pin_angle + 180.0) % 360.0)
    return (math.cos(angle), -math.sin(angle))


def _wire_block(x0: float, y0: float, x1: float, y1: float) -> str:
    return (
        f"{_INDENT}(wire\n"
        f"{_INDENT * 2}(pts\n"
        f"{_INDENT * 3}(xy {x0:.9g} {y0:.9g}) (xy {x1:.9g} {y1:.9g})\n"
        f"{_INDENT * 2})\n"
        f"{_INDENT * 2}(stroke\n{_INDENT * 3}(width 0)\n"
        f"{_INDENT * 3}(type default)\n{_INDENT * 2})\n"
        f"{_INDENT * 2}(uuid {_stable_uuid('stub', x0, y0, x1, y1)})\n"
        f"{_INDENT})\n"
    )


def _label_block(net: str, x: float, y: float, pin_angle: float) -> str:
    """A global label anchored on a pin's connection point.

    Global, not local, because that is what the converter already emits for
    named nets — mixing the two would leave two nets with one name.
    """
    angle = (pin_angle + 180.0) % 360.0
    justify = "right" if 90.0 < angle < 270.0 else "left"
    return (
        f'{_INDENT}(global_label "{_escape(net)}"\n'
        f"{_INDENT * 2}(shape input)\n"
        f"{_INDENT * 2}(at {x:.9g} {y:.9g} {angle:g})\n"
        f"{_INDENT * 2}(effects\n"
        f"{_INDENT * 3}(font\n"
        f"{_INDENT * 4}(size 1.27 1.27)\n"
        f"{_INDENT * 3})\n"
        f"{_INDENT * 3}(justify {justify})\n"
        f"{_INDENT * 2})\n"
        f"{_INDENT * 2}(uuid {_stable_uuid(net, x, y)})\n"
        f"{_INDENT})\n"
    )


def _stable_uuid(*parts: object) -> uuid.UUID:
    """A deterministic uuid: the same board must export the same bytes."""
    return uuid.uuid5(uuid.NAMESPACE_URL, "autonomous-circuit/kicad_sch/" + "|".join(
        f"{p}" for p in parts
    ))


def _synth_symbol_block(lib_id: str, ref_prefix: str,
                        pins: Sequence[tuple[str, str]]) -> tuple[str, list[LibPin]]:
    """Draw a rectangle with the part's pins on it.

    Used for a `lib_id` the converter references and never defines — which is
    what it does whenever `schematic_component.symbol_name` is null. Without a
    definition KiCad puts every pin on the origin and reads the part as one
    shorted node, so *any* honest body is better than none. Pins alternate
    left/right in pin order at 2.54mm pitch, the KiCad house style for a
    connector.
    """
    half = (len(pins) + 1) // 2
    height = max(2, half) * 2.54 + 2.54
    # Wide enough that the left column's pin names cannot land on the right
    # column's. 1.27mm of glyph width per character is KiCad's default font at
    # the default size, and the 5.08mm gutter is what keeps `A1` from touching
    # `B12` on a 16-way USB-C.
    longest = max((len(name) for _number, name in pins), default=1)
    width = max(10.16, 2 * longest * 1.27 + 5.08)
    body_x = width / 2
    lib_pins: list[LibPin] = []
    left = [p for i, p in enumerate(pins) if i % 2 == 0]
    right = [p for i, p in enumerate(pins) if i % 2 == 1]
    for side, items in (("left", left), ("right", right)):
        top = (len(items) - 1) * 2.54 / 2
        for index, (number, name) in enumerate(items):
            y = top - index * 2.54
            if side == "left":
                lib_pins.append(LibPin(number, name or number, -body_x - 3.81, y, 0.0))
            else:
                lib_pins.append(LibPin(number, name or number, body_x + 3.81, y, 180.0))

    body = [
        f'{_INDENT * 2}(symbol "{_escape(lib_id.split(":")[-1])}"\n',
        f"{_INDENT * 3}(pin_names\n{_INDENT * 4}(offset 0.762)\n{_INDENT * 3})\n",
        f"{_INDENT * 3}(exclude_from_sim no)\n{_INDENT * 3}(in_bom yes)\n"
        f"{_INDENT * 3}(on_board yes)\n",
        f'{_INDENT * 3}(property "Reference" "{_escape(ref_prefix)}"\n'
        f"{_INDENT * 4}(id 0)\n{_INDENT * 4}(at 0 {height / 2 + 2.54:.9g} 0)\n"
        f"{_INDENT * 4}(effects\n{_INDENT * 5}(font\n{_INDENT * 6}(size 1.27 1.27)\n"
        f"{_INDENT * 5})\n{_INDENT * 4})\n{_INDENT * 3})\n",
        f'{_INDENT * 3}(property "Value" "{_escape(lib_id.split(":")[-1])}"\n'
        f"{_INDENT * 4}(id 1)\n{_INDENT * 4}(at 0 {-height / 2 - 2.54:.9g} 0)\n"
        f"{_INDENT * 4}(effects\n{_INDENT * 5}(font\n{_INDENT * 6}(size 1.27 1.27)\n"
        f"{_INDENT * 5})\n{_INDENT * 4})\n{_INDENT * 3})\n",
        f'{_INDENT * 3}(symbol "{_escape(lib_id.split(":")[-1])}_0_1"\n'
        f"{_INDENT * 4}(rectangle\n"
        f"{_INDENT * 5}(start {-body_x:.9g} {height / 2:.9g})\n"
        f"{_INDENT * 5}(end {body_x:.9g} {-height / 2:.9g})\n"
        f"{_INDENT * 5}(stroke\n{_INDENT * 6}(width 0.254)\n"
        f"{_INDENT * 6}(type default)\n{_INDENT * 5})\n"
        f"{_INDENT * 5}(fill\n{_INDENT * 6}(type background)\n{_INDENT * 5})\n"
        f"{_INDENT * 4})\n{_INDENT * 3})\n",
        f'{_INDENT * 3}(symbol "{_escape(lib_id.split(":")[-1])}_1_1"\n',
    ]
    for pin in lib_pins:
        body.append(
            f"{_INDENT * 4}(pin passive line\n"
            f"{_INDENT * 5}(at {pin.x:.9g} {pin.y:.9g} {pin.angle:g})\n"
            f"{_INDENT * 5}(length 3.81)\n"
            f'{_INDENT * 5}(name "{_escape(pin.name)}"\n'
            f"{_INDENT * 6}(effects\n{_INDENT * 7}(font\n{_INDENT * 8}(size 1.27 1.27)\n"
            f"{_INDENT * 7})\n{_INDENT * 6})\n{_INDENT * 5})\n"
            f'{_INDENT * 5}(number "{_escape(pin.number)}"\n'
            f"{_INDENT * 6}(effects\n{_INDENT * 7}(font\n{_INDENT * 8}(size 1.27 1.27)\n"
            f"{_INDENT * 7})\n{_INDENT * 6})\n{_INDENT * 5})\n"
            f"{_INDENT * 4})\n"
        )
    body.append(f"{_INDENT * 3})\n{_INDENT * 2})\n")
    return "".join(body), lib_pins


# ---------------------------------------------------------------------------
# The pass.
# ---------------------------------------------------------------------------

#: A KiCad netlist reader: takes the .kicad_sch, returns (ref, pin) -> net name.
NetlistReader = Callable[[Path], "dict[tuple[str, str], str]"]

#: kicad-cli reads an A0 sheet with 165 symbols in about two seconds. The
#: budget for being sure is days (north-star, "why verification is nearly
#: free"), so this runs twice on every board without apology.
NETLIST_TIMEOUT_S = 300.0


def kicad_netlist(sch_path: Path, *, timeout: float = NETLIST_TIMEOUT_S
                  ) -> dict[tuple[str, str], str]:
    """Ask KiCad what the exported schematic is connected to.

    Deliberately KiCad's own answer rather than ours. The whole point of the
    comparison is that the reviewer's tool and the design disagree, and a
    reimplementation of KiCad's connectivity would just be a third opinion.
    """
    from circuitpy import toolchain  # local: keeps this module import-cheap

    out = sch_path.with_suffix(".truth.net")
    toolchain.run_kicad(
        ["sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out),
         str(sch_path)],
        timeout=timeout,
    )
    try:
        return parse_kicad_netlist(out.read_text(encoding="utf-8"))
    finally:
        out.unlink(missing_ok=True)


def parse_kicad_netlist(text: str) -> dict[tuple[str, str], str]:
    """``(ref, pin) -> net name`` from a kicadsexpr netlist."""
    tree = _parse(text)
    if not tree:
        return {}
    nets = _kid(tree[0], "nets")
    if nets is None:
        return {}
    out: dict[tuple[str, str], str] = {}
    for net in _kids(nets, "net"):
        name_node = _kid(net, "name")
        if name_node is None or len(name_node) < 2:
            continue
        name = _val(name_node[1])
        for node in _kids(net, "node"):
            ref = _kid(node, "ref")
            pin = _kid(node, "pin")
            if ref is None or pin is None or len(ref) < 2 or len(pin) < 2:
                continue
            out[(_val(ref[1]), _val(pin[1]))] = name
    return out


def _auto_named(net: str) -> bool:
    """True for a name KiCad invented rather than one the design chose."""
    return net.startswith("unconnected-") or net.startswith("Net-(")


#: How far apart stacked symbols get pushed, in mm. One 2.54mm pitch, down the
#: sheet, so the separated parts still read as a column.
_UNSTACK_MM = 2.54


def _symbol_spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, ref)`` for every placed symbol block, in file order."""
    out: list[tuple[int, int, str]] = []
    for start, end in _balanced_spans_all(text, "\n  (symbol\n"):
        block = text[start:end]
        match = re.search(r'\(property "Reference" "((?:[^"\\]|\\.)*)"', block)
        out.append((start, end, match.group(1) if match else ""))
    return out


def _shift_symbol(block: str, dx: float, dy: float) -> str:
    """Move a symbol block and everything positioned inside it."""

    def move(match: re.Match[str]) -> str:
        x = _to_iu(float(match.group(1))) + _to_iu(dx)
        y = _to_iu(float(match.group(2))) + _to_iu(dy)
        return (
            f"(at {x * _SCH_IU_MM:.9g} {y * _SCH_IU_MM:.9g}{match.group(3)})"
        )

    return re.sub(
        r"\(at\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)((?:\s+[0-9.eE+-]+)?)\)", move, block
    )


def _unstack_collisions(
    text: str, truth: SourceNetlist
) -> tuple[str, list[str]]:
    """Pull apart symbols whose pins sit on the same point on different nets.

    A label cannot repair this and neither can a cut: if two pins occupy one
    coordinate, KiCad reads one node there no matter what is written on it, and
    a pass that keeps cutting and relabelling the same point runs forever.
    Measured on the 2026-08-15 exports: one point on three boards —
    hydrate-coaster's `TP1`, `TP2` and `TP3` all placed at
    ``(660.125, 496.5541)``, so the drawing ties **SWCLK, SWD and GND
    together**, which is a dead debug port and a grounded SWD line to anyone
    reading it. terminal-keyboard and harness-puck have none.

    All but the first symbol on a contested point move one pitch down the
    sheet, and every wire that landed on the old point is cut, because a wire
    arriving at a node three nets shared cannot be attributed to any of them.
    The measure/repair rounds then give each pin its own label.
    """
    tree = _parse(text)
    if not tree:
        return text, []
    root = tree[0]
    library = _library_pins(root)
    at: dict[tuple[float, float], list[tuple[str, str]]] = {}
    for place in _placements(root):
        drawn = library.get(place.lib_id) or []
        if not drawn:
            continue
        mapping, reason = symbol_pin_sources(place, drawn, truth)
        if reason:
            continue
        for pin in drawn:
            nets = {truth.pin_net.get((place.ref, s))
                    for s in mapping.get(pin.number, [])}
            nets.discard(None)
            if len(nets) != 1:
                continue
            at.setdefault(_pin_position(place, pin), []).append(
                (place.ref, next(iter(nets)))
            )

    notes: list[str] = []
    move_by: dict[str, int] = {}
    cut_at: list[tuple[float, float]] = []
    for point, members in sorted(at.items()):
        if len({net for _ref, net in members}) < 2:
            continue
        refs = sorted({ref for ref, _net in members})
        names = [truth.net_name.get(net) or _short(net)
                 for net in dict.fromkeys(net for _ref, net in members)]
        notes.append(
            f"{', '.join(refs)} share the point {point} carrying "
            f"{len(names)} different nets ({', '.join(names)}); "
            f"{len(refs) - 1} symbol(s) moved apart"
        )
        for index, ref in enumerate(refs[1:], start=1):
            move_by[ref] = max(move_by.get(ref, 0), index)
        cut_at.append(point)

    if not move_by:
        return text, notes

    for start, end, ref in sorted(_symbol_spans(text), reverse=True):
        step = move_by.get(ref)
        if not step:
            continue
        text = text[:start] + _shift_symbol(text[start:end], 0.0,
                                            step * _UNSTACK_MM) + text[end:]
    text, _cut = _cut_wires_at(text, cut_at)
    return text, notes


def normalize_schematic_truth(
    sch_path: Path,
    elements: Sequence[dict],
    *,
    read_netlist: NetlistReader | None = None,
) -> SchematicTruth:
    """Hold the exported schematic to the design's own netlist.

    Idempotent, and it never raises: a schematic that fails to normalise is
    written back exactly as the converter produced it and the failure is a
    note, because a build that dies here would cost more than a drawing that
    lies. Returns both measurements so the caller can print them.
    """
    result = SchematicTruth()
    try:
        text = sch_path.read_text(encoding="utf-8")
    except OSError as exc:
        result.notes.append(f"could not read {sch_path.name}: {exc}")
        return result

    original = text
    try:
        truth = source_netlist(elements)
        text, result = _draw_missing_symbols(text, truth, result)
        text, stacked = _unstack_collisions(text, truth)
        result.notes.extend(stacked)
        result.symbols_unstacked = len(stacked)
        if text != original:
            sch_path.write_text(text, encoding="utf-8")

        if read_netlist is None:
            result.notes.append(
                "kicad-cli netlist unavailable; missing symbols were drawn but "
                "connectivity was not compared against the design"
            )
            return result

        # Measure, repair, measure again — up to MAX_ROUNDS, and stop early
        # the moment a round changes nothing or fixes nothing. Two repairs are
        # needed in practice: cutting a shorted pin's wires strands whatever
        # was on the far end, and the far end earns its label on the next
        # round. Bounded so a pathological board cannot loop.
        comparison = _compare(sch_path, elements, truth, read_netlist)
        result.measured = True
        result.pins_compared = comparison.compared
        result.wrong_before = len(comparison.wrong)
        result.unresolved = comparison.unresolved
        result.wrong_after = result.wrong_before

        for _round in range(MAX_ROUNDS):
            if not comparison.wrong:
                break
            text = sch_path.read_text(encoding="utf-8")
            positions = _pin_positions(text)
            shorted = [
                positions[key][:2]
                for key, (got, _want) in comparison.wrong.items()
                if _SHARED_MARK in got and key in positions
            ]
            text, cut = _cut_wires_at(text, shorted)
            text, added = _stamp_labels(text, comparison, truth, positions)
            text, orphans = _drop_orphan_labels(text)
            if not (cut or added or orphans):
                break
            result.wires_cut += cut
            result.labels_added += added
            result.orphan_labels_dropped += orphans
            sch_path.write_text(text, encoding="utf-8")
            comparison = _compare(sch_path, elements, truth, read_netlist)
            result.pins_compared = comparison.compared
            result.wrong_after = len(comparison.wrong)
        result.remaining = [
            f"{ref}.{pin}: kicad reads {got}, the design says {want}"
            for (ref, pin), (got, want) in sorted(comparison.wrong.items())
        ][:20]
    except Exception as exc:  # noqa: BLE001
        result.notes.append(
            f"schematic truth pass aborted ({type(exc).__name__}: {exc}); the "
            "schematic was left exactly as the converter produced it"
        )
        try:
            sch_path.write_text(original, encoding="utf-8")
        except OSError:
            pass
    return result


@dataclass
class _Comparison:
    compared: int = 0
    #: (ref, drawn pin) -> (what kicad reads, what the design says)
    wrong: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    #: (ref, drawn pin) -> the true net id
    truth_net: dict[tuple[str, str], str] = field(default_factory=dict)
    #: net id -> the name already bound to it somewhere KiCad got right
    bound_name: dict[str, str] = field(default_factory=dict)
    #: net id -> a pin KiCad already places correctly, to anchor a rename
    anchor: dict[str, tuple[str, str]] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)


def _compare(
    sch_path: Path,
    elements: Sequence[dict],
    truth: SourceNetlist,
    read_netlist: NetlistReader,
) -> _Comparison:
    """Pin-by-pin, what KiCad reads against what the design says."""
    kicad = read_netlist(sch_path)
    tree = _parse(sch_path.read_text(encoding="utf-8"))
    if not tree:
        return _Comparison()
    root = tree[0]
    library = _library_pins(root)
    out = _Comparison()

    # Which drawn pins share a net according to the design.
    for place in _placements(root):
        drawn = library.get(place.lib_id, [])
        if not drawn:
            continue
        mapping, reason = symbol_pin_sources(place, drawn, truth)
        if reason:
            out.unresolved.append(reason)
            continue
        for pin, sources in mapping.items():
            nets = {truth.pin_net.get((place.ref, s)) for s in sources}
            nets.discard(None)
            if len(nets) != 1:
                out.unresolved.append(
                    f"{place.ref}.{pin}: source pins {sources} sit on "
                    f"{len(nets)} different nets"
                )
                continue
            out.truth_net[(place.ref, pin)] = next(iter(nets))

    # A net with only one pin cannot be drawn wrongly.
    degree: dict[str, int] = {}
    for net in out.truth_net.values():
        degree[net] = degree.get(net, 0) + 1

    reads: dict[str, set[str]] = {}
    for key, net in out.truth_net.items():
        out.compared += 1
        got = kicad.get(key)
        if got is not None and not got.startswith("unconnected-"):
            reads.setdefault(net, set()).add(got)

    for net, names in reads.items():
        preferred = truth.net_name.get(net)
        chosen = None
        if preferred and preferred in names:
            chosen = preferred
        else:
            real = sorted(n for n in names if not _auto_named(n))
            chosen = real[0] if real else sorted(names)[0]
        out.bound_name[net] = chosen

    for key, net in out.truth_net.items():
        got = kicad.get(key)
        want = truth.net_name.get(net) or out.bound_name.get(net) or f"N${_short(net)}"
        if degree.get(net, 0) < 2 and (got is None or got.startswith("unconnected-")):
            continue  # a genuinely single-ended pin; nothing to be wrong about
        if got is None or got.startswith("unconnected-"):
            out.wrong[key] = (got or "absent", want)
            continue
        # Shared name but a different net underneath, or a name KiCad gave two
        # different design nets, both show up as a disagreement on the group.
        bound = out.bound_name.get(net)
        if bound is not None and got != bound:
            out.wrong[key] = (got, want)
        elif bound is None:
            out.wrong[key] = (got, want)
        else:
            out.anchor.setdefault(net, key)

    # One drawn net carrying two design nets is a short on paper. Blame it on
    # the *minority* pins, not on everything touching the net: a crystal wired
    # onto the wrong terminals of a stock symbol ties two nets to ground, and
    # calling all 38 ground pins defective buries the two that did it — and
    # would cut 42 correct wires to repair 2 wrong ones.
    by_kicad_net: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for key, net in out.truth_net.items():
        got = kicad.get(key)
        if got and not got.startswith("unconnected-"):
            by_kicad_net.setdefault(got, {}).setdefault(net, []).append(key)
    for kname, nets in by_kicad_net.items():
        if len(nets) < 2:
            continue
        owner = max(nets, key=lambda n: (len(nets[n]), len(truth.members(n))))
        owner_name = truth.net_name.get(owner) or kname
        for net, keys in nets.items():
            if net == owner:
                continue
            for key in keys:
                # Never reuse the name the drawing already gives these pins:
                # they are on the *wrong* net, so its name is the lie. An
                # unnamed net gets a minted one instead, which is what forces
                # the two halves apart on the next round.
                want = truth.net_name.get(net) or f"N${_short(net)}"
                out.wrong[key] = (
                    f"{kname}{_SHARED_MARK}{owner_name}, "
                    f"{len(nets) - 1} other design net(s))",
                    want,
                )
    return out


def _short(net_id: str) -> str:
    digits = re.findall(r"\d+", net_id or "")
    return digits[-1] if digits else re.sub(r"\W", "", net_id or "x")[-6:]


def _stamp_labels(
    text: str,
    comparison: _Comparison,
    truth: SourceNetlist,
    positions: dict[tuple[str, str], tuple[float, float, float]] | None = None,
) -> tuple[str, int]:
    """Put the true net name on every pin KiCad reads wrongly.

    Also stamps one *anchor* on a pin the drawing already gets right, whenever
    the true name is not the name KiCad currently uses for that net — without
    it the repaired pin joins a brand-new net that happens to share a shape
    with the real one, which is a different lie, not a fix.
    """
    if positions is None:
        positions = _pin_positions(text)

    wanted: dict[tuple[str, str], str] = {}
    for key, (_got, want) in comparison.wrong.items():
        wanted[key] = want
    # Anchors: bind the chosen name onto the net's existing drawn half.
    for net, key in comparison.anchor.items():
        want = truth.net_name.get(net) or comparison.bound_name.get(net)
        if not want:
            continue
        if comparison.bound_name.get(net) == want:
            continue  # already the name KiCad uses; nothing to bind
        if any(comparison.truth_net.get(k) == net for k in wanted):
            wanted.setdefault(key, want)

    # Where each repair lands, and what it must say there.
    at_point: dict[tuple[float, float], tuple[str, float]] = {}
    for key in sorted(wanted):
        position = positions.get(key)
        if position is None:
            continue
        x, y, angle = position
        at_point[(round(x, 6), round(y, 6))] = (wanted[key], angle)
    if not at_point:
        return text, 0

    # Clear whatever the converter said at those points first. A label that
    # names the wrong net is not neutral — it is the thing holding Y1's XOUT
    # on ground — and a second copy of the right label is how a pass that runs
    # twice grows a file without changing it. Both are removed here, which is
    # also what makes the whole pass idempotent.
    text, already = _clear_labels_at(text, at_point)

    # A label lying on a bare pin is *not* a connection in KiCad — it is the
    # `isolated_pin_label` violation, and it is why the first version of this
    # repair left harness-puck's crystal reading as four unconnected pins with
    # four correct names sitting on top of them. Every repaired pin gets a
    # 2.54mm stub so the chain is pin -> wire -> label, which is what KiCad
    # bonds. Pins that already have a wire keep it.
    blocks = [
        _label_block(name, x, y, angle)
        for (x, y), (name, angle) in sorted(at_point.items())
        if (x, y, name) not in already
    ]
    text, _stubs = _ensure_stubs(text, at_point)
    if not blocks:
        return text, 0

    end = text.rstrip().rfind(")")
    if end < 0:
        return text, 0
    return text[:end] + "".join(blocks) + text[end:], len(blocks)


def _ensure_stubs(
    text: str,
    at_point: dict[tuple[float, float], tuple[str, float]],
    tolerance: float = 0.01,
) -> tuple[str, int]:
    """Give every repaired pin a wire to hang its label on, if it has none."""
    endpoints: set[tuple[float, float]] = set()
    tree = _parse(text)
    if tree:
        for wire in _kids(tree[0], "wire"):
            pts = _kid(wire, "pts")
            for point in _kids(pts or [], "xy"):
                if len(point) > 2:
                    endpoints.add((round(_num(point[1]), 6), round(_num(point[2]), 6)))

    blocks: list[str] = []
    for (x, y), (_name, angle) in sorted(at_point.items()):
        if any(abs(x - ex) <= tolerance and abs(y - ey) <= tolerance
               for ex, ey in endpoints):
            continue
        dx, dy = _outward(angle)
        # The far end lands on KiCad's grid too, so a second pass recognises
        # this stub as its own rather than stacking another one beside it.
        far_x = round((_to_iu(x) + _to_iu(dx * _STUB_MM)) * _SCH_IU_MM, 6)
        far_y = round((_to_iu(y) + _to_iu(dy * _STUB_MM)) * _SCH_IU_MM, 6)
        blocks.append(_wire_block(x, y, far_x, far_y))
        endpoints.add((round(x, 6), round(y, 6)))
    if not blocks:
        return text, 0
    end = text.rstrip().rfind(")")
    if end < 0:
        return text, 0
    return text[:end] + "".join(blocks) + text[end:], len(blocks)


_LABEL_OPENERS = ("\n  (label", "\n  (global_label", "\n  (hierarchical_label")


def _drop_orphan_labels(text: str, tolerance: float = 0.01) -> tuple[str, int]:
    """Remove labels anchored where there is neither a wire nor a pin.

    Cutting a shorted wire leaves the name that rode on it floating in the
    middle of the sheet, and a name attached to nothing is exactly the kind of
    thing a reviewer spends ten minutes deciding to ignore. It carries no
    connectivity, so removing it cannot change the netlist — which is checked
    on the next measurement rather than asserted here.
    """
    anchors: set[tuple[float, float]] = set()
    tree = _parse(text)
    if tree:
        root = tree[0]
        for wire in _kids(root, "wire"):
            pts = _kid(wire, "pts")
            for point in _kids(pts or [], "xy"):
                if len(point) > 2:
                    anchors.add((round(_num(point[1]), 2), round(_num(point[2]), 2)))
    for (x, y, _angle) in _pin_positions(text).values():
        anchors.add((round(x, 2), round(y, 2)))

    spans: list[tuple[int, int]] = []
    for opener in _LABEL_OPENERS:
        spans.extend(_balanced_spans_all(text, opener))
    removed = 0
    for start, end in sorted(spans, reverse=True):
        at_match = re.search(r"\(at\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", text[start:end])
        if at_match is None:
            continue
        x, y = round(float(at_match.group(1)), 2), round(float(at_match.group(2)), 2)
        if any(abs(x - ax) <= tolerance and abs(y - ay) <= tolerance
               for ax, ay in anchors):
            continue
        text = text[:start] + "\n" + text[end:]
        removed += 1
    return text, removed


def _clear_labels_at(
    text: str,
    at_point: dict[tuple[float, float], tuple[str, float]],
    tolerance: float = 0.01,
) -> tuple[str, set[tuple[float, float, str]]]:
    """Drop every label on a repaired point that does not already say the
    right thing, and report the ones that do so they are not duplicated."""
    keep: set[tuple[float, float, str]] = set()
    spans: list[tuple[int, int]] = []
    for opener in _LABEL_OPENERS:
        spans.extend(_balanced_spans_all(text, opener))
    for start, end in sorted(spans, reverse=True):
        block = text[start:end]
        name_match = re.search(r'\(\w*label\s+"((?:[^"\\]|\\.)*)"', block)
        at_match = re.search(
            r"\(at\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", block
        )
        if name_match is None or at_match is None:
            continue
        x, y = float(at_match.group(1)), float(at_match.group(2))
        hit = next(
            (
                point
                for point in at_point
                if abs(point[0] - x) <= tolerance and abs(point[1] - y) <= tolerance
            ),
            None,
        )
        if hit is None:
            continue
        name = name_match.group(1).replace('\\"', '"').replace("\\\\", "\\")
        if name == at_point[hit][0]:
            keep.add((hit[0], hit[1], name))
        else:
            text = text[:start] + "\n" + text[end:]
    return text, keep


def _pin_positions(text: str) -> dict[tuple[str, str], tuple[float, float, float]]:
    tree = _parse(text)
    if not tree:
        return {}
    root = tree[0]
    library = _library_pins(root)
    out: dict[tuple[str, str], tuple[float, float, float]] = {}
    for place in _placements(root):
        for pin in library.get(place.lib_id, []):
            x, y = _pin_position(place, pin)
            out[(place.ref, pin.number)] = (x, y, pin.angle + place.rotation)
    return out


def _cut_wires_at(text: str, points: Iterable[tuple[float, float]],
                  tolerance: float = 0.01) -> tuple[str, int]:
    """Delete every wire that lands on one of these points.

    The one destructive edit in this file, and it is aimed at geometry that has
    already been *proved* wrong: a pin where KiCad reads two different design
    nets as one. A label can add a connection and never remove one, so a drawn
    short survives every other repair here by construction — the wire has to
    go, and the stamped label is what carries the connection instead.

    `Device:crystal_4pin_right` is the case that forced it: the stock symbol
    numbers its pins top/bottom/left/right while the part numbers them
    XIN/case/XOUT/case, so the converter's wires arrive on the wrong terminals
    and tie both crystal nets to ground. Nothing about the drawing says which
    wire is the mistaken one — only the comparison does.
    """
    targets = [(round(x, 6), round(y, 6)) for x, y in points]
    if not targets:
        return text, 0

    def hits(block: str) -> bool:
        coords = re.findall(r"\(xy\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*\)", block)
        for sx, sy in coords:
            px, py = float(sx), float(sy)
            for tx, ty in targets:
                if abs(px - tx) <= tolerance and abs(py - ty) <= tolerance:
                    return True
        return False

    spans = _balanced_spans_all(text, "\n  (wire")
    removed = 0
    for start, end in sorted(spans, reverse=True):
        block = text[start:end]
        if hits(block):
            text = text[:start] + "\n" + text[end:]
            removed += 1
    return text, removed


def _balanced_spans_all(text: str, opener: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        found = _balanced_span(text, opener, start)
        if found is None:
            return spans
        spans.append(found)
        start = found[1]


def _draw_missing_symbols(
    text: str, truth: SourceNetlist, result: SchematicTruth
) -> tuple[str, SchematicTruth]:
    """Define every ``lib_id`` the file uses and never declares.

    Measured on terminal-keyboard: `Device:J_TYPE-C-31-M-12` is referenced by
    J1 and defined nowhere, so KiCad drops all 16 pins on the origin and reads
    VBUS/GND/D+/D- as one net. The part still has to appear on the drawing, so
    a rectangle with its real pins is drawn from `circuit.json`.
    """
    tree = _parse(text)
    if not tree:
        return text, result
    root = tree[0]
    defined = set(_library_pins(root))
    places = _placements(root)
    missing: dict[str, Placement] = {}
    for place in places:
        if place.lib_id not in defined:
            missing.setdefault(place.lib_id, place)
    if not missing:
        return text, result

    span = _balanced_span(text, "(lib_symbols")
    if span is None:
        result.notes.append(
            f"{len(missing)} symbol(s) are referenced and never defined, and the "
            "file has no lib_symbols block to add them to"
        )
        return text, result

    insert_at = text.rfind(")", span[0], span[1])
    additions: list[str] = []
    for lib_id, place in sorted(missing.items()):
        pins = [
            (pin, truth.pin_name.get((place.ref, pin), ""))
            for pin in (truth.pins.get(place.ref) or list(place.instance_pins))
        ]
        if not pins:
            result.notes.append(
                f"{lib_id} is referenced by {place.ref} and never defined, and "
                f"{place.ref} has no pins in circuit.json to draw"
            )
            continue
        prefix = re.sub(r"\d+$", "", place.ref) or "U"
        block, _ = _synth_symbol_block(lib_id, prefix, pins)
        # The instance names the library entry; the definition must match.
        additions.append(block.replace(
            f'(symbol "{_escape(lib_id.split(":")[-1])}"',
            f'(symbol "{_escape(lib_id)}"', 1))
        result.symbols_synthesised += 1
        result.synthesised_ids.append(lib_id)
    if not additions:
        return text, result
    return text[:insert_at] + "".join(additions) + text[insert_at:], result
