"""Content-addressed export cache under ``<project>/.circuit/export-cache/``.

An exported artifact (gerbers zip, kicad_sch/kicad_pcb conversion, glb) is
keyed by sha256 of the canonical JSON of everything that determines its
bytes: the circuit.json content hash, the export kind, the toolchain
versions, and the fab profile. Tool versions in the key are the honest
invalidation dial — upgrading tscircuit re-exports everything once.

Best-effort by design: a miss just re-runs the exporter; a hit saves the
~5-10s subprocess. The full-build no-op case is handled one level up by the
idempotent short-circuit in :func:`circuitpy.generation.build_board`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

CACHE_VERSION = 1
CACHE_DIR_PARTS = (".circuit", "export-cache")


def export_cache_dir(project_root: Path) -> Path:
    return project_root.joinpath(*CACHE_DIR_PARTS)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_key(
    *,
    circuit_json_sha: str,
    kind: str,
    versions: dict[str, str | None],
    fab: str,
) -> str:
    payload = {
        "cacheVersion": CACHE_VERSION,
        "circuitJsonSha": circuit_json_sha,
        "kind": kind,
        "versions": versions,
        "fab": fab,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_path(project_root: Path, key: str, suffix: str) -> Path:
    return export_cache_dir(project_root) / f"{key}{suffix}"


def lookup(project_root: Path, key: str, suffix: str) -> Path | None:
    """The cached artifact for ``key``, or ``None``. Empty files never hit."""
    candidate = cache_path(project_root, key, suffix)
    try:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    except OSError:
        return None
    return None


def store(project_root: Path, key: str, suffix: str, artifact: Path) -> Path | None:
    """Copy a freshly exported artifact into the cache (best-effort atomic;
    failures are swallowed — the cache must never break a build).

    The scratch file is named per-process because builds run concurrently
    (``batch.build_many``). A shared ``.part`` name lets two writers interleave
    into one file and then publish the mix; the key is content-addressed, so
    per-writer scratch plus an atomic rename means whichever wins is correct.
    """
    partial: Path | None = None
    try:
        target = cache_path(project_root, key, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(f"{target.suffix}.{os.getpid()}.part")
        shutil.copy2(artifact, partial)
        partial.replace(target)
        return target
    except OSError:
        if partial is not None:
            partial.unlink(missing_ok=True)
        return None
