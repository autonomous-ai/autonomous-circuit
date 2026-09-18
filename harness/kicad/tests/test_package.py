"""The Solder harness package holds together: the manifest names files that exist and run.

Runs under plain `python3 -m unittest` and under pytest. No KiCad, no network, no install: the
scripts are exercised only where they need nothing vendored (the Python wrapper, workspace init).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
ROOT = PKG.parents[1]


def _manifest() -> dict:
    return json.loads((PKG / 'harness.json').read_text(encoding='utf-8'))


class ManifestTest(unittest.TestCase):

    def test_identity_and_engine(self):
        m = _manifest()
        self.assertEqual(m['spec'], 1)
        self.assertEqual(m['id'], 'autonomous/solder')
        self.assertEqual(m['name'], 'Solder')
        self.assertEqual(m['category'], 'PCB')
        self.assertIn(m['engine'], ('claude', 'codex'))
        self.assertEqual(m['verdict'], '.harness/verdict.json')

    def test_every_named_path_exists(self):
        m = _manifest()
        for rel in (m['workspace']['template'], m['workspace']['init'], m['agent']['instructions'],
                    m['toolchain']['setup'], m['toolchain']['doctor'], m['viewer']['command'], *m['agent']['skills']):
            self.assertTrue((PKG / rel).exists(), rel)
        self.assertTrue((PKG / m['workspace']['template'] / m['workspace']['marker']).is_file())

    def test_scripts_are_executable(self):
        for rel in ('toolchain/python', 'toolchain/setup.sh', 'toolchain/doctor.sh',
                    'toolchain/init-workspace.sh', 'toolchain/viewer.sh'):
            self.assertTrue(os.access(PKG / rel, os.X_OK), rel)

    def test_skills_dir_holds_skill_cards(self):
        m = _manifest()
        cards = [p for d in m['agent']['skills'] for p in (PKG / d).glob('*/SKILL.md')]
        self.assertTrue(cards)
        for card in cards:
            text = card.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('---\nname: '), card)

    def test_skills_dir_follows_the_engine(self):
        m = _manifest()
        expected = '.agents/skills' if m['engine'] == 'codex' else '.claude/skills'
        self.assertTrue(m['agent']['env']['CIRCUIT_SKILLS_DIR'].endswith(expected))
        self.assertEqual(m['agent']['env']['SOLDER_PYTHON'], '${dsh}/toolchain/python')

    def test_template_marker_is_a_native_project(self):
        meta = json.loads((PKG / 'template' / 'project.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['engine'], 'kicad-native')

    def test_agents_md_names_the_commands_the_agent_needs(self):
        text = (PKG / 'AGENTS.md').read_text(encoding='utf-8')
        for needle in ('kicadpy.publish --manufacturing design/main.kicad_pro', 'kicadpy.harness --active build',
                       'native-review-attestation.json', 'manufacturing.json', 'fab.ready'):
            self.assertIn(needle, text, needle)


class PythonWrapperTest(unittest.TestCase):

    def test_wrapper_execs_a_python_that_imports_kicadpy_and_circuitpy(self):
        out = subprocess.run([str(PKG / 'toolchain' / 'python'), '-c',
                              'import sys, kicadpy, circuitpy; print(sys.version_info >= (3, 10))'],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), 'True')

    def test_wrapper_points_circuit_toolchain_at_the_checkout(self):
        out = subprocess.run([str(PKG / 'toolchain' / 'python'), '-c', 'import os; print(os.environ["CIRCUIT_TOOLCHAIN"])'],
                             capture_output=True, text=True, timeout=60, env={k: v for k, v in os.environ.items() if k != 'CIRCUIT_TOOLCHAIN'})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(Path(out.stdout.strip()), ROOT / 'toolchain')


class InitWorkspaceTest(unittest.TestCase):

    def test_init_lays_out_the_workspace_and_seeds_the_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            for name in ('project.json', 'product.json'):
                (ws / name).write_text((PKG / 'template' / name).read_text(encoding='utf-8'), encoding='utf-8')
            env = {**os.environ, 'HARNESS_DSH_DIR': str(PKG)}
            out = subprocess.run([str(PKG / 'toolchain' / 'init-workspace.sh')], cwd=ws, env=env,
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            for d in ('.harness', 'design', 'tools', 'engineering', 'boards', '.circuit'):
                self.assertTrue((ws / d).is_dir(), d)
            meta = json.loads((ws / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['engine'], 'kicad-native')
            self.assertGreater(meta['created_at'], 0)
            verdict = json.loads((ws / '.harness' / 'verdict.json').read_text(encoding='utf-8'))
            self.assertEqual(verdict['spec'], 1)
            self.assertIs(verdict['ready'], False)
            self.assertEqual([p['state'] for p in verdict['phases']], ['pending', 'pending', 'pending'])

    def test_init_is_idempotent_and_keeps_the_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'project.json').write_text(json.dumps({'id': 'workspace', 'name': 'x', 'created_at': 5, 'updated_at': 5}))
            env = {**os.environ, 'HARNESS_DSH_DIR': str(PKG)}
            for _ in range(2):
                out = subprocess.run([str(PKG / 'toolchain' / 'init-workspace.sh')], cwd=ws, env=env,
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(out.returncode, 0, out.stderr)
            meta = json.loads((ws / 'project.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['created_at'], 5)
            self.assertEqual(meta['engine'], 'kicad-native')


if __name__ == '__main__':
    unittest.main()
