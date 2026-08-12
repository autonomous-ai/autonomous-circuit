from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from circuitpy.block_snapshot import validate_project_snapshot
from circuitpy.errors import ProjectShapeError


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, value in sorted(files.items()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(value.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _project(root: Path) -> Path:
    project = root / "project"
    block = project / "blocks" / "rp2040-core"
    block.mkdir(parents=True)
    (project / "blocks" / "glue.tsx").write_text("export const Glue = 1\n")
    (block / "rp2040-core.tsx").write_text("export const Core = 1\n")
    (block / "RASPBERRY_PI_MINIMAL_LICENSE.txt").write_text("MIT fixture\n")
    files = {
        "glue.tsx": _sha(project / "blocks" / "glue.tsx"),
        "rp2040-core/RASPBERRY_PI_MINIMAL_LICENSE.txt": _sha(
            block / "RASPBERRY_PI_MINIMAL_LICENSE.txt"
        ),
        "rp2040-core/rp2040-core.tsx": _sha(block / "rp2040-core.tsx"),
    }
    (project / "golden-blocks.lock.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "source": "fixture/golden",
                "blocks": ["rp2040-core"],
                "treeSha256": _tree(files),
                "files": files,
            }
        )
    )
    return project


def test_inline_board_without_blocks_needs_no_snapshot_lock(tmp_path: Path) -> None:
    assert validate_project_snapshot(tmp_path, imported_paths=["boards/main.tsx"]) is None


def test_imported_block_requires_a_lock_before_toolchain_work(tmp_path: Path) -> None:
    with pytest.raises(ProjectShapeError, match="has no golden-blocks.lock.json"):
        validate_project_snapshot(
            tmp_path, imported_paths=["boards/main.tsx", "blocks/foo/foo.tsx"]
        )


def test_valid_snapshot_includes_nonimported_license_bytes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    result = validate_project_snapshot(
        project,
        imported_paths=["boards/main.tsx", "blocks/rp2040-core/rp2040-core.tsx"],
    )
    assert result is not None
    assert "rp2040-core/RASPBERRY_PI_MINIMAL_LICENSE.txt" in result["files"]


def test_machine_profile_requires_selected_blocks_to_be_imported(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(ProjectShapeError, match="requires board imports"):
        validate_project_snapshot(
            project,
            imported_paths=["boards/main.tsx", "blocks/glue.tsx"],
            required_blocks=["rp2040-core"],
        )
    result = validate_project_snapshot(
        project,
        imported_paths=["blocks/rp2040-core/rp2040-core.tsx"],
        required_blocks=["rp2040-core"],
    )
    assert result is not None


def test_machine_profile_requires_its_blocks_in_the_content_lock(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(
        ProjectShapeError,
        match="requires exact golden-block lock entries: missing usb-power-entry; unexpected rp2040-core",
    ):
        validate_project_snapshot(
            project,
            imported_paths=["blocks/rp2040-core/rp2040-core.tsx"],
            required_blocks=["usb-power-entry"],
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("change", "changed rp2040-core/rp2040-core.tsx"),
        ("extra", "unexpected rp2040-core/extra.txt"),
        ("missing", "missing rp2040-core/RASPBERRY_PI_MINIMAL_LICENSE.txt"),
    ],
)
def test_snapshot_byte_drift_fails_closed(
    tmp_path: Path, mutation: str, message: str
) -> None:
    project = _project(tmp_path)
    block = project / "blocks" / "rp2040-core"
    if mutation == "change":
        (block / "rp2040-core.tsx").write_text("changed\n")
    elif mutation == "extra":
        (block / "extra.txt").write_text("extra\n")
    else:
        (block / "RASPBERRY_PI_MINIMAL_LICENSE.txt").unlink()
    with pytest.raises(ProjectShapeError, match=message):
        validate_project_snapshot(
            project, imported_paths=["blocks/rp2040-core/rp2040-core.tsx"]
        )


def test_importing_an_unselected_project_block_fails_closed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(ProjectShapeError, match="absent from"):
        validate_project_snapshot(
            project,
            imported_paths=[
                "blocks/rp2040-core/rp2040-core.tsx",
                "blocks/custom/custom.tsx",
            ],
        )


def test_manifest_path_traversal_is_rejected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    lock = project / "golden-blocks.lock.json"
    payload = json.loads(lock.read_text())
    payload["files"]["../escape"] = "0" * 64
    payload["treeSha256"] = _tree(payload["files"])
    lock.write_text(json.dumps(payload))
    with pytest.raises(ProjectShapeError, match="unsafe path"):
        validate_project_snapshot(
            project, imported_paths=["blocks/rp2040-core/rp2040-core.tsx"]
        )
