"""Validation for a project's frozen golden-block content lock.

The synchronizer lives at ``scripts/sync_golden_blocks.py`` because it mutates
project source.  The build pipeline needs a read-only implementation in its
self-contained runtime: any board that imports ``blocks/`` must prove that the
complete selected block snapshot, not merely the imported TSX leaf, is intact.
That includes review documentation and third-party license/provenance files.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from circuitpy.errors import ProjectShapeError


LOCK_NAME = "golden-blocks.lock.json"
SCHEMA_VERSION = 1
BLOCK_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, file_sha in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _snapshot_files(root: Path, blocks: list[str]) -> dict[str, str]:
    entries = ["glue.tsx", *blocks]
    files: dict[str, str] = {}
    for entry in entries:
        target = root / entry
        if target.is_symlink():
            raise ProjectShapeError(
                f"golden-block snapshot contains a symbolic link: {target}"
            )
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = sorted(target.rglob("*"))
        else:
            raise ProjectShapeError(
                f"golden-block snapshot is missing blocks/{entry}"
            )
        for path in candidates:
            if path.is_symlink():
                raise ProjectShapeError(
                    f"golden-block snapshot contains a symbolic link: {path}"
                )
            if path.is_file():
                files[path.relative_to(root).as_posix()] = _sha256(path)
    return dict(sorted(files.items()))


def _imported_block_entries(imported_paths: Iterable[str]) -> set[str]:
    entries: set[str] = set()
    for relative in imported_paths:
        path = Path(relative)
        parts = path.parts
        if len(parts) < 2 or parts[0] != "blocks":
            continue
        entry = parts[1]
        entries.add("glue.tsx" if entry == "glue.tsx" else entry)
    return entries


def _load_manifest(lock: Path) -> dict[str, object]:
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectShapeError(
            f"board imports frozen blocks but has no {LOCK_NAME}; synchronize "
            "the selected golden blocks before building"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectShapeError(f"{LOCK_NAME} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectShapeError(f"{LOCK_NAME} must contain a JSON object")
    return payload


def validate_project_snapshot(
    project_root: Path,
    *,
    imported_paths: Iterable[str],
    required_blocks: Iterable[str] = (),
) -> dict[str, object] | None:
    """Validate and return the frozen-block manifest used by this board.

    Inline boards with no ``blocks/`` imports remain valid without a lock.
    The moment a board imports any project block, the lock becomes mandatory
    and every imported top-level block must be one of its selected entries.
    """

    imported_entries = _imported_block_entries(imported_paths)
    required = set(required_blocks)
    invalid_required = sorted(
        block for block in required if not BLOCK_ID.fullmatch(block)
    )
    if invalid_required:
        raise ProjectShapeError(
            "design profile requires invalid block id(s): "
            + ", ".join(invalid_required)
        )
    lock = project_root / LOCK_NAME
    if not imported_entries and not lock.exists() and not required:
        return None
    payload = _load_manifest(lock)

    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ProjectShapeError(
            f"{LOCK_NAME} has unsupported schemaVersion "
            f"{payload.get('schemaVersion')!r}"
        )
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ProjectShapeError(f"{LOCK_NAME} has no source label")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not all(isinstance(x, str) for x in blocks):
        raise ProjectShapeError(f"{LOCK_NAME} has an invalid blocks list")
    if not blocks or blocks != sorted(set(blocks)):
        raise ProjectShapeError(
            f"{LOCK_NAME} blocks must be non-empty, unique, and sorted"
        )
    invalid_block = next((block for block in blocks if not BLOCK_ID.fullmatch(block)), None)
    if invalid_block is not None:
        raise ProjectShapeError(
            f"{LOCK_NAME} contains an invalid block id: {invalid_block!r}"
        )
    if required and set(blocks) != required:
        missing = sorted(required - set(blocks))
        unexpected = sorted(set(blocks) - required)
        details = [*(f"missing {block}" for block in missing)]
        details.extend(f"unexpected {block}" for block in unexpected)
        raise ProjectShapeError(
            "design profile requires exact golden-block lock entries: "
            + "; ".join(details)
        )

    files_raw = payload.get("files")
    if not isinstance(files_raw, dict) or not files_raw:
        raise ProjectShapeError(f"{LOCK_NAME} has an invalid files map")
    files: dict[str, str] = {}
    allowed_prefixes = tuple(f"{block}/" for block in blocks)
    covered: set[str] = set()
    for relative, digest in files_raw.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ProjectShapeError(
                f"{LOCK_NAME} files must map paths to SHA-256 strings"
            )
        parts = Path(relative).parts
        if not parts or relative.startswith("/") or ".." in parts:
            raise ProjectShapeError(
                f"{LOCK_NAME} contains an unsafe path: {relative!r}"
            )
        if not SHA256.fullmatch(digest):
            raise ProjectShapeError(
                f"{LOCK_NAME} has an invalid SHA-256 for {relative}"
            )
        if relative == "glue.tsx":
            covered.add("glue.tsx")
        elif relative.startswith(allowed_prefixes):
            covered.add(relative.split("/", 1)[0])
        else:
            raise ProjectShapeError(
                f"{LOCK_NAME} contains unselected file {relative}"
            )
        files[relative] = digest
    required_entries = {"glue.tsx", *blocks}
    if covered != required_entries:
        missing = ", ".join(sorted(required_entries - covered))
        raise ProjectShapeError(
            f"{LOCK_NAME} does not cover every selected entry"
            + (f": {missing}" if missing else "")
        )
    recorded_tree = payload.get("treeSha256")
    if not isinstance(recorded_tree, str) or not SHA256.fullmatch(recorded_tree):
        raise ProjectShapeError(f"{LOCK_NAME} has an invalid treeSha256")
    if recorded_tree != _tree_sha(files):
        raise ProjectShapeError(
            f"{LOCK_NAME} treeSha256 does not match its files map"
        )

    actual = _snapshot_files(project_root / "blocks", blocks)
    missing_files = sorted(set(files) - set(actual))
    extra_files = sorted(set(actual) - set(files))
    changed_files = sorted(
        relative
        for relative in set(files) & set(actual)
        if files[relative] != actual[relative]
    )
    if missing_files or extra_files or changed_files:
        details = [*(f"missing {path}" for path in missing_files)]
        details.extend(f"unexpected {path}" for path in extra_files)
        details.extend(f"changed {path}" for path in changed_files)
        raise ProjectShapeError(
            "golden-block snapshot does not match its lock: " + "; ".join(details)
        )

    selected = set(blocks) | {"glue.tsx"}
    unselected_imports = sorted(imported_entries - selected)
    if unselected_imports:
        raise ProjectShapeError(
            "board imports block entries absent from golden-blocks.lock.json: "
            + ", ".join(unselected_imports)
        )
    missing_imports = sorted(required - imported_entries)
    if missing_imports:
        raise ProjectShapeError(
            "design profile requires board imports for protected composition: "
            + ", ".join(missing_imports)
        )
    return payload
