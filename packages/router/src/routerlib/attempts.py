"""Every routing attempt, on one line, forever.

This is day 1 of the flywheel. The plan is the ordinary one for a hard problem
nobody solves exactly: ship a good-enough mixture of algorithms, let people
route real boards, keep every attempt, and train on it. Selection first (which
expert for this board), then parameters, then net ordering, then — the reason
any of it is worth doing — a model that has seen more boards than any human
router ever will.

**The data cannot be collected retroactively.** A tournament run that is not
logged is a few thousand labelled examples thrown away, so this exists before
the algorithms it will measure.

## What a row is

One attempt = one (instance, router, params, seed) evaluated by the scorer.
Features come from the *problem*, not the solution, because the point is to
predict which router to use before running one. The score comes with its
`ruler` hash attached, and that is not decoration: a rate improves either
because the router got better or because the check set got weaker, and the
number alone cannot tell you which. Rows measured against different rulers are
not comparable and the trainer must not mix them.

## What it is deliberately not

Not a database, not a service, not compressed. Append-only JSONL, one file, so
a run that dies mid-way keeps everything it already wrote and a half-written
last line is skipped by the reader instead of poisoning the set — the same
crash-safety the build-history log needed for the same reason.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterator

#: Bump when a field changes meaning. Readers filter on it rather than guessing
#: from which keys happen to be present.
SCHEMA_VERSION = 1

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "attempts.jsonl"


def _git_head(repo: Path | None = None) -> str:
    """The commit the attempt ran against. Empty when unknown — never a guess,
    because a wrong commit makes a row actively misleading."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo or Path(__file__).resolve().parents[4]),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _git_dirty(repo: Path | None = None) -> bool:
    """A dirty tree means the commit does not describe what ran. Recorded, not
    prevented — measuring a work-in-progress is normal; hiding it is not."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo or Path(__file__).resolve().parents[4]),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _plain(value: Any) -> Any:
    """Dataclasses, tuples and Paths into something json can hold."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def features_of(problem: Any) -> dict:
    """The cheap, geometric description a selector predicts from.

    Deliberately computed from the *problem*: the whole point is to choose a
    router before paying to run one. Everything here is available the moment a
    board is placed.

    Missing attributes degrade to None rather than raising — this must never be
    the reason an attempt goes unlogged.
    """

    def get(*names, default=None):
        for name in names:
            if hasattr(problem, name):
                return getattr(problem, name)
        return default

    nets = get("nets", default=[]) or []
    obstacles = get("obstacles", default=[]) or []
    pads = [o for o in obstacles if getattr(o, "kind", "") in ("pad", "smtpad")] or obstacles

    def net_class(net):
        return str(getattr(net, "net_class", "") or getattr(net, "kind", "") or "").lower()

    gnd = [n for n in nets if "gnd" in str(getattr(n, "name", "")).lower()]
    gnd_pads = sum(len(getattr(n, "pads", []) or []) for n in gnd)
    total_pads = sum(len(getattr(n, "pads", []) or []) for n in nets) or len(pads) or 0

    width = get("width_mm", "board_width_mm")
    height = get("height_mm", "board_height_mm")
    area = (width * height) if (isinstance(width, (int, float)) and isinstance(height, (int, float))) else None

    return {
        "net_count": len(nets),
        "pad_count": len(pads) or None,
        "board_w_mm": width,
        "board_h_mm": height,
        "area_mm2": area,
        "pad_density_per_cm2": (len(pads) / (area / 100.0)) if area else None,
        "layer_count": get("layer_count", "layers"),
        "gnd_pad_fraction": (gnd_pads / total_pads) if total_pads else None,
        "has_diff_pair": any("diff" in net_class(n) for n in nets) or None,
        "power_net_count": sum(1 for n in nets if net_class(n) in ("power", "ground")) or None,
        "max_net_degree": max((len(getattr(n, "pads", []) or []) for n in nets), default=None),
        "mean_net_degree": (total_pads / len(nets)) if nets else None,
    }


def record(
    *,
    instance: str,
    router: str,
    score: Any,
    problem: Any = None,
    params: dict | None = None,
    seed: int | None = None,
    features: dict | None = None,
    path: Path | None = None,
    extra: dict | None = None,
) -> bool:
    """Append one attempt. Never raises, never blocks a routing run.

    A logger that can break a router is a logger that gets switched off, and a
    switched-off logger collects nothing. Returns whether the row was written
    so a caller may report it, but nothing is expected to check.
    """
    target = Path(path or os.environ.get("ROUTER_ATTEMPTS_PATH") or DEFAULT_PATH)
    try:
        scored = _plain(score)
        ruler = scored.get("ruler") if isinstance(scored, dict) else None
        row = {
            "schema": SCHEMA_VERSION,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "instance": instance,
            "router": router,
            "params": _plain(params or {}),
            "seed": seed,
            "features": _plain(features if features is not None else (features_of(problem) if problem is not None else {})),
            "score": scored,
            # Hoisted out of the score so a reader can bucket by ruler without
            # parsing the whole row: mixing rulers is the one thing that
            # silently corrupts a trained selector.
            "ruler_hash": (ruler or {}).get("hash") if isinstance(ruler, dict) else None,
            "git_head": _git_head(),
            "git_dirty": _git_dirty(),
            "host": platform.node(),
        }
        if extra:
            row["extra"] = _plain(extra)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read(path: Path | None = None) -> Iterator[dict]:
    """Every attempt, oldest first. Malformed lines are skipped — a truncated
    final line is the normal state of a file being appended to while read."""
    target = Path(path or os.environ.get("ROUTER_ATTEMPTS_PATH") or DEFAULT_PATH)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def comparable(rows: list[dict]) -> dict[str, list[dict]]:
    """Group attempts by ruler hash.

    The only safe way to consume this data. Two scores measured against
    different check sets are different measurements wearing the same units, and
    a trainer that mixes them learns the history of our checks rather than
    anything about routing.
    """
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(str(row.get("ruler_hash") or "unknown"), []).append(row)
    return out
