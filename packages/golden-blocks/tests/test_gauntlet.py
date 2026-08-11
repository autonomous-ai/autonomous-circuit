"""Every block must survive the same gauntlet a board does.

**Why this file exists.** The other block tests check the *compiler's* findings
— the `*_error` elements in circuit.json. That is a fraction of what a real
board is judged by. The DFM gate, the KiCad cross-check and the BOM gate all
run at board level and never ran here, so a block could pass every test it had
and still make every board built from it fail.

That is exactly what happened on 2026-08-11: `usb-c-power`'s footprint has a
0.525mm channel between an alignment hole and a shell pad, too narrow for any
legal track. The block's own tests were green. All three example boards came
back blocked, and the finding pointed at the *board* — the hardest possible
place to attribute it.

So: build each testbench as a real project and run `build_board`, the same
seven-stage gauntlet a user's board gets. A defect that would block every
downstream board now blocks the block that causes it, named after that block,
before anyone composes with it.

Slow by design (a full build per block). Worth it — this is the check that
makes the library trustworthy rather than merely present.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
GB_ROOT = TESTS_DIR.parent
REPO_ROOT = GB_ROOT.parents[1]

sys.path.insert(0, str(REPO_ROOT / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(TESTS_DIR))

from conftest import BLOCK_IDS_FOR_GAUNTLET, _toolchain_bin_dir  # noqa: E402

SKELETON = REPO_ROOT / "skills" / "circuitcode" / "templates" / "project_skeleton"

#: Board props every real board sets. Without them the router emits sub-spec
#: vias and the gauntlet blocks on geometry the composer never chose — the
#: block would be blamed for the harness's omission.
BOARD_PROPS = (
    'thickness={1.6} minTraceWidth="0.2mm" '
    'minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm"'
)


def _bench_board_source(bench_id: str) -> str:
    """The testbench, re-wrapped with production board props."""
    source = (GB_ROOT / "testbench" / f"{bench_id}.tsx").read_text(encoding="utf-8")
    marker = "<board"
    start = source.index(marker)
    end = source.index(">", start)
    head, tail = source[:start], source[end + 1:]
    existing = source[start:end]
    # Keep the bench's own width/height; add the production props.
    for prop in ("thickness", "minTraceWidth", "minViaPadDiameter", "minViaHoleDiameter"):
        if prop in existing:
            existing = existing  # bench already opted in; leave it
    return f"{head}{existing} {BOARD_PROPS}>{tail}"


@pytest.fixture(scope="session")
def gauntlet_projects(tmp_path_factory):
    """One real project per block, built through the full pipeline."""
    if not (_toolchain_bin_dir() / "tscircuit-cli").exists():
        pytest.skip("pinned toolchain missing — run scripts/setup-toolchain.sh")
    root = tmp_path_factory.mktemp("block-gauntlet")
    return root


def _run_gauntlet(root: Path, bench_id: str) -> dict:
    from circuitpy.generation import build_board

    project = root / bench_id
    project.mkdir(parents=True, exist_ok=True)
    for name in ("tsconfig.json", "tscircuit.config.json"):
        shutil.copy(SKELETON / name, project / name)
    shutil.copytree(GB_ROOT / "blocks", project / "blocks", dirs_exist_ok=True)
    (project / "product.json").write_text(json.dumps({
        "name": f"{bench_id}-gauntlet",
        "description": f"full-gauntlet harness for the {bench_id} block",
        "power": "usb-c-5v",
        "envelopeMm": [200, 200],
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": True,
    }, indent=2), encoding="utf-8")
    boards = project / "boards"
    boards.mkdir(exist_ok=True)
    (boards / "main.tsx").write_text(_bench_board_source(bench_id), encoding="utf-8")

    build_board(boards / "main.tsx", boards / "main.circuit.json")
    return json.loads((boards / "main.board.json").read_text(encoding="utf-8"))


@pytest.mark.slow
@pytest.mark.parametrize("bench", BLOCK_IDS_FOR_GAUNTLET)
def test_block_survives_the_board_gauntlet(gauntlet_projects, bench):
    """No block may ship a defect that blocks every board built from it."""
    sidecar = _run_gauntlet(gauntlet_projects, bench)
    warnings = sidecar.get("validation", {}).get("warnings", [])
    blocking = [w for w in warnings if w.get("severity") == "error"]
    assert not blocking, (
        f"{bench}: this block blocks any board that uses it — "
        + "; ".join(f"{w['kind']} @ {w['part']}: {w['detail'][:110]}" for w in blocking)
    )
