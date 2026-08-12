"""Exact-ref parts-book behavior.  The suite is offline and self-contained."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
PARTS_TOOL = SKILL_DIR / "scripts" / "parts"

BLOCK_TSX = '''export const DemoBlock = (props: {
  u?: string
  r1?: string
  r2?: string
  c?: string
}) => {
  const u = props.u ?? "U9"
  const r1 = props.r1 ?? "R90"
  const r2 = props.r2 ?? "R91"
  const c = props.c ?? "C90"
  return (
    <group>
      <chip name={u} supplierPartNumbers={{ jlcpcb: ["C6186"] }}
        manufacturerPartNumber="AMS1117-3.3" footprint="sot223" />
      <resistor name={r1} resistance="4.7k" footprint="0402"
        supplierPartNumbers={{ jlcpcb: ["C25900"] }} />
      <resistor name={r2} resistance="4.7k" footprint="0402"
        supplierPartNumbers={{ jlcpcb: ["C25900"] }} />
      <capacitor name={c} capacitance="10uF" footprint="0805"
        supplierPartNumbers={{ jlcpcb: ["C15850"] }} />
    </group>
  )
}
'''

BLOCK_MD = """# demo-block

## Parts (pinned)

| Refdes | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| U9 | AMS1117-3.3 | C6186 | SOT-223 | yes | regulator |
| R90/R91 | 0402WGF4701TCE, 4.7k | C25900 | 0402 | yes | pull-ups |
| C90 | CL21A106KAYNNNE, 10uF X5R | C15850 | 0805 | yes | bulk |
| TP9 | DNP copper pad | — | copper | — | not populated |
"""

BOARD_TSX = '''import { G } from "../blocks/glue"
import { DemoBlock } from "../blocks/demo-block/demo-block"
export default () => <board><G /><DemoBlock /></board>
'''


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, file_sha in sorted(files.items()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(file_sha.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _write_lock(project: Path, selected: tuple[str, ...]) -> None:
    blocks = project / "blocks"
    files: dict[str, str] = {"glue.tsx": _sha(blocks / "glue.tsx")}
    for block in selected:
        for path in sorted((blocks / block).rglob("*")):
            if path.is_file():
                files[path.relative_to(blocks).as_posix()] = _sha(path)
    payload = {
        "schemaVersion": 1,
        "source": "test",
        "blocks": sorted(selected),
        "treeSha256": _tree(files),
        "files": dict(sorted(files.items())),
    }
    (project / "golden-blocks.lock.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _make_project(
    tmp_path: Path,
    *,
    board: str = BOARD_TSX,
    block_tsx: str = BLOCK_TSX,
    block_md: str = BLOCK_MD,
    extra_blocks: dict[str, tuple[str, str]] | None = None,
) -> Path:
    (tmp_path / "product.json").write_text(
        '{"name":"test-board","assembly":true}', encoding="utf-8"
    )
    boards = tmp_path / "boards"
    boards.mkdir()
    (boards / "main.tsx").write_text(board, encoding="utf-8")
    blocks = tmp_path / "blocks"
    blocks.mkdir()
    (blocks / "glue.tsx").write_text("export const G = () => null\n", encoding="utf-8")
    block = blocks / "demo-block"
    block.mkdir()
    (block / "demo-block.tsx").write_text(block_tsx, encoding="utf-8")
    (block / "BLOCK.md").write_text(block_md, encoding="utf-8")
    for block_id, (source, docs) in (extra_blocks or {}).items():
        folder = blocks / block_id
        folder.mkdir()
        (folder / f"{block_id}.tsx").write_text(source, encoding="utf-8")
        (folder / "BLOCK.md").write_text(docs, encoding="utf-8")
    _write_lock(tmp_path, tuple(sorted(["demo-block", *(extra_blocks or {})])))
    return tmp_path


def _run(*args: str, cwd: Path | None = None) -> tuple[dict, str]:
    env = dict(os.environ)
    env.setdefault("CIRCUIT_PARTS_CACHE_DIR", str(Path(args[0]) / ".cache"))
    proc = subprocess.run(
        [sys.executable, str(PARTS_TOOL), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=cwd,
    )
    lines = (proc.stdout or "").strip().splitlines()
    assert lines, f"no stdout (stderr: {proc.stderr[:400]!r})"
    payload = json.loads(lines[-1])
    assert (proc.returncode == 0) == payload["ok"], (proc.stderr, payload)
    return payload, proc.stdout


def _parts(project: Path) -> dict:
    return json.loads((project / "parts.json").read_text(encoding="utf-8"))


def test_offline_sync_writes_only_exact_ref_entries(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    payload, _ = _run(str(project))

    assert payload["ok"] is True
    assert [part["ref"] for part in payload["parts"]] == ["C90", "R90", "R91", "U9"]
    on_disk = _parts(project)
    assert set(on_disk) == {"C90", "R90", "R91", "U9"}
    assert not ({"version", "summary", "parts"} & set(on_disk))
    assert on_disk["U9"] == {
        "lcsc": "C6186",
        "basic": True,
        "description": "AMS1117-3.3",
        "block": "demo-block",
        "mfr": "AMS1117-3.3",
        "package": "SOT-223",
        "source": "block-default",
    }
    assert on_disk["R90"]["lcsc"] == on_disk["R91"]["lcsc"] == "C25900"
    assert "TP9" not in on_disk


def test_stdout_is_exactly_one_json_line(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _, stdout = _run(str(project))
    assert len(stdout.strip().splitlines()) == 1


def test_legacy_wrapper_migrates_and_carries_catalog_facts_by_lcsc(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    legacy = {
        "version": 1,
        "summary": {"lines": 1},
        "parts": [
            {
                "id": "r-4.7k-0402",
                "lcsc": "C25900",
                "basic": True,
                "stock": 123456,
                "unit_price_usd": 0.0005,
                "stock_checked": "2026-08-10",
                "source": "jlcsearch",
            }
        ],
    }
    (project / "parts.json").write_text(json.dumps(legacy), encoding="utf-8")

    payload, _ = _run(str(project))
    assert payload["ok"] is True
    on_disk = _parts(project)
    for ref in ("R90", "R91"):
        assert on_disk[ref]["stock"] == 123456
        assert on_disk[ref]["unit_price_usd"] == 0.0005
        assert on_disk[ref]["stock_checked"] == "2026-08-10"
        assert on_disk[ref]["source"] == "jlcsearch-cached"


def test_whole_file_rewrite_drops_stale_exact_ref(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _run(str(project))
    lock = _parts(project)
    lock["R99"] = {
        "lcsc": "C999999",
        "basic": False,
        "description": "stale",
        "block": "board",
    }
    (project / "parts.json").write_text(json.dumps(lock), encoding="utf-8")
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert "R99" not in _parts(project)


def test_literal_ref_overrides_are_resolved(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
export default () => <board><DemoBlock u="U8" r1="R8" r2={"R9"} c="C8" /></board>
'''
    project = _make_project(tmp_path, board=board)
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert set(_parts(project)) == {"C8", "R8", "R9", "U8"}


def test_dynamic_ref_override_fails_closed(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
const chosen = "U8"
export default () => <board><DemoBlock u={chosen} /></board>
'''
    project = _make_project(tmp_path, board=board)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "dynamic/non-exact" in payload["error"]["message"]
    assert not (project / "parts.json").exists()


def test_jsx_prop_spread_that_could_override_refs_fails_closed(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
const refs = { u: "U8" }
export default () => <board><DemoBlock {...refs} /></board>
'''
    project = _make_project(tmp_path, board=board)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "JSX prop spread" in payload["error"]["message"]


def test_parametric_documented_ref_fails_closed(tmp_path: Path) -> None:
    docs = BLOCK_MD.replace("R90/R91", "R`n`")
    project = _make_project(tmp_path, block_md=docs)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "unresolved parametric" in payload["error"]["message"]
    assert not (project / "parts.json").exists()


def test_duplicate_populated_ref_from_two_instances_is_refused(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
export default () => <board><DemoBlock /><DemoBlock /></board>
'''
    project = _make_project(tmp_path, board=board)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "ambiguous duplicate populated ref" in payload["error"]["message"]


def test_dependency_selected_by_lock_is_not_mistaken_for_owner(tmp_path: Path) -> None:
    dep_source = '''export const Dependency = () => (
  <chip name="U9" supplierPartNumbers={{ jlcpcb: ["C777"] }} />
)\n'''
    dep_docs = """# dependency
## Parts
| Ref | Part | LCSC | Package | Basic | Note |
|---|---|---|---|---|---|
| U9 | Dependency IC | C777 | QFN | no | dependency only |
"""
    project = _make_project(
        tmp_path, extra_blocks={"demo-dependency": (dep_source, dep_docs)}
    )
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert _parts(project)["U9"]["lcsc"] == "C6186"
    assert _parts(project)["U9"]["block"] == "demo-block"


def test_changed_frozen_block_is_refused(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    source = project / "blocks" / "demo-block" / "demo-block.tsx"
    source.write_text(source.read_text() + "// drift\n", encoding="utf-8")
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "does not match its lock" in payload["error"]["message"]


def test_missing_golden_lock_is_refused(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project / "golden-blocks.lock.json").unlink()
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "golden-blocks.lock.json" in payload["error"]["message"]


def test_missing_basic_classification_refuses_without_truncating_lock(tmp_path: Path) -> None:
    docs = BLOCK_MD.replace("| U9 | AMS1117-3.3 | C6186 | SOT-223 | yes |", "| U9 | AMS1117-3.3 | C6186 | SOT-223 | — |")
    project = _make_project(tmp_path, block_md=docs)
    previous = '{"SAFE":{"lcsc":"C1","basic":true,"description":"keep","block":"board"}}\n'
    (project / "parts.json").write_text(previous, encoding="utf-8")
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "no reviewed Basic/Extended" in payload["error"]["message"]
    assert (project / "parts.json").read_text() == previous


def test_existing_exact_ref_preserves_reviewed_basic_classification(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _run(str(project))
    lock = _parts(project)
    lock["U9"]["basic"] = False
    (project / "parts.json").write_text(json.dumps(lock), encoding="utf-8")
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert _parts(project)["U9"]["basic"] is False


def test_duplicate_json_ref_key_is_refused_without_rewrite(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    duplicate = (
        '{"U9":{"lcsc":"C6186","basic":true},'
        '"U9":{"lcsc":"C6186","basic":true}}\n'
    )
    (project / "parts.json").write_text(duplicate, encoding="utf-8")
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "duplicate JSON key 'U9'" in payload["error"]["message"]
    assert (project / "parts.json").read_text() == duplicate


def test_add_requires_exact_ref_reviewed_description_and_classification(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    payload, _ = _run(
        str(project),
        "--add",
        "J9",
        "--lcsc",
        "C158012",
        "--description",
        "S2B-PH-K-S connector",
        "--extended",
        "--mfr",
        "S2B-PH-K-S",
        "--package",
        "JST-PH",
    )
    assert payload["ok"] is True
    assert _parts(project)["J9"] == {
        "lcsc": "C158012",
        "basic": False,
        "description": "S2B-PH-K-S connector",
        "block": "board",
        "mfr": "S2B-PH-K-S",
        "package": "JST-PH",
        "source": "manual",
        "override": True,
    }


def test_add_rejects_group_or_lowercase_ref(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    for ref in ("J1/J2", "j9"):
        payload, _ = _run(
            str(project),
            "--add",
            ref,
            "--lcsc",
            "C158012",
            "--description",
            "connector",
            "--extended",
        )
        assert payload["ok"] is False
        assert "exact uppercase" in payload["error"]["message"]


def test_footprint_changing_swap_warns_and_keeps_exact_ref(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    payload, _ = _run(
        str(project),
        "--swap",
        "C90",
        "--lcsc",
        "C15525",
        "--package",
        "0603",
        "--basic",
    )
    assert payload["ok"] is True
    assert "FOOTPRINT CHANGE" in " ".join(payload["notes"])
    record = _parts(project)["C90"]
    assert record["lcsc"] == "C15525"
    assert record["footprint_risk"] is True
    assert record["swapped_from"] == "C15850"


def test_swap_unknown_ref_is_refused(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    payload, _ = _run(str(project), "--swap", "U99", "--lcsc", "C6186", "--basic")
    assert payload["ok"] is False
    assert "no populated ref U99" in payload["error"]["message"]


def test_missing_product_json_is_refused(tmp_path: Path) -> None:
    payload, _ = _run(str(tmp_path))
    assert payload["ok"] is False
    assert "product.json" in payload["error"]["message"]
