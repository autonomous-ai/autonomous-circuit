"""source_hash.py — the TS import scanner + whole-graph fingerprint."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402

from circuitpy.source_hash import board_source_hash  # noqa: E402


class SourceHashGraph(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        circuitproj.write_project(
            self.root,
            tsx=circuitproj.BLOCK_IMPORT_TSX,
            parts={"R1": {"lcsc": "C11702"}},
            blocks={"led-indicator.tsx": circuitproj.LED_BLOCK_TSX},
        )
        self.entry = self.root / "boards" / "main.tsx"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fingerprint(self) -> str:
        return board_source_hash(self.entry, self.root).source_fingerprint

    def test_manifest_folds_entry_bible_lock_and_block(self) -> None:
        identity = board_source_hash(self.entry, self.root)
        paths = {f.path for f in identity.files}
        self.assertIn("boards/main.tsx", paths)
        self.assertIn("product.json", paths)
        self.assertIn("parts.json", paths)
        self.assertIn("blocks/led-indicator.tsx", paths)
        self.assertEqual(identity.source_path, "boards/main.tsx")
        self.assertTrue(identity.source_hash)

    def test_block_edit_changes_fingerprint(self) -> None:
        before = self._fingerprint()
        block = self.root / "blocks" / "led-indicator.tsx"
        block.write_text(block.read_text() + "\n// edited\n", encoding="utf-8")
        self.assertNotEqual(before, self._fingerprint())

    def test_product_edit_changes_fingerprint(self) -> None:
        before = self._fingerprint()
        product = self.root / "product.json"
        product.write_text(product.read_text().replace("test-board", "renamed"))
        self.assertNotEqual(before, self._fingerprint())

    def test_parts_edit_changes_fingerprint(self) -> None:
        before = self._fingerprint()
        (self.root / "parts.json").write_text('{"R1":{"lcsc":"C999"}}')
        self.assertNotEqual(before, self._fingerprint())

    def test_golden_block_snapshot_lock_is_part_of_source_identity(self) -> None:
        lock = self.root / "golden-blocks.lock.json"
        lock.write_text(
            '{"schemaVersion":1,"treeSha256":"' + "0" * 64 + '"}\n',
            encoding="utf-8",
        )
        first = board_source_hash(self.entry, self.root)
        self.assertIn(
            "golden-blocks.lock.json", {file.path for file in first.files}
        )

        lock.write_text(
            '{"schemaVersion":1,"treeSha256":"' + "1" * 64 + '"}\n',
            encoding="utf-8",
        )
        self.assertNotEqual(first.source_fingerprint, self._fingerprint())

    def test_unrelated_file_does_not_change_fingerprint(self) -> None:
        before = self._fingerprint()
        (self.root / "notes.txt").write_text("irrelevant")
        (self.root / "boards" / "other.tsx").write_text("export const x = 1\n")
        self.assertEqual(before, self._fingerprint())

    def test_built_artifacts_never_join_the_manifest(self) -> None:
        (self.root / "boards" / "main.circuit.json").write_text("[]")
        (self.root / "boards" / "main.board.json").write_text("{}")
        identity = board_source_hash(self.entry, self.root)
        paths = {f.path for f in identity.files}
        self.assertNotIn("boards/main.circuit.json", paths)
        self.assertNotIn("boards/main.board.json", paths)


class ImportForms(unittest.TestCase):
    def _graph(self, entry_source: str, extra: dict[str, str]) -> set[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            circuitproj.write_project(root, tsx=entry_source)
            for rel, text in extra.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            identity = board_source_hash(root / "boards" / "main.tsx", root)
            return {f.path for f in identity.files}

    def test_export_from_and_side_effect_and_require(self) -> None:
        paths = self._graph(
            'export { a } from "./a"\nimport "./b"\nconst c = require("./c")\n'
            "export default () => (<board width=\"10mm\" height=\"10mm\" />)\n",
            {
                "boards/a.tsx": "export const a = 1\n",
                "boards/b.ts": "export const b = 2\n",
                "boards/c.ts": "export const c = 3\n",
            },
        )
        self.assertIn("boards/a.tsx", paths)
        self.assertIn("boards/b.ts", paths)
        self.assertIn("boards/c.ts", paths)

    def test_transitive_imports_walked_breadth_first(self) -> None:
        paths = self._graph(
            'import { a } from "./a"\nexport default () => (<board width="10mm" height="10mm" />)\n',
            {
                "boards/a.tsx": 'import { b } from "../blocks/b"\nexport const a = 1\n',
                "blocks/b.tsx": "export const b = 2\n",
            },
        )
        self.assertIn("boards/a.tsx", paths)
        self.assertIn("blocks/b.tsx", paths)

    def test_multiline_golden_imports_are_in_the_transitive_identity(self) -> None:
        paths = self._graph(
            'import { a } from "../blocks/a/a"\n'
            'export default () => (<board width="10mm" height="10mm" />)\n',
            {
                "blocks/a/a.tsx": (
                    "import {\n"
                    "  b,\n"
                    "  type BProps,\n"
                    '} from "../b/b"\n'
                    "export const a = b\n"
                ),
                "blocks/b/b.tsx": (
                    "export const b = 2\n"
                    "export type BProps = { value: number }\n"
                ),
            },
        )
        self.assertIn("blocks/a/a.tsx", paths)
        self.assertIn("blocks/b/b.tsx", paths)

    def test_bare_specifiers_ignored(self) -> None:
        paths = self._graph(
            'import { x } from "tscircuit"\nimport { y } from "@tsci/some.pkg"\n'
            "export default () => (<board width=\"10mm\" height=\"10mm\" />)\n",
            {},
        )
        self.assertEqual(
            paths, {"boards/main.tsx", "product.json"}
        )  # no parts.json written in this helper call

    def test_index_resolution(self) -> None:
        paths = self._graph(
            'import { z } from "../blocks/power"\n'
            "export default () => (<board width=\"10mm\" height=\"10mm\" />)\n",
            {"blocks/power/index.tsx": "export const z = 1\n"},
        )
        self.assertIn("blocks/power/index.tsx", paths)


if __name__ == "__main__":
    unittest.main()
