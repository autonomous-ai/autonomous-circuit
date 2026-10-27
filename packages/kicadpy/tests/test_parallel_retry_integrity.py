"""Capacity retries must keep sibling work and the integration boundary intact."""
import os
from collections import Counter
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from kicadpy import parallel, parallel_tile

# Unit runs must not depend on the developer machine's free disk.
os.environ.setdefault('CIRCUIT_TEAM_MIN_FREE_GB', '0')

try:
    from . import test_parallel as fixture
    from . import test_parallel_tile as bridge_fixture
except ImportError:  # unittest discover imports the test directory as top-level modules
    import test_parallel as fixture
    import test_parallel_tile as bridge_fixture


class CapacityRunner:
    def __init__(self, *, partial_owned=False, contaminate_unowned=False,
                 mutate_contract=False, mutate_git=False,
                 bad_second_result=False, always_capacity=False, opaque_error=False,
                 stray_root=None):
        self.normal = fixture.Fake()
        self.stray_root = stray_root
        self.calls = Counter()
        self.partial_owned = partial_owned
        self.contaminate_unowned = contaminate_unowned
        self.mutate_contract = mutate_contract
        self.mutate_git = mutate_git
        self.bad_second_result = bad_second_result
        self.always_capacity = always_capacity
        self.opaque_error = opaque_error
        self.saw_partial_on_retry = False

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        self.calls[stage] += 1
        if stage == 'firmware' and (self.calls[stage] == 1 or self.always_capacity):
            if self.partial_owned:
                fixture.put(workspace, 'firmware/partial.c', 'unfinished first attempt')
            if self.contaminate_unowned:
                fixture.put(workspace, 'design/foreign.kicad_pro', 'outside firmware ownership')
            if self.stray_root:
                fixture.put(workspace, 'result.json', self.stray_root)
            if self.mutate_contract:
                fixture.put(workspace, 'PILOT_CONTRACT.json', '{}')
            if self.mutate_git:
                config = Path(root) / 'workspace/.git/config'
                config.write_text(config.read_text() + '\n[pilot]\n changed = true\n')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .1,
                    'usage': None, 'result': None,
                    'error': 'Codex exited 1' if self.opaque_error else 'Selected model is at capacity',
                    'failureKind': 'model_capacity'}
        if stage == 'firmware' and self.partial_owned:
            self.saw_partial_on_retry = (Path(workspace) / 'firmware/partial.c').read_text() == 'unfinished first attempt'
            fixture.put(workspace, 'firmware/partial.c', 'completed after retry')
        result = self.normal(root, state, stage, model, workspace, prompt, schema, readonly)
        if stage == 'firmware' and self.bad_second_result:
            result['result']['contractSha256'] = 'stale-contract'
        return result


class RetryIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.prompt = fixture.put(base, 'brief.txt', 'Make a reference USB sensor and case.')
        self.root = base / 'pilot'
        parallel.initialize(self.root, self.prompt, instructions=fixture.CARD)

    def run_with(self, runner):
        planned = parallel.plan(self.root, execute=True, runner=runner)
        self.assertEqual(planned['status'], 'planned', planned['findings'])
        return parallel.run(self.root, execute=True, runner=runner, checker=fixture.checker)

    def test_capacity_retry_calls_only_failed_worker_again(self):
        runner = CapacityRunner()
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(runner.calls['board'], 1)
        self.assertEqual(runner.calls['case'], 1)
        self.assertEqual(runner.calls['firmware'], 2)
        self.assertEqual(runner.calls['firmware-final'], 1)
        receipts = [job for job in result['jobs'] if job['stage'] == 'firmware']
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0]['failureKind'], 'model_capacity')
        self.assertIsNone(receipts[1]['error'])
        self.assertTrue((self.root / 'workspace/firmware/main.c').is_file())
        self.assertEqual(parallel.git(self.root / 'workspace', 'status', '--porcelain'), '')

    def test_owned_partial_worker_output_can_continue_without_redoing_siblings(self):
        runner = CapacityRunner(partial_owned=True)
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(runner.calls['firmware'], 2)
        self.assertTrue(runner.saw_partial_on_retry)
        self.assertEqual((self.root / 'workspace/firmware/partial.c').read_text(),
                         'completed after retry')
        self.assertEqual(runner.calls['board'], 1)
        self.assertEqual(runner.calls['case'], 1)

    def test_retry_accepts_controller_import_of_completed_sibling(self):
        class DelayedCapacity(fixture.Fake):
            def __init__(self, root):
                super().__init__()
                self.root = root
                self.calls = Counter()
                self.case_imported = threading.Event()

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                self.calls[stage] += 1
                if stage == 'firmware' and self.calls[stage] == 1:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if (self.root / 'workspace/case/frame.txt').is_file():
                            self.case_imported.set()
                            break
                        time.sleep(.02)
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .1, 'usage': None, 'result': None,
                            'error': 'Selected model is at capacity', 'failureKind': 'model_capacity'}
                return super().__call__(root, state, stage, model, workspace, prompt,
                                        schema, readonly)

        runner = DelayedCapacity(self.root)
        with patch.object(parallel, 'CAPACITY_RETRY_DELAYS', (0, 0)):
            result = self.run_with(runner)
        self.assertTrue(runner.case_imported.is_set())
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(runner.calls['firmware'], 2)
        self.assertEqual(runner.calls['case'], 1)

    def test_unowned_failed_worktree_is_not_retried_but_safe_sibling_is_imported(self):
        runner = CapacityRunner(contaminate_unowned=True)
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertEqual(runner.calls['firmware'], 1)
        self.assertEqual(runner.calls['board'], 1)
        self.assertEqual(runner.calls['case'], 1)
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertFalse((self.root / 'workspace/design/foreign.kicad_pro').exists())
        self.assertFalse((self.root / 'workspace/firmware/main.c').exists())
        self.assertTrue((self.root / 'workspace/case/frame.txt').exists())
        self.assertIn('firmware', result['findings'][0])
        self.assertIn('retry suppressed', result['findings'][0])

    def test_slicer_receipt_in_failed_worktree_does_not_block_retry(self):
        receipt = '{"error_string": "Success.", "return_code": 0, "plate_index": 0}'
        runner = CapacityRunner(stray_root=receipt)
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(runner.calls['firmware'], 2)
        self.assertFalse((self.root / 'workspace/result.json').exists())

    def test_other_root_result_json_still_blocks_retry(self):
        runner = CapacityRunner(stray_root='{"pins": "not a slicer receipt"}')
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertEqual(runner.calls['firmware'], 1)
        self.assertIn('retry suppressed', result['findings'][0])

    def test_contract_edit_on_capacity_cannot_be_carried_into_retry(self):
        runner = CapacityRunner(mutate_contract=True)
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertEqual(runner.calls['firmware'], 1)
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertIn('retry suppressed', result['findings'][0])

    def test_shared_git_edit_on_capacity_cannot_be_carried_into_retry(self):
        runner = CapacityRunner(mutate_git=True)
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertEqual(runner.calls['firmware'], 1)
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertIn('Git metadata', result['findings'][0])

    def test_retried_worker_still_needs_current_contract(self):
        runner = CapacityRunner(bad_second_result=True)
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertEqual(runner.calls['firmware'], 2)
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertFalse((self.root / 'workspace/firmware/main.c').exists())
        self.assertTrue((self.root / 'workspace/case/frame.txt').exists())
        self.assertIn('another contract', result['findings'][0])

    def test_capacity_plus_real_blocker_stops_before_replanning(self):
        class MixedFailure(CapacityRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = super().__call__(root, state, stage, model, workspace,
                                           prompt, schema, readonly)
                if stage == 'case':
                    receipt['result']['blockers'] = ['joint mount cannot fit']
                return receipt

        runner = MixedFailure(always_capacity=True)
        with patch.object(parallel.time, 'sleep', return_value=None):
            result = self.run_with(runner)
        self.assertEqual(result['status'], 'model_unavailable')
        self.assertIn('Selected model is at capacity', result['findings'][0])
        self.assertIn('joint mount cannot fit', result['findings'][0])
        self.assertEqual(runner.calls['firmware'], 3)
        self.assertEqual(runner.calls['case'], 1 + parallel.MAX_ADVICE_ROUNDS)
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())

    def test_exhausted_receipt_with_generic_exit_reports_capacity_and_attempts(self):
        runner = CapacityRunner(always_capacity=True, opaque_error=True)
        with patch.object(parallel.time, 'sleep', return_value=None):
            result = self.run_with(runner)
        self.assertEqual(result['status'], 'model_unavailable')
        self.assertIn('model_capacity after 3 attempt(s): Codex exited 1', result['findings'][0])
        terminal = [job for job in result['jobs'] if job['stage'] == 'firmware'][-1]
        self.assertEqual(terminal['error'], 'Codex exited 1')

    def test_timeout_without_progress_plus_real_blocker_is_execution_limit(self):
        class TimedOut(fixture.Fake):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == 'firmware':
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .1, 'usage': None, 'result': None,
                            'error': 'Job timed out after 1800 seconds', 'failureKind': 'timeout'}
                receipt = super().__call__(root, state, stage, model, workspace,
                                           prompt, schema, readonly)
                if stage == 'case':
                    receipt['result']['blockers'] = ['joint mount cannot fit']
                return receipt

        self.assertEqual(parallel.plan(self.root, execute=True, runner=fixture.Fake())['status'],
                         'planned')
        result = parallel.run(self.root, execute=True, runner=TimedOut(), checker=fixture.checker)
        self.assertEqual(result['status'], 'execution_limit')
        self.assertIn('timeout after 1 attempt(s)', result['findings'][0])
        self.assertIn('joint mount cannot fit', result['findings'][0])
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())

    def test_timeout_continues_owned_partial_worker_with_shorter_bound(self):
        class TimedOutOnce(fixture.Fake):
            def __init__(self):
                super().__init__()
                self.calls = Counter()
                self.saw_partial = False
                self.retry_timeout = None

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                self.calls[stage] += 1
                if stage == 'firmware' and self.calls[stage] == 1:
                    fixture.put(workspace, 'firmware/partial.c', 'first pass draft')
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .1, 'usage': None, 'result': None,
                            'error': 'Job timed out after 1800 seconds', 'failureKind': 'timeout'}
                if stage == 'firmware':
                    self.saw_partial = ((Path(workspace) / 'firmware/partial.c').read_text()
                                        == 'first pass draft')
                    self.retry_timeout = state['timeoutSeconds']
                    fixture.put(workspace, 'firmware/partial.c', 'completed second pass')
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = TimedOutOnce()
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertTrue(runner.saw_partial)
        self.assertEqual(runner.retry_timeout, 600)
        self.assertEqual(runner.calls['board'], 1)
        self.assertEqual(runner.calls['case'], 1)
        self.assertEqual(runner.calls['firmware'], 2)
        firmware_jobs = [job for job in result['jobs'] if job['stage'] == 'firmware']
        self.assertEqual([job['attemptTimeoutSeconds'] for job in firmware_jobs], [1800, 600])
        self.assertEqual((self.root / 'workspace/firmware/partial.c').read_text(),
                         'completed second pass')

    def test_case_dispatch_keeps_long_continuations_past_the_short_bound(self):
        clock = [0.0]

        class CaseTimesOutTwice(fixture.Fake):
            def __init__(self):
                super().__init__()
                self.calls = Counter()

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                self.calls[stage] += 1
                if stage == 'case' and self.calls[stage] <= 2:
                    clock[0] += state['timeoutSeconds']
                    fixture.put(workspace, 'case/partial.py', f'draft {self.calls[stage]}')
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .1, 'usage': None, 'result': None,
                            'error': 'Job timed out after 1800 seconds', 'failureKind': 'timeout'}
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = CaseTimesOutTwice()
        with patch.object(parallel, '_stage_clock', side_effect=lambda: clock[0]):
            result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertEqual(runner.calls['case'], 3)
        case_jobs = [job for job in result['jobs'] if job['stage'] == 'case']
        self.assertEqual([job['attemptTimeoutSeconds'] for job in case_jobs], [1800, 1800, 1800])

    def test_second_timeout_stops_but_imports_completed_sibling(self):
        class TimedOutTwice(fixture.Fake):
            def __init__(self):
                super().__init__()
                self.calls = Counter()

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                self.calls[stage] += 1
                if stage == 'firmware':
                    fixture.put(workspace, 'firmware/partial.c', f'draft {self.calls[stage]}')
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .1, 'usage': None, 'result': None,
                            'error': 'Job timed out', 'failureKind': 'timeout'}
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = TimedOutTwice()
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'execution_limit')
        self.assertEqual(runner.calls['firmware'], 2)
        self.assertEqual(runner.calls['board'], 1)
        self.assertEqual(runner.calls['case'], 1)
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertFalse((self.root / 'workspace/firmware/partial.c').exists())
        self.assertTrue((self.root / 'workspace/case/frame.txt').exists())
        self.assertIn('timeout after 2 attempt(s)', result['findings'][0])

    def test_interrupted_plan_and_worker_are_not_design_revisions(self):
        class Interrupted(fixture.Fake):
            def __init__(self, interrupted_stage):
                super().__init__()
                self.interrupted_stage = interrupted_stage

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == self.interrupted_stage:
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .1, 'usage': None, 'result': None,
                            'error': 'Operator interrupted model process',
                            'failureKind': 'interrupted'}
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        interrupted_plan = parallel.plan(self.root, execute=True, runner=Interrupted('plan'))
        self.assertEqual(interrupted_plan['status'], 'interrupted')
        self.assertEqual(interrupted_plan['revision'], 0)
        planned = parallel.plan(self.root, execute=True, revise=True, runner=fixture.Fake())
        self.assertEqual(planned['status'], 'planned')
        interrupted_worker = parallel.run(self.root, execute=True, runner=Interrupted('firmware'),
                                          checker=fixture.checker)
        self.assertEqual(interrupted_worker['status'], 'interrupted')
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())


class TeamCapacityTests(unittest.TestCase):
    def run_limited_team(self, failure_kind, failed_stage='board'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name) / 'visible'
        workspace.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(bridge_fixture.ROOT / 'harness/kicad/template' / name,
                         workspace / name)
        fixture.put(workspace, '.circuit/team-auto', '')
        brief = fixture.put(workspace, 'prompts/history/first.md', 'Build a sensor and case.')

        class UnavailableBoard(bridge_fixture.FakeRunner):
            def __call__(self, root, state, stage, model, worktree, prompt, schema, readonly):
                if stage == failed_stage:
                    self.visits.append(stage)
                    return {'stage': stage, 'model': model, 'startedAt': time.time(),
                            'seconds': .1, 'usage': None, 'result': None,
                            'error': 'Selected model is at capacity' if failure_kind == 'model_capacity'
                            else 'Job timed out after 1800 seconds',
                            'failureKind': failure_kind}
                return super().__call__(root, state, stage, model, worktree, prompt, schema, readonly)

        runner = UnavailableBoard(workspace)
        with patch.dict('os.environ', {'KICAD_HARNESS_ROOT': str(bridge_fixture.ROOT)}), \
             patch.object(parallel.time, 'sleep', return_value=None):
            state = parallel_tile.start(workspace, brief, execute=True, runner=runner,
                                        checker=bridge_fixture.FakeChecker(), max_rounds=3)
        return workspace, runner, state

    def test_exhausted_capacity_stops_before_a_new_contract_revision(self):
        workspace, runner, state = self.run_limited_team('model_capacity')
        self.assertEqual(state['status'], 'model_unavailable')
        self.assertEqual(state['stage'], 'model-unavailable')
        self.assertFalse(state['teamReady'])
        self.assertEqual(state['roundsAttempted'], 1)
        self.assertEqual(runner.visits.count('plan'), 1)
        self.assertEqual(runner.visits.count('board'), 3)
        self.assertEqual(runner.visits.count('firmware'), 0)
        self.assertEqual(runner.visits.count('case'), 0)
        self.assertFalse((workspace / 'design/main.kicad_pro').exists())
        pilot = parallel.status(workspace / '.circuit/team-runs/r001')
        self.assertEqual(pilot['revision'], 1)
        self.assertEqual(pilot['status'], 'model_unavailable')

    def test_no_progress_timeout_stops_before_a_new_contract_revision(self):
        workspace, runner, state = self.run_limited_team('timeout')
        self.assertEqual(state['status'], 'execution_limit')
        self.assertEqual(state['stage'], 'execution-limit')
        self.assertFalse(state['teamReady'])
        self.assertEqual(state['roundsAttempted'], 1)
        self.assertEqual(runner.visits.count('plan'), 1)
        self.assertEqual(runner.visits.count('board'), 1)
        self.assertFalse((workspace / 'design/main.kicad_pro').exists())
        pilot = parallel.status(workspace / '.circuit/team-runs/r001')
        self.assertEqual(pilot['revision'], 1)
        self.assertEqual(pilot['status'], 'execution_limit')

    def test_planning_capacity_has_consistent_terminal_stage(self):
        workspace, runner, state = self.run_limited_team('model_capacity', 'plan')
        self.assertEqual(state['status'], 'model_unavailable')
        self.assertEqual(state['stage'], 'model-unavailable')
        self.assertEqual(state['roundsAttempted'], 0)
        self.assertEqual(runner.visits.count('plan'), 3)
        self.assertEqual(parallel.status(workspace / '.circuit/team-runs/r001')['revision'], 0)

    def test_planning_timeout_has_consistent_terminal_stage(self):
        workspace, runner, state = self.run_limited_team('timeout', 'plan')
        self.assertEqual(state['status'], 'execution_limit')
        self.assertEqual(state['stage'], 'execution-limit')
        self.assertEqual(state['roundsAttempted'], 0)
        self.assertEqual(runner.visits.count('plan'), 1)
        self.assertEqual(parallel.status(workspace / '.circuit/team-runs/r001')['revision'], 0)


if __name__ == '__main__':
    unittest.main()
