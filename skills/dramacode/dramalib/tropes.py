"""Genre → trope resolution over :data:`dramalib.tables.TROPE_TABLE`.

``series.genre`` keys these tables (contract §1). Compound genres like
``"revenge-romance"`` resolve on their first component that names a trope
(directly or via :data:`dramalib.tables.GENRE_ALIASES`).
"""

from __future__ import annotations

from dramalib.tables import GENRE_ALIASES, TROPE_TABLE

__all__ = ["TROPE_TABLE", "trope_for_genre"]


def trope_for_genre(*, genre: str) -> dict:
    """Resolve a genre string to its trope-table entry.

    Example::

        trope = trope_for_genre(genre="revenge-romance")
        trope["beats"]  # ['betrayal', 'awakening', ...]

    Raises ``ValueError`` when no component of ``genre`` names a known
    trope — the spec is wrong, fix the genre (or extend the table).
    """
    if not genre or not isinstance(genre, str):
        raise ValueError(f"genre must be a non-empty string, got {genre!r}")
    for part in genre.strip().lower().replace("_", "-").split("-"):
        part = part.strip()
        if not part:
            continue
        if part in TROPE_TABLE:
            return TROPE_TABLE[part]
        if part in GENRE_ALIASES:
            return TROPE_TABLE[GENRE_ALIASES[part]]
    known = ", ".join(sorted(set(TROPE_TABLE) | set(GENRE_ALIASES)))
    raise ValueError(
        f"genre {genre!r} names no known trope — use one of: {known}"
    )
