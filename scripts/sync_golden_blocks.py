#!/usr/bin/env python3
"""Synchronize an explicit golden-block snapshot into one board project.

Projects own copied block sources so their generated manufacturing evidence is
reproducible.  This helper makes that copy reviewable: it mirrors only the
selected block directories plus ``glue.tsx`` and writes a deterministic
content-hash lock at the project root.

The ordinary ``--check`` verifies the frozen project snapshot against its
lock.  ``--check-upstream`` additionally proves that the snapshot is still
byte-identical to the currently checked-out golden-block sources; that stricter
mode is useful while atomically migrating the repository's canonical examples.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterable


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[1]
_REPO_SOURCE = REPO_ROOT / "packages" / "golden-blocks" / "blocks"
_SKILL_SOURCE = SCRIPT_PATH.parents[2] / "blocks"
DEFAULT_SOURCE = _REPO_SOURCE if _REPO_SOURCE.is_dir() else _SKILL_SOURCE
LOCK_NAME = "golden-blocks.lock.json"
SCHEMA_VERSION = 1
BLOCK_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SyncError(RuntimeError):
    """A localized, user-actionable synchronization failure."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, file_sha in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _entry_files(root: Path, entries: Iterable[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in entries:
        target = root / entry
        if target.is_symlink():
            raise SyncError(f"symbolic links are not allowed: {target}")
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = sorted(target.rglob("*"))
        else:
            raise SyncError(f"missing golden-block entry: {target}")
        for path in candidates:
            if path.is_symlink():
                raise SyncError(f"symbolic links are not allowed: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in files:
                raise SyncError(f"duplicate golden-block file: {relative}")
            files[relative] = _sha256(path)
    return dict(sorted(files.items()))


def _normalize_blocks(blocks: Iterable[str]) -> list[str]:
    normalized = sorted(set(blocks))
    if not normalized:
        raise SyncError("at least one --block is required for the first sync")
    for block in normalized:
        if not BLOCK_ID.fullmatch(block):
            raise SyncError(f"invalid golden-block id: {block!r}")
    return normalized


def _entries(blocks: Iterable[str]) -> list[str]:
    return ["glue.tsx", *_normalize_blocks(blocks)]


def _manifest(
    source: Path,
    source_label: str,
    blocks: Iterable[str],
) -> dict[str, object]:
    normalized = _normalize_blocks(blocks)
    entries = _entries(normalized)
    for block in normalized:
        if not (source / block).is_dir():
            raise SyncError(f"unknown golden block: {block}")
    files = _entry_files(source, entries)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": source_label,
        "blocks": normalized,
        "treeSha256": _tree_sha(files),
        "files": files,
    }


def _read_manifest(project: Path) -> dict[str, object]:
    lock = project / LOCK_NAME
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SyncError(f"missing {lock}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"unreadable {lock}: {exc}") from exc
    if not isinstance(data, dict):
        raise SyncError(f"{lock} must contain a JSON object")
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise SyncError(
            f"{lock} has unsupported schemaVersion {data.get('schemaVersion')!r}"
        )
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise SyncError(f"{lock} has no source label")
    blocks = data.get("blocks")
    files = data.get("files")
    if not isinstance(blocks, list) or not all(isinstance(x, str) for x in blocks):
        raise SyncError(f"{lock} has an invalid blocks list")
    if blocks != sorted(set(blocks)) or not blocks:
        raise SyncError(f"{lock} blocks must be non-empty, unique, and sorted")
    if not isinstance(files, dict) or not files:
        raise SyncError(f"{lock} has an invalid files map")
    for relative, digest in files.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise SyncError(f"{lock} files must map paths to SHA-256 strings")
        parts = Path(relative).parts
        if not parts or relative.startswith("/") or ".." in parts:
            raise SyncError(f"{lock} contains an unsafe path: {relative!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SyncError(f"{lock} has an invalid SHA-256 for {relative}")
    expected_entries = _entries(blocks)
    prefixes = tuple(f"{block}/" for block in blocks)
    if any(
        relative != "glue.tsx" and not relative.startswith(prefixes)
        for relative in files
    ):
        raise SyncError(f"{lock} contains a file outside its selected entries")
    if set(_entry_files_from_map(files)) != set(expected_entries):
        raise SyncError(f"{lock} does not cover glue.tsx and every selected block")
    tree_sha = data.get("treeSha256")
    if tree_sha != _tree_sha(files):
        raise SyncError(f"{lock} treeSha256 does not match its files map")
    return data


def _entry_files_from_map(files: dict[str, str]) -> list[str]:
    entries: set[str] = set()
    for relative in files:
        if relative == "glue.tsx":
            entries.add(relative)
        else:
            entries.add(relative.split("/", 1)[0])
    return sorted(entries)


def _actual_snapshot_files(project: Path, blocks: list[str]) -> dict[str, str]:
    root = project / "blocks"
    return _entry_files(root, _entries(blocks))


def _describe_file_delta(
    expected: dict[str, str], actual: dict[str, str], label: str
) -> list[str]:
    errors: list[str] = []
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(
        relative
        for relative in set(expected) & set(actual)
        if expected[relative] != actual[relative]
    )
    errors.extend(f"{label} missing {relative}" for relative in missing)
    errors.extend(f"{label} has unexpected {relative}" for relative in extra)
    errors.extend(f"{label} changed {relative}" for relative in changed)
    return errors


def check_project(
    project: Path,
    *,
    source: Path = DEFAULT_SOURCE,
    check_upstream: bool = False,
) -> list[str]:
    manifest = _read_manifest(project)
    blocks = list(manifest["blocks"])
    expected = dict(manifest["files"])
    errors: list[str] = []
    try:
        actual = _actual_snapshot_files(project, blocks)
    except SyncError as exc:
        errors.append(str(exc))
    else:
        errors.extend(_describe_file_delta(expected, actual, "snapshot"))
    if check_upstream:
        try:
            upstream = _entry_files(source, _entries(blocks))
        except SyncError as exc:
            errors.append(str(exc))
        else:
            errors.extend(_describe_file_delta(expected, upstream, "upstream"))
    return errors


def _copy_entry(source: Path, staged: Path, entry: str) -> None:
    src = source / entry
    dst = staged / entry
    if src.is_symlink():
        raise SyncError(f"symbolic links are not allowed: {src}")
    if src.is_dir():
        shutil.copytree(src, dst, copy_function=shutil.copyfile)
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    else:
        raise SyncError(f"missing golden-block entry: {src}")


def sync_project(
    project: Path,
    *,
    blocks: Iterable[str] | None = None,
    source: Path = DEFAULT_SOURCE,
    source_label: str = "packages/golden-blocks/blocks",
    replace_unlocked: bool = False,
) -> dict[str, object]:
    project = project.resolve()
    source = source.resolve()
    if not project.is_dir():
        raise SyncError(f"project directory does not exist: {project}")
    old: dict[str, object] | None = None
    if (project / LOCK_NAME).exists():
        old = _read_manifest(project)
        old_errors = check_project(project, source=source, check_upstream=False)
        if old_errors:
            raise SyncError(
                "refusing to overwrite a modified frozen snapshot:\n  "
                + "\n  ".join(old_errors)
            )
    selected = list(blocks) if blocks is not None else list((old or {}).get("blocks", []))
    manifest = _manifest(source, source_label, selected)
    selected_entries = _entries(manifest["blocks"])
    blocks_dir = project / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)
    if old is None and not replace_unlocked:
        existing = [
            entry
            for entry in selected_entries
            if (blocks_dir / entry).exists() or (blocks_dir / entry).is_symlink()
        ]
        if existing:
            raise SyncError(
                "refusing to replace an unlocked existing block snapshot: "
                + ", ".join(existing)
                + "; review it, then use --replace-unlocked for the one-time "
                "migration"
            )

    with tempfile.TemporaryDirectory(
        prefix=".golden-blocks-sync-", dir=project
    ) as temp_name:
        staged = Path(temp_name) / "blocks"
        staged.mkdir()
        for entry in selected_entries:
            _copy_entry(source, staged, entry)
        staged_files = _entry_files(staged, selected_entries)
        if staged_files != manifest["files"]:
            raise SyncError("staged golden-block bytes do not match the manifest")

        old_entries = _entries((old or {}).get("blocks", [])) if old else []
        for entry in sorted(set(old_entries) | set(selected_entries)):
            destination = blocks_dir / entry
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            elif destination.exists() or destination.is_symlink():
                destination.unlink()
        for entry in selected_entries:
            os.replace(staged / entry, blocks_dir / entry)

    lock = project / LOCK_NAME
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    temporary_lock = project / f".{LOCK_NAME}.tmp"
    temporary_lock.write_text(payload, encoding="utf-8")
    os.replace(temporary_lock, lock)
    errors = check_project(project, source=source, check_upstream=True)
    if errors:
        raise SyncError("post-sync verification failed:\n  " + "\n  ".join(errors))
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="board project directory")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="golden-block source directory (defaults to the repo catalog)",
    )
    parser.add_argument(
        "--source-label",
        default="packages/golden-blocks/blocks",
        help="stable source label recorded in the lock",
    )
    parser.add_argument(
        "--block",
        dest="blocks",
        action="append",
        help="golden block id to synchronize; repeat for each block",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the frozen project snapshot without writing",
    )
    parser.add_argument(
        "--check-upstream",
        action="store_true",
        help="also require byte identity with the current golden-block source",
    )
    parser.add_argument(
        "--replace-unlocked",
        action="store_true",
        help=(
            "one-time migration: replace selected pre-existing block entries "
            "that have no lock (never bypasses drift checks once locked)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.check or args.check_upstream:
            if args.blocks or args.replace_unlocked:
                raise SyncError(
                    "--block/--replace-unlocked cannot be combined with a check mode"
                )
            errors = check_project(
                args.project,
                source=args.source,
                check_upstream=args.check_upstream,
            )
            if errors:
                raise SyncError("\n".join(errors))
            print(
                f"golden-block snapshot clean: {args.project}"
                + (" (upstream-identical)" if args.check_upstream else "")
            )
            return 0
        manifest = sync_project(
            args.project,
            blocks=args.blocks,
            source=args.source,
            source_label=args.source_label,
            replace_unlocked=args.replace_unlocked,
        )
        print(
            f"synchronized {len(manifest['blocks'])} golden blocks into "
            f"{args.project} ({manifest['treeSha256']})"
        )
        return 0
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
