from __future__ import annotations

import hashlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "packages" / "golden-blocks" / "blocks"
VENDORED = REPO_ROOT / "skills" / "circuitcode" / "blocks"
SYNC_SOURCE = REPO_ROOT / "scripts" / "sync_golden_blocks.py"
SYNC_VENDORED = (
    REPO_ROOT
    / "skills"
    / "circuitcode"
    / "scripts"
    / "packages"
    / "sync_golden_blocks.py"
)


def _files(root: Path, *, generated: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if generated and relative in {"README.md", ".gitignore"}:
            # These two tracked files explain/protect the otherwise generated
            # directory and are deliberately retained by vendor_package().
            continue
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_self_contained_skill_vendors_every_golden_block_byte() -> None:
    assert SOURCE.is_dir()
    assert VENDORED.is_dir(), (
        "generated skill blocks are absent; run "
        "scripts/build/build-skill-runtimes.sh before the skill tests"
    )
    assert _files(VENDORED, generated=True) == _files(SOURCE, generated=False)

    rp = VENDORED / "rp2040-core"
    assert (rp / "RASPBERRY_PI_MINIMAL_LICENSE.txt").is_file()
    assert (rp / "RASPBERRY_PI_MINIMAL_REFERENCE.md").is_file()


def test_self_contained_skill_vendors_the_exact_snapshot_synchronizer() -> None:
    assert SYNC_VENDORED.is_file(), (
        "generated synchronizer is absent; run "
        "scripts/build/build-skill-runtimes.sh before the skill tests"
    )
    assert SYNC_VENDORED.read_bytes() == SYNC_SOURCE.read_bytes()
