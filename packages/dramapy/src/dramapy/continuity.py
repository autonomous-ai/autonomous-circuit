"""Shot-to-shot continuity — the last-frame → first-frame chain (#34).

The single strongest continuity lever short of a video model that remembers:
seed shot N+1's image-to-video from the LAST frame of shot N, so a continuous
action run reads as one unbroken take instead of N independent generations that
drift in wardrobe, light and framing.

Two pure, provider-agnostic pieces live here; the render-loop wiring (serialize
a chain, upload the tail frame as the next shot's i2v seed) is the live step in
the cinematic provider, guarded so mock/fal are unaffected:

- ``chain_plan(episode)`` — decide WHICH adjacent shots should chain. A pure
  function over the episode; fully testable. Conservative on purpose: chaining
  the wrong pair smears a deliberate cut, which is worse than not chaining.
- ``tail_frame(clip, out)`` — extract a clip's final frame to a PNG (ffmpeg).
  Testable against any real clip, including the mock provider's.

Chaining is *within-scene only*. A scene boundary is a hard cut; carrying a
frame across it would blur two locations together. Inserts and establishes
(cutaway / reset-to-wide) break a chain on both sides. And an emotion flip
(e.g. contempt → triumph) breaks it: the whole point of the next beat is that
the face has changed — chaining would fight the performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dramapy import media

# Shot kinds that break a chain when they sit on EITHER side of a boundary: an
# insert is a deliberate cutaway, an establish resets the frame to a wide.
_CUTAWAY_KINDS = frozenset({"insert", "establish"})


@dataclass(frozen=True)
class ChainLink:
    """One directed continuity edge: seed ``to_id``'s first frame from
    ``from_id``'s last frame. ``reason`` documents why the pair qualified."""

    from_id: str
    to_id: str
    scene_id: str
    reason: str


def _primary_cast(shot) -> str:
    """The shot's lead subject — first cast id, or '' for a castless shot."""
    cast = tuple(getattr(shot, "cast", ()) or ())
    return str(cast[0]) if cast else ""


def _links_for_scene(scene) -> list[ChainLink]:
    scene_id = str(getattr(scene, "id", "") or "")
    shots = list(getattr(scene, "shots", ()) or ())
    links: list[ChainLink] = []
    for prev, nxt in zip(shots, shots[1:]):
        # A cutaway on either side is a real cut — never chain through it.
        if prev.kind in _CUTAWAY_KINDS or nxt.kind in _CUTAWAY_KINDS:
            continue
        # A shared lead subject is what makes the tail frame a valid seed.
        lead = _primary_cast(prev)
        if not lead or lead != _primary_cast(nxt):
            continue
        # An emotion change is a beat change — let the next shot generate fresh.
        prev_emotion = str(getattr(prev, "emotion", "") or "")
        next_emotion = str(getattr(nxt, "emotion", "") or "")
        if prev_emotion and next_emotion and prev_emotion != next_emotion:
            continue
        links.append(
            ChainLink(
                from_id=str(prev.id),
                to_id=str(nxt.id),
                scene_id=scene_id,
                reason=f"same-scene continuous action on '{lead}'"
                + (f", emotion held ({prev_emotion})" if prev_emotion else ""),
            )
        )
    return links


def chain_plan(*, episode) -> list[ChainLink]:
    """The episode's continuity graph — every adjacent shot pair that should
    seed the next from the previous shot's tail frame.

    Pure function; duck-types against any Episode/Scene/Shot with ``.scenes``,
    ``.shots``, ``.id``, ``.kind``, ``.cast``, ``.emotion``. Returns links in
    reading order. A shot is the ``to`` end of at most one link (each next shot
    has one immediate predecessor), so the render loop can walk chains cleanly.

    Example::

        for link in chain_plan(episode=ep):
            seed = tail_frame(clip=clips[link.from_id], out=frames / f"{link.to_id}.png")
            # render shot link.to_id with first_frame=seed
    """
    links: list[ChainLink] = []
    for scene in getattr(episode, "scenes", ()) or ():
        links.extend(_links_for_scene(scene))
    return links


def chains(*, episode) -> list[list[str]]:
    """The chain graph collapsed into runs of shot ids that render as one take.

    Each run is a maximal sequence ``[a, b, c]`` where a→b and b→c both chain.
    Shots not in any link appear as singletons, so the union of all runs is the
    full shot list in order. Useful for the render loop: render each run serially
    (seeding forward), and run separate runs concurrently.
    """
    links = {l.from_id: l.to_id for l in chain_plan(episode=episode)}
    order: list[str] = [
        str(shot.id)
        for scene in getattr(episode, "scenes", ()) or ()
        for shot in (getattr(scene, "shots", ()) or ())
    ]
    successors = set(links.values())
    runs: list[list[str]] = []
    for shot_id in order:
        if shot_id in successors:
            continue  # already the tail of a run started upstream
        run = [shot_id]
        cur = shot_id
        while cur in links:
            cur = links[cur]
            run.append(cur)
        runs.append(run)
    return runs


def tail_frame(*, clip: Path | str, out: Path | str, timeout: float | None = 60.0) -> Path:
    """Extract ``clip``'s final frame to ``out`` (PNG). Returns ``out``.

    Uses ``-sseof -N`` to seek near the end and grabs the last decoded frame, so
    it is O(1) regardless of clip length. Raises ``RuntimeError`` (via
    ``media.run_ffmpeg``) if ffmpeg fails or the input has no video stream.
    """
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Seek to the last ~0.5s, keep only the final frame. -update writes a single
    # image; -q:v 2 keeps it near-lossless as an i2v seed.
    media.run_ffmpeg(
        [
            "-sseof", "-0.5",
            "-i", str(clip),
            "-update", "1",
            "-frames:v", "1",
            "-q:v", "2",
            "-y", str(out_path),
        ],
        timeout=timeout,
    )
    if not out_path.is_file():  # pragma: no cover — ffmpeg success implies the file
        raise RuntimeError(f"tail_frame produced no output for {clip}")
    return out_path
