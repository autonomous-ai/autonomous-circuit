"""Pick real, stocked parts instead of inventing part numbers.

The catalog behind this is a snapshot of JLCPCB's **Basic and Preferred**
libraries — the components the assembler already keeps loaded. That list is
not arbitrary: it is filtered by real demand and real stock, and Basic parts
carry no per-line feeder fee, so designing from it is both cheaper to build
and more likely to actually get built.

Use it for **glue** — the passives, LEDs and connectors a board needs around
its blocks. Do not use it to pick an IC: ICs come from golden blocks with
pinned parts, because a part number is the easy half and the land pattern and
pinout are the half that kills boards.

    from circuitlib.parts import pick, lookup, cheaper_basic_part

    pick("resistors", resistance=4700)       # -> the stocked 4.7k everyone uses
    lookup("C6186")                          # -> what is this part?
    cheaper_basic_part("C25100")             # -> avoid the ~$3/line feeder fee

Returns ``None`` when the catalog has nothing matching. That is a real answer:
it means the ask is outside what JLC stocks, and the honest move is to say so
rather than to name a part nobody can assemble.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
#: Vendored copy first (the skill must be self-contained at runtime), then the
#: repo package for dev-before-vendoring.
_CANDIDATES = (
    _SKILL_ROOT / "parts_catalog",
    _SKILL_ROOT.parents[1] / "packages" / "parts-catalog",
)

_catalog = None
for _candidate in _CANDIDATES:
    if (_candidate / "catalog.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        try:
            import catalog as _catalog  # type: ignore[no-redef]
        except ImportError:  # pragma: no cover - defensive
            _catalog = None
        if _catalog is not None:
            break


def available() -> bool:
    """False when no catalog snapshot is present. Callers should say so rather
    than silently falling back to guessing a part number."""
    return _catalog is not None and bool(_catalog.categories())


def summary() -> str:
    if not available():
        return (
            "no parts catalog vendored — run scripts/build/build-skill-runtimes.sh "
            "(and packages/parts-catalog/fetch_catalog.py to refresh the snapshot)"
        )
    return _catalog.summary()


def categories() -> tuple[str, ...]:
    return _catalog.categories() if available() else ()


def pick(category: str, /, **filters) -> dict | None:
    """The one part to use for this slot, or None when nothing stocked fits."""
    if not available():
        return None
    return _catalog.best(category, **filters)


def options(category: str, /, limit: int = 10, **filters) -> list[dict]:
    """Candidates, best first (Basic before extended, then by stock depth)."""
    if not available():
        return []
    return _catalog.find(category, limit=limit, **filters)


def lookup(lcsc: str) -> dict | None:
    """What is this C-number?"""
    if not available():
        return None
    return _catalog.get(lcsc)


def cheaper_basic_part(lcsc: str) -> dict | None:
    """A Basic part that could replace an extended one, or None.

    Each extended BOM line costs roughly $3 in feeder setup — on a five-board
    run that is often more than the components themselves.
    """
    if not available():
        return None
    alternatives = _catalog.basic_alternatives(lcsc)
    return alternatives[0] if alternatives else None


def savings_report(lcsc_numbers: list[str]) -> list[dict[str, str]]:
    """Contract-shaped ``extended_part`` advisories for a BOM's part numbers,
    naming the Basic replacement where one exists. Never raises."""
    out: list[dict[str, str]] = []
    if not available():
        return out
    for lcsc in lcsc_numbers:
        try:
            part = _catalog.get(lcsc)
            if part is None or part.get("is_basic"):
                continue
            swap = cheaper_basic_part(lcsc)
            if swap is None:
                continue
            out.append({
                "part": lcsc,
                "kind": "extended_part",
                "severity": "info",
                "detail": (
                    f"{lcsc} is an extended part (~$3 feeder fee per line); "
                    f"{swap['lcsc']} is Basic in the same package"
                ),
            })
        except Exception:  # pragma: no cover - advisory must never break a build
            continue
    return out
