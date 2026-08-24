"""The golden-block library, and whether a project is still holding its hand.

Every project owns a frozen copy of `blocks/` and that is deliberate — a board
keeps rebuilding the same after the shared library moves on. What is *not*
deliberate is where the copy comes from. Measured 2026-08-21: eight projects
created across two and a half days (weather-badge-16 through -25) carry
byte-identical blocks, all stamped with wb-16's creation minute, none matching
the library. New boards had been cloning the previous board's copy. One fix —
the tactile switch's internal pairing, which shorted every button on every
board that placed one — sat in the library for the whole afternoon and reached
nothing.

So: the library is the source of truth for a *new* project (`seed_blocks`), and
a build says out loud when a project's copy has drifted (`drift_warnings`).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Iterable

#: A block is a directory holding `<id>/<id>.tsx`. `glue.tsx` sits at the top.
_TSX = "*.tsx"


def library_root(start: Path | None = None) -> Path | None:
    """Locate the golden-block library, or ``None``.

    **Found, never computed.** `fab.catalog_root` carries the scar for this:
    its first version counted `parents[n]` from `__file__`, which was right in
    the repo and wrong the moment the package was vendored one level deeper
    into the skill — the catalog came back empty and every BOM shipped a blank
    Footprint column, silently. So walk the parents and *look* for the
    directory, in both layouts it legitimately lives in:

    - vendored: ``skills/circuitcode/blocks``
    - repo:     ``packages/golden-blocks/blocks``

    A project's own ``blocks/`` is refused outright rather than merely being
    unreachable: a board directory holds `product.json` beside its `blocks/`,
    and grading a project against itself would report every board as perfectly
    in sync forever. The default walk starts at this file, which lives in
    neither a project nor above one, so the guard is a belt on braces — but
    ``start`` is a parameter, and this is the failure it would produce.
    """
    override = os.environ.get("CIRCUIT_BLOCK_LIBRARY")
    candidates: list[Path] = [Path(override).expanduser()] if override else []
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        candidates.append(parent / "blocks")
        candidates.append(parent / "golden-blocks" / "blocks")
    for candidate in candidates:
        try:
            if (candidate / ".." / "product.json").resolve().exists():
                continue  # this is a board's own frozen copy, not the library
            if candidate.is_dir() and any(candidate.glob(f"*/{_TSX}")):
                return candidate
        except OSError:
            continue
    return None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _block_map(root: Path) -> dict[str, str]:
    """`<block id>/<file>` -> digest, for every `.tsx` under a block tree.

    Only `.tsx` is compared. BLOCK.md and REVIEW.md are documentation — they
    drift for reasons that never change a board, and a check that fires on
    prose is a check people learn to scroll past.
    """
    out: dict[str, str] = {}
    try:
        for tsx in sorted(root.rglob(_TSX)):
            if not tsx.is_file():
                continue
            try:
                out[str(tsx.relative_to(root))] = _digest(tsx)
            except OSError:
                continue
    except OSError:
        pass
    return out


def seed_blocks(project_root: Path, library: Path | None = None) -> list[str]:
    """Copy the library into a project that has no ``blocks/`` yet.

    Returns the block ids written, or ``[]`` when the project already has a
    copy. **Never overwrites.** A project that already holds blocks is a board
    with a history — re-syncing it changes what its source means without
    leaving a way back, which is the one move this pipeline has been burned by
    before. Re-syncing an existing board is an explicit, separate act.
    """
    lib = library or library_root()
    if lib is None:
        return []
    dest = project_root / "blocks"
    if dest.exists():
        return []
    shutil.copytree(lib, dest)
    return sorted(p.name for p in dest.iterdir() if p.is_dir())


def drift_warnings(
    project_root: Path,
    *,
    is_first_build: bool,
    library: Path | None = None,
) -> list[dict[str, str]]:
    """Say whether this board's blocks still match the library, and how it got
    that way.

    Two readings, and they are not the same news:

    - **A board that has built before and drifted** is the freeze working. It
      is history, and re-syncing it is the owner's call, so: ``info``.
    - **A board on its first build that is already drifting** cannot be
      history — nothing has frozen yet. Its copy came from somewhere other
      than the library, which is exactly #29's signature, so: ``warning``.
    """
    lib = library or library_root()
    dest = project_root / "blocks"
    # An unreadable library is not an empty one. Without this, `want` comes
    # back empty, nothing differs from nothing, and the check reports a clean
    # board — the silent-pass shape this repo keeps paying for. A test caught
    # exactly that here before the code ever ran on a board.
    if lib is None or not _block_map(lib):
        return [{
            "part": "board",
            "kind": "block_library_unavailable",
            "detail": (
                f"the golden-block library holds no blocks at "
                f"{lib if lib is not None else '(not found beside this package)'}"
                f", so nothing checked whether this board's blocks/ still "
                f"matches it. Point CIRCUIT_BLOCK_LIBRARY at the library "
                f"directory. This is not a clean result — it is no result"
            ),
            "severity": "info",
        }]
    if not dest.is_dir():
        return []
    have, want = _block_map(dest), _block_map(lib)
    changed = sorted(k for k in have.keys() & want.keys() if have[k] != want[k])
    missing = sorted(want.keys() - have.keys())
    extra = sorted(have.keys() - want.keys())
    if not (changed or missing):
        return []
    parts = []
    if changed:
        parts.append(f"{len(changed)} differ ({', '.join(changed[:4])}"
                     f"{', ...' if len(changed) > 4 else ''})")
    if missing:
        parts.append(f"{len(missing)} absent here ({', '.join(missing[:3])}"
                     f"{', ...' if len(missing) > 3 else ''})")
    if extra:
        parts.append(f"{len(extra)} this board has and the library does not")
    summary = "; ".join(parts)
    if is_first_build:
        return [{
            "part": "board",
            "kind": "block_library_not_seeded",
            "detail": (
                f"this board has never been built, and its blocks/ already "
                f"disagrees with the library: {summary}. A first build cannot "
                f"have drifted — the copy came from somewhere other than the "
                f"library. Measured 2026-08-21: eight boards in a row inherited "
                f"one snapshot this way and a switch fix reached none of them"
            ),
            "severity": "warning",
        }]
    return [{
        "part": "board",
        "kind": "block_library_drift",
        "detail": (
            f"this board's blocks/ no longer matches the library: {summary}. "
            f"That is the freeze doing its job — the board keeps building the "
            f"way it always did. Re-syncing it is a deliberate act, not "
            f"something a build should do behind you"
        ),
        "severity": "info",
    }]
