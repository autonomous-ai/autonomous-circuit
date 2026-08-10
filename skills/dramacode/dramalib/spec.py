"""Episode spec dataclasses — the shape ``gen_episode()`` returns.

Contract §1: dramapy duck-types on attributes (mirroring the donor's
``.wrapped`` rule), so a plain dict with the same keys also works. These
dataclasses are the convenient way to write the spec; they carry no
behavior and validate nothing — validation is dramapy's job
(``duration_s`` in [1.5, 15], dialogue requires ``cast`` + ``line``,
every cast id must exist in ``series.CAST``).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class Shot:
    """One generated clip. ``kind`` is one of ``establish | action |
    dialogue | insert``. ``dialogue`` shots MUST carry ``cast`` and
    ``line``. The skill's hard cap is 10s (tables.SHOT_HARD_CAP_S).

    ``sfx`` is the per-shot sound-effects cue list — 1-3 phrases the engine
    lays on the shot (a peak plus a texture). Build it with
    ``dramalib.helpers.sfx_for(moment=…)``; it defaults to empty and is
    ignored by any engine build that predates sfx support (the pipeline
    duck-types on the fields it knows), so it is always safe to author.
    """

    id: str
    kind: str
    duration_s: float
    prompt: str = ""
    cast: list[str] = field(default_factory=list)
    line: str | None = None
    emotion: str | None = None
    sfx: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class Scene:
    """One location. Scenes exist for asset reuse — 1-3 per episode."""

    id: str
    location: str
    shots: list[Shot] = field(default_factory=list)


@dataclass(kw_only=True)
class Episode:
    """One generation target. ``cliffhanger`` names the final freeze; the
    verifier warns (``no_cliffhanger``) when it is unset. ``hook_max_s``
    is the cold-open rule the ``hook_too_long`` verifier reads."""

    number: int
    title: str
    scenes: list[Scene] = field(default_factory=list)
    hook_max_s: float = 3.0
    cliffhanger: str | None = None
    bgm: str | None = None
    burn_subtitles: bool = True
