"""Create a planner-derived, content-locked protected USB starter project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = SCRIPTS_DIR.parent
for candidate in (SCRIPTS_DIR, SKILL_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from circuitlib.starter import protected_usb_indicator_starter
from packages.sync_golden_blocks import DEFAULT_SOURCE, SyncError, sync_project


TSCIRCUIT_CONFIG = {
    "$schema": "https://cdn.jsdelivr.net/npm/@tscircuit/cli/types/tscircuit.config.schema.json"
}
TSCONFIG = {
    "compilerOptions": {
        "target": "ESNext",
        "module": "ESNext",
        "moduleResolution": "bundler",
        "jsx": "react-jsx",
        "esModuleInterop": True,
        "skipLibCheck": True,
        "resolveJsonModule": True,
        "allowSyntheticDefaultImports": True,
        "types": ["tscircuit"],
    },
    "include": ["**/*.ts", "**/*.tsx"],
    "exclude": ["node_modules", "dist", ".circuit", ".claude"],
}
UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
VIEWER_INPUT_SUFFIXES = {".png", ".jpg", ".webp", ".gif"}
VIEWER_INPUT_MAX_FILES = 6
VIEWER_INPUT_MAX_BYTES = 10 * 1024 * 1024


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_viewer_inputs(target: Path) -> dict[str, tuple[int, str]]:
    """Return immutable evidence for the viewer-owned reference-image set."""

    inputs = target / "inputs"
    if not inputs.exists() and not inputs.is_symlink():
        return {}
    if inputs.is_symlink() or not inputs.is_dir():
        raise ValueError("server-owned inputs must be a real directory")
    entries = sorted(inputs.iterdir(), key=lambda entry: entry.name)
    if len(entries) > VIEWER_INPUT_MAX_FILES:
        raise ValueError(
            f"server-owned inputs may contain at most {VIEWER_INPUT_MAX_FILES} files"
        )
    evidence: dict[str, tuple[int, str]] = {}
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise ValueError("server-owned inputs may contain only regular files")
        if (
            entry.suffix not in VIEWER_INPUT_SUFFIXES
            or not UUID_V4.fullmatch(entry.stem)
        ):
            raise ValueError(
                "server-owned input names must be UUIDv4 plus one of "
                ".png/.jpg/.webp/.gif"
            )
        size = entry.stat().st_size
        if size < 1 or size > VIEWER_INPUT_MAX_BYTES:
            raise ValueError(
                "each server-owned input must contain 1..10MiB of image data"
            )
        evidence[entry.name] = (size, _file_sha256(entry))
    return evidence


def create_project(
    target: Path,
    *,
    name: str,
    description: str,
    source: Path = DEFAULT_SOURCE,
    initialize_existing: bool = False,
) -> dict[str, object]:
    """Atomically create the public machine-profile project."""

    lexical_target = target.expanduser()
    if not lexical_target.is_absolute():
        lexical_target = Path.cwd() / lexical_target
    if lexical_target.is_symlink():
        raise ValueError(
            f"existing initialization target must not be a symlink: {lexical_target}"
        )
    target = lexical_target.resolve()
    viewer_metadata: bytes | None = None
    viewer_inputs: dict[str, tuple[int, str]] = {}
    if target.exists() and not initialize_existing:
        raise ValueError(f"refusing to overwrite existing project path: {target}")
    if target.exists():
        if not target.is_dir() or target.is_symlink():
            raise ValueError(
                f"existing initialization target must be a real directory: {target}"
            )
        entries = sorted(target.iterdir(), key=lambda entry: entry.name)
        if [entry.name for entry in entries] not in (
            ["project.json"],
            ["inputs", "project.json"],
        ):
            raise ValueError(
                "existing initialization target must contain only server-owned "
                "project.json and optional validated reference inputs"
            )
        metadata = target / "project.json"
        if metadata.is_symlink() or not metadata.is_file():
            raise ValueError("server-owned project.json metadata must be a regular file")
        viewer_metadata = metadata.read_bytes()
        try:
            parsed_metadata = json.loads(viewer_metadata)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"server-owned project.json metadata is invalid: {exc}") from exc
        expected_metadata_keys = {"id", "name", "created_at", "updated_at"}
        if (
            not isinstance(parsed_metadata, dict)
            or set(parsed_metadata) != expected_metadata_keys
            or not isinstance(parsed_metadata.get("id"), str)
            or not UUID_V4.fullmatch(parsed_metadata["id"])
            or parsed_metadata["id"] != target.name
            or not isinstance(parsed_metadata.get("name"), str)
            or not parsed_metadata["name"].strip()
            or not all(
                isinstance(parsed_metadata.get(key), (int, float))
                and not isinstance(parsed_metadata.get(key), bool)
                and float(parsed_metadata[key]) > 0
                and float(parsed_metadata[key]) != float("inf")
                for key in ("created_at", "updated_at")
            )
        ):
            raise ValueError(
                "server-owned project.json metadata must contain exactly a "
                "basename-matching UUIDv4 id, non-empty name, and finite "
                "positive created_at/updated_at milliseconds"
            )
        viewer_inputs = _validated_viewer_inputs(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    starter = protected_usb_indicator_starter(name=name, description=description)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.create-", dir=target.parent
    ) as temporary:
        staged = Path(temporary) / target.name
        (staged / "boards").mkdir(parents=True)
        (staged / "boards" / "main.tsx").write_text(
            starter.board_source, encoding="utf-8"
        )
        _write_json(staged / "product.json", starter.product)
        _write_json(staged / "parts.json", starter.parts)
        _write_json(staged / "tscircuit.config.json", TSCIRCUIT_CONFIG)
        _write_json(staged / "tsconfig.json", TSCONFIG)
        manifest = sync_project(
            staged,
            blocks=starter.block_ids,
            source=source,
            source_label="circuitcode/golden-blocks",
        )
        if viewer_metadata is not None:
            (staged / "project.json").write_bytes(viewer_metadata)
            # Keep the server-owned directory inode/metadata. All validation
            # and generation happened in a sibling staging tree; only after it
            # is complete do individual new entries move into the otherwise
            # empty directory. A rollback removes only entries moved by this
            # invocation and never touches project.json.
            moved: list[Path] = []
            try:
                for entry in sorted(staged.iterdir(), key=lambda item: item.name):
                    if entry.name == "project.json":
                        continue
                    destination = target / entry.name
                    os.replace(entry, destination)
                    moved.append(destination)
                if (target / "project.json").read_bytes() != viewer_metadata:
                    raise ValueError(
                        "server-owned project.json metadata changed during initialization"
                    )
                if _validated_viewer_inputs(target) != viewer_inputs:
                    raise ValueError(
                        "server-owned reference inputs changed during initialization"
                    )
            except Exception:
                for destination in reversed(moved):
                    if destination.is_dir() and not destination.is_symlink():
                        import shutil

                        shutil.rmtree(destination)
                    elif destination.exists() or destination.is_symlink():
                        destination.unlink()
                raise
        else:
            os.replace(staged, target)
    return {
        "ok": True,
        "project": str(target),
        "designProfile": starter.product["designProfile"],
        "plannerBlocks": list(starter.block_ids),
        "blocks": list(manifest["blocks"]),
        "treeSha256": manifest["treeSha256"],
        "boardSizeMm": [
            starter.placement["width_mm"],
            starter.placement["height_mm"],
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/create",
        description=(
            "Create a planner-derived protected USB + 3.3V indicator project "
            "with exact product intent and a content-verified golden snapshot."
        ),
    )
    parser.add_argument("project", type=Path, help="new project directory")
    parser.add_argument("--name", default="new-board", help="product name")
    parser.add_argument(
        "--description",
        default="protected USB-powered board with a 3.3V status indicator",
        help="one-sentence product description",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="golden-block source (normally the skill's bundled catalog)",
    )
    parser.add_argument(
        "--initialize-existing",
        action="store_true",
        help=(
            "initialize an existing viewer workspace containing valid regular "
            "project.json metadata and, optionally, its validated reference inputs"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = create_project(
            args.project,
            name=args.name,
            description=args.description,
            source=args.source,
            initialize_existing=args.initialize_existing,
        )
    except (ValueError, OSError, SyncError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {"code": "PROJECT_CREATE_FAILED", "message": str(exc)},
                }
            )
        )
        return 1
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
