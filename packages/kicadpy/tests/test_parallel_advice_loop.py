"""A lead can coach the same board worker without silently changing its contract."""
from pathlib import Path
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from kicadpy import parallel, parallel_tile

# Unit runs must not depend on the developer machine's free disk.
os.environ.setdefault('CIRCUIT_TEAM_MIN_FREE_GB', '0')

try:
    from . import test_parallel as fixture
    from . import test_parallel_tile as bridge_fixture
except ImportError:
    import test_parallel as fixture
    import test_parallel_tile as bridge_fixture


class BoardAdviceRunner(fixture.Fake):
    def __init__(self, *, first_blocker=False, always_blocked=False,
                 decision='repair', unsafe_edit=False, repair_native=False,
                 changed_fields=('powerRails',)):
        super().__init__()
        self.changed_fields = list(changed_fields)
        self.first_blocker = first_blocker
        self.always_blocked = always_blocked
        self.decision = decision
        self.unsafe_edit = unsafe_edit
        self.repair_native = repair_native
        self.board_worktrees = []
        self.advice_worktrees = []
        self.advice_prompts = []

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        if stage == 'board-advice':
            self.visits.append((stage, model, readonly))
            self.advice_worktrees.append(Path(workspace))
            self.advice_prompts.append(prompt)
            return {
                'stage': stage, 'model': model, 'startedAt': time.time(),
                'seconds': .1, 'usage': None, 'error': None,
                'result': {
                    'contractSha256': state['contractSha256'], 'stage': 'board',
                    'decision': self.decision,
                    'summary': 'Preserve the sealed interface and repair this board.'
                    if self.decision == 'repair' else 'The power interface must change.',
                    'actions': [{'path': 'design/main.kicad_pro',
                                 'message': 'Continue the board from the existing source.'}],
                    'changedFields': self.changed_fields if self.decision == 'interface_change' else [],
                },
            }
        receipt = super().__call__(root, state, stage, model, workspace, prompt, schema, readonly)
        if stage == 'board':
            self.board_worktrees.append(Path(workspace))
            attempt = len(self.board_worktrees)
            if self.unsafe_edit and attempt == 1:
                fixture.put(workspace, 'case/foreign.txt', 'board worker crossed ownership')
            if self.always_blocked or (self.first_blocker and attempt == 1):
                receipt['result']['blockers'] = ['Battery pack sourcing remains open']
            if self.repair_native and attempt > 1:
                fixture.put(workspace, 'design/main.kicad_pro', 'repaired native board source')
        return receipt


class BoardReviewRepairRunner(BoardAdviceRunner):
    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        receipt = super().__call__(root, state, stage, model, workspace, prompt,
                                   schema, readonly)
        if stage == 'board' and 'LEAD REVIEW FINDINGS' in prompt:
            fixture.put(workspace, 'design/main.kicad_pro', 'lead review repair')
        if stage == 'board-review' and (workspace / 'design/main.kicad_pro').read_text() != 'lead review repair':
            receipt['result'].update(decision='revise', summary='Correct mounting location',
                                     findings=[{'path': 'design/main.kicad_pro',
                                                'message': 'Mount is outside the approved location'}])
        return receipt


class LateRepairRunner(BoardAdviceRunner):
    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        receipt = super().__call__(root, state, stage, model, workspace, prompt,
                                   schema, readonly)
        if stage == 'case' and 'LEAD REVIEW FINDINGS' in prompt:
            fixture.put(workspace, 'case/frame.txt', 'repaired frame')
        if stage == 'case-final' and 'LEAD REVIEW FINDINGS' in prompt:
            fixture.put(workspace, 'case/cover.txt', 'repaired cover')
        if stage == 'firmware-final' and 'LEAD REVIEW FINDINGS' in prompt:
            fixture.put(workspace, 'firmware/WIRING.md', 'native fixed wiring')
        if stage == 'structure-review' and (workspace / 'case/frame.txt').read_text() != 'repaired frame':
            receipt['result'].update(decision='revise', summary='Repair frame',
                                     findings=[{'path': 'case/frame.txt',
                                                'message': 'Frame lacks retention'}])
        if stage == 'final-review' and (workspace / 'case/cover.txt').read_text() != 'repaired cover':
            receipt['result'].update(decision='revise', summary='Repair cover',
                                     findings=[{'path': 'case/cover.txt',
                                                'message': 'Cover blocks fastener access'}])
        return receipt


class BoardAdviceIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.prompt = fixture.put(self.base, 'brief.txt', 'Build a sensor board and case.')
        self.root = self.base / 'pilot'
        parallel.initialize(self.root, self.prompt, instructions=fixture.CARD)

    def run_with(self, runner, *, checker=fixture.checker):
        planned = parallel.plan(self.root, execute=True, runner=runner)
        self.assertEqual(planned['status'], 'planned', planned.get('findings'))
        return parallel.run(self.root, execute=True, runner=runner, checker=checker)

    def test_worker_blocker_gets_lead_advice_and_same_worktree_continues(self):
        runner = BoardAdviceRunner(first_blocker=True)
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result.get('findings'))
        self.assertEqual(result['revision'], 1)
        self.assertEqual(len(runner.board_worktrees), 2)
        self.assertEqual(runner.board_worktrees[0], runner.board_worktrees[1])
        self.assertEqual(runner.advice_worktrees, runner.board_worktrees[:1])
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').is_file())
        self.assertFalse(result.get('replanRequired', False))

    def test_progressing_board_timeout_gets_one_lead_advice_before_continuation(self):
        class TimedOutBoard(BoardAdviceRunner):
            def __init__(self):
                super().__init__()
                self.board_calls = 0
                self.retry_prompt = None

            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == 'board':
                    self.board_calls += 1
                    if self.board_calls == 1:
                        fixture.put(workspace, 'design/main.kicad_pro', 'partial native board')
                        return {'stage': stage, 'model': model, 'startedAt': time.time(),
                                'seconds': .1, 'usage': None, 'result': None,
                                'error': 'Board model timed out', 'failureKind': 'timeout'}
                    self.retry_prompt = prompt
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = TimedOutBoard()
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result.get('findings'))
        self.assertEqual(result['revision'], 1)
        self.assertEqual(runner.board_calls, 2)
        self.assertEqual(len(runner.advice_worktrees), 1)
        self.assertIn('LEAD ADVICE ON THE PARTIAL WORK', runner.retry_prompt)
        self.assertEqual(result['advisorCalls'], 1)

    def test_interface_change_is_the_explicit_replan_path(self):
        runner = BoardAdviceRunner(first_blocker=True, decision='interface_change')
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertEqual(result['revision'], 1)
        self.assertTrue(result.get('replanRequired'))
        self.assertEqual(len(runner.board_worktrees), 1)
        self.assertEqual(len(runner.advice_worktrees), 1)
        self.assertFalse((self.root / 'workspace/design/main.kicad_pro').exists())

    def test_interface_change_accepts_lead_paths_to_nested_contract_fields(self):
        # Real r002 lead advice cited these paths; the round must reach replan, not crash.
        runner = BoardAdviceRunner(first_blocker=True, decision='interface_change',
                                   changed_fields=('interfaces.pins[8].pin',
                                                   'interfaces.firmwareBehavior[1]', 'pins'))
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertTrue(result.get('replanRequired'))
        self.assertEqual(result['requestedChangedFields'], ['firmwareBehavior', 'pins'])

    def test_interface_change_still_rejects_unknown_contract_fields(self):
        runner = BoardAdviceRunner(first_blocker=True, decision='interface_change',
                                   changed_fields=('interfaces.batteryColor',))
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertFalse(result.get('replanRequired'))
        self.assertIn('named shared interface fields', result['findings'][0])

    def test_replan_must_actually_change_the_cited_contract_field(self):
        runner = BoardAdviceRunner(first_blocker=True, decision='interface_change')
        failed = self.run_with(runner)
        old_hash = failed['contractSha256']
        replanned = parallel.plan(self.root, execute=True, revise=True, runner=runner)
        self.assertEqual(replanned['status'], 'planning_failed')
        self.assertEqual(replanned['revision'], 1)
        self.assertEqual(replanned['contractSha256'], old_hash)
        self.assertIn('did not change a cited', replanned['findings'][0])

    def test_external_blocker_remains_unready_without_replan(self):
        runner = BoardAdviceRunner(first_blocker=True, decision='external_blocker')
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertFalse(result.get('replanRequired', False))
        self.assertEqual(result['revision'], 1)
        self.assertEqual(len(runner.board_worktrees), 1)

    def test_lead_cannot_waive_outside_a_stuck_tester_dispute(self):
        runner = BoardAdviceRunner(first_blocker=True, decision='waive')
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('waive only a stuck tester finding', json.dumps(result['findings']))

    def test_task_label_change_is_not_an_interface_revision(self):
        class TaskOnlyAdvice(BoardAdviceRunner):
            def __call__(self, *args):
                receipt = super().__call__(*args)
                if args[2] == 'board-advice':
                    receipt['result']['changedFields'] = ['tasks.board']
                return receipt

        runner = TaskOnlyAdvice(first_blocker=True, decision='interface_change')
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertFalse(result.get('replanRequired', False))
        self.assertIn('shared interface fields', result['findings'][0])

    def test_unowned_worker_edit_cannot_be_advised_or_imported(self):
        runner = BoardAdviceRunner(first_blocker=True, unsafe_edit=True)
        result = self.run_with(runner)
        self.assertNotEqual(result['status'], 'candidate_ready')
        self.assertEqual(len(runner.board_worktrees), 1)
        self.assertEqual(runner.advice_worktrees, [])
        self.assertFalse((self.root / 'workspace/case/foreign.txt').exists())
        self.assertFalse((self.root / 'workspace/design/main.kicad_pro').exists())

    def test_private_native_checker_cannot_stage_worker_git_index(self):
        runner = BoardAdviceRunner()

        def staged_check(workspace, complete):
            result = fixture.checker(workspace, complete)
            if not complete:
                parallel.git(workspace, 'add', '-f', '--', 'design/main.kicad_pro')
            return result

        result = self.run_with(runner, checker=staged_check)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('Git metadata', result['findings'][0])
        self.assertFalse((self.root / 'workspace/design/main.kicad_pro').exists())

    def test_repeated_identical_blocker_stops_after_bounded_advice(self):
        runner = BoardAdviceRunner(always_blocked=True)
        result = self.run_with(runner)
        self.assertNotEqual(result['status'], 'candidate_ready')
        self.assertEqual(result['revision'], 1)
        self.assertFalse(result.get('replanRequired', False))
        self.assertGreaterEqual(len(runner.board_worktrees), 2)
        self.assertLessEqual(len(runner.board_worktrees), 4)
        self.assertLessEqual(len(runner.advice_worktrees), 2)
        self.assertEqual(len(set(runner.board_worktrees)), 1)
        self.assertFalse((self.root / 'workspace/design/main.kicad_pro').exists())

    def test_private_native_check_failure_is_repaired_in_same_worktree(self):
        runner = BoardAdviceRunner(repair_native=True)
        checked_worktrees = []

        def check(workspace, complete):
            checked_worktrees.append(Path(workspace))
            result = fixture.checker(workspace, complete)
            if not complete and (Path(workspace) / 'design/main.kicad_pro').read_text() != 'repaired native board source':
                return {**result, 'passed': False, 'findings': ['Native DRC pad clearance']}
            return result

        result = self.run_with(runner, checker=check)
        self.assertEqual(result['status'], 'candidate_ready', result.get('findings'))
        self.assertEqual(result['revision'], 1)
        self.assertEqual(len(runner.board_worktrees), 2)
        self.assertEqual(len(set(runner.board_worktrees)), 1)
        self.assertEqual(runner.advice_worktrees, runner.board_worktrees[:1])
        self.assertIn(runner.board_worktrees[0], checked_worktrees)
        self.assertEqual((self.root / 'workspace/design/main.kicad_pro').read_text(),
                         'repaired native board source')
        self.assertFalse(result.get('replanRequired', False))

    def test_rejected_board_review_repairs_under_same_contract_before_consumers(self):
        runner = BoardReviewRepairRunner()
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result.get('findings'))
        self.assertEqual(result['revision'], 1)
        self.assertEqual(len(runner.board_worktrees), 2)
        self.assertNotEqual(runner.board_worktrees[0], runner.board_worktrees[1])
        self.assertEqual((self.root / 'workspace/design/main.kicad_pro').read_text(),
                         'lead review repair')
        stages = [stage for stage, _, _ in runner.visits]
        self.assertLess(stages.index('board-review'), stages.index('firmware'))
        self.assertEqual(stages.count('board-review'), 2)
        self.assertFalse(result.get('replanRequired', False))

    def test_board_review_contract_finding_requires_lead_interface_decision(self):
        class ContractReviewRunner(BoardAdviceRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = super().__call__(root, state, stage, model, workspace,
                                           prompt, schema, readonly)
                if stage == 'board-review':
                    receipt['result'].update(decision='revise', summary='Power rule must change',
                                             findings=[{'path': 'PILOT_CONTRACT.json',
                                                        'message': 'Power rail is incompatible'}])
                return receipt

        runner = ContractReviewRunner(decision='interface_change')
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertTrue(result['replanRequired'])
        self.assertEqual(result['requestedChangedFields'], ['powerRails'])
        self.assertEqual(result['revision'], 1)
        self.assertNotIn('firmware', [stage for stage, _, _ in runner.visits])

    def test_structure_and_final_reviews_send_cited_case_fixes_to_case_worker(self):
        runner = LateRepairRunner()
        result = self.run_with(runner)
        self.assertEqual(result['status'], 'candidate_ready', result.get('findings'))
        self.assertEqual(result['revision'], 1)
        self.assertEqual((self.root / 'workspace/case/frame.txt').read_text(), 'repaired frame')
        self.assertEqual((self.root / 'workspace/case/cover.txt').read_text(), 'repaired cover')
        stages = [stage for stage, _, _ in runner.visits]
        self.assertEqual(stages.count('structure-review'), 2)
        self.assertEqual(stages.count('final-review'), 2)
        self.assertEqual(stages.count('firmware'), 1)
        self.assertEqual(stages.count('firmware-final'), 1)

    def test_final_native_failure_gets_file_cited_advice_and_firmware_repair(self):
        class WiringRepairRunner(BoardAdviceRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = super().__call__(root, state, stage, model, workspace,
                                           prompt, schema, readonly)
                if stage == 'firmware-final' and 'LEAD REVIEW FINDINGS' in prompt:
                    fixture.put(workspace, 'firmware/WIRING.md', 'native fixed wiring')
                return receipt

        runner = WiringRepairRunner()

        def check(workspace, complete):
            verdict = fixture.checker(workspace, complete)
            if complete and (Path(workspace) / 'firmware/WIRING.md').read_text() != 'native fixed wiring':
                return {**verdict, 'passed': False, 'findings': ['Firmware wiring is stale']}
            return verdict

        result = self.run_with(runner, checker=check)
        self.assertEqual(result['status'], 'candidate_ready', result.get('findings'))
        self.assertEqual(result['revision'], 1)
        stages = [stage for stage, _, _ in runner.visits]
        self.assertEqual(stages.count('final-advice'), 1)
        self.assertEqual(stages.count('firmware-final'), 2)
        self.assertEqual(stages.count('case-final'), 1)

    def test_final_native_repair_can_target_an_exact_missing_artifact(self):
        class MissingWiringRunner(BoardAdviceRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                receipt = super().__call__(root, state, stage, model, workspace,
                                           prompt, schema, readonly)
                if stage == 'firmware-final':
                    path = Path(workspace) / 'firmware/WIRING.md'
                    if 'LEAD REVIEW FINDINGS' in prompt:
                        fixture.put(workspace, 'firmware/WIRING.md', 'restored wiring')
                    else:
                        path.unlink()
                return receipt

        runner = MissingWiringRunner()

        def check(workspace, complete):
            verdict = fixture.checker(workspace, complete)
            if complete and not (Path(workspace) / 'firmware/WIRING.md').is_file():
                return {**verdict, 'passed': False, 'findings': ['firmware/WIRING.md is missing']}
            return verdict

        result = self.run_with(runner, checker=check)
        self.assertEqual(result['status'], 'candidate_ready', result.get('findings'))
        self.assertEqual((self.root / 'workspace/firmware/WIRING.md').read_text(),
                         'restored wiring')
        self.assertEqual(result['revision'], 1)

    def test_repaired_board_keeps_integrated_native_verdict_for_viewer_checkpoint(self):
        visible = self.base / 'visible'
        visible.mkdir()
        for name in ('project.json', 'product.json'):
            shutil.copy2(fixture.ROOT / 'harness/kicad/template' / name, visible / name)
        parallel.write(visible / '.harness/verdict.json',
                       {'spec': 1, 'ready': False, 'summary': 'No board yet'})
        brief = fixture.put(visible, 'prompts/history/first.md',
                            'Build a sensor board and case.')
        runner = BoardAdviceRunner(repair_native=True)
        native = bridge_fixture.FakeChecker()

        def check(workspace, complete):
            result = native(workspace, complete)
            fixture.put(workspace, 'boards/published.json', 'actual board')
            source = Path(workspace) / 'design/main.kicad_pro'
            if not complete and source.read_text() != 'repaired native board source':
                parallel.write(Path(workspace) / '.harness/verdict.json',
                               {'spec': 1, 'ready': False, 'summary': 'Native DRC failed'})
                return {**result, 'passed': False, 'findings': ['Native DRC pad clearance']}
            return result

        with patch.dict(os.environ, {'KICAD_HARNESS_ROOT': str(fixture.ROOT)}):
            result = parallel_tile.start(visible, brief, execute=True, runner=runner,
                                         checker=check, max_rounds=1)
        self.assertEqual(result['status'], 'candidate_ready', result.get('error'))
        self.assertTrue(json.loads((visible / '.harness/verdict.json').read_text())['ready'])
        self.assertEqual(len(set(runner.board_worktrees)), 1)
        self.assertEqual(len(runner.board_worktrees), 2)
        self.assertTrue((visible / 'design/main.kicad_pro').is_file())


if __name__ == '__main__':
    unittest.main()
