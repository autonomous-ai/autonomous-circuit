"""The donor source-hash algorithm, ported: entry hash + whole-graph
fingerprint folding series.py and statically discovered local imports."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramaproj import write_series  # noqa: E402
from dramapy.source_hash import episode_source_hash  # noqa: E402


class SourceHashTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        write_series(root)
        (root / "beats.py").write_text("TROPE = 'amnesia'\n", encoding="utf-8")
        episodes = root / "episodes"
        episodes.mkdir()
        script = episodes / "ep001.py"
        script.write_text(
            "import series\nimport beats\n\ndef gen_episode():\n    return {}\n",
            encoding="utf-8",
        )
        return script

    def test_graph_folds_entry_series_and_local_imports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-hash-") as tempdir:
            root = Path(tempdir)
            script = self._project(root)
            identity = episode_source_hash(script, root)
            tracked = {file.path for file in identity.files}
            self.assertEqual({"episodes/ep001.py", "series.py", "beats.py"}, tracked)
            self.assertEqual("episodes/ep001.py", identity.source_path)
            self.assertRegex(identity.source_hash, r"^[0-9a-f]{64}$")
            self.assertRegex(identity.source_fingerprint, r"^[0-9a-f]{64}$")

    def test_series_always_folded_even_without_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-hash-") as tempdir:
            root = Path(tempdir)
            write_series(root)
            episodes = root / "episodes"
            episodes.mkdir()
            script = episodes / "ep001.py"
            script.write_text("def gen_episode():\n    return {}\n", encoding="utf-8")
            identity = episode_source_hash(script, root)
            tracked = {file.path for file in identity.files}
            self.assertIn("series.py", tracked)

    def test_editing_a_dependency_moves_the_fingerprint_not_the_entry_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dramapy-hash-") as tempdir:
            root = Path(tempdir)
            script = self._project(root)
            before = episode_source_hash(script, root)

            (root / "beats.py").write_text("TROPE = 'contract-marriage'\n", encoding="utf-8")
            after_dep = episode_source_hash(script, root)
            self.assertNotEqual(before.source_fingerprint, after_dep.source_fingerprint)
            self.assertEqual(before.source_hash, after_dep.source_hash)

            series_path = root / "series.py"
            series_path.write_text(
                series_path.read_text(encoding="utf-8") + "# tweak\n", encoding="utf-8"
            )
            after_series = episode_source_hash(script, root)
            self.assertNotEqual(
                after_dep.source_fingerprint, after_series.source_fingerprint
            )
            self.assertEqual(before.source_hash, after_series.source_hash)


if __name__ == "__main__":
    unittest.main()
