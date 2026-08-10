"""Series bible dataclasses — the shapes ``series.py`` is written in.

Contract §1: ``series.py`` holds module-level constants ``SERIES`` (a
:class:`Series`) and ``CAST`` (a list of :class:`Character`). dramapy
duck-types on attributes, never isinstance, so these are plain attribute
holders — a dict with the same keys also works.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class Series:
    """The series bible: one per project, in ``series.py``.

    ``genre`` keys the :mod:`dramalib.tropes` tables ("revenge-romance"
    resolves on its first matching component). ``style`` is the provider
    style preset ("photoreal-drama", "manhwa", "anime").
    """

    title: str
    genre: str
    style: str = "photoreal-drama"
    aspect: str = "9:16"
    resolution: tuple[int, int] = (1080, 1920)
    fps: int = 24
    language: str = "en"


@dataclass(kw_only=True)
class Character:
    """One cast member. ``ref_images`` is filled by the cast-book skill —
    leave it ``[]`` when writing a new series by hand."""

    id: str
    name: str
    look: str
    voice: str
    ref_images: list[str] = field(default_factory=list)
