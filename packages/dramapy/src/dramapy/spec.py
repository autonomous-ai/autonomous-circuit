"""Episode/Scene/Shot spec dataclasses + duck-typed resolution and validation.

Mirrors the donor's ``.wrapped`` duck-typing rule: resolution is
**attribute-based, never isinstance-based** — a plain dict with the same keys
is accepted anywhere an ``Episode``/``Scene``/``Shot``/``Series``/``Character``
is (contract §1). ``resolve_episode`` normalizes whatever the project returned
into frozen ``Resolved*`` dataclasses and enforces the shot rules:

  * ``duration_s`` within [1.5, 15]
  * ``kind`` in {establish, action, dialogue, insert}
  * ``dialogue`` requires ``cast`` and ``line``
  * every ``cast`` id must exist in the series bible's ``CAST``

Violations raise :class:`dramapy.errors.SpecValidationError` with the shot id
in the message.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from dramapy.errors import ProjectShapeError, SpecValidationError

SHOT_KINDS = ("establish", "action", "dialogue", "insert")
SHOT_MIN_DURATION_S = 1.5
SHOT_MAX_DURATION_S = 15.0

# Shot ids become file names (shot_<id>.mp4) and cache keys; keep them safe.
_ID_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

_MISSING = object()


# ---------------------------------------------------------------------------
# Author-facing dataclasses (what episodes/epNNN.py instantiates).
# ---------------------------------------------------------------------------


@dataclass
class Shot:
    id: str
    kind: str
    duration_s: float
    prompt: str = ""
    cast: list[str] = field(default_factory=list)
    line: str | None = None
    emotion: str | None = None


@dataclass
class Scene:
    id: str
    location: str = ""
    shots: list = field(default_factory=list)


@dataclass
class Episode:
    number: int = 1
    title: str = ""
    scenes: list = field(default_factory=list)
    hook_max_s: float = 5.0
    cliffhanger: str | None = None
    bgm: str | None = None
    burn_subtitles: bool = False


# ---------------------------------------------------------------------------
# Resolved (normalized, validated) forms used by the pipeline.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedShot:
    id: str
    kind: str
    duration_s: float
    prompt: str
    cast: tuple[str, ...]
    line: str | None
    emotion: str | None


@dataclass(frozen=True)
class ResolvedScene:
    id: str
    location: str
    shots: tuple[ResolvedShot, ...]


@dataclass(frozen=True)
class ResolvedEpisode:
    number: int
    title: str
    hook_max_s: float
    scenes: tuple[ResolvedScene, ...]
    cliffhanger: str | None
    bgm: str | None
    burn_subtitles: bool

    @property
    def shots(self) -> tuple[ResolvedShot, ...]:
        return tuple(shot for scene in self.scenes for shot in scene.shots)

    @property
    def spec_duration_s(self) -> float:
        return sum(shot.duration_s for shot in self.shots)


@dataclass(frozen=True)
class ResolvedSeries:
    title: str
    genre: str
    style: str
    aspect: str
    resolution: tuple[int, int]
    fps: int
    language: str


@dataclass(frozen=True)
class ResolvedCharacter:
    id: str
    name: str
    look: str
    voice: str
    ref_images: tuple[str, ...]


# ---------------------------------------------------------------------------
# Duck-typed field access.
# ---------------------------------------------------------------------------


def _get(obj: object, name: str, default: object = _MISSING) -> object:
    """Attribute-based field access; mappings with the same keys also work."""
    if isinstance(obj, Mapping):
        value = obj.get(name, _MISSING)
    else:
        value = getattr(obj, name, _MISSING)
    if value is _MISSING:
        return default
    return value


def _require(obj: object, name: str, *, where: str) -> object:
    value = _get(obj, name)
    if value is _MISSING:
        raise SpecValidationError(f"{where} is missing required field '{name}'")
    return value


def _as_str(value: object, *, name: str, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SpecValidationError(
            f"{where}: '{name}' must be a string (got {type(value).__name__})"
        )
    if not allow_empty and not value.strip():
        raise SpecValidationError(f"{where}: '{name}' must be a non-empty string")
    return value


def _as_number(value: object, *, name: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecValidationError(
            f"{where}: '{name}' must be a number (got {type(value).__name__})"
        )
    return float(value)


def _as_items(value: object, *, name: str, where: str) -> list:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SpecValidationError(
            f"{where}: '{name}' must be a list (got {type(value).__name__})"
        )
    return list(value)


def _safe_id(value: object, *, name: str, where: str) -> str:
    text = _as_str(value, name=name, where=where)
    if not all(ch in _ID_SAFE for ch in text):
        raise SpecValidationError(
            f"{where}: '{name}' must contain only letters, digits, '_' or '-' "
            f"(got {text!r}) — ids become file names and cache keys"
        )
    return text


# ---------------------------------------------------------------------------
# Resolution + validation.
# ---------------------------------------------------------------------------


def resolve_shot(raw: object, *, scene_id: str, cast_ids: frozenset[str]) -> ResolvedShot:
    where = f"scene '{scene_id}' shot"
    shot_id = _safe_id(_require(raw, "id", where=where), name="id", where=where)
    where = f"shot '{shot_id}'"

    kind = _as_str(_require(raw, "kind", where=where), name="kind", where=where)
    if kind not in SHOT_KINDS:
        raise SpecValidationError(
            f"{where}: kind must be one of {', '.join(SHOT_KINDS)} (got {kind!r})"
        )

    duration_s = _as_number(
        _require(raw, "duration_s", where=where), name="duration_s", where=where
    )
    if not (SHOT_MIN_DURATION_S <= duration_s <= SHOT_MAX_DURATION_S):
        raise SpecValidationError(
            f"{where}: duration_s must be within "
            f"[{SHOT_MIN_DURATION_S:g}, {SHOT_MAX_DURATION_S:g}] (got {duration_s:g})"
        )

    prompt = _as_str(_get(raw, "prompt", ""), name="prompt", where=where, allow_empty=True)

    raw_cast = _get(raw, "cast", ())
    cast_list = _as_items(raw_cast, name="cast", where=where) if raw_cast else []
    cast: list[str] = []
    for member in cast_list:
        member_id = _as_str(member, name="cast[]", where=where)
        if member_id not in cast_ids:
            raise SpecValidationError(
                f"{where}: cast id '{member_id}' does not exist in series CAST "
                f"(known: {', '.join(sorted(cast_ids)) or 'none'})"
            )
        cast.append(member_id)

    raw_line = _get(raw, "line", None)
    line = (
        None
        if raw_line is None
        else _as_str(raw_line, name="line", where=where)
    )
    raw_emotion = _get(raw, "emotion", None)
    emotion = (
        None
        if raw_emotion is None
        else _as_str(raw_emotion, name="emotion", where=where)
    )

    if kind == "dialogue":
        if not cast:
            raise SpecValidationError(f"{where}: dialogue shots require 'cast'")
        if line is None:
            raise SpecValidationError(f"{where}: dialogue shots require 'line'")

    return ResolvedShot(
        id=shot_id,
        kind=kind,
        duration_s=duration_s,
        prompt=prompt,
        cast=tuple(cast),
        line=line,
        emotion=emotion,
    )


def resolve_scene(raw: object, *, cast_ids: frozenset[str]) -> ResolvedScene:
    scene_id = _safe_id(_require(raw, "id", where="scene"), name="id", where="scene")
    where = f"scene '{scene_id}'"
    location = _as_str(
        _get(raw, "location", ""), name="location", where=where, allow_empty=True
    )
    raw_shots = _as_items(_require(raw, "shots", where=where), name="shots", where=where)
    if not raw_shots:
        raise SpecValidationError(f"{where}: 'shots' must not be empty")
    shots = tuple(
        resolve_shot(raw_shot, scene_id=scene_id, cast_ids=cast_ids)
        for raw_shot in raw_shots
    )
    return ResolvedScene(id=scene_id, location=location, shots=shots)


def resolve_episode(raw: object, *, cast_ids: frozenset[str]) -> ResolvedEpisode:
    where = "episode"
    number = _as_number(_require(raw, "number", where=where), name="number", where=where)
    if number != int(number) or number < 0:
        raise SpecValidationError(f"{where}: 'number' must be a non-negative integer")
    title = _as_str(_require(raw, "title", where=where), name="title", where=where)
    hook_max_s = _as_number(
        _get(raw, "hook_max_s", 5.0), name="hook_max_s", where=where
    )
    if hook_max_s <= 0:
        raise SpecValidationError(f"{where}: 'hook_max_s' must be > 0")

    raw_scenes = _as_items(
        _require(raw, "scenes", where=where), name="scenes", where=where
    )
    if not raw_scenes:
        raise SpecValidationError(f"{where}: 'scenes' must not be empty")
    scenes = tuple(resolve_scene(raw_scene, cast_ids=cast_ids) for raw_scene in raw_scenes)

    seen: set[str] = set()
    for scene in scenes:
        for shot in scene.shots:
            if shot.id in seen:
                raise SpecValidationError(
                    f"shot id '{shot.id}' is used more than once — "
                    "shot ids must be unique across the episode"
                )
            seen.add(shot.id)

    raw_cliff = _get(raw, "cliffhanger", None)
    cliffhanger = (
        None
        if raw_cliff is None or (isinstance(raw_cliff, str) and not raw_cliff.strip())
        else _as_str(raw_cliff, name="cliffhanger", where=where)
    )
    raw_bgm = _get(raw, "bgm", None)
    bgm = None if raw_bgm is None else _as_str(raw_bgm, name="bgm", where=where)
    burn_subtitles = bool(_get(raw, "burn_subtitles", False))

    return ResolvedEpisode(
        number=int(number),
        title=title,
        hook_max_s=hook_max_s,
        scenes=scenes,
        cliffhanger=cliffhanger,
        bgm=bgm,
        burn_subtitles=burn_subtitles,
    )


def resolve_series(raw: object) -> ResolvedSeries:
    """Normalize a project's ``series.SERIES``.

    Series problems are *project shape* problems (the bible is part of the
    workspace layout), so they raise :class:`ProjectShapeError`, not
    :class:`SpecValidationError`.
    """
    where = "series.py SERIES"

    def require(name: str) -> object:
        value = _get(raw, name)
        if value is _MISSING:
            raise ProjectShapeError(f"{where} is missing required field '{name}'")
        return value

    try:
        title = _as_str(require("title"), name="title", where=where)
        genre = _as_str(require("genre"), name="genre", where=where)
        style = _as_str(require("style"), name="style", where=where)
        aspect = _as_str(_get(raw, "aspect", "9:16"), name="aspect", where=where)
        language = _as_str(_get(raw, "language", "en"), name="language", where=where)
    except SpecValidationError as exc:
        raise ProjectShapeError(str(exc)) from exc

    raw_resolution = _get(raw, "resolution", (1080, 1920))
    try:
        width, height = (int(raw_resolution[0]), int(raw_resolution[1]))
    except (TypeError, ValueError, IndexError) as exc:
        raise ProjectShapeError(
            f"{where}: 'resolution' must be a (width, height) pair"
        ) from exc
    if width <= 0 or height <= 0:
        raise ProjectShapeError(f"{where}: 'resolution' must be positive")
    if width % 2 or height % 2:
        raise ProjectShapeError(
            f"{where}: 'resolution' dimensions must be even for H.264 yuv420p "
            f"(got {width}x{height})"
        )

    parts = aspect.split(":")
    if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
        raise ProjectShapeError(f"{where}: 'aspect' must look like '9:16' (got {aspect!r})")

    fps_value = _get(raw, "fps", 24)
    if isinstance(fps_value, bool) or not isinstance(fps_value, (int, float)):
        raise ProjectShapeError(f"{where}: 'fps' must be a number")
    fps = int(fps_value)
    if fps <= 0 or fps != fps_value:
        raise ProjectShapeError(f"{where}: 'fps' must be a positive integer")

    return ResolvedSeries(
        title=title,
        genre=genre,
        style=style,
        aspect=aspect,
        resolution=(width, height),
        fps=fps,
        language=language,
    )


def resolve_cast(raw: object) -> tuple[ResolvedCharacter, ...]:
    """Normalize a project's ``series.CAST`` list (missing/empty is fine)."""
    if raw is None or raw is _MISSING:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ProjectShapeError("series.py CAST must be a list of Character entries")
    members: list[ResolvedCharacter] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        where = f"series.py CAST[{index}]"

        def require(name: str) -> object:
            value = _get(entry, name)
            if value is _MISSING:
                raise ProjectShapeError(f"{where} is missing required field '{name}'")
            return value

        try:
            member_id = _safe_id(require("id"), name="id", where=where)
            name = _as_str(require("name"), name="name", where=where)
            look = _as_str(_get(entry, "look", ""), name="look", where=where, allow_empty=True)
            voice = _as_str(
                _get(entry, "voice", ""), name="voice", where=where, allow_empty=True
            )
        except SpecValidationError as exc:
            raise ProjectShapeError(str(exc)) from exc
        raw_refs = _get(entry, "ref_images", ()) or ()
        if isinstance(raw_refs, (str, bytes)) or not isinstance(raw_refs, Sequence):
            raise ProjectShapeError(f"{where}: 'ref_images' must be a list")
        ref_images = tuple(str(item) for item in raw_refs)
        if member_id in seen:
            raise ProjectShapeError(f"{where}: duplicate cast id '{member_id}'")
        seen.add(member_id)
        members.append(
            ResolvedCharacter(
                id=member_id, name=name, look=look, voice=voice, ref_images=ref_images
            )
        )
    return tuple(members)
