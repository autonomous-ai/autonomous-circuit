"""Tests for the parts-book skill's scripts/parts tool.

No network, ever: the offline path is the default and the lookup path is
exercised with a fake catalog client in test_lookup.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
PARTS_TOOL = SKILL_DIR / "scripts" / "parts"
REPO_BLOCKS = SKILL_DIR.parents[1] / "packages" / "golden-blocks" / "blocks"

BLOCK_TSX = '''/**
 * golden-block: demo-block (test fixture)
 */
export const DemoBlock = (props: { u?: string }) => {
  const u = props.u ?? "U9"
  return (
    <group>
      <chip
        name={u}
        supplierPartNumbers={{ jlcpcb: ["C6186"] }}
        manufacturerPartNumber="AMS1117-3.3"
        footprint="sot223"
      />
      <resistor name="R90" resistance="4.7k" footprint="0402"
        supplierPartNumbers={{ jlcpcb: ["C25900"] }} />
      <capacitor name="C90" capacitance="10uF" footprint="0805"
        supplierPartNumbers={{ jlcpcb: ["C15850"] }} />
    </group>
  )
}
'''

BLOCK_MD = """# demo-block — a test fixture

**Function:** nothing real.

## Parts (pinned; verified 2026-08-10 via jlcsearch)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| U9 | AMS1117-3.3 | C6186 | SOT-223 | yes | $0.15, 1.49M stock |
| R90 | 0402WGF4701TCE, 4.7k | C25900 | 0402 | yes | pull-up |
| C90 | CL21A106KAYNNNE, 10uF X5R | C15850 | 0805 | yes | bulk |

## Provenance

Fixture.
"""


def _run(*args: str, cwd: Path | None = None) -> tuple[dict, str]:
    env = dict(os.environ)
    env.setdefault("CIRCUIT_PARTS_CACHE_DIR", str(Path(args[0]) / ".cache"))
    proc = subprocess.run(
        [sys.executable, str(PARTS_TOOL), *args],
        capture_output=True, text=True, timeout=120, env=env, cwd=cwd,
    )
    stdout = proc.stdout or ""
    lines = stdout.strip().splitlines()
    assert lines, f"no stdout (stderr: {proc.stderr[:400]!r})"
    return json.loads(lines[-1]), stdout


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "product.json").write_text(json.dumps({
        "name": "test-board", "power": "usb-c-5v", "layers": 2,
        "fab": "jlcpcb", "assembly": True,
    }), encoding="utf-8")
    block = tmp_path / "blocks" / "demo-block"
    block.mkdir(parents=True)
    (block / "demo-block.tsx").write_text(BLOCK_TSX, encoding="utf-8")
    (block / "BLOCK.md").write_text(BLOCK_MD, encoding="utf-8")
    return tmp_path


def _parts(project: Path) -> dict:
    return json.loads((project / "parts.json").read_text(encoding="utf-8"))


# --- the offline path -----------------------------------------------------


def test_offline_sync_writes_candidate_slots(tmp_path: Path):
    project = _make_project(tmp_path)
    payload, _ = _run(str(project))

    assert payload["ok"] is True
    ids = {p["id"] for p in payload["parts"]}
    assert ids == {"ams1117-3.3", "r-4.7k-0402", "c-10uf-0805"}
    # Candidate slots: pinned identity, no stock check yet.
    for part in payload["parts"]:
        assert part["stock_checked"] is None
        assert part["basic"] is True

    on_disk = _parts(project)
    assert on_disk["version"] == 1
    assert on_disk["generator"] == "parts-book"
    assert on_disk["summary"]["lines"] == 3
    record = next(p for p in on_disk["parts"] if p["id"] == "ams1117-3.3")
    assert record["lcsc"] == "C6186"
    assert record["mfr"] == "AMS1117-3.3"
    assert record["package"] == "SOT-223"        # BLOCK.md beats the footprint
    assert record["refdes"] == ["U9"]
    assert record["blocks"] == ["demo-block"]
    assert record["stock"] is None
    assert record["unit_price_usd"] is None
    assert record["source"] == "block-default"
    assert record["datasheet_url"].endswith("C6186.html")


def test_stdout_is_exactly_one_json_line(tmp_path: Path):
    project = _make_project(tmp_path)
    _, stdout = _run(str(project))
    assert len(stdout.strip().splitlines()) == 1


def test_whole_file_rewrite_drops_stale_content(tmp_path: Path):
    """parts-book owns parts.json wholly — nothing survives that the blocks
    (or an explicit --add) do not justify."""
    project = _make_project(tmp_path)
    (project / "parts.json").write_text(json.dumps({
        "version": 0,
        "hand_written": "should not survive",
        "parts": [
            {"id": "ghost-part", "lcsc": "C999999", "basic": False},
            {"id": "ams1117-3.3", "lcsc": "C6186", "stock": 1,
             "unit_price_usd": 9.99, "stock_checked": "2020-01-01"},
        ],
    }), encoding="utf-8")

    payload, _ = _run(str(project))
    assert payload["ok"] is True
    on_disk = _parts(project)
    assert "hand_written" not in on_disk
    assert on_disk["version"] == 1
    assert "ghost-part" not in {p["id"] for p in on_disk["parts"]}


def test_previous_lookup_carries_forward(tmp_path: Path):
    """An offline re-sync must never erase the last checked stock/price."""
    project = _make_project(tmp_path)
    _run(str(project))
    on_disk = _parts(project)
    for part in on_disk["parts"]:
        if part["lcsc"] == "C6186":
            part.update({"stock": 1490681, "unit_price_usd": 0.151271,
                         "stock_checked": "2026-08-10"})
    (project / "parts.json").write_text(json.dumps(on_disk), encoding="utf-8")

    payload, _ = _run(str(project))
    record = next(p for p in _parts(project)["parts"] if p["lcsc"] == "C6186")
    assert record["stock"] == 1490681
    assert record["unit_price_usd"] == 0.151271
    assert record["stock_checked"] == "2026-08-10"
    assert next(p for p in payload["parts"]
                if p["lcsc"] == "C6186")["stock_checked"] == "2026-08-10"


# --- refusals -------------------------------------------------------------


def test_duplicate_id_refused_and_file_untouched(tmp_path: Path):
    project = _make_project(tmp_path)
    _run(str(project))
    before = (project / "parts.json").read_text(encoding="utf-8")

    payload, _ = _run(str(project), "--add", "ams1117-3.3", "--lcsc", "C123456")
    assert payload["ok"] is False
    assert "already exists" in payload["error"]["message"]
    assert (project / "parts.json").read_text(encoding="utf-8") == before


def test_add_requires_one_exact_orderable_number(tmp_path: Path):
    project = _make_project(tmp_path)

    payload, _ = _run(str(project), "--add", "jst-ph-2")
    assert payload["ok"] is False
    assert "--lcsc" in payload["error"]["message"]

    payload, _ = _run(str(project), "--add", "jst-ph-2", "--lcsc", "TP4056")
    assert payload["ok"] is False
    assert "exact LCSC number" in payload["error"]["message"]


def test_missing_product_json_refused(tmp_path: Path):
    payload, _ = _run(str(tmp_path))
    assert payload["ok"] is False
    assert "product.json" in payload["error"]["message"]
    assert not (tmp_path / "parts.json").exists()


# --- swaps ----------------------------------------------------------------


def test_add_glue_part(tmp_path: Path):
    project = _make_project(tmp_path)
    payload, _ = _run(str(project), "--add", "jst-ph-2", "--lcsc", "C158012",
                      "--mfr", "S2B-PH-K-S", "--package", "JST-PH", "--refdes", "J9")
    assert payload["ok"] is True
    record = next(p for p in _parts(project)["parts"] if p["id"] == "jst-ph-2")
    assert record["lcsc"] == "C158012"
    assert record["refdes"] == ["J9"]
    assert record["source"] == "manual"
    assert record["blocks"] == []


def test_footprint_changing_swap_warns_loudly(tmp_path: Path):
    project = _make_project(tmp_path)
    payload, _ = _run(str(project), "--swap", "c-10uf-0805",
                      "--lcsc", "C15525", "--package", "0603")
    assert payload["ok"] is True
    notes = " ".join(payload.get("notes", []))
    assert "FOOTPRINT CHANGE" in notes
    assert "LAYOUT" in notes
    record = next(p for p in _parts(project)["parts"] if p["id"] == "c-10uf-0805")
    assert record["lcsc"] == "C15525"
    assert record["footprint_risk"] is True
    assert record["swapped_from"] == "C15850"
    # A swap invalidates the old stock check.
    assert record["stock_checked"] is None


def test_same_footprint_swap_does_not_cry_wolf(tmp_path: Path):
    project = _make_project(tmp_path)
    payload, _ = _run(str(project), "--swap", "r-4.7k-0402",
                      "--lcsc", "C137885", "--package", "0402")
    assert payload["ok"] is True
    assert "FOOTPRINT CHANGE" not in " ".join(payload.get("notes", []))
    record = next(p for p in _parts(project)["parts"] if p["id"] == "r-4.7k-0402")
    assert "footprint_risk" not in record
    # Still tells the truth about the block/parts.json divergence.
    assert any("part_drift" in n for n in payload.get("notes", []))


def test_swap_unknown_id_refused(tmp_path: Path):
    project = _make_project(tmp_path)
    payload, _ = _run(str(project), "--swap", "nope", "--lcsc", "C6186")
    assert payload["ok"] is False
    assert "no part id" in payload["error"]["message"]


# --- the real library -----------------------------------------------------


@pytest.mark.skipif(not REPO_BLOCKS.is_dir(), reason="golden-blocks absent")
def test_real_golden_blocks_lock_cleanly(tmp_path: Path):
    """The shipped blocks must parse into one slot per orderable number with
    no drift notes — this is the docs-vs-source cross-check."""
    project = tmp_path
    (project / "product.json").write_text('{"name": "real"}', encoding="utf-8")
    payload, _ = _run(str(project), "--blocks", str(REPO_BLOCKS))

    assert payload["ok"] is True, payload
    assert payload.get("notes") is None, payload.get("notes")
    by_id = {p["id"]: p["lcsc"] for p in payload["parts"]}
    assert by_id["ams1117-3.3"] == "C6186"
    assert by_id["rp2040"] == "C2040"
    assert by_id["type-c-31-m-12"] == "C165948"
    lcscs = [p["lcsc"] for p in payload["parts"]]
    assert len(lcscs) == len(set(lcscs)), "one part = one exact orderable number"
    for part in _parts(project)["parts"]:
        assert part["lcsc"].startswith("C") and part["lcsc"][1:].isdigit()
        assert part["package"], f"{part['id']} has no package"
