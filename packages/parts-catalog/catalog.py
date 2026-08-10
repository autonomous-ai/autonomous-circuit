"""Query the committed JLC parts catalog. Zero dependencies, zero network.

The catalog is a snapshot of JLCPCB's Basic + Preferred libraries — the parts
the assembler already keeps loaded (see fetch_catalog.py for why). Everything
in the design loop reads *this*, never the live service: a 47-90s cold query
cannot sit between an agent and its next edit.

    from catalog import best, find, get, basic_alternatives

    best("ldos", output_voltage_min=3.3, output_current_max=0.8)
    get("C6186")
    basic_alternatives("C25100")     # the $3/line feeder fee, avoided
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"


@lru_cache(maxsize=None)
def _load(category: str) -> tuple[dict, ...]:
    path = CATALOG_DIR / f"{category}.json"
    if not path.is_file():
        return ()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    return tuple(r for r in rows if isinstance(r, dict))


@lru_cache(maxsize=1)
def categories() -> tuple[str, ...]:
    if not CATALOG_DIR.is_dir():
        return ()
    return tuple(sorted(
        p.stem for p in CATALOG_DIR.glob("*.json") if p.stem != "index"
    ))


@lru_cache(maxsize=1)
def index() -> dict:
    path = CATALOG_DIR / "index.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _matches(row: dict, filters: dict) -> bool:
    """A filter matches when the row's value equals it, or — for numeric
    filters on ``*_min`` / ``*_max`` fields — when the row's range covers it.
    Substring match, case-insensitive, for text."""
    for key, want in filters.items():
        if want is None:
            continue
        have = row.get(key)
        if have is None:
            return False
        if isinstance(want, (int, float)) and isinstance(have, (int, float)):
            if key.endswith("_max"):
                if have < want:          # part must supply at least `want`
                    return False
            elif key.endswith("_min"):
                if have > want:          # part must go at least as low
                    return False
            elif abs(float(have) - float(want)) > 1e-9:
                return False
        elif str(want).lower() not in str(have).lower():
            return False
    return True


def find(category: str, /, limit: int = 20, **filters) -> list[dict]:
    """Matching parts, best first (Basic before extended, then by stock)."""
    rows = [r for r in _load(category) if _matches(r, filters)]
    rows.sort(key=lambda r: (not r.get("is_basic"), -(r.get("stock") or 0)))
    return rows[:limit]


def best(category: str, /, **filters) -> dict | None:
    """The one part to use, or None when the catalog has nothing matching.

    None is a real answer — it means the ask is outside what JLC keeps
    stocked, and the honest move is to say so rather than to name a part
    nobody can assemble.
    """
    hits = find(category, limit=1, **filters)
    return hits[0] if hits else None


@lru_cache(maxsize=1)
def _by_lcsc() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for category in categories():
        for row in _load(category):
            lcsc = row.get("lcsc")
            if isinstance(lcsc, str):
                out[lcsc.upper()] = {**row, "category": category}
    return out


def get(lcsc: str) -> dict | None:
    """Look a part up by C-number (with or without the leading C)."""
    key = lcsc.strip().upper()
    if not key.startswith("C"):
        key = f"C{key}"
    return _by_lcsc().get(key)


def basic_alternatives(lcsc: str, /, limit: int = 5) -> list[dict]:
    """Basic parts that could stand in for an extended one.

    Every extended part on a BOM costs about $3 in feeder setup, which on a
    five-board run is often more than the components themselves. This finds
    the same-category, same-package, same-value candidates that do not.
    Returns [] when the part is already Basic, unknown, or genuinely has no
    stocked equivalent.
    """
    part = get(lcsc)
    if part is None or part.get("is_basic"):
        return []
    category = part.get("category")
    if not category:
        return []
    filters: dict = {}
    if part.get("package"):
        filters["package"] = part["package"]
    for key in ("resistance", "capacitance", "output_voltage_min", "voltage"):
        if part.get(key) is not None:
            filters[key] = part[key]
    return [
        row for row in find(category, limit=limit + 1, **filters)
        if row.get("is_basic") and row.get("lcsc") != part.get("lcsc")
    ][:limit]


def summary() -> str:
    meta = index()
    if not meta:
        return "no catalog — run packages/parts-catalog/fetch_catalog.py"
    return (
        f"{meta.get('total_parts', 0)} stocked Basic/Preferred parts across "
        f"{len(meta.get('categories', {}))} categories "
        f"(snapshot {meta.get('fetched', 'unknown')})"
    )
