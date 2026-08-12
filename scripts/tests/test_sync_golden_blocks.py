from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.sync_golden_blocks import (
    LOCK_NAME,
    SyncError,
    check_project,
    sync_project,
)


def _source(root: Path) -> Path:
    source = root / "golden"
    source.mkdir()
    (source / "glue.tsx").write_text("export const Glue = 1\n", encoding="utf-8")
    for name in ("alpha", "beta", "rp2040-core"):
        block = source / name
        block.mkdir()
        (block / f"{name}.tsx").write_text(
            f"export const name = {name!r}\n", encoding="utf-8"
        )
        (block / "BLOCK.md").write_text(f"# {name}\n", encoding="utf-8")
    rp = source / "rp2040-core"
    (rp / "RASPBERRY_PI_MINIMAL_LICENSE.txt").write_text(
        "MIT fixture\n", encoding="utf-8"
    )
    (rp / "RASPBERRY_PI_MINIMAL_REFERENCE.md").write_text(
        "# Provenance\n", encoding="utf-8"
    )
    return source


def _project(root: Path) -> Path:
    project = root / "project"
    (project / "blocks" / "custom").mkdir(parents=True)
    (project / "blocks" / "custom" / "custom.tsx").write_text(
        "export const Custom = 1\n", encoding="utf-8"
    )
    return project


def test_sync_writes_a_deterministic_exact_snapshot_with_provenance(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)

    first = sync_project(
        project,
        blocks=["rp2040-core", "alpha", "alpha"],
        source=source,
        source_label="fixture/golden",
    )
    lock_bytes = (project / LOCK_NAME).read_bytes()
    second = sync_project(project, source=source, source_label="fixture/golden")

    assert first == second
    assert (project / LOCK_NAME).read_bytes() == lock_bytes
    assert first["blocks"] == ["alpha", "rp2040-core"]
    assert (project / "blocks" / "glue.tsx").read_bytes() == (
        source / "glue.tsx"
    ).read_bytes()
    assert (
        project
        / "blocks"
        / "rp2040-core"
        / "RASPBERRY_PI_MINIMAL_LICENSE.txt"
    ).read_bytes() == (
        source / "rp2040-core" / "RASPBERRY_PI_MINIMAL_LICENSE.txt"
    ).read_bytes()
    assert (
        project / "blocks" / "rp2040-core" / "RASPBERRY_PI_MINIMAL_REFERENCE.md"
    ).is_file()
    assert (project / "blocks" / "custom" / "custom.tsx").is_file()
    assert check_project(project, source=source, check_upstream=True) == []


def test_sync_content_locks_transitive_relative_golden_imports(tmp_path: Path) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    (source / "alpha" / "alpha.tsx").write_text(
        'import { name as betaName } from "../beta/beta"\n'
        "export const name = `alpha-${betaName}`\n",
        encoding="utf-8",
    )

    manifest = sync_project(
        project,
        blocks=["alpha"],
        source=source,
        source_label="fixture/golden",
    )

    assert manifest["blocks"] == ["alpha", "beta"]
    assert (project / "blocks" / "alpha" / "alpha.tsx").is_file()
    assert (project / "blocks" / "beta" / "beta.tsx").is_file()
    assert "beta/beta.tsx" in manifest["files"]
    assert check_project(project, source=source, check_upstream=True) == []


def test_snapshot_and_upstream_drift_are_distinguished(tmp_path: Path) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    sync_project(project, blocks=["alpha"], source=source)

    (project / "blocks" / "alpha" / "alpha.tsx").write_text(
        "modified snapshot\n", encoding="utf-8"
    )
    snapshot_errors = check_project(project, source=source)
    assert snapshot_errors == ["snapshot changed alpha/alpha.tsx"]
    assert check_project(project, source=source, check_upstream=True) == snapshot_errors

    (project / "blocks" / "alpha" / "alpha.tsx").write_bytes(
        (source / "alpha" / "alpha.tsx").read_bytes()
    )
    (source / "alpha" / "BLOCK.md").write_text("# changed upstream\n", encoding="utf-8")
    assert check_project(project, source=source) == []
    assert check_project(project, source=source, check_upstream=True) == [
        "upstream changed alpha/BLOCK.md"
    ]


def test_refresh_removes_only_previously_managed_entries(tmp_path: Path) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    sync_project(project, blocks=["alpha", "beta"], source=source)

    sync_project(project, blocks=["beta"], source=source)

    assert not (project / "blocks" / "alpha").exists()
    assert (project / "blocks" / "beta" / "beta.tsx").is_file()
    assert (project / "blocks" / "custom" / "custom.tsx").is_file()
    assert json.loads((project / LOCK_NAME).read_text())["blocks"] == ["beta"]


def test_refresh_refuses_to_overwrite_a_modified_frozen_snapshot(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    sync_project(project, blocks=["alpha"], source=source)
    changed = project / "blocks" / "alpha" / "alpha.tsx"
    changed.write_text("local project change\n", encoding="utf-8")

    with pytest.raises(SyncError, match="refusing to overwrite"):
        sync_project(project, blocks=["beta"], source=source)

    assert changed.read_text(encoding="utf-8") == "local project change\n"
    assert not (project / "blocks" / "beta").exists()


def test_first_migration_requires_explicit_authority_to_replace_unlocked_bytes(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    legacy = project / "blocks" / "alpha"
    legacy.mkdir()
    (legacy / "alpha.tsx").write_text("user-owned legacy bytes\n")

    with pytest.raises(SyncError, match="replace an unlocked"):
        sync_project(project, blocks=["alpha"], source=source)
    assert (legacy / "alpha.tsx").read_text() == "user-owned legacy bytes\n"
    assert not (project / LOCK_NAME).exists()

    sync_project(
        project,
        blocks=["alpha"],
        source=source,
        replace_unlocked=True,
    )
    assert (legacy / "alpha.tsx").read_bytes() == (
        source / "alpha" / "alpha.tsx"
    ).read_bytes()
    assert check_project(project, source=source, check_upstream=True) == []


@pytest.mark.parametrize("block", ["../alpha", "Alpha", "", "alpha/beta"])
def test_invalid_block_ids_fail_closed(tmp_path: Path, block: str) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)

    with pytest.raises(SyncError):
        sync_project(project, blocks=[block], source=source)

    assert not (project / LOCK_NAME).exists()


def test_unselected_upstream_changes_do_not_invalidate_a_frozen_project(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    sync_project(project, blocks=["alpha"], source=source)
    (source / "beta" / "beta.tsx").write_text("changed\n", encoding="utf-8")

    assert check_project(project, source=source, check_upstream=True) == []


def test_cli_accepts_a_self_contained_skill_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)

    from scripts.sync_golden_blocks import main

    assert main(
        [
            str(project),
            "--source",
            str(source),
            "--source-label",
            "circuitcode/golden-blocks",
            "--block",
            "alpha",
        ]
    ) == 0
    manifest = json.loads((project / LOCK_NAME).read_text(encoding="utf-8"))
    assert manifest["source"] == "circuitcode/golden-blocks"
    assert main(
        [str(project), "--source", str(source), "--check-upstream"]
    ) == 0
