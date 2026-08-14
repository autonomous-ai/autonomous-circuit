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
    <group name={`__parts_block__demo-block__${u}`}>
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


def _inventory_elements(
    identities: list[tuple[str, str]] | None = None,
    *,
    dnp: set[str] | None = None,
    extra: list[dict] | None = None,
    block_owned: bool = True,
    board_owned_refs: set[str] | None = None,
) -> list[dict]:
    identities = identities or [
        ("U9", "C6186"),
        ("R90", "C25900"),
        ("R91", "C25900"),
        ("C90", "C15850"),
    ]
    dnp = dnp or set()
    board_owned_refs = board_owned_refs or set()
    out: list[dict] = []
    group_id = "source_group_demo_block"
    board_group_id = "source_group_board"
    if block_owned:
        out.append(
            {
                "type": "source_group",
                "source_group_id": group_id,
                "name": "__parts_block__demo-block__U9",
                "was_automatically_named": False,
            }
        )
    if board_owned_refs or not block_owned:
        out.append(
            {
                "type": "source_group",
                "source_group_id": board_group_id,
                "name": "__parts_board__test-parts",
                "was_automatically_named": False,
            }
        )
    for index, (ref, lcsc) in enumerate(identities):
        sid = f"source_component_{index}"
        pid = f"pcb_component_{index}"
        out.extend(
            [
                {
                    "type": "source_component",
                    "source_component_id": sid,
                    "name": ref,
                    "manufacturer_part_number": f"MPN-{lcsc}",
                    "supplier_part_numbers": {"jlcpcb": [lcsc]},
                    "source_group_id": (
                        group_id
                        if block_owned and ref not in board_owned_refs
                        else board_group_id
                    ),
                },
                {
                    "type": "pcb_component",
                    "pcb_component_id": pid,
                    "source_component_id": sid,
                    "do_not_place": ref in dnp,
                },
                {
                    "type": "pcb_smtpad",
                    "pcb_smtpad_id": f"pcb_smtpad_{index}",
                    "pcb_component_id": pid,
                },
            ]
        )
    out.extend(extra or [])
    return out


def _write_fake_toolchain(project: Path) -> Path:
    toolchain = project / ".test-toolchain"
    files = {
        "package.json": '{"private":true}\n',
        "package-lock.json": "{}\n",
        "node_modules/@tscircuit/cli/package.json": '{"type":"module"}\n',
        "node_modules/@tscircuit/cli/dist/cli/main.js": """
import fs from "node:fs"
import path from "node:path"
const source = path.resolve(".test-inventory.json")
if (!fs.existsSync(source)) throw new Error("staged .test-inventory.json is required")
if (process.env.CIRCUIT_PARTS_ENGINE !== "off") {
  throw new Error("CIRCUIT_PARTS_ENGINE must be off")
}
const expected = [
  "build", "boards/main.tsx", "--routing-disabled", "--disable-parts-engine",
  "--ignore-errors", "--concurrency", "1",
]
if (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(expected)) {
  throw new Error(`unexpected compiler argv: ${JSON.stringify(process.argv.slice(2))}`)
}
const target = path.resolve("dist/boards/main/circuit.json")
fs.mkdirSync(path.dirname(target), { recursive: true })
fs.copyFileSync(source, target)
""",
        "node_modules/@tscircuit/core/dist/index.js": "// fake core\n",
        "node_modules/@tscircuit/props/dist/index.js": "// fake props\n",
        "node_modules/tsx/dist/loader.mjs": "export {}\n",
    }
    for relative, contents in files.items():
        path = toolchain / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return toolchain


def _make_project(
    tmp_path: Path,
    *,
    board: str = BOARD_TSX,
    block_tsx: str = BLOCK_TSX,
    block_md: str = BLOCK_MD,
    extra_blocks: dict[str, tuple[str, str]] | None = None,
    inventory: list[dict] | None = None,
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
    (tmp_path / ".test-inventory.json").write_text(
        json.dumps(inventory if inventory is not None else _inventory_elements()),
        encoding="utf-8",
    )
    _write_fake_toolchain(tmp_path)
    return tmp_path


def _run(*args: str, cwd: Path | None = None) -> tuple[dict, str]:
    env = dict(os.environ)
    env.setdefault("CIRCUIT_PARTS_CACHE_DIR", str(Path(args[0]) / ".cache"))
    project = Path(args[0])
    env["CIRCUIT_TOOLCHAIN"] = str(project / ".test-toolchain")
    env["PARTS_BOOK_FAKE_INVENTORY"] = str(project / ".test-inventory.json")
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
        "source": "compiled-block",
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
    project = _make_project(
        tmp_path,
        board=board,
        inventory=_inventory_elements(
            [("U8", "C6186"), ("R8", "C25900"), ("R9", "C25900"), ("C8", "C15850")]
        ),
    )
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert set(_parts(project)) == {"C8", "R8", "R9", "U8"}


def test_dynamic_ref_override_is_resolved_by_fresh_compiler(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
const chosen = "U8"
export default () => <board><DemoBlock u={chosen} /></board>
'''
    project = _make_project(
        tmp_path,
        board=board,
        inventory=_inventory_elements(
            [("U8", "C6186"), ("R90", "C25900"), ("R91", "C25900"), ("C90", "C15850")]
        ),
    )
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert set(_parts(project)) == {"U8", "R90", "R91", "C90"}


def test_jsx_prop_spread_is_resolved_by_fresh_compiler(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
const refs = { u: "U8" }
export default () => <board><DemoBlock {...refs} /></board>
'''
    project = _make_project(
        tmp_path,
        board=board,
        inventory=_inventory_elements(
            [("U8", "C6186"), ("R90", "C25900"), ("R91", "C25900"), ("C90", "C15850")]
        ),
    )
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert "U8" in _parts(project)


def test_parametric_documented_ref_is_reconciled_to_compiled_refs(tmp_path: Path) -> None:
    docs = BLOCK_MD.replace("R90/R91", "R`n`")
    project = _make_project(tmp_path, block_md=docs)
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert _parts(project)["R90"]["block"] == "demo-block"


def test_duplicate_populated_ref_from_two_instances_is_refused(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
export default () => <board><DemoBlock /><DemoBlock /></board>
'''
    inventory = _inventory_elements()
    duplicate = _inventory_elements([("U9", "C6186")])
    duplicate = [
        element
        for element in duplicate
        if element.get("type") != "source_group"
    ]
    for element in duplicate:
        for key in (
            "source_component_id",
            "pcb_component_id",
            "pcb_smtpad_id",
        ):
            if key in element:
                element[key] = f"{element[key]}_duplicate"
    inventory.extend(duplicate)
    project = _make_project(tmp_path, board=board, inventory=inventory)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "duplicate component ref U9" in payload["error"]["message"]


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
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
export default () => <board><group name="__parts_board__test-parts"><DemoBlock /></group></board>
'''
    project = _make_project(
        tmp_path,
        board=board,
        inventory=_inventory_elements(
            [
                ("U9", "C6186"),
                ("R90", "C25900"),
                ("R91", "C25900"),
                ("C90", "C15850"),
                ("J9", "C158012"),
            ],
            board_owned_refs={"J9"},
        ),
    )
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
        "--description",
        "replacement capacitor",
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
    payload, _ = _run(
        str(project), "--swap", "U99", "--lcsc", "C6186",
        "--description", "replacement", "--package", "SOT-223", "--basic",
    )
    assert payload["ok"] is False
    assert "no populated ref U99" in payload["error"]["message"]


def test_missing_product_json_is_refused(tmp_path: Path) -> None:
    payload, _ = _run(str(tmp_path))
    assert payload["ok"] is False
    assert "product.json" in payload["error"]["message"]


def test_serialized_compiler_error_refuses_without_rewriting(tmp_path: Path) -> None:
    inventory = _inventory_elements(extra=[{"type": "source_trace_not_connected_error"}])
    project = _make_project(tmp_path, inventory=inventory)
    previous = '{"SAFE":{"lcsc":"C1","basic":true,"description":"keep","block":"board"}}\n'
    (project / "parts.json").write_text(previous, encoding="utf-8")
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "serialized errors" in payload["error"]["message"]
    assert (project / "parts.json").read_text(encoding="utf-8") == previous


def test_dnp_is_owned_and_physical_but_needs_no_supplier(tmp_path: Path) -> None:
    inventory = _inventory_elements([("U9", "C6186"), ("TP9", "C999")], dnp={"TP9"})
    tp = next(item for item in inventory if item.get("type") == "source_component" and item.get("name") == "TP9")
    tp.pop("supplier_part_numbers")
    project = _make_project(tmp_path, inventory=inventory)
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert set(_parts(project)) == {"U9"}


def test_dnp_duplicate_ref_and_missing_land_are_refused(tmp_path: Path) -> None:
    inventory = _inventory_elements([("U9", "C6186"), ("TP9", "C999")], dnp={"TP9"})
    duplicate = _inventory_elements([("TP9", "C999")], dnp={"TP9"})
    duplicate = [item for item in duplicate if item.get("type") != "source_group"]
    for item in duplicate:
        for key in ("source_component_id", "pcb_component_id", "pcb_smtpad_id"):
            if key in item:
                item[key] += "_other"
    project = _make_project(tmp_path, inventory=[*inventory, *duplicate])
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "duplicate component ref TP9" in payload["error"]["message"]

    inventory = _inventory_elements([("U9", "C6186"), ("TP9", "C999")], dnp={"TP9"})
    inventory = [
        item for item in inventory
        if not (item.get("type") == "pcb_smtpad" and item.get("pcb_component_id") == "pcb_component_1")
    ]
    project = _make_project(tmp_path / "missing-land", inventory=inventory)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "TP9 has no physical" in payload["error"]["message"]


def test_populated_supplier_identity_must_be_one_exact_jlc_number(tmp_path: Path) -> None:
    mutations = (
        None,
        {"digikey": ["123"]},
        {"jlcpcb": []},
        {"jlcpcb": ["C1", "C2"]},
        {"jlcpcb": ["bad"]},
    )
    for index, supplier in enumerate(mutations):
        inventory = _inventory_elements()
        source = next(item for item in inventory if item.get("type") == "source_component")
        if supplier is None:
            source.pop("supplier_part_numbers")
        else:
            source["supplier_part_numbers"] = supplier
        project = _make_project(tmp_path / str(index), inventory=inventory)
        payload, _ = _run(str(project))
        assert payload["ok"] is False
        assert "exactly one JLCPCB" in payload["error"]["message"]


def test_inventory_requires_real_group_and_bijective_physical_join(tmp_path: Path) -> None:
    cases: list[tuple[str, list[dict], str]] = []
    missing_group = _inventory_elements()
    next(item for item in missing_group if item.get("type") == "source_component")["source_group_id"] = "missing"
    cases.append(("group", missing_group, "unknown source_group_id"))
    two_pcb = _inventory_elements()
    duplicate_pcb = dict(next(item for item in two_pcb if item.get("type") == "pcb_component"))
    duplicate_pcb["pcb_component_id"] = "pcb_component_extra"
    two_pcb.append(duplicate_pcb)
    cases.append(("join", two_pcb, "exactly one pcb_component"))
    orphan = _inventory_elements(extra=[{
        "type": "pcb_component", "pcb_component_id": "pcb_orphan",
        "source_component_id": "arbitrary_owner", "do_not_place": False,
    }])
    cases.append(("orphan", orphan, "unknown source owner"))
    for folder, inventory, message in cases:
        project = _make_project(tmp_path / folder, inventory=inventory)
        payload, _ = _run(str(project))
        assert payload["ok"] is False
        assert message in payload["error"]["message"]


def test_known_manual_via_is_not_an_assembly_part(tmp_path: Path) -> None:
    inventory = _inventory_elements(extra=[
        {
            "type": "source_manually_placed_via",
            "source_manually_placed_via_id": "source_manually_placed_via_0",
            "source_group_id": "source_group_demo_block",
        },
        {
            "type": "pcb_component", "pcb_component_id": "pcb_via_component",
            "source_component_id": "source_manually_placed_via_0",
        },
    ])
    project = _make_project(tmp_path, inventory=inventory)
    payload, _ = _run(str(project))
    assert payload["ok"] is True
    assert set(_parts(project)) == {"C90", "R90", "R91", "U9"}


def test_owner_marker_spoofs_and_missing_compiled_marker_are_refused(tmp_path: Path) -> None:
    board = BOARD_TSX.replace(
        "export default", 'const fake = "__parts_block__demo-block__U9"\nexport default'
    )
    project = _make_project(tmp_path / "spoof", board=board)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "reserved compiled block-owner" in payload["error"]["message"]

    inventory = [
        item for item in _inventory_elements()
        if item.get("type") != "source_group"
    ]
    inventory.insert(0, {
        "type": "source_group", "source_group_id": "source_group_demo_block",
        "name": "ordinary", "was_automatically_named": False,
    })
    project = _make_project(tmp_path / "missing", inventory=inventory)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "no owner marker for active" in payload["error"]["message"]


def test_board_owner_marker_is_explicit_and_compiler_reconciled(tmp_path: Path) -> None:
    board = '''import { DemoBlock } from "../blocks/demo-block/demo-block"
export default () => <board><group name="__parts_board__glue"><DemoBlock /></group></board>
'''
    inventory = _inventory_elements(board_owned_refs={"J9"}, identities=[
        ("U9", "C6186"), ("R90", "C25900"), ("R91", "C25900"),
        ("C90", "C15850"), ("J9", "C158012"),
    ])
    group = next(item for item in inventory if item.get("name") == "__parts_board__test-parts")
    group["name"] = "__parts_board__other"
    project = _make_project(tmp_path, board=board, inventory=inventory)
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "not declared by project source" in payload["error"]["message"]


def test_deterministic_source_contract_refuses_ambient_population_inputs(tmp_path: Path) -> None:
    mutations = (
        'import fs from "node:fs"\n',
        'import fs from "fs"\n',
        'const enabled = process.env.FEATURE\n',
        'const p = "./feature"; import (p)\n',
        'const prior = "parts.json"\n',
    )
    for index, prefix in enumerate(mutations):
        project = _make_project(tmp_path / str(index), board=prefix + BOARD_TSX)
        payload, _ = _run(str(project))
        assert payload["ok"] is False
        assert "deterministic" in payload["error"]["message"] or "nonliteral" in payload["error"]["message"]


def test_malformed_existing_review_metadata_is_refused(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project / "parts.json").write_text(json.dumps({
        "U9": {"lcsc": "C6186", "basic": True, "description": {"bad": True}, "block": "demo-block"}
    }), encoding="utf-8")
    payload, _ = _run(str(project))
    assert payload["ok"] is False
    assert "non-string/empty reviewed description" in payload["error"]["message"]


def test_swap_requires_new_identity_metadata(tmp_path: Path) -> None:
    for args in (
        ("--package", "0603"),
        ("--description", "replacement"),
    ):
        project = _make_project(tmp_path / str(len(args[0])))
        payload, _ = _run(
            str(project), "--swap", "C90", "--lcsc", "C15525", *args, "--basic"
        )
        assert payload["ok"] is False
        assert "requires reviewed --description and --package" in payload["error"]["message"]
