"""Offline Harness team bridge tests: no model calls, native CAD, or hardware."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from kicadpy import parallel, parallel_tile


ROOT = Path(__file__).resolve().parents[3]


def put(root, name, contents):
    file = Path(root) / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(contents)
    return file


def contract():
    return {
        'name': 'Reference sensor',
        'coordinateFrame': {'units': 'mm', 'origin': 'board centre, top copper',
                            'axes': '+x right, +y up, +z out'},
        'board': {'outlineMm': [[-20, -15], [20, -15], [20, 15], [-20, 15]],
                  'thicknessMm': 1.6,
                  'mounts': [{'id': 'H1', 'xMm': -17, 'yMm': -12, 'holeDiameterMm': 2.2}]},
        'powerRails': [{'name': '3V3', 'voltageV': 3.3, 'source': 'reference regulator'}],
        'pins': [{'signal': 'SDA', 'device': 'U1', 'pin': '4', 'net': 'SDA', 'voltageV': 3.3}],
        'modules': [{'id': 'U1', 'interface': 'I2C', 'positionMm': [0, 0, 2],
                     'sizeMm': [20, 15, 3]}],
        'mechanicalEnvelopeMm': [60, 50, 30],
        'firmwareBehavior': ['Report sensor values over USB'],
        'assumptions': ['GIẢ ĐỊNH module dimensions; hardware untested'],
        'tasks': {'board': 'Native sensor board', 'firmware': 'Sensor and USB',
                  'case': 'Frame and service cover'},
    }


class FakeRunner:
    def __init__(self, visible, label='first', reject_stage=None):
        self.visible = visible
        self.label = label
        self.reject_stage = reject_stage
        self.visits = []
        self.plan_inputs = []
        self.visible_at_reviews = []

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        self.visits.append(stage)
        if stage == 'plan':
            asset = workspace / 'prompts/assets/ref.png'
            self.plan_inputs.append({
                'prior': (workspace / 'design/main.kicad_pro').read_text()
                if (workspace / 'design/main.kicad_pro').exists() else None,
                'asset': asset.read_bytes() if asset.exists() else None,
                'brief': (workspace / 'PILOT_BRIEF.md').read_text(),
            })
            result = contract()
        elif readonly:
            self.visible_at_reviews.append({
                'stage': stage,
                'source': (self.visible / 'design/main.kicad_pro').read_text(),
                'publication': (self.visible / 'boards/native.txt').read_text(),
                'verdict': json.loads((self.visible / '.harness/verdict.json').read_text()),
            })
            rejected = stage == self.reject_stage
            result = {'contractSha256': state['contractSha256'],
                      'decision': 'revise' if rejected else 'approve',
                      'summary': 'Case cover requires revision' if rejected else 'Reviewed checked files',
                      'findings': [{'path': 'case/cover.txt', 'message': 'Cover blocks interface'}]
                      if rejected else []}
        else:
            if stage == 'board':
                put(workspace, 'design/main.kicad_pro',
                    f'native source {self.label} revision {state["revision"]}')
            if stage == 'firmware':
                put(workspace, 'firmware/main.c', 'sensor firmware')
                put(workspace, 'firmware/WIRING.md', 'I2C wiring')
            if stage == 'case':
                put(workspace, 'case/frame.txt', 'device frame')
            if stage in ('firmware-final', 'case-final'):
                assert (workspace / 'boards/native.txt').read_text().startswith('checked board')
                if stage == 'firmware-final':
                    put(workspace, 'firmware/WIRING.md', 'wiring reconciled to native board')
                else:
                    put(workspace, 'case/cover.txt', 'removable cover')
                    put(workspace, 'case/README.md', 'assembly guide')
            result = {'contractSha256': state['contractSha256'], 'stage': stage,
                      'summary': 'Scoped output', 'blockers': []}
        return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .1,
                'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 2,
                          'reasoning_output_tokens': 1}, 'result': result, 'error': None}


class FakeChecker:
    def __init__(self, fail_board_revisions=()):
        self.fail_board_revisions = set(fail_board_revisions)
        self.calls = []

    def __call__(self, workspace, complete):
        revision = json.loads((workspace / 'PILOT_CONTRACT.json').read_text())['revision']
        failed = not complete and revision in self.fail_board_revisions
        phase = 'final' if complete else 'board'
        self.calls.append((revision, phase))
        put(workspace, 'boards/native.txt', f'checked {phase} revision {revision}')
        verdict = {'spec': 1, 'ready': not failed, 'summary': 'DRC error' if failed else 'Native ready'}
        parallel.write(workspace / '.harness/verdict.json', verdict)
        return {'passed': not failed, 'scope': phase,
                'findings': ['real DRC error'] if failed else []}


class TeamBridgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.workspace = self.base / 'visible-project'
        self.workspace.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(ROOT / 'harness/kicad/template' / name, self.workspace / name)
        put(self.workspace, '.circuit/team-auto', '')
        parallel.write(self.workspace / '.harness/verdict.json',
                       {'spec': 1, 'ready': False, 'summary': 'No board yet'})
        self.first_brief = put(self.workspace, 'prompts/history/first.md',
                               'Build the reference USB sensor with a case.')
        env = patch.dict(os.environ, {'KICAD_HARNESS_ROOT': str(ROOT)})
        env.start()
        self.addCleanup(env.stop)

    def run_team(self, brief=None, *, runner=None, checker=None, max_rounds=1):
        return parallel_tile.start(
            self.workspace, brief or self.first_brief, execute=True,
            runner=runner or FakeRunner(self.workspace), checker=checker or FakeChecker(),
            max_rounds=max_rounds)

    def test_first_prompt_mirrors_checked_artifacts_and_native_verdict_into_viewer_workspace(self):
        runner = FakeRunner(self.workspace)
        checker = FakeChecker()
        project_metadata = (self.workspace / 'project.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '--execute'):
            parallel_tile.start(self.workspace, self.first_brief, runner=runner, checker=checker)
        self.assertEqual(runner.visits, [])

        state = self.run_team(runner=runner, checker=checker)
        self.assertEqual(state['status'], 'candidate_ready', state.get('error'))
        self.assertEqual(state['lastMirroredRun'], 'r001')
        self.assertEqual(state['roundsAttempted'], 1)
        self.assertEqual(checker.calls, [(1, 'board'), (1, 'final')])
        self.assertEqual((self.workspace / 'design/main.kicad_pro').read_text(),
                         'native source first revision 1')
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(), 'checked final revision 1')
        self.assertTrue((self.workspace / 'case/cover.txt').is_file())
        self.assertEqual(json.loads((self.workspace / '.harness/verdict.json').read_text()),
                         {'spec': 1, 'ready': True, 'summary': 'Native ready'})
        self.assertEqual([item['stage'] for item in runner.visible_at_reviews],
                         ['structure-review', 'final-review'])
        self.assertEqual(runner.visible_at_reviews[0]['publication'], 'checked board revision 1')
        self.assertEqual(runner.visible_at_reviews[1]['publication'], 'checked final revision 1')
        self.assertEqual(runner.plan_inputs[0]['brief'], self.first_brief.read_text())
        self.assertFalse((self.workspace / '.git').exists())
        self.assertEqual((self.workspace / 'project.json').read_bytes(), project_metadata)
        self.assertEqual(parallel_tile.status(self.workspace)['pilot']['status'], 'candidate_ready')

    def test_followup_seeds_accepted_design_and_copies_attached_reference(self):
        first = self.run_team()
        self.assertEqual(first['status'], 'candidate_ready', first.get('error'))
        asset = self.workspace / 'prompts/assets/ref.png'
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b'original image bytes')
        second_brief = put(self.workspace, 'prompts/history/second.md',
                           'Keep the sensor board; adjust the case to the attached image.')
        runner = FakeRunner(self.workspace, label='followup')
        second = self.run_team(second_brief, runner=runner)
        self.assertEqual(second['status'], 'candidate_ready', second.get('error'))
        self.assertEqual(second['lastMirroredRun'], 'r002')
        self.assertEqual(runner.plan_inputs[0]['prior'], 'native source first revision 1')
        self.assertEqual(runner.plan_inputs[0]['asset'], b'original image bytes')
        self.assertIn('prompts/assets/ref.png', runner.plan_inputs[0]['brief'])
        self.assertEqual((self.workspace / 'design/main.kicad_pro').read_text(),
                         'native source followup revision 1')
        prior_pilot = self.workspace / '.circuit/team-runs/r001/workspace'
        next_state = parallel.status(self.workspace / '.circuit/team-runs/r002')
        self.assertEqual(next_state['seedCommit'], parallel.git(prior_pilot, 'rev-parse', 'HEAD'))
        self.assertEqual(self.first_brief.read_text(), 'Build the reference USB sensor with a case.')

    def test_external_edit_refuses_followup_without_clobbering_user_file(self):
        self.assertEqual(self.run_team()['status'], 'candidate_ready')
        source = put(self.workspace, 'design/main.kicad_pro', 'user edit in visible project')
        followup = put(self.workspace, 'prompts/history/followup.md', 'Make a small case change.')
        runner = FakeRunner(self.workspace, label='should-not-run')
        with self.assertRaisesRegex(ValueError, 'changed outside the team'):
            self.run_team(followup, runner=runner)
        self.assertEqual(source.read_text(), 'user edit in visible project')
        self.assertEqual(runner.visits, [])
        self.assertFalse((self.workspace / '.circuit/team-runs/r002').exists())

    def test_reference_assets_cannot_escape_through_a_symlinked_parent(self):
        external = self.base / 'external-prompts'
        shutil.move(self.workspace / 'prompts', external)
        put(external, 'assets/ref.png', 'external image')
        (self.workspace / 'prompts').symlink_to(external, target_is_directory=True)
        runner = FakeRunner(self.workspace)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.run_team(runner=runner)
        self.assertEqual(runner.visits, [])
        self.assertFalse((self.workspace / '.circuit/team-runs/r001').exists())

    def test_native_failure_stays_unready_and_repair_is_bounded(self):
        runner = FakeRunner(self.workspace)
        checker = FakeChecker(fail_board_revisions=(1, 2))
        state = self.run_team(runner=runner, checker=checker, max_rounds=2)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertEqual(state['roundsAttempted'], 2)
        self.assertEqual(checker.calls, [(1, 'board'), (2, 'board')])
        self.assertFalse(json.loads((self.workspace / '.harness/verdict.json').read_text())['ready'])
        self.assertEqual([item['verdict']['ready'] for item in runner.visible_at_reviews], [False, False])
        self.assertFalse((self.workspace / 'case/cover.txt').exists())
        self.assertIn('Native board check failed', state['error'])

    def test_one_failed_round_can_revise_and_publish_final_native_result(self):
        runner = FakeRunner(self.workspace)
        checker = FakeChecker(fail_board_revisions=(1,))
        state = self.run_team(runner=runner, checker=checker, max_rounds=2)
        self.assertEqual(state['status'], 'candidate_ready', state.get('error'))
        self.assertEqual(state['roundsAttempted'], 2)
        self.assertEqual(checker.calls, [(1, 'board'), (2, 'board'), (2, 'final')])
        self.assertEqual([item['verdict']['ready'] for item in runner.visible_at_reviews],
                         [False, True, True])
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(), 'checked final revision 2')

    def test_rejected_final_review_keeps_native_verdict_and_reports_team_unready(self):
        runner = FakeRunner(self.workspace, reject_stage='final-review')
        state = self.run_team(runner=runner, max_rounds=1)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertFalse(state['teamReady'])
        self.assertIn('Cover blocks interface', state['error'])
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(), 'checked final revision 1')
        self.assertTrue((self.workspace / 'case/cover.txt').is_file())
        # Board readiness comes only from the native checker; team readiness also needs lead approval.
        self.assertTrue(json.loads((self.workspace / '.harness/verdict.json').read_text())['ready'])

    def test_mid_copy_failure_replays_hash_bound_checkpoint_on_sync(self):
        real_replace = os.replace
        failed_once = False

        def interrupt_copy(source, target):
            nonlocal failed_once
            if not failed_once and Path(target) == self.workspace / 'design/main.kicad_pro':
                failed_once = True
                raise OSError('injected copy interruption')
            return real_replace(source, target)

        with patch.object(parallel_tile.os, 'replace', side_effect=interrupt_copy):
            state = self.run_team(checker=FakeChecker(fail_board_revisions=(1,)))
        self.assertTrue(failed_once)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertTrue(parallel_tile.status(self.workspace)['mirrorPending'])
        self.assertTrue((self.workspace / 'case/frame.txt').is_file())
        self.assertFalse((self.workspace / 'design/main.kicad_pro').exists())
        self.assertFalse(json.loads((self.workspace / '.harness/verdict.json').read_text())['ready'])

        synced = parallel_tile.sync(self.workspace)
        self.assertEqual(synced['status'], 'needs_revision')
        self.assertFalse(parallel_tile.status(self.workspace)['mirrorPending'])
        self.assertEqual((self.workspace / 'design/main.kicad_pro').read_text(),
                         'native source first revision 1')
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(), 'checked board revision 1')
        self.assertFalse(json.loads((self.workspace / '.harness/verdict.json').read_text())['ready'])
        self.assertEqual(synced['lastMirroredRun'], 'r001')


if __name__ == '__main__':
    unittest.main()
