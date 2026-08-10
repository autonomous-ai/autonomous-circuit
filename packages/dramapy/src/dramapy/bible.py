"""The series bible dataclasses a project's ``series.py`` instantiates.

Contract §1: ``series.py`` holds module-level constants —

    SERIES = Series(title=..., genre=..., style=..., aspect="9:16",
                    resolution=(1080, 1920), fps=24, language="en")
    CAST = [Character(id=..., name=..., look=..., voice=..., ref_images=[]), ...]

Like everything else in dramapy these are duck-typed downstream: a plain dict
with the same keys works anywhere a ``Series``/``Character`` is accepted
(see :mod:`dramapy.spec` for the resolvers).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Series:
    title: str
    genre: str
    style: str
    aspect: str = "9:16"
    resolution: tuple[int, int] = (1080, 1920)
    fps: int = 24
    language: str = "en"


@dataclass
class Character:
    id: str
    name: str
    look: str
    voice: str
    ref_images: list[str] = field(default_factory=list)
