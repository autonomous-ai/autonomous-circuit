from __future__ import annotations

import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "circuitcode"))
sys.path.insert(0, str(REPO / "evals"))

from circuitlib.blocks import BLOCKS  # noqa: E402
from composition import INSTANTIATION  # noqa: E402


REGISTERED_BLOCKS = tuple(sorted(BLOCKS))


def _exported_props(block_id: str, symbol: str) -> tuple[set[str], set[str]]:
    source = (
        REPO
        / "packages"
        / "golden-blocks"
        / "blocks"
        / block_id
        / f"{block_id}.tsx"
    ).read_text(encoding="utf-8")
    match = re.search(
        rf"export const {re.escape(symbol)} = \(props: \{{(?P<body>.*?)^\}}\) =>",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"cannot find exported props for {symbol}"
    declared: set[str] = set()
    required: set[str] = set()
    for name, optional in re.findall(
        r"^\s{2}([A-Za-z][A-Za-z0-9_]*)(\?)?:",
        match.group("body"),
        re.MULTILINE,
    ):
        declared.add(name)
        if optional != "?":
            required.add(name)
    return declared, required


def test_migrated_registry_props_match_live_golden_exports() -> None:
    """Planner metadata is an API mirror, not approximate documentation.

    A stale prop list makes generated source fail only after a costly TSX
    compile. Compare every plannable block directly against the live exported
    type surface so a golden API change, composition constructor, and the
    self-contained skill metadata must land together.
    """
    assert set(INSTANTIATION) == set(REGISTERED_BLOCKS)
    for block_id in REGISTERED_BLOCKS:
        symbol, _ = INSTANTIATION[block_id]
        declared, _ = _exported_props(block_id, symbol)
        assert set(BLOCKS[block_id].props) == declared, block_id


def test_composition_supplies_every_required_golden_prop() -> None:
    assert set(INSTANTIATION) == set(REGISTERED_BLOCKS)
    for block_id in REGISTERED_BLOCKS:
        symbol, attributes = INSTANTIATION[block_id]
        _, required = _exported_props(block_id, symbol)
        supplied = set(re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*=", attributes))
        assert required <= supplied, (
            f"{block_id} composition is missing required props: "
            f"{sorted(required - supplied)}"
        )
