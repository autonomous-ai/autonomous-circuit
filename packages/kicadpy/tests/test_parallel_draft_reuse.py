"""Prior worker source is an untrusted draft after a contract revision."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kicadpy import parallel as pilot

try:
    from . import test_parallel as fixture
except ImportError:
    import test_parallel as fixture


class DraftRunner(fixture.Fake):
    def __init__(self):
        super().__init__()
        self.second_board_inputs = None
        self.second_board_prompt = None
        self.review_prompts = []

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        if readonly and stage.endswith('-review'):
            self.review_prompts.append(prompt)
        if stage == 'board' and state['revision'] == 2:
            self.second_board_inputs = {
                name: (Path(workspace) / name).read_text() if (Path(workspace) / name).is_file() else None
                for name in ('design/main.kicad_pro', 'tools/board/width.py',
                             'engineering/power.md', 'boards/packet.json',
                             'build/board/native.json', 'manufacturing.json')
            }
            self.second_board_prompt = prompt
        receipt = super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)
        if stage == 'board' and state['revision'] == 1:
            fixture.put(workspace, 'design/main.kicad_pro', 'draft KiCad project')
            fixture.put(workspace, 'tools/board/width.py', 'draft placement script')
            fixture.put(workspace, 'engineering/power.md', 'draft power notes')
            fixture.put(workspace, 'boards/packet.json', 'derived packet')
            fixture.put(workspace, 'build/board/native.json', 'derived build')
            receipt['result']['blockers'] = ['Board still needs a mount fix']
        elif stage == 'board' and state['revision'] == 2:
            fixture.put(workspace, 'design/main.kicad_pro', 'reconciled with revision two')
        return receipt


class DraftReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        prompt = fixture.put(base, 'brief.txt', 'Make a reference board and frame.')
        self.root = base / 'pilot'
        pilot.initialize(self.root, prompt, instructions=fixture.CARD)
        self.runner = DraftRunner()
        self.assertEqual(pilot.plan(self.root, execute=True, runner=self.runner)['status'], 'planned')
        failed = pilot.run(self.root, execute=True, runner=self.runner, checker=fixture.checker)
        self.assertEqual(failed['status'], 'needs_revision', failed['findings'])

    def revise_and_run(self):
        planned = pilot.plan(self.root, execute=True, revise=True, runner=self.runner)
        self.assertEqual(planned['status'], 'planned', planned['findings'])
        return pilot.run(self.root, execute=True, runner=self.runner, checker=fixture.checker)

    def test_reseeds_only_prior_owned_source_and_requires_current_contract(self):
        result = self.revise_and_run()
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(self.runner.second_board_inputs, {
            'design/main.kicad_pro': 'draft KiCad project',
            'tools/board/width.py': 'draft placement script',
            'engineering/power.md': None,
            'boards/packet.json': None,
            'build/board/native.json': None,
            'manufacturing.json': None,
        })
        self.assertIn('untrusted drafts copied from revision 1', self.runner.second_board_prompt)
        self.assertIn('CURRENT sealed contract', self.runner.second_board_prompt)
        self.assertTrue(any('DRAFT PROVENANCE:' in prompt and 'design/main.kicad_pro' in prompt
                            for prompt in self.runner.review_prompts))
        self.assertEqual((self.root / 'workspace/design/main.kicad_pro').read_text(),
                         'reconciled with revision two')
        provenance = json.loads((self.root / '.pilot/drafts/r002-board.json').read_text())
        self.assertEqual(provenance['priorRevision'], 1)
        self.assertEqual({item['path'] for item in provenance['sources']},
                         {'design/main.kicad_pro', 'tools/board/width.py'})

    def test_changed_integration_source_wins_over_old_draft(self):
        current = self.root / 'workspace'
        fixture.put(current, 'design/main.kicad_pro', 'newer integrated source')
        pilot.commit(current, ['design/main.kicad_pro'], 'Integrate newer design input')
        result = self.revise_and_run()
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(self.runner.second_board_inputs['design/main.kicad_pro'],
                         'newer integrated source')
        self.assertNotIn('design/main.kicad_pro',
                         {x['path'] for x in json.loads((self.root / '.pilot/drafts/r002-board.json').read_text())['sources']})

    def test_tampered_prior_pointer_suppresses_all_draft_reuse(self):
        pointer = self.root / 'workers/r001-board/.git'
        pointer.write_text(pointer.read_text() + '\n')
        result = self.revise_and_run()
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertTrue(all(value is None for value in self.runner.second_board_inputs.values()))
        self.assertEqual(json.loads((self.root / '.pilot/drafts/r002-board.json').read_text())['sources'], [])

    def test_unowned_prior_edit_suppresses_all_draft_reuse(self):
        fixture.put(self.root / 'workers/r001-board', 'firmware/foreign.c', 'cross-role write')
        result = self.revise_and_run()
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertTrue(all(value is None for value in self.runner.second_board_inputs.values()))

    def test_staged_prior_index_suppresses_all_draft_reuse(self):
        old = self.root / 'workers/r001-board'
        pilot.git(old, 'add', '--', 'design/main.kicad_pro')
        result = self.revise_and_run()
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertTrue(all(value is None for value in self.runner.second_board_inputs.values()))

    def test_changed_shared_git_metadata_blocks_revision_and_draft_reuse(self):
        config = self.root / 'workspace/.git/config'
        config.write_text(config.read_text() + '\n[pilot]\n altered = true\n')
        with self.assertRaisesRegex(ValueError, 'shared Git metadata changed'):
            self.revise_and_run()
        self.assertIsNone(self.runner.second_board_inputs)
        self.assertFalse((self.root / 'workers/r002-board').exists())

    def test_source_changed_between_snapshot_and_copy_is_rejected(self):
        planned = pilot.plan(self.root, execute=True, revise=True, runner=self.runner)
        self.assertEqual(planned['status'], 'planned')
        target = self.root / 'workers/r001-board/design/main.kicad_pro'
        real_read = Path.read_bytes
        reads = 0

        def racy_read(path):
            nonlocal reads
            data = real_read(path)
            if path == target:
                reads += 1
                if reads == 2:
                    return b'changed after snapshot'
            return data

        with patch.object(Path, 'read_bytes', racy_read):
            drafts = pilot._prior_draft(self.root, planned, 'board', 'board',
                                        pilot.snapshot(self.root / 'workspace'))
        self.assertGreaterEqual(reads, 2)
        self.assertEqual(drafts, [])

    def test_source_allowlist_keeps_build_artifacts_out(self):
        allowed = [('board', 'design/main.kicad_pcb'), ('board', 'tools/board/width.py'),
                   ('firmware', 'firmware/crab/main.ino'), ('firmware', 'tools/firmware/recover.cpp'),
                   ('case', 'case/build.py'), ('case', 'tools/case/model.py')]
        denied = [('board', 'boards/main.enclosure.json'), ('board', 'manufacturing.json'),
                  ('board', 'engineering/power.md'),
                  ('board', 'tools/board/placement.json'), ('firmware', 'firmware/app.bin'),
                  ('firmware', 'firmware/libraries/vendor/main.cpp'),
                  ('case', 'case/out/part.stl'), ('case', 'case/reports/case-check.json'),
                  ('case', 'tools/case/out/model.py')]
        for role, name in allowed:
            self.assertTrue(pilot._draft_source(role, name), name)
        for role, name in denied:
            self.assertFalse(pilot._draft_source(role, name), name)
