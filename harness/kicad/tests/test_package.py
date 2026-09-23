"""The KiCad harness package holds together: the manifest names files that exist and run.

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
# The sibling tile: the same harness on Grok Build. Everything but the manifest and README is a symlink here.
GROK = PKG.parent / 'kicad-grok'
TILES = {'autonomous/kicad': PKG, 'autonomous/kicad-grok': GROK}
SKILLS_DIR_FOR = {'claude': '.claude/skills', 'codex': '.agents/skills', 'grok': '.agents/skills'}


def _manifest(pkg: Path = PKG) -> dict:
    return json.loads((pkg / 'harness.json').read_text(encoding='utf-8'))


class ManifestTest(unittest.TestCase):

    def test_identity_and_engine(self):
        m = _manifest()
        self.assertEqual(m['spec'], 1)
        self.assertEqual(m['id'], 'autonomous/kicad')
        self.assertEqual(m['name'], 'KiCad')
        self.assertEqual(m['category'], 'PCB')
        self.assertIn(m['engine'], ('claude', 'codex'))
        self.assertEqual(m['verdict'], '.harness/verdict.json')

    def test_grok_tile_is_the_same_harness_on_grok(self):
        m = _manifest(GROK)
        self.assertEqual(m['spec'], 1)
        self.assertEqual(m['id'], 'autonomous/kicad-grok')
        self.assertEqual(m['engine'], 'grok')
        self.assertEqual(m['category'], 'PCB')
        self.assertEqual(m['verdict'], '.harness/verdict.json')
        base = _manifest()
        for key in ('workspace', 'toolchain', 'viewer'):
            self.assertEqual(m[key], base[key], key)
        self.assertEqual(m['agent']['instructions'], base['agent']['instructions'])
        self.assertEqual(m['agent']['skills'], base['agent']['skills'])
        self.assertEqual(m['agent']['env'], base['agent']['env'])
        for rel in ('AGENTS.md', 'skills', 'toolchain'):
            self.assertTrue((GROK / rel).is_symlink(), rel)
            self.assertEqual((GROK / rel).resolve(), (PKG / rel).resolve(), rel)
        # The template is a REAL directory of REAL files: the daemon copies it with cpSync without
        # dereferencing, so a symlinked template becomes a symlink where the workspace should be
        # (EEXIST, 2026-09-23) and a symlinked file would let init write through into this repo.
        self.assertFalse((GROK / 'template').is_symlink())
        for name in ('project.json', 'product.json'):
            self.assertFalse((GROK / 'template' / name).is_symlink(), name)
            self.assertEqual((GROK / 'template' / name).read_bytes(), (PKG / 'template' / name).read_bytes(), name)

    def test_grok_args_carry_model_always_approve_and_trust(self):
        args = _manifest(GROK)['agent']['args']
        self.assertEqual(args[args.index('-m') + 1], 'grok-4.7')
        self.assertEqual(args[args.index('--reasoning-effort') + 1], 'high')
        # The daemon has no permission-mode table for grok: the tile carries its own, the way the
        # codex manifest carries approval_policy=never. --trust gates AGENTS.md, skills and hooks.
        self.assertEqual(args[args.index('--permission-mode') + 1], 'bypassPermissions')
        self.assertIn('--trust', args)
        self.assertIn('--no-auto-update', args)
        self.assertFalse(any(a.startswith('-c') or a.startswith('hooks.') for a in args), 'codex-only flags')

    def test_every_named_path_exists(self):
        for tile_id, pkg in TILES.items():
            m = _manifest(pkg)
            for rel in (m['workspace']['template'], m['workspace']['init'], m['agent']['instructions'],
                        m['toolchain']['setup'], m['toolchain']['doctor'], m['viewer']['command'], *m['agent']['skills']):
                self.assertTrue((pkg / rel).exists(), f'{tile_id}: {rel}')
            self.assertTrue((pkg / m['workspace']['template'] / m['workspace']['marker']).is_file(), tile_id)

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
        # Harness links a tile's skills into .claude/skills for claude and .agents/skills for every
        # other engine (cli 0.2.89 `dshSkillsDirFor`); Grok Build scans .agents/skills too.
        for tile_id, pkg in TILES.items():
            m = _manifest(pkg)
            expected = SKILLS_DIR_FOR[m['engine']]
            self.assertTrue(m['agent']['env']['CIRCUIT_SKILLS_DIR'].endswith(expected), tile_id)
            self.assertEqual(m['agent']['env']['KICAD_HARNESS_PYTHON'], '${dsh}/toolchain/python', tile_id)

    def test_manifest_wires_stop_and_interrupt_without_trust_bypass(self):
        args = _manifest()['agent']['args']
        for event in ('Stop', 'Interrupt'):
            config = next(arg for arg in args if arg.startswith('hooks.' + event + '='))
            self.assertIn('kicadpy.autofinish', config)
            self.assertIn('$KICAD_HARNESS_PYTHON', config)
        self.assertFalse(any('bypass-hook-trust' in arg for arg in args))

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

    def test_init_writes_the_grok_stop_hook_only_for_the_grok_tile(self):
        for pkg, expect_hook in ((GROK, True), (PKG, False)):
            with tempfile.TemporaryDirectory() as tmp:
                ws = Path(tmp)
                (ws / 'project.json').write_text((PKG / 'template' / 'project.json').read_text(encoding='utf-8'), encoding='utf-8')
                env = {**os.environ, 'HARNESS_DSH_DIR': str(pkg)}
                out = subprocess.run([str(pkg / 'toolchain' / 'init-workspace.sh')], cwd=ws, env=env,
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(out.returncode, 0, out.stderr)
                hook = ws / '.grok' / 'hooks' / 'kicad.json'
                self.assertEqual(hook.is_file(), expect_hook, pkg.name)
                # Grok finds project hooks only at a git root (2026-09-23): the grok workspace is one.
                self.assertEqual((ws / '.git').is_dir(), expect_hook, pkg.name)
                if expect_hook:
                    hooks = json.loads(hook.read_text(encoding='utf-8'))['hooks']
                    for event, timeout in (('Stop', 1200), ('StopCancelled', 3)):
                        (cmd,) = hooks[event][0]['hooks']
                        self.assertEqual(cmd['type'], 'command')
                        self.assertIn('kicadpy.autofinish', cmd['command'])
                        self.assertIn('$KICAD_HARNESS_PYTHON', cmd['command'])
                        self.assertEqual(cmd['timeout'], timeout)

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
