"""The new team pipeline runs independently without recreating its contract."""
import os
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from kicadpy import parallel, parallel_tile
from test_parallel import CARD, contract, put

# Unit runs must not depend on the developer machine's free disk.
os.environ.setdefault('CIRCUIT_TEAM_MIN_FREE_GB', '0')


class TeamRunner:
    def __init__(self):
        self.visits = []
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(3)
        self.peak = 0
        self.active = 0
        self.wait_for_three = True
        self.pause_stage = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.ask_expected_file = None

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        with self.lock:
            self.visits.append((stage, model, readonly, prompt))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if stage == 'plan':
                result = contract()
            elif stage == 'ask':
                if self.ask_expected_file is not None:
                    name, expected = self.ask_expected_file
                    assert (workspace / name).read_bytes() == expected
                result = {'answer': 'The lead has the current project context.'}
            elif stage == 'lead-addendum-0001':
                result = {'routes': ['firmware'], 'note': 'Change firmware sampling rate.',
                          'changedFields': ['firmwareBehavior']}
            elif readonly:
                result = {'contractSha256': state['contractSha256'], 'decision': 'approve',
                          'summary': 'Actual team files checked', 'findings': []}
            else:
                if self.wait_for_three and stage in ('board', 'firmware', 'case'):
                    self.barrier.wait(timeout=5)
                if stage == self.pause_stage:
                    self.entered.set()
                    if not self.release.wait(timeout=10):
                        raise TimeoutError('Test did not release the paused worker')
                path = {'board': 'design/main.kicad_pro',
                        'firmware': 'firmware/main.c', 'case': 'case/frame.txt'}[stage]
                put(workspace, path, f'{stage} output')
                if stage == 'firmware':
                    put(workspace, 'firmware/WIRING.md', 'Pins from contract')
                if stage == 'case':
                    put(workspace, 'case/README.md', 'Assembly guide')
                result = {'contractSha256': state['contractSha256'], 'stage': stage,
                          'summary': 'Done', 'blockers': []}
            return {'stage': stage, 'model': model, 'startedAt': time.time(),
                    'seconds': .01, 'usage': None, 'result': result, 'error': None}
        finally:
            with self.lock:
                self.active -= 1


def checker(workspace, complete):
    put(workspace, 'boards/published.json', 'native board')
    put(workspace, '.harness/verdict.json', json.dumps({'ready': True}))
    required = (('design/main.kicad_pro', 'firmware/main.c', 'case/frame.txt')
                if complete else ('design/main.kicad_pro',))
    passed = all((workspace / path).is_file() for path in required)
    return {'passed': passed, 'findings': [] if passed else ['Missing team output']}


class SlowFirmwareRunner(TeamRunner):
    """crab-robot-v9: firmware used its build clock, then the tester asked for a repair."""

    def __init__(self, clock):
        super().__init__()
        self.wait_for_three = False
        self.clock = clock
        self.firmware_limits = []
        self.tested = 0

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        if stage == 'firmware':
            self.firmware_limits.append(state['timeoutSeconds'])
            if len(self.firmware_limits) == 1:
                self.clock[0] += state['timeoutSeconds']
                put(workspace, 'firmware/main.c', 'first draft')
                return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                        'usage': None, 'result': None, 'error': 'Job timed out',
                        'failureKind': 'timeout'}
            self.clock[0] += 300
        if stage == 'firmware-test':
            self.tested += 1
            if self.tested == 1:
                return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                        'usage': None, 'error': None,
                        'result': {'contractSha256': state['contractSha256'], 'decision': 'revise',
                                   'summary': 'Reconnect tests missing',
                                   'findings': [{'path': 'firmware/main.c',
                                                 'message': 'No reconnect test'}]}}
        return super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)


class DisputeRunner(TeamRunner):
    """Firmware builder and tester disagree about firmware/main.c."""

    def __init__(self, ruling, builder_obeys=True):
        super().__init__()
        self.wait_for_three = False
        self.ruling = ruling
        self.builder_obeys = builder_obeys
        self.firmware_prompts = []
        self.advice_problems = []

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        if stage == 'firmware':
            self.firmware_prompts.append(prompt)
            if len(self.firmware_prompts) > 1:
                if self.builder_obeys and 'LEAD RULING' in prompt:
                    put(workspace, 'firmware/main.c', 'fixed')
                return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                        'usage': None, 'error': None,
                        'result': {'contractSha256': state['contractSha256'], 'stage': stage,
                                   'summary': 'Kept my design', 'blockers': []}}
        if stage == 'firmware-advice':
            self.advice_problems.append(prompt)
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'error': None,
                    'result': {'contractSha256': state['contractSha256'], 'stage': 'firmware',
                               'decision': self.ruling, 'summary': 'Lead ruling',
                               'actions': [{'path': 'firmware/main.c',
                                            'message': 'Debounce test is out of V0 scope'}],
                               'changedFields': []}}
        if stage == 'firmware-test':
            fixed = (Path(workspace) / 'firmware/main.c').read_text() == 'fixed'
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'error': None,
                    'result': {'contractSha256': state['contractSha256'],
                               'decision': 'approve' if fixed else 'revise',
                               'summary': 'Checked firmware',
                               'findings': [] if fixed else [{'path': 'firmware/main.c',
                                                              'message': 'Add a debounce test'}]}}
        return super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)


class TeamFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.prompt = put(self.base, 'prompt.txt', 'Build a sensor with firmware and case.')
        self.root = self.base / 'first'
        self.runner = TeamRunner()
        parallel.initialize(self.root, self.prompt, instructions=CARD, mode='teams')

    def test_all_three_builders_start_together_and_each_has_a_tester(self):
        planned = parallel.plan(self.root, execute=True, runner=self.runner)
        self.assertEqual(planned['status'], 'planned')
        previews = []
        checkpoints = []
        result = parallel.run(self.root, execute=True, runner=self.runner, checker=checker,
                              on_progress=lambda *args: previews.append(args),
                              on_checkpoint=lambda _workspace, _state, stage: checkpoints.append(stage))
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(self.runner.peak, 3)
        stages = [item[0] for item in self.runner.visits]
        for role in ('board', 'firmware', 'case'):
            self.assertIn(role + '-test', stages)
            self.assertLess(stages.index(role), stages.index(role + '-test'))
            self.assertIn(role, [p[0] for p in previews])
        self.assertEqual(checkpoints, ['board', 'final'])
        self.assertNotIn('board-review', stages)
        self.assertNotIn('structure-review', stages)
        self.assertNotIn('final-review', stages)
        self.assertEqual(stages.count('plan'), 1)
        self.assertEqual(stages.count('integration-test'), 1)

    def test_tester_requested_repair_gets_a_fresh_clock(self):
        clock = [0.0]
        runner = SlowFirmwareRunner(clock)
        parallel.plan(self.root, execute=True, runner=runner)
        with patch.object(parallel, '_stage_clock', side_effect=lambda: clock[0]):
            result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(runner.firmware_limits, [1800, parallel.TIMEOUT_CONTINUATION_SECONDS, 1800])

    def test_stuck_builder_and_tester_get_one_lead_ruling_then_continue(self):
        runner = DisputeRunner('repair')
        parallel.plan(self.root, execute=True, runner=runner)
        result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(len(runner.advice_problems), 1)
        self.assertIn('team_dispute', runner.advice_problems[0])
        self.assertEqual(len(runner.firmware_prompts), 3)
        self.assertNotIn('LEAD RULING', runner.firmware_prompts[1])
        self.assertIn('LEAD RULING', runner.firmware_prompts[2])

    def test_lead_waiver_closes_a_dispute_and_reaches_integration(self):
        runner = DisputeRunner('waive', builder_obeys=False)
        parallel.plan(self.root, execute=True, runner=runner)
        result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        waivers = json.loads((self.root / '.pilot/waivers/firmware.json').read_text())
        self.assertEqual([item['path'] for item in waivers], ['firmware/main.c'])
        prompts = {stage: prompt for stage, _model, _readonly, prompt in runner.visits}
        self.assertIn('LEAD WAIVERS', prompts['integration-test'])

    def test_still_stuck_after_the_lead_ruling_stops_instead_of_looping(self):
        runner = DisputeRunner('repair', builder_obeys=False)
        parallel.plan(self.root, execute=True, runner=runner)
        result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('stuck after lead arbitration', json.dumps(result['findings']))
        self.assertEqual(len(runner.advice_problems), 1)
        self.assertLess(len(runner.firmware_prompts), parallel.MAX_TEAM_REPAIR_ROUNDS)

    def test_teams_record_owned_deviations_instead_of_stalling_on_sealed_details(self):
        # crab-robot-v6: the lead sealed LM74800 EP to GND and an invented switch cavity;
        # builders must fix such details in their domain rather than wait for a replan.
        parallel.plan(self.root, execute=True, runner=self.runner)
        result = parallel.run(self.root, execute=True, runner=self.runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        prompts = {stage: prompt for stage, _model, _readonly, prompt in self.runner.visits}
        self.assertIn('only what crosses teams', prompts['plan'])
        for role in ('board', 'firmware', 'case'):
            for stage in (role, role + '-test'):
                self.assertIn(parallel.DEVIATIONS[role], prompts[stage])
            self.assertTrue(parallel.owns(role, parallel.DEVIATIONS[role]))
        self.assertIn('choose the closest verified V0 option', prompts['board'])
        self.assertIn('engineering/DEVIATIONS.md', prompts['integration-test'])

    def test_checked_board_checkpoint_is_visible_while_case_is_still_working(self):
        parallel.plan(self.root, execute=True, runner=self.runner)
        self.runner.pause_stage = 'case'
        board_visible = threading.Event()
        result = {}
        def build():
            result['state'] = parallel.run(
                self.root, execute=True, runner=self.runner, checker=checker,
                on_checkpoint=lambda _workspace, _state, stage:
                board_visible.set() if stage == 'board' else None)
        thread = threading.Thread(target=build)
        thread.start()
        self.assertTrue(self.runner.entered.wait(timeout=10))
        self.assertTrue(board_visible.wait(timeout=10))
        self.assertTrue((self.root / 'workspace/boards/published.json').is_file())
        self.assertTrue(thread.is_alive())
        self.runner.release.set()
        thread.join(timeout=15)
        self.assertEqual(result['state']['status'], 'candidate_ready')

    def test_tester_rejection_returns_to_same_builder_worktree(self):
        class RepairRunner(TeamRunner):
            def __init__(self):
                super().__init__()
                self.wait_for_three = False
                self.board_paths = []
                self.board_tests = 0

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = super().__call__(root, state, stage, model, workspace,
                                           prompt, schema, readonly)
                if stage == 'board':
                    self.board_paths.append(workspace)
                if stage == 'board-test':
                    self.board_tests += 1
                    if self.board_tests == 1:
                        receipt['result'] = {
                            'contractSha256': state['contractSha256'], 'decision': 'revise',
                            'summary': 'Board source needs a correction',
                            'findings': [{'path': 'design/main.kicad_pro',
                                          'message': 'Correct board source'}]}
                return receipt

        runner = RepairRunner()
        parallel.plan(self.root, execute=True, runner=runner)
        finished = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(finished['status'], 'candidate_ready', finished['findings'])
        self.assertEqual(runner.board_tests, 2)
        self.assertEqual(len(runner.board_paths), 2)
        self.assertEqual(runner.board_paths[0], runner.board_paths[1])
        self.assertFalse(any(stage.endswith('-advice') for stage, *_ in runner.visits))

    def test_followup_adopts_identical_contract_without_a_new_plan(self):
        original = parallel.plan(self.root, execute=True, runner=self.runner)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            parallel.plan(self.root, execute=True, revise=True, runner=self.runner)
        followup = self.base / 'followup'
        parallel.initialize(followup, self.prompt, instructions=CARD, mode='teams')
        adopted = parallel.adopt_contract(followup, self.root)
        self.assertEqual(adopted['contractSha256'], original['contractSha256'])
        self.assertEqual((followup / 'workspace/PILOT_CONTRACT.json').read_bytes(),
                         (self.root / 'workspace/PILOT_CONTRACT.json').read_bytes())
        self.assertEqual([stage for stage, *_ in self.runner.visits], ['plan'])

    def test_bridge_routes_followup_to_firmware_with_one_original_contract(self):
        visible = self.base / 'visible'
        visible.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(CARD / 'template' / name, visible / name)
        put(visible, '.circuit/team-auto', '')
        put(visible, '.harness/verdict.json', json.dumps({'ready': False}))
        first = parallel_tile.start(visible, self.prompt, execute=True,
                                    runner=self.runner, checker=checker, teams=True)
        self.assertEqual(first['status'], 'candidate_ready', first.get('error'))
        base_contract = first['contractSha256']
        followup_text = 'Change firmware sampling rate to 100 Hz.'
        followup = put(self.base, 'followup.txt', followup_text)
        self.runner.wait_for_three = False
        second = parallel_tile.start(visible, followup, execute=True,
                                     runner=self.runner, checker=checker, teams=True)
        self.assertEqual(second['status'], 'candidate_ready', second.get('error'))
        self.assertEqual(second['contractSha256'], base_contract)
        stages = [stage for stage, *_ in self.runner.visits]
        self.assertEqual(stages.count('plan'), 1)
        self.assertEqual(stages.count('lead-addendum-0001'), 1)
        self.assertEqual(stages.count('board'), 1)
        self.assertEqual(stages.count('case'), 1)
        self.assertEqual(stages.count('firmware'), 2)
        note = parallel_tile.status(visible)['pilot']
        chain = __import__('kicadpy.parallel_addendum', fromlist=['load_addenda'])
        entries = chain.load_addenda(visible / '.circuit/team-runs/r002')['entries']
        self.assertEqual(entries[0]['userText'], followup_text)
        self.assertEqual(note['assignedRoles'], ['firmware'])

    def test_question_goes_to_read_only_lead_before_contract(self):
        visible = self.base / 'question-project'
        visible.mkdir()
        question = put(self.base, 'question.txt', 'What are the options?')
        result = parallel_tile.ask(visible, question, execute=True, runner=self.runner)
        self.assertTrue(result['ok'])
        self.assertIn('lead', result['answer'])
        self.assertIsNone(result['contractSha256'])
        self.assertEqual([(stage, readonly) for stage, _, readonly, _ in self.runner.visits],
                         [('ask', True)])
        self.assertEqual(parallel_tile.status(visible)['status'], 'idle')

    def test_lead_question_can_read_actual_artifacts_and_native_checks(self):
        visible = self.base / 'evidence-project'
        visible.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(CARD / 'template' / name, visible / name)
        put(visible, '.circuit/team-auto', '')
        put(visible, '.harness/verdict.json', json.dumps({'ready': False}))
        built = parallel_tile.start(visible, self.prompt, execute=True,
                                    runner=self.runner, checker=checker, teams=True)
        self.assertEqual(built['status'], 'candidate_ready', built.get('error'))

        class EvidenceRunner(TeamRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == 'ask':
                    self.asserted = True
                    assert readonly
                    assert (workspace / 'PROJECT_ARTIFACTS/firmware/main.c').read_text() == 'firmware output'
                    assert (workspace / 'PROJECT_ARTIFACTS/design/main.kicad_pro').is_file()
                    assert json.loads((workspace / 'NATIVE_CHECKS.json').read_text())[-1]['result']['passed']
                    assert json.loads((workspace / 'NATIVE_VERDICT.json').read_text())['ready']
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = EvidenceRunner()
        result = parallel_tile.ask(visible, put(self.base, 'evidence-question.txt',
                                                 'What files and native checks passed?'),
                                   execute=True, runner=runner)
        self.assertTrue(result['ok'])
        self.assertTrue(runner.asserted)

        put(visible / '.circuit/team-runs/r001/workspace', 'design/main.kicad_pro',
            'uncommitted change')
        class UnstableRunner(TeamRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == 'ask':
                    evidence = json.loads((workspace / 'PROJECT_EVIDENCE.json').read_text())
                    assert evidence['unstableSource'] is True
                    assert not (workspace / 'PROJECT_ARTIFACTS').exists()
                    assert not (workspace / 'NATIVE_CHECKS.json').exists()
                    assert not (workspace / 'NATIVE_VERDICT.json').exists()
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)
        self.assertTrue(parallel_tile.ask(
            visible, put(self.base, 'unstable-question.txt', 'What is verified right now?'),
            execute=True, runner=UnstableRunner())['ok'])

    def test_change_during_run_is_recorded_once_then_resumed(self):
        visible = self.base / 'running-project'
        visible.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(CARD / 'template' / name, visible / name)
        put(visible, '.circuit/team-auto', '')
        put(visible, '.harness/verdict.json', json.dumps({'ready': False}))
        self.runner.pause_stage = 'firmware'
        result = {}
        def build():
            result['first'] = parallel_tile.start(visible, self.prompt, execute=True,
                                                  runner=self.runner, checker=checker,
                                                  teams=True)
        thread = threading.Thread(target=build)
        thread.start()
        self.assertTrue(self.runner.entered.wait(timeout=10))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (visible / 'boards/published.json').is_file():
            time.sleep(.02)
        self.assertTrue((visible / 'boards/published.json').is_file())
        self.assertTrue(thread.is_alive())
        image = put(visible, 'prompts/assets/live.png', 'new attached image')
        change = put(self.base, 'live-change.txt', 'Change firmware sampling rate to 100 Hz.')
        noted = parallel_tile.note(visible, change, execute=True, runner=self.runner)
        self.assertTrue(noted['queuedForResume'])
        self.assertEqual(noted['routes'], ['firmware'])
        image.unlink()
        self.runner.ask_expected_file = (
            'PROJECT_ARTIFACTS/sealed-note-assets/n0001/prompts/assets/live.png',
            b'new attached image')
        answer = parallel_tile.ask(visible, put(self.base, 'live-question.txt',
                                                'What image did I send?'), execute=True,
                                   runner=self.runner)
        self.assertTrue(answer['ok'])
        self.runner.release.set()
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result['first']['status'], 'needs_revision')
        self.runner.pause_stage = None
        self.runner.wait_for_three = False
        resumed = parallel_tile.resume(visible, execute=True, runner=self.runner, checker=checker)
        self.assertEqual(resumed['status'], 'candidate_ready', resumed.get('error'))
        stages = [stage for stage, *_ in self.runner.visits]
        self.assertEqual(stages.count('plan'), 1)
        self.assertEqual(stages.count('lead-addendum-0001'), 1)
        self.assertEqual(stages.count('firmware'), 2)

    def test_resume_keeps_unfinished_owned_board_source_as_untrusted_draft(self):
        class DraftRunner(TeamRunner):
            def __init__(self):
                super().__init__()
                self.wait_for_three = False
                self.block_board = True
                self.saw_draft = False

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == 'board' and self.block_board:
                    put(workspace, 'design/main.kicad_pro', 'partial board source')
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .01, 'usage': None,
                            'result': {'contractSha256': state['contractSha256'],
                                       'stage': stage, 'summary': 'Still working',
                                       'blockers': ['Need another bounded run']},
                            'error': None}
                if stage == 'board':
                    self.saw_draft = ((workspace / 'design/main.kicad_pro').read_text() ==
                                      'partial board source')
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = DraftRunner()
        visible = self.base / 'draft-project'
        visible.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(CARD / 'template' / name, visible / name)
        put(visible, '.circuit/team-auto', '')
        put(visible, '.harness/verdict.json', json.dumps({'ready': False}))
        first = parallel_tile.start(visible, self.prompt, execute=True,
                                    runner=runner, checker=checker, teams=True)
        self.assertEqual(first['status'], 'needs_revision')
        runner.block_board = False
        second = parallel_tile.resume(visible, execute=True, runner=runner, checker=checker)
        self.assertEqual(second['status'], 'candidate_ready', second.get('error'))
        self.assertTrue(runner.saw_draft)


if __name__ == '__main__':
    unittest.main()
