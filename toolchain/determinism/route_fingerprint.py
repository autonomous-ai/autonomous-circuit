"""A route fingerprint: the copper, with the names taken off.

Two builds of the same board are "the same route" when the copper lands in the
same places. They are almost never the same *bytes*, because tscircuit mints a
random 10-character id for every element it creates, so a byte comparison of
``circuit.json`` reports a difference on every run and tells you nothing.

So the comparison is done on geometry:

* ``route_bytes`` — the exact bytes of every ``pcb_trace``/``pcb_via`` element
  as the file writes them, ids included, in file order. This is the strict
  reading of "byte-identical routes" and it is the acceptance bar.
* ``geometry`` — every routed segment and via, rounded to 1 nm, sorted, with
  every id and every name removed. The same question asked of the copper
  alone, so a route that is identical but renamed still reads as identical.
* ``ordered``  — the same segments in the order the file lists them, so a run
  that finds the same copper by a different search order is still visible.
* ``bytes``    — sha256 of the whole file as written. Strictest of all: it
  also covers everything the router did not produce.

Nothing here imports circuitpy: it reads a ``circuit.json`` and nothing else,
so it can be pointed at any build (pipeline, bare CLI, an old artifact).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

#: 1 nm. tscircuit works in mm and carries float noise well below this; a
#: rounding coarser than the fab's resolution would hide a real difference.
_QUANTUM = 1e-6

#: Element fields that carry a minted id or a minted name. Stripped before
#: hashing so a route is compared as copper, not as bookkeeping.
_ID_SUFFIXES = ("_id", "_ids")
_NAME_KEYS = frozenset({"name", "source_trace_id", "subcircuit_id"})

#: The element types that ARE the route. Everything else (footprints, silk,
#: source graph) is input to the router, not output from it.
ROUTE_TYPES = ("pcb_trace", "pcb_via", "pcb_smtpad", "pcb_plated_hole")
COPPER_TYPES = ("pcb_trace", "pcb_via")


def _q(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(float(value) / _QUANTUM) * _QUANTUM


def _strip(value: Any) -> Any:
    """Recursively drop minted ids/names and quantise every number."""
    if isinstance(value, dict):
        out = {}
        for key, sub in value.items():
            if key.endswith(_ID_SUFFIXES) or key in _NAME_KEYS:
                continue
            out[key] = _strip(sub)
        return out
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return _q(value)


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_elements(circuit_json: Path) -> list[dict]:
    data = json.loads(Path(circuit_json).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{circuit_json} is not a circuit.json element array")
    return [e for e in data if isinstance(e, dict)]


def copper_records(elements: list[dict]) -> list[str]:
    """One canonical string per piece of routed copper."""
    records: list[str] = []
    for element in elements:
        if element.get("type") not in COPPER_TYPES:
            continue
        records.append(_canon(_strip(element)))
    return records


def route_byte_records(elements: list[dict]) -> list[str]:
    """The route elements exactly as the file carries them, ids and all."""
    return [
        json.dumps(e, sort_keys=True, separators=(",", ":"))
        for e in elements
        if e.get("type") in COPPER_TYPES
    ]


def fingerprint(circuit_json: Path) -> dict[str, Any]:
    path = Path(circuit_json)
    raw = path.read_bytes()
    elements = load_elements(path)
    records = copper_records(elements)
    strict = route_byte_records(elements)
    counts: dict[str, int] = {}
    for element in elements:
        kind = str(element.get("type"))
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "path": str(path),
        "bytes": hashlib.sha256(raw).hexdigest(),
        "route_bytes": _sha(strict),
        "geometry": _sha(sorted(records)),
        "ordered": _sha(records),
        "copper_elements": len(records),
        "elements": len(elements),
        "counts": {k: counts[k] for k in sorted(counts) if k.startswith("pcb_")},
    }


def diff_summary(a: Path, b: Path, *, limit: int = 6) -> list[str]:
    """The first few pieces of copper present in one build and not the other."""
    ra = set(copper_records(load_elements(a)))
    rb = set(copper_records(load_elements(b)))
    only_a = sorted(ra - rb)
    only_b = sorted(rb - ra)
    lines = [f"only in A: {len(only_a)}   only in B: {len(only_b)}"]
    for rec in only_a[:limit]:
        lines.append(f"  A only: {rec[:200]}")
    for rec in only_b[:limit]:
        lines.append(f"  B only: {rec[:200]}")
    return lines


if __name__ == "__main__":  # pragma: no cover - a hand tool
    import sys

    for arg in sys.argv[1:]:
        print(json.dumps(fingerprint(Path(arg)), sort_keys=True))
