"""Offline Harness team bridge tests: no model calls, native CAD, or hardware."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

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
        elif stage.endswith('-advice'):
            role = stage.removesuffix('-advice')
            action_path = {'board': 'design/main.kicad_pro',
                           'firmware': 'firmware/main.c',
                           'case': 'case/frame.txt',
                           'firmware-final': 'firmware/WIRING.md',
                           'case-final': 'case/cover.txt'}.get(role, 'PILOT_CONTRACT.json')
            result = {'contractSha256': state['contractSha256'], 'stage': role,
                      'decision': 'repair', 'summary': 'Repair the current implementation',
                      'actions': [{'path': action_path, 'message': 'Fix the reported finding'}],
                      'changedFields': []}
        elif readonly:
            self.visible_at_reviews.append({
                'stage': stage,
                'source': (self.visible / 'design/main.kicad_pro').read_text(),
                'publication': (self.visible / 'boards/native.txt').read_text(),
                'verdict': json.loads((self.visible / '.harness/verdict.json').read_text()),
            })
            rejected = stage == self.reject_stage
            finding_path = ('design/main.kicad_pro' if stage == 'board-review'
                            else 'case/cover.txt')
            result = {'contractSha256': state['contractSha256'],
                      'decision': 'revise' if rejected else 'approve',
                      'summary': 'Review requires revision' if rejected else 'Reviewed checked files',
                      'findings': [{'path': finding_path, 'message': 'Review found a blocker'}]
                      if rejected else []}
        else:
            if stage == 'board':
                put(workspace, 'design/main.kicad_pro',
                    f'native source {self.label} revision {state["revision"]}')
            if stage == 'firmware':
                assert (workspace / 'boards/native.txt').read_text().startswith('checked board')
                put(workspace, 'firmware/main.c', 'sensor firmware')
                put(workspace, 'firmware/WIRING.md', 'I2C wiring')
            if stage == 'case':
                assert (workspace / 'boards/native.txt').read_text().startswith('checked board')
                put(workspace, 'case/frame.txt', 'device frame')
            if stage in ('firmware-final', 'case-final'):
                assert (workspace / 'boards/native.txt').read_text().startswith('checked ')
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


class BlockOneRoleRunner(FakeRunner):
    """The blocked worker waits until its sibling appears in the live view."""

    def __init__(self, visible, blocked_role, sibling_artifact):
        super().__init__(visible)
        self.blocked_role = blocked_role
        self.sibling_artifact = sibling_artifact
        self.saw_sibling_before_return = False

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        if stage == self.blocked_role:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (self.visible / self.sibling_artifact).is_file():
                    self.saw_sibling_before_return = True
                    break
                time.sleep(.02)
        receipt = super().__call__(root, state, stage, model, workspace, prompt,
                                   schema, readonly)
        if stage == self.blocked_role:
            receipt['result']['blockers'] = [f'{stage} needs design revision']
        return receipt


class BoardInterfaceChangeRunner(FakeRunner):
    """The lead cites a real shared interface field before requesting a new contract."""

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        receipt = super().__call__(root, state, stage, model, workspace, prompt,
                                   schema, readonly)
        if stage == 'board-advice' and state['revision'] == 1:
            receipt['result'].update(
                decision='interface_change',
                summary='Board outline must expand to route the required nets',
                changedFields=['board.outlineMm'],
            )
        elif stage == 'plan' and state['revision'] == 1:
            receipt['result']['board']['outlineMm'][0][0] = -25
        return receipt


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

    def test_macos_idle_sleep_assertion_is_parent_bound_and_released_on_error(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('caffeinate', 5), 0]
        with patch.object(parallel_tile.sys, 'platform', 'darwin'), \
                patch.object(parallel_tile.subprocess, 'Popen', return_value=process) as launch:
            with self.assertRaisesRegex(RuntimeError, 'worker failed'):
                with parallel_tile._prevent_idle_sleep():
                    raise RuntimeError('worker failed')
        launch.assert_called_once_with(
            ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_macos_idle_sleep_assertion_releases_on_interrupt(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(parallel_tile.sys, 'platform', 'darwin'), \
                patch.object(parallel_tile.subprocess, 'Popen', return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                with parallel_tile._prevent_idle_sleep():
                    raise KeyboardInterrupt
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)

    def test_macos_idle_sleep_cleanup_tolerates_process_exit_races(self):
        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = ProcessLookupError
        process.wait.side_effect = [subprocess.TimeoutExpired('caffeinate', 5), 0]
        process.kill.side_effect = ProcessLookupError
        with patch.object(parallel_tile.sys, 'platform', 'darwin'), \
                patch.object(parallel_tile.subprocess, 'Popen', return_value=process):
            with parallel_tile._prevent_idle_sleep():
                pass
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_macos_idle_sleep_cleanup_reaps_already_exited_process(self):
        process = Mock()
        process.poll.side_effect = [None, 0]
        with patch.object(parallel_tile.sys, 'platform', 'darwin'), \
                patch.object(parallel_tile.subprocess, 'Popen', return_value=process):
            with parallel_tile._prevent_idle_sleep():
                pass
        process.terminate.assert_not_called()
        process.wait.assert_called_once_with(timeout=5)

    def test_idle_sleep_assertion_is_noop_outside_macos(self):
        with patch.object(parallel_tile.sys, 'platform', 'linux'), \
                patch.object(parallel_tile.subprocess, 'Popen',
                             side_effect=AssertionError('unexpected caffeinate')):
            with parallel_tile._prevent_idle_sleep():
                pass

    def test_macos_idle_sleep_assertion_reports_launch_failure(self):
        with patch.object(parallel_tile.sys, 'platform', 'darwin'), \
                patch.object(parallel_tile.subprocess, 'Popen',
                             side_effect=OSError('command unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Unable to start macOS idle-sleep assertion'):
                with parallel_tile._prevent_idle_sleep():
                    pass

    def test_team_assertion_covers_model_calls_and_releases_on_return(self):
        awake = []
        runner = FakeRunner(self.workspace)

        @contextlib.contextmanager
        def assertion():
            awake.append(True)
            try:
                yield
            finally:
                awake.pop()

        def guarded_runner(*args):
            self.assertTrue(awake)
            return runner(*args)

        with patch.object(parallel_tile, '_prevent_idle_sleep', side_effect=assertion):
            state = self.run_team(runner=guarded_runner)
        self.assertEqual(state['status'], 'candidate_ready', state.get('error'))
        self.assertTrue(runner.visits)
        self.assertEqual(awake, [])

    def test_team_assertion_releases_when_initialization_fails(self):
        awake = []

        @contextlib.contextmanager
        def assertion():
            awake.append(True)
            try:
                yield
            finally:
                awake.pop()

        with patch.object(parallel_tile, '_prevent_idle_sleep', side_effect=assertion), \
                patch.object(parallel, 'initialize', side_effect=RuntimeError('setup failed')):
            state = self.run_team()
        self.assertEqual(state['status'], 'failed')
        self.assertIn('setup failed', state['error'])
        self.assertEqual(awake, [])

    def staged_pilot(self):
        """Create a clean pilot source so checkpoint replay can be tested alone."""
        pilot_root = self.workspace / '.circuit/team-runs/r001'
        parallel.initialize(pilot_root, self.first_brief)
        source = pilot_root / 'workspace'
        put(source, 'design/main.kicad_pro', 'checked board source')
        put(source, 'boards/native.txt', 'checked board publication')
        parallel.write(source / '.harness/verdict.json',
                       {'spec': 1, 'ready': True, 'summary': 'Native board ready'})
        parallel.commit(source, ['design/main.kicad_pro', 'boards/native.txt'],
                        'Record checked board')
        state = parallel_tile._read_state(self.workspace)
        state.update(activeRun='r001', status='running',
                     initialVerdictSha256=parallel_tile._sha(
                         self.workspace / '.harness/verdict.json'))
        parallel_tile._save_state(self.workspace, state)
        pilot_state = {'revision': 1, 'contractSha256': 'contract-one'}
        return pilot_root, source, state, pilot_state

    def test_progressive_checkpoints_publish_each_completed_role_and_board_approval(self):
        root, source, state, pilot_state = self.staged_pilot()
        mirror = lambda stage: parallel_tile._mirror(self.workspace, root, source, state,
                                                      pilot_state, stage)
        mirror('board')
        self.assertEqual(state['lastCheckpointStage'], 'board')
        self.assertTrue(state['nativeBoardReady'])
        self.assertFalse(state['teamReady'])
        self.assertFalse((self.workspace / 'firmware/main.c').exists())
        mirror('board-approved')  # Same files, but lead approval must advance progress.
        self.assertEqual(state['lastCheckpointStage'], 'board-approved')
        self.assertEqual(parallel_tile.status(self.workspace)['lastCheckpointStage'], 'board-approved')
        put(source, 'firmware/main.c', 'compiled-source draft')
        parallel.commit(source, ['firmware/main.c'], 'Integrate firmware')
        mirror('firmware')
        self.assertEqual((self.workspace / 'firmware/main.c').read_text(), 'compiled-source draft')
        self.assertFalse((self.workspace / 'case/frame.txt').exists())
        put(source, 'case/frame.txt', 'native frame')
        parallel.commit(source, ['case/frame.txt'], 'Integrate case')
        mirror('case')
        self.assertEqual((self.workspace / 'case/frame.txt').read_text(), 'native frame')
        self.assertEqual(state['lastCheckpointStage'], 'case')
        self.assertFalse(state['teamReady'])

    def test_worker_preview_is_separate_unverified_and_bounded(self):
        root, source, state, _ = self.staged_pilot()
        draft = root / 'roles/board-draft'
        put(draft, 'design/main.kicad_pro', 'unreviewed native source')
        put(draft, 'boards/main_review/_pcb.svg', '<svg xmlns="http://www.w3.org/2000/svg"/>')
        (draft / 'boards/main_review/too-large.png').write_bytes(
            b'x' * (parallel_tile.PREVIEW_MAX_FILE + 1))
        before_verdict = (self.workspace / '.harness/verdict.json').read_bytes()
        preview = parallel_tile._preview_progress(
            self.workspace, root, 'board', 'board', draft, 'draft', ['Routing is in progress'])
        self.assertFalse(preview['teams']['board']['verified'])
        self.assertEqual(preview['teams']['board']['status'], 'draft')
        self.assertIn('design/main.kicad_pro', preview['teams']['board']['files'])
        image = preview['teams']['board']['media'][0]
        self.assertEqual(len(preview['teams']['board']['media']), 1)
        self.assertEqual((self.workspace / image['path']).read_text(),
                         '<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertEqual((self.workspace / '.harness/verdict.json').read_bytes(), before_verdict)
        self.assertFalse((self.workspace / 'design/main.kicad_pro').exists())
        self.assertFalse(parallel_tile._read_state(self.workspace)['teamReady'])
        (draft / 'boards/main_review/_pcb.svg').unlink()
        refreshed = parallel_tile._preview_progress(
            self.workspace, root, 'board', 'board', draft, 'draft', [])
        self.assertEqual(refreshed['teams']['board']['media'], [])
        self.assertFalse((self.workspace / image['path']).exists())
        with self.assertRaisesRegex(ValueError, 'outside the active pilot'):
            parallel_tile._preview_progress(self.workspace, root, 'board', 'board',
                                            self.workspace, 'draft', [])

    def test_worker_preview_rejects_symlink_destination(self):
        root, _, _, _ = self.staged_pilot()
        draft = root / 'roles/board-draft'
        put(draft, 'boards/main_review/_pcb.svg', '<svg/>')
        destination = self.workspace / '.circuit/team-previews'
        destination.symlink_to(self.base / 'outside', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink in preview destination'):
            parallel_tile._preview_progress(self.workspace, root, 'board', 'board',
                                            draft, 'draft', [])

    def test_role_checkpoint_replays_after_interrupted_copy(self):
        root, source, state, pilot_state = self.staged_pilot()
        parallel_tile._mirror(self.workspace, root, source, state, pilot_state, 'board')
        parallel_tile._mirror(self.workspace, root, source, state, pilot_state,
                              'board-approved')
        put(source, 'firmware/main.c', 'reviewable firmware')
        parallel.commit(source, ['firmware/main.c'], 'Integrate firmware')
        real_replace = os.replace
        failed_once = False

        def interrupt_copy(source_path, target):
            nonlocal failed_once
            if not failed_once and Path(target) == self.workspace / 'firmware/main.c':
                failed_once = True
                raise OSError('injected role-copy interruption')
            return real_replace(source_path, target)

        with patch.object(parallel_tile.os, 'replace', side_effect=interrupt_copy):
            with self.assertRaisesRegex(OSError, 'role-copy interruption'):
                parallel_tile._mirror(self.workspace, root, source, state, pilot_state,
                                      'firmware')
        self.assertTrue(failed_once)
        self.assertTrue(parallel_tile.status(self.workspace)['mirrorPending'])
        self.assertEqual(state['lastCheckpointStage'], 'board-approved')
        self.assertFalse((self.workspace / 'firmware/main.c').exists())
        synced = parallel_tile.sync(self.workspace)
        self.assertEqual(synced['lastCheckpointStage'], 'firmware')
        self.assertEqual((self.workspace / 'firmware/main.c').read_text(), 'reviewable firmware')
        self.assertFalse(synced['teamReady'])
        self.assertFalse(parallel_tile.status(self.workspace)['mirrorPending'])

    def test_checkpoint_refuses_git_metadata_drift_from_pilot_baseline(self):
        root, source, state, pilot_state = self.staged_pilot()
        pilot_state['gitMetadataSha256'] = parallel_tile._git_metadata_digest(source)
        with (source / '.git/config').open('a') as stream:
            stream.write('\n[harmless]\nvalue = changed by sibling\n')
        self.assertEqual(parallel.git(source, 'status', '--porcelain'), '')
        with self.assertRaisesRegex(ValueError, 'Git metadata changed before checkpoint'):
            parallel_tile._mirror(self.workspace, root, source, state, pilot_state, 'board')
        self.assertFalse(parallel_tile.status(self.workspace)['mirrorPending'])
        self.assertFalse((self.workspace / 'design/main.kicad_pro').exists())

    def test_pending_role_checkpoint_refuses_replay_after_git_config_changes(self):
        root, source, state, pilot_state = self.staged_pilot()
        parallel_tile._mirror(self.workspace, root, source, state, pilot_state, 'board')
        put(source, 'firmware/main.c', 'checkpointed firmware')
        parallel.commit(source, ['firmware/main.c'], 'Integrate firmware')
        real_replace = os.replace

        def interrupt_copy(source_path, target):
            if Path(target) == self.workspace / 'firmware/main.c':
                raise OSError('injected interruption')
            return real_replace(source_path, target)

        with patch.object(parallel_tile.os, 'replace', side_effect=interrupt_copy):
            with self.assertRaisesRegex(OSError, 'injected interruption'):
                parallel_tile._mirror(self.workspace, root, source, state, pilot_state,
                                      'firmware')
        with (source / '.git/config').open('a') as stream:
            stream.write('\n[harmless]\nvalue = changed after journal\n')
        self.assertEqual(parallel.git(source, 'status', '--porcelain'), '')
        with self.assertRaisesRegex(ValueError, 'Git metadata changed since checkpoint'):
            parallel_tile.sync(self.workspace)
        self.assertTrue(parallel_tile.status(self.workspace)['mirrorPending'])
        self.assertFalse((self.workspace / 'firmware/main.c').exists())

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
        self.assertEqual(checker.calls, [(1, 'board'), (1, 'board'), (1, 'final')])
        self.assertEqual((self.workspace / 'design/main.kicad_pro').read_text(),
                         'native source first revision 1')
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(), 'checked final revision 1')
        self.assertTrue((self.workspace / 'case/cover.txt').is_file())
        self.assertEqual(json.loads((self.workspace / '.harness/verdict.json').read_text()),
                         {'spec': 1, 'ready': True, 'summary': 'Native ready'})
        self.assertEqual([item['stage'] for item in runner.visible_at_reviews],
                         ['board-review', 'structure-review', 'final-review'])
        self.assertEqual(runner.visible_at_reviews[0]['publication'], 'checked board revision 1')
        self.assertEqual(runner.visible_at_reviews[1]['publication'], 'checked board revision 1')
        self.assertEqual(runner.visible_at_reviews[2]['publication'], 'checked final revision 1')
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

    def test_followup_refuses_checkpointed_pilot_with_mutated_git_config(self):
        first = self.run_team()
        self.assertEqual(first['status'], 'candidate_ready', first.get('error'))
        source = self.workspace / '.circuit/team-runs/r001/workspace'
        self.assertEqual(first['sourceHead'], parallel.git(source, 'rev-parse', 'HEAD'))
        self.assertEqual(first['sourceGitMetadataSha256'],
                         parallel_tile._git_metadata_digest(source))
        with (source / '.git/config').open('a') as stream:
            stream.write('\n[harmless]\nvalue = changed after checkpoint\n')
        self.assertEqual(parallel.git(source, 'status', '--porcelain'), '')
        followup = put(self.workspace, 'prompts/history/followup.md', 'Refine the cover.')
        runner = FakeRunner(self.workspace)
        with self.assertRaisesRegex(ValueError, 'Git metadata changed since checkpoint'):
            self.run_team(followup, runner=runner)
        self.assertEqual(runner.visits, [])
        self.assertFalse((self.workspace / '.circuit/team-runs/r002').exists())

    def test_followup_refuses_checkpointed_pilot_with_new_clean_head(self):
        first = self.run_team()
        self.assertEqual(first['status'], 'candidate_ready', first.get('error'))
        source = self.workspace / '.circuit/team-runs/r001/workspace'
        put(source, 'README.md', 'Changed without a new viewer checkpoint')
        parallel.commit(source, ['README.md'], 'Uncheckpointed change')
        self.assertEqual(parallel.git(source, 'status', '--porcelain'), '')
        followup = put(self.workspace, 'prompts/history/followup.md', 'Refine the cover.')
        with self.assertRaisesRegex(ValueError, 'commit changed since checkpoint'):
            self.run_team(followup)
        self.assertFalse((self.workspace / '.circuit/team-runs/r002').exists())

    def test_followup_refuses_partial_checkpoint_if_sibling_changes_git_config(self):
        runner = BlockOneRoleRunner(self.workspace, 'case', 'firmware/main.c')
        first = self.run_team(runner=runner, max_rounds=1)
        self.assertEqual(first['status'], 'needs_revision')
        self.assertEqual(first['lastCheckpointStage'], 'firmware')
        self.assertTrue((self.workspace / 'firmware/main.c').is_file())
        source = self.workspace / '.circuit/team-runs/r001/workspace'
        with (source / '.git/config').open('a') as stream:
            stream.write('\n[harmless]\nvalue = changed after partial checkpoint\n')
        self.assertEqual(parallel.git(source, 'status', '--porcelain'), '')
        followup = put(self.workspace, 'prompts/history/followup.md', 'Continue the design.')
        with self.assertRaisesRegex(ValueError, 'Git metadata changed since checkpoint'):
            self.run_team(followup)
        self.assertFalse((self.workspace / '.circuit/team-runs/r002').exists())

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
        self.assertEqual(state['roundsAttempted'], 1)
        self.assertFalse(state['replanRequired'])
        self.assertEqual(runner.visits.count('plan'), 1)
        self.assertTrue(checker.calls)
        self.assertTrue(all(call == (1, 'board') for call in checker.calls))
        self.assertFalse(json.loads((self.workspace / '.harness/verdict.json').read_text())['ready'])
        self.assertEqual(runner.visible_at_reviews, [])
        self.assertNotIn('firmware', runner.visits)
        self.assertNotIn('case', runner.visits)
        self.assertFalse((self.workspace / 'case/cover.txt').exists())
        self.assertIn('Native board check failed after', state['error'])

    def test_lead_cited_interface_change_revises_and_publishes_final_native_result(self):
        runner = BoardInterfaceChangeRunner(self.workspace)
        checker = FakeChecker(fail_board_revisions=(1,))
        state = self.run_team(runner=runner, checker=checker, max_rounds=2)
        self.assertEqual(state['status'], 'candidate_ready', state.get('error'))
        self.assertEqual(state['roundsAttempted'], 2)
        self.assertEqual(runner.visits.count('plan'), 2)
        self.assertIn('board-advice', runner.visits)
        pilot = self.workspace / '.circuit/team-runs/r001/workspace'
        revised = json.loads((pilot / 'PILOT_CONTRACT.json').read_text())
        self.assertEqual(revised['interfaces']['board']['outlineMm'][0][0], -25)
        self.assertIn((2, 'board'), checker.calls)
        self.assertEqual(checker.calls[-1], (2, 'final'))
        self.assertTrue(all(phase == 'board' for _, phase in checker.calls[:-1]))
        self.assertEqual([item['verdict']['ready'] for item in runner.visible_at_reviews],
                         [True, True, True])
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(), 'checked final revision 2')

    def test_revision_clears_previous_error_while_new_round_is_running(self):
        checker = FakeChecker(fail_board_revisions=(1,))
        runner = BoardInterfaceChangeRunner(self.workspace)
        saved = []
        prior_findings = []
        original_save = parallel_tile._save_state

        def record_save(workspace, state):
            saved.append(dict(state))
            if state['stage'] == 'revising':
                pilot = workspace / '.circuit/team-runs' / state['activeRun'] / '.pilot/state.json'
                prior_findings.extend(json.loads(pilot.read_text())['findings'])
            return original_save(workspace, state)

        with patch.object(parallel_tile, '_save_state', side_effect=record_save):
            result = self.run_team(runner=runner, checker=checker, max_rounds=2)

        self.assertEqual(result['status'], 'candidate_ready')
        self.assertTrue(any('Board outline must expand' in item
                            for item in prior_findings))
        for stage in ('revising', 'planning-2', 'building-2'):
            current = next(item for item in saved if item['stage'] == stage)
            self.assertEqual(current['status'], 'running')
            self.assertIsNone(current['error'], stage)

    def test_rejected_board_review_stops_dependent_workers_but_keeps_checked_board_visible(self):
        runner = FakeRunner(self.workspace, reject_stage='board-review')
        state = self.run_team(runner=runner, max_rounds=2)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertEqual(state['roundsAttempted'], 1)
        self.assertFalse(state['replanRequired'])
        self.assertEqual(runner.visits.count('plan'), 1)
        self.assertEqual(state['lastCheckpointStage'], 'board')
        self.assertFalse(state['teamReady'])
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(),
                         'checked board revision 1')
        self.assertEqual([entry['stage'] for entry in runner.visible_at_reviews],
                         ['board-review'] * 3)
        self.assertEqual(runner.visits.count('board'), 3)
        self.assertNotIn('firmware', runner.visits)
        self.assertNotIn('case', runner.visits)
        self.assertFalse((self.workspace / 'firmware/main.c').exists())

    def test_firmware_is_visible_while_case_is_still_running_then_case_rejects(self):
        runner = BlockOneRoleRunner(self.workspace, 'case', 'firmware/main.c')
        state = self.run_team(runner=runner, max_rounds=2)
        self.assertTrue(runner.saw_sibling_before_return)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertEqual(state['roundsAttempted'], 1)
        self.assertFalse(state['replanRequired'])
        self.assertEqual(runner.visits.count('plan'), 1)
        self.assertEqual(state['lastCheckpointStage'], 'firmware')
        self.assertFalse(state['teamReady'])
        self.assertEqual((self.workspace / 'firmware/main.c').read_text(), 'sensor firmware')
        self.assertFalse((self.workspace / 'case/frame.txt').exists())

    def test_case_is_visible_while_firmware_is_still_running_then_firmware_rejects(self):
        runner = BlockOneRoleRunner(self.workspace, 'firmware', 'case/frame.txt')
        state = self.run_team(runner=runner, max_rounds=1)
        self.assertTrue(runner.saw_sibling_before_return)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertEqual(state['lastCheckpointStage'], 'case')
        self.assertFalse(state['teamReady'])
        self.assertEqual((self.workspace / 'case/frame.txt').read_text(), 'device frame')
        self.assertFalse((self.workspace / 'firmware/main.c').exists())

    def test_rejected_final_review_keeps_native_verdict_and_reports_team_unready(self):
        runner = FakeRunner(self.workspace, reject_stage='final-review')
        state = self.run_team(runner=runner, max_rounds=1)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertFalse(state['teamReady'])
        self.assertIn('Review found a blocker', state['error'])
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
            state = self.run_team(checker=FakeChecker())
        self.assertTrue(failed_once)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertTrue(parallel_tile.status(self.workspace)['mirrorPending'])
        self.assertFalse((self.workspace / 'case/frame.txt').exists())
        self.assertFalse((self.workspace / 'design/main.kicad_pro').exists())
        self.assertFalse(json.loads((self.workspace / '.harness/verdict.json').read_text())['ready'])

        synced = parallel_tile.sync(self.workspace)
        self.assertEqual(synced['status'], 'needs_revision')
        self.assertFalse(parallel_tile.status(self.workspace)['mirrorPending'])
        self.assertEqual((self.workspace / 'design/main.kicad_pro').read_text(),
                         'native source first revision 1')
        self.assertEqual((self.workspace / 'boards/native.txt').read_text(), 'checked board revision 1')
        self.assertTrue(json.loads((self.workspace / '.harness/verdict.json').read_text())['ready'])
        self.assertEqual(synced['lastMirroredRun'], 'r001')


if __name__ == '__main__':
    unittest.main()
