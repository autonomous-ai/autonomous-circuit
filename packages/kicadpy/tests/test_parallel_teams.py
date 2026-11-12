"""The new team pipeline runs independently without recreating its contract."""
import functools
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
from kicadpy.parallel_contract import RECHECK_SCHEMA
from test_parallel import CARD, contract, put

# Unit runs must not depend on the developer machine's free disk.
os.environ.setdefault('CIRCUIT_TEAM_MIN_FREE_GB', '0')


def _answers_retest(call):
    """Scripted testers write first-review results; a re-test numbers each finding it keeps open."""
    @functools.wraps(call)
    def wrapped(self, root, state, stage, model, workspace, prompt, schema, readonly):
        receipt = call(self, root, state, stage, model, workspace, prompt, schema, readonly)
        result = receipt.get('result')
        if schema is RECHECK_SCHEMA and result and 'newIssues' not in result:
            result['findings'] = [{'recheckOf': number, **finding}
                                  for number, finding in enumerate(result['findings'], 1)]
            result['newIssues'] = []
        return receipt
    return wrapped


class TeamRunner:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if '__call__' in cls.__dict__:
            cls.__call__ = _answers_retest(cls.__dict__['__call__'])

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
            elif stage.startswith('lead-addendum-'):
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
        self.advice_models = []

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
            self.advice_models.append(model)
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


TeamRunner.__call__ = _answers_retest(TeamRunner.__call__)


class TeamFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.prompt = put(self.base, 'prompt.txt', 'Build a sensor with firmware and case.')
        self.root = self.base / 'first'
        self.runner = TeamRunner()
        parallel.initialize(self.root, self.prompt, instructions=CARD, mode='teams')

    def test_note_routed_to_one_team_on_an_empty_project_runs_every_team(self):
        planned = parallel.plan(self.root, execute=True, runner=self.runner)
        self.assertEqual(planned['status'], 'planned')
        planned['assignedRoles'] = ['case']
        parallel.write(self.root / '.pilot/state.json', planned)
        result = parallel.run(self.root, execute=True, runner=self.runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(result['assignedRoles'], list(parallel.ROLES))
        stages = [stage for stage, *_ in self.runner.visits]
        self.assertEqual({stage for stage in stages if stage in parallel.ROLES}, set(parallel.ROLES))

    def integration_findings(self, findings):
        class Runner(TeamRunner):
            def __call__(inner, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = TeamRunner.__call__(inner, root, state, stage, model, workspace,
                                              prompt, schema, readonly)
                if stage == 'integration-test':
                    receipt['result'] = {'contractSha256': state['contractSha256'], 'decision': 'revise',
                                         'summary': 'Reviewed', 'findings': findings}
                return receipt

        runner = Runner()
        parallel.plan(self.root, execute=True, runner=runner)
        root, state = parallel.load(self.root)
        return root, parallel.team_integration_test(root, state, {'passed': True}, runner)

    def test_integration_finding_on_a_lead_document_does_not_end_the_run(self):
        document = {'path': 'README.md', 'message': 'README is stale'}
        root, result = self.integration_findings([document])
        self.assertEqual((result['decision'], result['findings']), ('approve', []))
        kept = json.loads((root / '.pilot/lead-document-findings.json').read_text())
        self.assertEqual(kept, [document])

    def test_integration_finding_on_a_team_file_survives_beside_a_lead_document(self):
        real = {'path': 'firmware/main.c', 'message': 'Wrong pin'}
        _, result = self.integration_findings([{'path': 'BOM.md', 'message': 'Stale'}, real])
        self.assertEqual((result['decision'], result['findings']), ('revise', [real]))

    def test_other_top_level_files_still_need_an_owner(self):
        with self.assertRaisesRegex(ValueError, 'team-owned path'):
            self.integration_findings([{'path': 'notes.txt', 'message': 'Stale'}])

    def test_first_review_states_blockers_and_a_retest_checks_only_its_list(self):
        bug = {'path': 'firmware/main.c', 'message': 'Wrong pin'}

        class Runner(TeamRunner):
            def __init__(inner):
                super().__init__()
                inner.wait_for_three = False
                inner.rounds = 0

            def __call__(inner, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)
                if stage == 'firmware-test':
                    inner.rounds += 1
                    if inner.rounds == 1:
                        receipt['result'] = {'contractSha256': state['contractSha256'], 'decision': 'revise',
                                             'summary': 'One blocker', 'findings': [bug]}
                    elif inner.rounds == 2:
                        receipt['result'] = {'contractSha256': state['contractSha256'], 'decision': 'approve',
                                             'summary': 'New idea', 'findings': [], 'newIssues': [
                                                 {'path': 'firmware/README.md', 'message': 'A new complaint'}]}
                return receipt

        runner = Runner()
        parallel.plan(self.root, execute=True, runner=runner)
        result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(runner.rounds, 2)  # the off-list complaint was dropped, not another round
        prompts = [prompt for stage, _, _, prompt in runner.visits if stage == 'firmware-test']
        self.assertIn('one full review', prompts[0])
        self.assertIn('moving parts collide or jam', prompts[0])
        self.assertIn('reversed battery polarity', prompts[0])
        notes = json.loads((self.root / '.pilot/tester-notes/firmware.json').read_text())
        self.assertEqual(notes['summary'], 'One blocker')  # a re-test does not replace the review
        self.assertNotIn('RE-TEST', prompts[0])
        self.assertIn('RE-TEST', prompts[1])
        self.assertIn('Wrong pin', prompts[1])
        self.assertNotIn('one full review', prompts[1])

    def test_integration_repair_rechecks_instead_of_reviewing_again(self):
        bug = {'path': 'firmware/main.c', 'message': 'Pin clash'}

        class Runner(TeamRunner):
            def __init__(inner):
                super().__init__()
                inner.rejected = False

            def __call__(inner, root, state, stage, model, workspace, prompt, schema, readonly):
                if inner.rejected:
                    inner.wait_for_three = False
                receipt = super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)
                if stage == 'integration-test' and not inner.rejected:
                    inner.rejected = True
                    receipt['result'] = {'contractSha256': state['contractSha256'], 'decision': 'revise',
                                         'summary': 'Clash', 'findings': [bug]}
                return receipt

        runner = Runner()
        parallel.plan(self.root, execute=True, runner=runner)
        result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        firmware = [prompt for stage, _, _, prompt in runner.visits if stage == 'firmware-test']
        integration = [prompt for stage, _, _, prompt in runner.visits if stage == 'integration-test']
        self.assertEqual(len(firmware), 2)
        self.assertIn('RE-TEST', firmware[1])
        self.assertIn('Pin clash', firmware[1])
        self.assertIn('one full review', integration[0])
        self.assertIn('RE-TEST', integration[1])

    def test_a_finding_still_open_at_a_moved_line_stays_open(self):
        class Runner(TeamRunner):
            def __init__(inner):
                super().__init__()
                inner.reviews = 0

            def __call__(inner, root, state, stage, model, workspace, prompt, schema, readonly):
                if inner.reviews:
                    inner.wait_for_three = False
                receipt = super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)
                if stage == 'firmware':
                    put(workspace, 'firmware/main.c', f'repair {inner.reviews}')
                if stage == 'firmware-test':
                    inner.reviews += 1
                    if inner.reviews < 3:
                        receipt['result'] = {'contractSha256': state['contractSha256'], 'decision': 'revise',
                                             'summary': 'Open', 'findings': [
                                                 {'path': f'firmware/main.c:{10 + inner.reviews}',
                                                  'message': 'Pin clash'}]}
                return receipt

        runner = Runner()
        parallel.plan(self.root, execute=True, runner=runner)
        result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        firmware = [prompt for stage, _, _, prompt in runner.visits if stage == 'firmware-test']
        self.assertEqual(len(firmware), 3)
        self.assertIn('(line 11) Pin clash', firmware[1])

    def retest(self, reply, old):
        class Runner(TeamRunner):
            def __call__(inner, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = TeamRunner.__call__(inner, root, state, stage, model, workspace,
                                              prompt, schema, readonly)
                if stage == 'integration-test':
                    receipt['result'] = {'contractSha256': state['contractSha256'], **reply}
                return receipt

        runner = Runner()
        parallel.plan(self.root, execute=True, runner=runner)
        root, state = parallel.load(self.root)
        return root, parallel.team_integration_test(root, state, {'passed': True}, runner, recheck=old)

    def test_a_finding_still_open_in_another_file_stays_open(self):
        old = [{'path': 'case/model.py', 'message': 'PCB cannot be inserted'}]
        moved = {'path': 'design/main.kicad_pcb', 'message': 'PCB outline blocks insertion'}
        _, result = self.retest({'decision': 'revise', 'summary': 'Open', 'newIssues': [],
                                 'findings': [{'recheckOf': 1, **moved}]}, old)
        self.assertEqual((result['decision'], result['findings']), ('revise', [moved]))

    def test_a_new_issue_on_a_retest_is_recorded_and_does_not_block(self):
        old = [{'path': 'case/model.py', 'message': 'Leg hits body'}]
        new = {'path': 'case/model.py', 'message': 'Lid rib now touches knee'}
        root, result = self.retest({'decision': 'approve', 'summary': 'Fixed', 'findings': [],
                                    'newIssues': [new]}, old)
        self.assertEqual((result['decision'], result['findings']), ('approve', []))
        saved = json.loads((root / '.pilot/tester-notes/integration-retest.json').read_text())
        self.assertEqual(saved['newIssues'], [new])

    def test_a_finding_with_an_unknown_number_becomes_a_note(self):
        old = [{'path': 'case/model.py', 'message': 'Leg hits body'}]
        stray = {'path': 'firmware/main.c', 'message': 'Unrelated'}
        root, result = self.retest({'decision': 'revise', 'summary': 'Open', 'newIssues': [],
                                    'findings': [{'recheckOf': 2, **stray}]}, old)
        self.assertEqual((result['decision'], result['findings']), ('approve', []))
        saved = json.loads((root / '.pilot/tester-notes/integration-retest.json').read_text())
        self.assertEqual(saved['newIssues'], [stray])

    def test_retest_notes_accumulate_without_duplicates(self):
        first, second = ({'path': 'a', 'message': 'one'}, {'path': 'b', 'message': 'two'})
        parallel._save_retest_notes(self.base, 'case', {'newIssues': [first]})
        parallel._save_retest_notes(self.base, 'case', {'newIssues': [first, second]})
        saved = json.loads((self.base / '.pilot/tester-notes/case-retest.json').read_text())
        self.assertEqual(saved['newIssues'], [first, second])

    def test_cited_files_strips_only_a_line_suffix(self):
        result = parallel._cited_files({'findings': [{'path': 'a/b.py:12', 'message': 'x'},
                                                     {'path': 'a/b.py:12-20', 'message': 'y'},
                                                     {'path': 'a/b.py', 'message': 'z'}]})
        self.assertEqual([(item['path'], item['message']) for item in result['findings']],
                         [('a/b.py', '(line 12) x'), ('a/b.py', '(line 12-20) y'), ('a/b.py', 'z')])

    def test_a_retest_with_nothing_to_check_makes_no_model_call(self):
        calls = []
        parallel.plan(self.root, execute=True, runner=self.runner)
        root, state = parallel.load(self.root)
        worktree = root / 'workspace'
        result = parallel.team_test(root, state, 'firmware', 'firmware', worktree, {},
                                    lambda *args: calls.append(args), recheck=[])
        self.assertEqual((result['decision'], calls), ('approve', []))

    def test_case_builder_is_told_to_avoid_thin_hanging_parts(self):
        parallel.plan(self.root, execute=True, runner=self.runner)
        root, state = parallel.load(self.root)
        self.assertIn('hanging free', parallel.worker_prompt(state, 'case', 'case'))
        self.assertNotIn('hanging free', parallel.worker_prompt(state, 'firmware', 'firmware'))
        self.assertIn('every CPU core', parallel.worker_prompt(state, 'firmware', 'firmware'))

    def test_case_is_an_open_skeleton_and_skin_waits_for_a_request(self):
        parallel.plan(self.root, execute=True, runner=self.runner)
        root, state = parallel.load(self.root)
        for stage in ('case', 'case-final'):
            prompt = parallel.worker_prompt(state, 'case', stage)
            self.assertIn('open skeleton', prompt)
            self.assertNotIn('box sections', prompt)
            self.assertIn('only if the brief', prompt)
        self.assertIn('closed box', parallel.TESTER_RULES)
        self.assertNotIn('box sections', parallel.TESTER_RULES)

    def test_testers_use_their_own_model(self):
        root = self.base / 'split'
        parallel.initialize(root, self.prompt, instructions=CARD, mode='teams',
                            lead_model='claude-lead', tester_model='claude-tester')
        parallel.plan(root, execute=True, runner=self.runner)
        result = parallel.run(root, execute=True, runner=self.runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        models = {stage: model for stage, model, *_ in self.runner.visits}
        self.assertEqual(models['plan'], 'claude-lead')
        for stage in ('board-test', 'firmware-test', 'case-test', 'integration-test'):
            self.assertEqual(models[stage], 'claude-tester')
        for stage in ('board', 'firmware', 'case'):
            self.assertEqual(models[stage], parallel.WORKER)
        self.assertEqual(parallel.metrics(result)['testerModel'], 'claude-tester')

    def test_one_role_can_swap_builder_and_tester_models(self):
        root = self.base / 'swapped'
        parallel.initialize(root, self.prompt, instructions=CARD, mode='teams',
                            lead_model='claude-lead', tester_model='claude-tester',
                            builder_models={'case': 'claude-builder'}, tester_models={'case': 'sol-tester'},
                            integration_tester_model='claude-final')
        parallel.plan(root, execute=True, runner=self.runner)
        result = parallel.run(root, execute=True, runner=self.runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        models = {stage: model for stage, model, *_ in self.runner.visits}
        self.assertEqual((models['case'], models['case-test']), ('claude-builder', 'sol-tester'))
        self.assertEqual((models['board'], models['firmware']), (parallel.WORKER, parallel.WORKER))
        self.assertEqual((models['board-test'], models['firmware-test']), ('claude-tester', 'claude-tester'))
        self.assertEqual(models['integration-test'], 'claude-final')
        self.assertEqual(parallel.metrics(result)['builderModels'], {'case': 'claude-builder'})

    def test_an_older_run_without_a_tester_model_keeps_its_worker_model(self):
        self.assertEqual(parallel._tester_model({'workerModel': 'worker'}), 'worker')

    def test_template_files_do_not_count_as_a_delivered_board(self):
        workspace = self.base / 'fresh'
        put(workspace, 'product.json', '{}')
        put(workspace, 'project.json', '{}')
        self.assertEqual(parallel._undelivered_roles(workspace), set(parallel.ROLES))
        put(workspace, 'design/main.kicad_pcb', 'board')
        put(workspace, 'case/out/parts.json', '{}')
        self.assertEqual(parallel._undelivered_roles(workspace), {'firmware'})

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
        # A role that finishes after the checked board is mirrored too; which one does is timing.
        self.assertEqual((checkpoints[0], checkpoints[-1]), ('board', 'final'))
        self.assertLessEqual(set(checkpoints[1:-1]), {'firmware', 'case'})
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

    def test_an_advisor_model_answers_in_place_of_the_lead(self):
        root = self.base / 'advised'
        parallel.initialize(root, self.prompt, instructions=CARD, mode='teams',
                            lead_model='claude-lead', advisor_model='claude-advisor')
        runner = DisputeRunner('repair')
        parallel.plan(root, execute=True, runner=runner)
        result = parallel.run(root, execute=True, runner=runner, checker=checker)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        models = {stage: model for stage, model, *_ in runner.visits}
        self.assertEqual((models['plan'], runner.advice_models), ('claude-lead', ['claude-advisor']))
        self.assertEqual(parallel.metrics(result)['advisorModel'], 'claude-advisor')

    def test_without_an_advisor_model_the_lead_advises(self):
        runner = DisputeRunner('repair')
        parallel.plan(self.root, execute=True, runner=runner)
        result = parallel.run(self.root, execute=True, runner=runner, checker=checker)
        models = {stage: model for stage, model, *_ in runner.visits}
        self.assertEqual(runner.advice_models, [models['plan']])
        self.assertEqual(parallel.metrics(result)['advisorModel'], models['plan'])

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

    def integration_repair(self, repairs):
        class RejectOnceRunner(TeamRunner):
            def __init__(self):
                super().__init__()
                self.wait_for_three = False
                self.rejected = False

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if self.rejected and stage == 'board':
                    time.sleep(.3)  # siblings import first
                receipt = super().__call__(root, state, stage, model, workspace,
                                           prompt, schema, readonly)
                if self.rejected and stage in repairs:
                    put(workspace, repairs[stage], f'{stage} repaired')
                if stage == 'integration-test' and not self.rejected:
                    self.rejected = True
                    checkpoints.append('rejected')
                    receipt['result'] = {
                        'contractSha256': state['contractSha256'], 'decision': 'revise',
                        'summary': 'Repair the interface',
                        'findings': [{'path': path, 'message': 'Repair'} for path in repairs.values()]}
                return receipt

        checkpoints = []
        runner = RejectOnceRunner()
        parallel.plan(self.root, execute=True, runner=runner)
        finished = parallel.run(self.root, execute=True, runner=runner, checker=checker,
                                on_checkpoint=lambda _workspace, _state, stage: checkpoints.append(stage))
        self.assertEqual(finished['status'], 'candidate_ready', finished['findings'])
        return checkpoints[checkpoints.index('rejected') + 1:]

    def test_integration_repair_checkpoints_each_imported_role(self):
        repaired = self.integration_repair({'firmware': 'firmware/main.c', 'case': 'case/frame.txt'})
        self.assertEqual(set(repaired[:2]), {'firmware', 'case'})

    def test_integration_repair_never_checkpoints_an_unchecked_board(self):
        repaired = self.integration_repair({'board': 'design/main.kicad_pro', 'firmware': 'firmware/main.c'})
        self.assertEqual(repaired[0], 'board')

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

    def test_followups_prune_old_copies_and_keep_working(self):
        visible = self.base / 'visible'
        visible.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(CARD / 'template' / name, visible / name)
        put(visible, '.circuit/team-auto', '')
        put(visible, '.harness/verdict.json', json.dumps({'ready': False}))
        first = parallel_tile.start(visible, self.prompt, execute=True,
                                    runner=self.runner, checker=checker, teams=True)
        self.assertEqual(first['status'], 'candidate_ready', first.get('error'))
        self.runner.wait_for_three = False
        for number in range(2, 6):
            followup = put(self.base, f'followup{number}.txt', f'Change sampling rate to {number}0 Hz.')
            state = parallel_tile.start(visible, followup, execute=True,
                                        runner=self.runner, checker=checker, teams=True)
            self.assertEqual(state['status'], 'candidate_ready', state.get('error'))
        runs = visible / '.circuit/team-runs'
        # r001 holds the contract, r004 the drafts r005 started from.
        self.assertEqual(sorted(p.parent.name for p in runs.glob('*/workspace')),
                         ['r001', 'r004', 'r005'])
        self.assertFalse((runs / 'r002/workers').exists())
        self.assertTrue((runs / 'r002/.pilot/state.json').is_file())

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



def _receipt(stage, model, result, error=None):
    return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
            'usage': None, 'result': result, 'error': error}


class CaseCrewRunner(TeamRunner):
    """The case builder exports parts; an optional case-docs member writes plates and the README."""

    def __init__(self, docs_blockers=(), docs_touches_part=False, case_test_revise=0):
        super().__init__()
        self.wait_for_three = False
        self.docs_blockers = list(docs_blockers)
        self.docs_touches_part = docs_touches_part
        self.case_test_revise = case_test_revise
        self.prompts = {}
        self.order = []

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        with self.lock:
            self.prompts.setdefault(stage, []).append(prompt)
            self.order.append((stage, model))
        if stage == 'case':
            put(workspace, 'case/out/frame.stl', 'solid frame ' + str(len(self.prompts['case'])))
            put(workspace, 'case/frame.txt', 'case output')
            if not state.get('caseDocsModel'):
                put(workspace, 'case/README.md', 'Assembly guide')
            return _receipt(stage, model, {'contractSha256': state['contractSha256'], 'stage': stage,
                                           'summary': 'CAD done', 'blockers': []})
        if stage == 'case-test' and self.case_test_revise:
            self.case_test_revise -= 1
            return _receipt(stage, model, {'contractSha256': state['contractSha256'], 'decision': 'revise',
                                           'summary': 'Leg hits body',
                                           'findings': [{'path': 'case/frame.txt', 'message': 'leg hits'}]})
        if stage == 'case-docs':
            put(workspace, 'case/README.md', 'Assembly guide by docs')
            put(workspace, 'case/print/production.3mf', 'plate')
            if self.docs_touches_part:
                put(workspace, 'case/out/frame.stl', 'docs moved a part')
            blockers = [self.docs_blockers.pop(0)] if self.docs_blockers else []
            return _receipt(stage, model, {'contractSha256': state['contractSha256'], 'stage': stage,
                                           'summary': 'Plates and README', 'blockers': blockers})
        return super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)


class CaseCrewTests(unittest.TestCase):
    """v17e: the case builder did CAD, sweeps, plates, slicing and docs alone for 2.5 h."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.prompt = put(self.base, 'prompt.txt', 'Build a robot with firmware and case.')
        self.check_calls = []

    def fake_checks(self, verdict='fail'):
        def run(worktree, env, case_python, root=None):
            self.check_calls.append((Path(worktree), case_python, env.get('KICAD_CASE_PYTHON')))
            return {'schema': 1, 'verdict': verdict, 'seconds': 1.0, 'checkedAt': 'now',
                    'checks': [{'name': 'mech', 'level': verdict, 'message': 'leg-body 0.1 mm at hip 40',
                                'seconds': 1.0},
                               {'name': 'native', 'level': 'pass', 'message': 'fits', 'seconds': 1.0}]}
        return patch.object(parallel, 'run_case_checks', side_effect=run)

    def start(self, runner, **models):
        root = self.base / ('run-' + str(len(list(self.base.iterdir()))))
        parallel.initialize(root, self.prompt, instructions=CARD, mode='teams', **models)
        parallel.plan(root, execute=True, runner=runner)
        return root, parallel.run(root, execute=True, runner=runner, checker=checker)

    def test_harness_runs_case_checks_and_both_tester_and_repair_see_them(self):
        runner = CaseCrewRunner(case_test_revise=1)
        with self.fake_checks():
            root, result = self.start(runner)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        tester = runner.prompts['case-test'][0]
        self.assertIn('caseAutoCheck', tester)
        self.assertIn('leg-body 0.1 mm at hip 40', tester)
        self.assertIn('never a pass', tester)
        repair = runner.prompts['case'][1]
        self.assertIn('AUTOMATIC CASE CHECKS', repair)
        self.assertIn('leg-body 0.1 mm at hip 40', repair)
        self.assertIn('the harness reruns the case checks', repair)
        # one run per builder turn, each kept as a report for the lead and the person
        self.assertEqual(len(self.check_calls), 2)
        worktree, case_python, env_python = self.check_calls[0]
        self.assertEqual(case_python, env_python)
        self.assertTrue(case_python.endswith('harness/kicad/toolchain/case-python'))
        reports = sorted((root / '.pilot/case-checks').glob('*.json'))
        self.assertEqual([path.name for path in reports], ['r001-case-01.json', 'r001-case-02.json'])
        self.assertEqual(json.loads(reports[0].read_text())['verdict'], 'fail')
        # board and firmware keep their own checks
        self.assertNotIn('caseAutoCheck', runner.prompts['firmware-test'][0])

    def test_builder_is_told_not_to_run_long_sweeps_itself(self):
        root = self.base / 'prompt'
        parallel.initialize(root, self.prompt, instructions=CARD, mode='teams')
        parallel.plan(root, execute=True, runner=TeamRunner())
        _, state = parallel.load(root)
        case_prompt = parallel.worker_prompt(state, 'case', 'case')
        self.assertIn('case/checks.json', case_prompt)
        self.assertIn('Do not run long sweeps', case_prompt)
        self.assertNotIn('full check suite once before you finish', case_prompt)
        self.assertIn('full check suite once before you finish', parallel.worker_prompt(state, 'board', 'board'))
        # one case builder by default: it still owns plates, overview and README
        self.assertNotIn('case-docs', case_prompt)

    def test_unchanged_case_inputs_reuse_the_last_check(self):
        class Runner(CaseCrewRunner):
            def __call__(inner, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == 'case' and len(inner.prompts.get('case', [])) >= 1:
                    inner.prompts['case'].append(prompt)  # the repair changes nothing
                    return _receipt(stage, model, {'contractSha256': state['contractSha256'],
                                                   'stage': stage, 'summary': 'Nothing to change',
                                                   'blockers': []})
                return super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)
        runner = Runner(case_test_revise=1)
        with self.fake_checks():
            _, result = self.start(runner)
        self.assertEqual(len(self.check_calls), 1)
        self.assertIn('"cached": true', runner.prompts['case-test'][1])

    def test_without_case_docs_no_docs_member_runs(self):
        runner = CaseCrewRunner()
        with self.fake_checks('pass'):
            _, result = self.start(runner)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertNotIn('case-docs', runner.prompts)
        self.assertIsNone(result['caseDocsModel'])

    def test_case_docs_member_runs_after_the_cad_passes(self):
        runner = CaseCrewRunner(case_test_revise=1)
        with self.fake_checks('pass'):
            root, result = self.start(runner, builder_models={'case': 'claude-opus-5-5'},
                                      case_docs_model='claude-sonnet-5-5')
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        stages = [stage for stage, _ in runner.order if stage.startswith('case')]
        self.assertEqual(stages, ['case', 'case-test', 'case', 'case-test', 'case-docs'])
        models = dict(runner.order)
        self.assertEqual((models['case'], models['case-docs']), ('claude-opus-5-5', 'claude-sonnet-5-5'))
        self.assertIn('case-docs member', runner.prompts['case'][0])
        self.assertIn('do not cite their absence', runner.prompts['case-test'][0])
        docs = runner.prompts['case-docs'][0]
        self.assertIn('CASE-DOCS', docs)
        self.assertIn('print/events.jsonl', docs)
        self.assertIn('AUTOMATIC CASE CHECKS', docs)
        workspace = root / 'workspace'
        self.assertEqual((workspace / 'case/README.md').read_text(), 'Assembly guide by docs')
        self.assertTrue((workspace / 'case/print/production.3mf').is_file())
        self.assertEqual(parallel.metrics(result)['caseDocsModel'], 'claude-sonnet-5-5')
        # docs left the CAD alone, so its outputs did not re-run the checks
        self.assertEqual(len(self.check_calls), 2)

    def test_case_docs_blocker_goes_back_to_the_cad_builder_then_docs_again(self):
        runner = CaseCrewRunner(docs_blockers=['Frame does not fit plate 1 at 220 mm'])
        with self.fake_checks('pass'):
            _, result = self.start(runner, case_docs_model='docs-model')
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        stages = [stage for stage, _ in runner.order if stage.startswith('case')]
        # the re-test has no tester findings to re-check, so it makes no model call
        self.assertEqual(stages, ['case', 'case-test', 'case-docs', 'case', 'case-docs'])
        self.assertIn('Frame does not fit plate 1', runner.prompts['case'][1])
        self.assertIn('This is run 2', runner.prompts['case-docs'][1])

    def test_case_docs_may_not_change_the_checked_parts(self):
        runner = CaseCrewRunner(docs_touches_part=True)
        with self.fake_checks('pass'):
            _, result = self.start(runner, case_docs_model='docs-model')
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('Case docs changed the checked parts', ' '.join(result['findings']))

    def test_case_docs_true_uses_the_case_builder_model(self):
        root = self.base / 'models'
        put(root, str(parallel.TEAM_MODELS), json.dumps({'caseDocs': True, 'builders': {'case': 'opus'}}))
        state = {'caseDocsModel': parallel.case_docs_model(parallel.team_models(root)),
                 'builderModels': {'case': 'opus'}, 'workerModel': parallel.WORKER}
        self.assertEqual(parallel._builder_model(state, 'case-docs'), 'opus')
        self.assertEqual(parallel._builder_model({**state, 'caseDocsModel': None}, 'case-docs'), 'opus')
        self.assertEqual(parallel._builder_model(state, 'firmware'), parallel.WORKER)

    def test_agent_written_checks_run_in_the_builder_sandbox(self):
        worktree, root = self.base / 'root/workers/r001-case', self.base / 'root'
        put(worktree, 'tools/case/mech.py', 'pass')
        put(worktree, 'case/checks.json', json.dumps({'checks': [
            {'name': 'mech', 'script': 'tools/case/mech.py', 'report': 'case/reports/mech.json'}]}))
        seen = []
        with patch('kicadpy.case_checks.run_process',
                   side_effect=lambda argv, *a: seen.append(argv) or (0, '')):
            from kicadpy import case_checks
            with patch.object(case_checks, 'run', wraps=case_checks.run) as wrapped:
                parallel.run_case_checks(worktree, {}, 'case-python', root)
        prefix = wrapped.call_args.kwargs['prefix']
        if Path('/usr/bin/sandbox-exec').is_file():
            self.assertEqual(prefix[:2], ['/usr/bin/sandbox-exec', '-p'])
            self.assertIn(f'(deny file-write* (subpath "{root.resolve() / ".pilot"}"))', prefix[2])
            self.assertIn(f'(deny file-write* (subpath "{root.resolve() / "workspace"}"))', prefix[2])

    def test_cad_digest_ignores_the_print_binding_but_not_a_part(self):
        worktree = self.base / 'digest'
        put(worktree, 'case/out/parts.json', json.dumps({'parts': [{'name': 'a'}]}))
        put(worktree, 'case/out/a.stl', 'solid a')
        before = parallel._case_cad_digest(worktree)
        inputs = parallel._case_check_inputs(worktree)
        put(worktree, 'case/out/parts.json', json.dumps({'parts': [{'name': 'a'}], 'printProject': {'p': 1},
                                                         'overview': '../overview.html'}))
        put(worktree, 'case/README.md', 'docs')
        put(worktree, 'case/reports/mech.json', '{}')
        self.assertEqual(parallel._case_cad_digest(worktree), before)
        self.assertEqual(parallel._case_check_inputs(worktree), inputs)
        put(worktree, 'tools/case/mech_check.py', 'new sweep')
        self.assertNotEqual(parallel._case_check_inputs(worktree), inputs)
        put(worktree, 'case/out/a.stl', 'solid a moved')
        self.assertNotEqual(parallel._case_cad_digest(worktree), before)


if __name__ == '__main__':
    unittest.main()
