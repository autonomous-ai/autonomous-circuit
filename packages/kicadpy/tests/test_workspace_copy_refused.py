"""A kicadpy copied into a board workspace refuses to import; the tile's copy is the checker."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import kicadpy

PKG = Path(kicadpy.__file__).resolve().parent


class WorkspaceCopyRefusedTest(unittest.TestCase):
    def run_import(self, cwd: Path) -> subprocess.CompletedProcess:
        # `-c`/`-m` put the cwd first on sys.path, which is exactly how the copy shadowed the tile.
        # The tile's copy stays reachable through an absolute PYTHONPATH (a relative one would
        # resolve against the temporary cwd and prove nothing).
        env = {**os.environ, 'PYTHONPATH': str(PKG.parent) + os.pathsep + os.environ.get('PYTHONPATH', '')}
        return subprocess.run([sys.executable, '-c', 'import kicadpy, pathlib; print(pathlib.Path(kicadpy.__file__).parent)'],
                              cwd=cwd, env=env, capture_output=True, text=True, timeout=60)

    def test_a_copy_beside_project_json_is_refused_with_the_rule_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'project.json').write_text(json.dumps({'engine': 'kicad-native'}))
            shutil.copytree(PKG, ws / 'kicadpy', ignore=shutil.ignore_patterns('__pycache__'))
            run = self.run_import(ws)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn('inside a board workspace', run.stderr)
            self.assertIn('The checker is the tile', run.stderr)

    def test_the_tile_copy_imports_from_anywhere_without_a_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.run_import(Path(tmp))
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), str(PKG))


if __name__ == '__main__':
    unittest.main()
