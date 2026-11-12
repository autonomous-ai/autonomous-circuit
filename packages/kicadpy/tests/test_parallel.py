"""Offline orchestration tests: no real models, native CAD, network or hardware."""
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from kicadpy import parallel as pilot
from kicadpy.firmware import firmware_built
from kicadpy.parallel_contract import validate_contract

ROOT = Path(__file__).resolve().parents[3]
CARD = ROOT / 'harness/kicad'


def contract():
    return {
        'name': 'Reference sensor',
        'coordinateFrame': {'units': 'mm', 'origin': 'board centre, top copper', 'axes': '+x right, +y up, +z out'},
        'board': {'outlineMm': [[-20, -15], [20, -15], [20, 15], [-20, 15]], 'thicknessMm': 1.6,
                  'mounts': [{'id': 'H1', 'xMm': -17, 'yMm': -12, 'holeDiameterMm': 2.2}]},
        'powerRails': [{'name': '3V3', 'voltageV': 3.3, 'source': 'reference module regulator'}],
        'pins': [{'signal': 'SDA', 'device': 'U1', 'pin': '4', 'net': 'SDA', 'voltageV': 3.3}],
        'modules': [{'id': 'U1', 'interface': 'I2C', 'positionMm': [0, 0, 2], 'sizeMm': [20, 15, 3]}],
        'mechanicalEnvelopeMm': [60, 50, 30], 'firmwareBehavior': ['Read sensor and report USB values'],
        'assumptions': ['GIẢ ĐỊNH reference module dimensions; physical test pending'],
        'tasks': {'board': 'Native sensor board', 'firmware': 'I2C and USB', 'case': 'Simple serviceable frame, covers later'},
    }


def put(workspace, name, text):
    p = Path(workspace) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class Fake:
    def __init__(self):
        self.visits = []
        self.lock = threading.Lock()
        self.barrier = None
        self.defect = None
        self.peak = 0
        self.active = 0
        self.lead_decision = 'approve'

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        with self.lock:
            self.visits.append((stage, model, readonly))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if stage in ('firmware', 'case'):
                assert (workspace / 'boards/published.json').read_text() == 'actual board'
                if self.barrier:
                    self.barrier.wait(timeout=5)  # both consumers share the checked board base
            if stage == 'plan':
                result = contract()
            elif stage.endswith('-advice'):
                cited = ('firmware/WIRING.md' if stage == 'final-advice' else
                         'design/main.kicad_pro' if (workspace / 'design/main.kicad_pro').is_file()
                         else 'PILOT_CONTRACT.json')
                result = {'contractSha256': state['contractSha256'],
                          'stage': stage.removesuffix('-advice'), 'decision': 'repair',
                          'summary': 'Repair the implementation under the sealed contract',
                          'actions': [{'path': cited, 'message': 'Correct the reported failure'}],
                          'changedFields': []}
            elif readonly:
                result = {'contractSha256': state['contractSha256'], 'decision': self.lead_decision,
                          'summary': 'Reviewed actual files', 'findings': []}
                if self.lead_decision == 'revise':
                    result['findings'] = [{'path': 'design/main.kicad_pro', 'message': 'Mount mismatch'}]
            else:
                if stage in ('board', 'solo'):
                    put(workspace, 'design/main.kicad_pro', 'fake native source')
                    put(workspace, '.kicadpy/worker-cache/revision.json', 'private worker cache')
                    put(workspace, 'manufacturing.json', 'revision-bound native manufacturing review')
                if stage in ('firmware', 'solo'):
                    source = put(workspace, 'firmware/main.c', 'initial pins')
                    binary = put(workspace, 'firmware/a.bin', 'compiled initial')
                    os.utime(source, (100, 100))
                    os.utime(binary, (200, 200))
                    put(workspace, 'firmware/README.md', 'build and bringup')
                    put(workspace, 'firmware/WIRING.md', 'contract pin table')
                if stage in ('case', 'solo'):
                    put(workspace, 'case/frame.txt', 'exposed reference frame')
                if stage.endswith('-final'):
                    assert (workspace / 'boards/published.json').read_text() == 'actual board'
                    assert (workspace / 'case/frame.txt').is_file()
                    if stage in ('firmware-final', 'solo-final'):
                        put(workspace, 'firmware/WIRING.md', 'reconciled actual netlist')
                    if stage in ('case-final', 'solo-final'):
                        put(workspace, 'case/README.md', 'guide and overview')
                        put(workspace, 'case/cover.txt', 'cover around checked frame')
                result = {'contractSha256': state['contractSha256'], 'stage': stage,
                          'summary': 'Scoped task done', 'blockers': []}
            if self.defect:
                self.defect(stage, workspace, result)
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .1,
                    'usage': {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 20,
                              'reasoning_output_tokens': 10}, 'result': result, 'error': None}
        finally:
            with self.lock:
                self.active -= 1


def checker(workspace, complete):
    put(workspace, 'boards/published.json', 'actual board')
    put(workspace, '.kicadpy/host-cache/revision.json', 'private native cache')
    return {'passed': True, 'scope': 'final' if complete else 'board', 'findings': []}


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.prompt = put(self.base, 'brief.txt', 'Make a simple reference USB sensor and case.')
        self.fake = Fake()
        self.root = self.base / 'pilot'
        self.init()

    def init(self, root=None, **kwargs):
        return pilot.initialize(root or self.root, self.prompt, instructions=CARD, **kwargs)

    def plan(self, **kwargs):
        state = pilot.plan(self.root, execute=True, runner=self.fake, **kwargs)
        self.assertEqual(state['status'], 'planned', state.get('findings'))
        return state

    def run_round(self, **kwargs):
        return pilot.run(self.root, execute=True, runner=self.fake,
                         checker=kwargs.get('checker', checker),
                         on_checkpoint=kwargs.get('on_checkpoint'))

    def test_no_model_call_without_explicit_execution(self):
        self.assertEqual(pilot.status(self.root)['status'], 'initialized')
        for function in (pilot.plan, pilot.run):
            with self.assertRaisesRegex(ValueError, '--execute'):
                function(self.root, runner=self.fake)
        self.assertEqual(self.fake.visits, [])
        with self.assertRaisesRegex(ValueError, 'must not exist'):
            self.init()

    def test_alternate_instructions_do_not_select_another_runtime(self):
        import shutil
        source = self.base / 'custom/cards'
        shutil.copytree(CARD, source)
        target = self.base / 'custom-pilot'
        state = pilot.initialize(target, self.prompt, instructions=source)
        self.assertEqual(Path(state['toolchainRoot']), ROOT)
        self.assertEqual(state['pipelineSha256'], pilot.pipeline_sha(ROOT))
        (source / 'AGENTS.md').write_text('changed original after snapshot')
        self.assertEqual(pilot.status(target)['status'], 'initialized')

    def test_board_passes_native_gate_before_two_workers_overlap(self):
        self.plan()
        self.fake.barrier = threading.Barrier(2)
        state = self.run_round()
        self.assertEqual(state['status'], 'candidate_ready', state['findings'])
        self.assertEqual(self.fake.peak, 2)
        stages = [s for s, _, _ in self.fake.visits]
        self.assertLess(stages.index('board'), stages.index('board-review'))
        self.assertLess(stages.index('board-review'), stages.index('firmware'))
        self.assertLess(stages.index('board-review'), stages.index('case'))
        self.assertLess(stages.index('case'), stages.index('structure-review'))
        self.assertLess(stages.index('firmware'), stages.index('structure-review'))
        self.assertLess(stages.index('structure-review'), stages.index('case-final'))
        self.assertEqual(stages[-1], 'final-review')
        self.assertEqual(pilot.status(self.root)['status'], 'candidate_ready')
        self.assertFalse(state['hardwareTested'])
        workspace = self.root / 'workspace'
        self.assertGreater((workspace / 'firmware/a.bin').stat().st_mtime,
                           (workspace / 'firmware/main.c').stat().st_mtime)
        self.assertEqual(pilot.git(workspace, 'status', '--porcelain'), '')
        self.assertEqual(pilot.git(workspace, 'remote'), '')
        self.assertFalse((workspace / '.kicadpy/worker-cache').exists())
        self.assertTrue((workspace / '.kicadpy/host-cache').is_dir())
        self.assertTrue(all(m == (pilot.LEAD if ro else pilot.WORKER) for _, m, ro in self.fake.visits))
        totals = pilot.metrics(state)
        self.assertEqual([j['stage'] for j in totals['leadJobs']],
                         ['plan', 'board-review', 'structure-review', 'final-review'])
        self.assertNotIn('board-review', [j['stage'] for j in totals['workerJobs']])
        self.assertEqual(totals['reportedUsage']['fresh_input_tokens'], len(state['jobs']) * 40)
        self.assertTrue(totals['usageComplete'])
        self.assertIsNone(totals['actualBilledUsd'])
        self.assertIsNone(totals['estimatedCostUsd'])

    def test_checkpoints_see_committed_native_artifacts_before_lead_reviews(self):
        self.plan()
        seen = []

        def checked(workspace, complete):
            result = checker(workspace, complete)
            put(workspace, 'boards/checkpoint.txt', 'final native' if complete else 'board native')
            return result

        def checkpoint(workspace, state, stage):
            if stage not in ('board', 'board-approved', 'final'):
                return
            native_stage = 'board' if stage == 'board-approved' else stage
            receipt = json.loads((self.root / f".pilot/rounds/{state['revision']}/{native_stage}-check.json").read_text())
            seen.append({
                'stage': stage,
                'workspace': workspace,
                'marker': (workspace / 'boards/checkpoint.txt').read_text(),
                'receipt': receipt,
                'commit': pilot.git(workspace, 'log', '-1', '--format=%s'),
                'dirty': pilot.git(workspace, 'status', '--porcelain'),
                'status': state['status'],
                'reviewed': [name for name, _, _ in self.fake.visits if name.endswith('-review')],
                'boardReviewSaved': (self.root / f".pilot/rounds/{state['revision']}/board-review.json").is_file(),
                'verdictExists': (workspace / '.harness/verdict.json').exists(),
            })

        state = self.run_round(checker=checked, on_checkpoint=checkpoint)
        self.assertEqual(state['status'], 'candidate_ready', state['findings'])
        self.assertEqual([event['stage'] for event in seen],
                         ['board', 'board-approved', 'final'])
        self.assertEqual([event['marker'] for event in seen],
                         ['board native', 'board native', 'final native'])
        self.assertEqual([event['commit'] for event in seen],
                         ['Record native board publication', 'Record native board publication',
                          'Record final native checks'])
        self.assertEqual([event['reviewed'] for event in seen],
                         [[], ['board-review'], ['board-review', 'structure-review']])
        self.assertEqual([event['boardReviewSaved'] for event in seen], [False, True, True])
        for event in seen:
            self.assertEqual(event['workspace'], self.root / 'workspace')
            self.assertTrue(event['receipt']['passed'])
            self.assertEqual(event['dirty'], '')
            self.assertEqual(event['status'], 'running')
            self.assertFalse(event['verdictExists'])  # pilot never invents a native verdict

    def test_failed_native_checkpoint_preserves_unready_verdict_and_blocks_final(self):
        self.plan()
        seen = []

        def failed(workspace, complete):
            result = checker(workspace, complete)
            pilot.write(workspace / '.harness/verdict.json', {'ready': False, 'summary': 'DRC failed'})
            return {**result, 'passed': False, 'findings': ['real DRC error']}

        def checkpoint(workspace, state, stage):
            seen.append((stage, json.loads((workspace / '.harness/verdict.json').read_text()),
                         json.loads((self.root / f'.pilot/rounds/{state["revision"]}/{stage}-check.json').read_text())))

        state = self.run_round(checker=failed, on_checkpoint=checkpoint)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertEqual(seen, [])  # private failed board never enters the viewer checkpoint
        self.assertFalse((self.root / 'workspace/.harness/verdict.json').exists())
        self.assertNotIn('firmware', [name for name, _, _ in self.fake.visits])
        self.assertNotIn('case', [name for name, _, _ in self.fake.visits])
        self.assertNotIn('board-review', [name for name, _, _ in self.fake.visits])
        self.assertNotIn('structure-review', [name for name, _, _ in self.fake.visits])
        self.assertNotIn('case-final', [name for name, _, _ in self.fake.visits])

    def test_checkpoint_error_fails_closed_before_lead_review(self):
        self.plan()

        def broken(workspace, state, stage):
            raise RuntimeError('bridge copy failed')

        state = self.run_round(on_checkpoint=broken)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertIn('bridge copy failed', state['findings'][0])
        self.assertNotIn('board-review', [name for name, _, _ in self.fake.visits])

    def test_lead_rejects_checked_board_before_consumers_start(self):
        self.plan()
        self.fake.lead_decision = 'revise'
        checkpoints = []
        state = self.run_round(on_checkpoint=lambda workspace, state, stage:
                               checkpoints.append(stage))
        self.assertEqual(state['status'], 'needs_revision')
        self.assertIn('Mount mismatch', state['findings'][0])
        self.assertEqual(checkpoints, ['board'] * (1 + pilot.MAX_ADVICE_ROUNDS))
        stages = [stage for stage, _, _ in self.fake.visits]
        self.assertEqual(stages, ['plan'] + ['board', 'board-review'] *
                         (1 + pilot.MAX_ADVICE_ROUNDS))
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').is_file())
        self.assertFalse((self.root / 'workspace/firmware/main.c').exists())
        self.assertFalse((self.root / 'workspace/case/frame.txt').exists())
        self.assertFalse((self.root / '.pilot/rounds/1/board-review.json').exists())
        self.assertEqual([job['stage'] for job in pilot.metrics(state)['leadJobs']],
                         ['plan'] + ['board-review'] * (1 + pilot.MAX_ADVICE_ROUNDS))

    def test_sequential_and_single_builder_controls_reuse_identical_plan(self):
        parallel = self.plan()
        for mode in ('sequential', 'solo'):
            with self.subTest(mode=mode):
                target = self.base / mode
                self.init(target, mode=mode)
                control = pilot.plan(target, contract_from=self.root)
                self.assertEqual(control['contractSha256'], parallel['contractSha256'])
                self.assertEqual(control['jobs'], [])
                fake = Fake()
                result = pilot.run(target, execute=True, runner=fake, checker=checker)
                self.assertEqual(result['status'], 'candidate_ready', result['findings'])
                self.assertEqual(fake.peak, 1)
                workers = [stage for stage, _, ro in fake.visits if not ro]
                self.assertEqual(len(workers), 2 if mode == 'solo' else 5)

    def test_changed_control_brief_is_rejected(self):
        self.plan()
        self.prompt.write_text('different device')
        other = self.base / 'other'
        self.init(other)
        result = pilot.plan(other, contract_from=self.root)
        self.assertEqual(result['status'], 'planning_failed')
        self.assertIn('inputs differ', result['findings'][0])
        self.assertEqual(result['revision'], 0)

    def test_cross_role_edit_rejects_only_unsafe_role(self):
        self.plan()
        def defect(stage, workspace, result):
            if stage == 'case':
                put(workspace, 'firmware/main.c', 'wrong owner')
        self.fake.defect = defect
        result = self.run_round()
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('unowned', result['findings'][0])
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertTrue((self.root / 'workspace/firmware/main.c').exists())
        self.assertFalse((self.root / 'workspace/case/frame.txt').exists())
        self.assertEqual(len(result['jobs']), 5)  # all costs retained despite rejecting one role

    def test_stale_worker_or_blocker_prevents_import(self):
        self.plan()
        def defect(stage, workspace, result):
            if stage == 'firmware':
                result['contractSha256'] = 'old revision'
                result['blockers'] = ['pin uncertain']
        self.fake.defect = defect
        result = self.run_round()
        self.assertEqual(result['status'], 'needs_revision')
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertFalse((self.root / 'workspace/firmware/main.c').exists())
        self.assertTrue((self.root / 'workspace/case/frame.txt').exists())

    def test_completed_firmware_is_checkpointed_while_case_is_still_running(self):
        self.plan()
        case_started = threading.Event()
        release_case = threading.Event()
        seen = []

        def defect(stage, workspace, result):
            if stage == 'case':
                case_started.set()
                if not release_case.wait(timeout=5):
                    raise TimeoutError('Case test did not receive the firmware checkpoint')

        def checkpoint(workspace, state, stage):
            seen.append(stage)
            if stage == 'firmware':
                self.assertTrue(case_started.wait(timeout=2))
                self.assertTrue((workspace / 'firmware/main.c').is_file())
                self.assertFalse((workspace / 'case/frame.txt').exists())
                self.assertEqual(pilot.git(workspace, 'status', '--porcelain'), '')
                release_case.set()

        self.fake.defect = defect
        try:
            result = self.run_round(on_checkpoint=checkpoint)
        finally:
            release_case.set()
        self.assertEqual(result['status'], 'candidate_ready', result['findings'])
        self.assertLess(seen.index('firmware'), seen.index('case'))
        self.assertLess(seen.index('board-approved'), seen.index('firmware'))
        self.assertEqual(seen[-1], 'final')

    def test_native_failure_cannot_be_overridden_by_lead(self):
        self.plan()
        def failed(workspace, complete):
            return {'passed': False, 'findings': ['real DRC error']}
        result = self.run_round(checker=failed)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('Native board check failed', result['findings'][0])
        self.assertNotIn('case-final', [s for s, _, _ in self.fake.visits])

    def test_final_native_failure_blocks_candidate(self):
        self.plan()
        def failed(workspace, complete):
            result = checker(workspace, complete)
            return {**result, 'passed': not complete, 'findings': ['stale binary'] if complete else []}
        result = self.run_round(checker=failed)
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('Native final check failed', result['findings'][0])

    def test_lead_revision_preserves_old_contract_and_files(self):
        old = self.plan()
        self.fake.lead_decision = 'revise'
        failed = self.run_round()
        self.assertEqual(failed['status'], 'needs_revision')
        self.assertIn('Mount mismatch', failed['findings'][0])
        self.fake.lead_decision = 'approve'
        revised = self.plan(revise=True)
        self.assertEqual(revised['revision'], 2)
        self.assertNotEqual(revised['contractSha256'], old['contractSha256'])
        self.assertTrue((self.root / old['contractFile']).is_file())
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').is_file())
        self.assertFalse((self.root / 'workspace/case/frame.txt').exists())
        passed = self.run_round()
        self.assertEqual(passed['status'], 'candidate_ready', passed['findings'])
        self.assertEqual(pilot.metrics(passed)['repairRounds'], 1)
        self.assertEqual(len(passed['rounds']), 2)

    def test_invalid_plan_records_usage_and_can_retry(self):
        self.fake.defect = lambda stage, ws, result: result.update(board={}) if stage == 'plan' else None
        bad = pilot.plan(self.root, execute=True, runner=self.fake)
        self.assertEqual(bad['status'], 'planning_failed')
        self.assertEqual(pilot.status(self.root)['revision'], 0)
        self.assertEqual(len(bad['jobs']), 1)
        self.fake.defect = None
        good = self.plan()
        self.assertEqual(good['planAttempts'], 2)
        self.assertEqual(len(good['jobs']), 2)

    def test_failed_replan_keeps_previous_seal_readable(self):
        old = self.plan()
        self.fake.defect = lambda stage, ws, result: result.update(board={}) if stage == 'plan' else None
        bad = pilot.plan(self.root, execute=True, revise=True, runner=self.fake)
        self.assertEqual(bad['status'], 'planning_failed')
        self.assertEqual(pilot.status(self.root)['contractSha256'], old['contractSha256'])

    def test_post_review_change_is_stale(self):
        self.plan()
        self.assertEqual(self.run_round()['status'], 'candidate_ready')
        put(self.root / 'workspace', 'firmware/main.c', 'changed')
        self.assertEqual(pilot.status(self.root)['status'], 'stale')

    def test_frozen_prompt_and_contract_tampering_are_rejected(self):
        self.plan()
        put(self.root, '.pilot/prompt.txt', 'new prompt')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            pilot.status(self.root)
        put(self.root, '.pilot/prompt.txt', self.prompt.read_text())
        put(self.root, 'workspace/PILOT_CONTRACT.json', '{}')
        with self.assertRaisesRegex(ValueError, 'contract is stale'):
            pilot.status(self.root)

    def test_board_worker_cannot_commit_before_native_gate(self):
        self.plan()
        def defect(stage, workspace, result):
            if stage == 'board':
                pilot.commit(workspace, ['design/main.kicad_pro'], 'unexpected worker commit')
        self.fake.defect = defect
        failed = self.run_round()
        self.assertEqual(failed['status'], 'needs_revision')
        self.assertIn('moved HEAD', failed['findings'][0])
        self.assertFalse((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertNotIn('firmware', [stage for stage, _, _ in self.fake.visits])

    def test_case_worker_cannot_import_symlink_or_firmware_sibling(self):
        self.plan()
        def defect(stage, workspace, result):
            if stage == 'case':
                (workspace / 'case/link').symlink_to(self.prompt)
        self.fake.defect = defect
        failed = self.run_round()
        self.assertEqual(failed['status'], 'needs_revision')
        self.assertIn('symlinks', failed['findings'][0])
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertTrue((self.root / 'workspace/firmware/main.c').exists())

    def test_case_worker_build_scratch_links_are_skipped_not_imported(self):
        self.plan()
        def defect(stage, workspace, result):
            if stage == 'case':
                profile = workspace / 'build/case/chrome-profile'
                profile.mkdir(parents=True)
                (profile / 'SingletonLock').symlink_to('host-1234')
        self.fake.defect = defect
        state = self.run_round()
        self.assertEqual(state['status'], 'candidate_ready', state['findings'])
        self.assertFalse((self.root / 'workspace/build/case/chrome-profile/SingletonLock').is_symlink())
        self.assertTrue((self.root / 'workspace/case/frame.txt').exists())

    def write_handwired_case(self, workspace):
        sha = lambda name: hashlib.sha256((workspace / name).read_bytes()).hexdigest()
        put(workspace, 'case/out/frame.stl', 'solid frame')
        put(workspace, 'case/out/frame.step', 'step frame')
        put(workspace, 'case/out/parts.json', json.dumps({
            'verification': {'nativePCBCheckerApplies': False, 'report': '../reports/mechanical.json'},
            'parts': [{'name': 'frame', 'stl': 'frame.stl', 'step': 'frame.step'}]}))
        put(workspace, 'case/reports/mechanical.json', json.dumps({
            'passed': True, 'scope': 'handwired', 'collisions': [],
            'partShas': {name: sha(f'case/out/{name}') for name in ('frame.stl', 'frame.step')}}))
        put(workspace, 'case/reports/print-check.json', json.dumps({
            'passed': True, 'errors': [], 'stem': 'main', 'meshValidation': 'surface-v1',
            'validationScope': 'handwired-print', 'intentSha': None,
            'verificationReport': 'case/reports/mechanical.json',
            'verificationSha': sha('case/reports/mechanical.json'),
            'partShas': {name: sha(f'case/out/{name}') for name in ('parts.json', 'frame.stl', 'frame.step')}}))

    def test_final_case_status_uses_print_receipt_for_handwired_builds(self):
        workspace = self.base / 'handwired'
        self.write_handwired_case(workspace)
        with patch('kicadpy.case.status', side_effect=AssertionError('PCB check on a handwired build')):
            self.assertEqual(pilot.final_case_status(workspace)[0], 'passed')
            put(workspace, 'case/out/frame.stl', 'solid changed')
            self.assertEqual(pilot.final_case_status(workspace)[0], 'stale')

    def test_final_case_status_keeps_the_pcb_check_for_pcb_builds(self):
        workspace = self.base / 'pcb'
        put(workspace, 'case/out/parts.json', json.dumps({'parts': []}))
        with patch('kicadpy.case.status', return_value=('passed', 'native')) as native:
            self.assertEqual(pilot.final_case_status(workspace), ('passed', 'native'))
        native.assert_called_once()

    def firmware_defect(self, *, rebuild):
        def defect(stage, workspace, result):
            if stage == 'firmware':
                put(workspace, 'firmware/flash.json', json.dumps({'family': 'rp2040', 'uf2': 'a.bin'}))
                os.utime(workspace / 'firmware/main.c', (300 if not rebuild else 100,) * 2)
        return defect

    def test_integrated_firmware_build_stays_fresh_after_mtimes_reorder(self):
        self.plan()
        self.fake.defect = self.firmware_defect(rebuild=True)
        state = self.run_round()
        self.assertEqual(state['status'], 'candidate_ready', state['findings'])
        workspace = self.root / 'workspace'
        self.assertIn('firmware/build-proof.json', pilot.git(workspace, 'ls-files'))
        os.utime(workspace / 'firmware/main.c', (900, 900))
        self.assertTrue(firmware_built(workspace))

    def test_unbuilt_firmware_edit_gets_no_build_proof(self):
        self.plan()
        self.fake.defect = self.firmware_defect(rebuild=False)
        self.run_round()
        workspace = self.root / 'workspace'
        self.assertFalse((workspace / 'firmware/build-proof.json').exists())
        self.assertFalse(firmware_built(workspace))

    def test_snapshot_ignores_a_live_browser_profile_only_in_build(self):
        workspace = self.base / 'profile'
        put(workspace, 'case/frame.txt', 'frame')
        put(workspace, 'build/case/chrome/Local State', '{}')
        put(workspace, 'build/case/chrome/Default/Cookies', 'first')
        before = pilot.snapshot(workspace)
        put(workspace, 'build/case/chrome/Default/Cookies', 'orphan still writing')
        self.assertEqual(before, pilot.snapshot(workspace))
        self.assertEqual(set(pilot._retry_files(workspace)), {'case/frame.txt'})
        put(workspace, 'case/chrome/Local State', '{}')
        self.assertIn('case/chrome/Local State', pilot.snapshot(workspace))

    def test_cancel_tolerates_an_exited_unreaped_job(self):
        job = subprocess.Popen(['true'], start_new_session=True)
        time.sleep(.3)  # exited but not reaped: macOS killpg answers EPERM here
        with pilot.ACTIVE_LOCK:
            pilot.ACTIVE[str(self.root)] = {job.pid}
        self.addCleanup(pilot.ACTIVE.pop, str(self.root), None)
        pilot.cancel_jobs(self.root)
        job.wait()

    def test_job_end_reaps_detached_tools_of_that_worktree_only(self):
        worktree = self.root / 'workers/r001-case'
        spawn = lambda path: subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)', f'--user-data-dir={path}/build/chrome'],
            start_new_session=True)
        tool, sibling = spawn(worktree), spawn(str(worktree) + '-2')
        self.addCleanup(sibling.kill)
        self.addCleanup(tool.kill)
        time.sleep(.2)
        pilot._reap_worktree_processes(self.root, self.root / 'workspace')
        pilot._reap_worktree_processes(self.root, self.base)
        self.assertIsNone(tool.poll())
        pilot._reap_worktree_processes(self.root, worktree)
        self.assertEqual(tool.wait(timeout=5), -signal.SIGKILL)
        self.assertIsNone(sibling.poll())
        sibling.kill()
        sibling.wait()

    def test_snapshot_skips_build_links_but_rejects_source_links(self):
        workspace = self.base / 'links'
        put(workspace, 'case/frame.txt', 'frame')
        (workspace / 'build').mkdir()
        (workspace / 'build/lock').symlink_to('nowhere')
        self.assertEqual(set(pilot.snapshot(workspace)), {'case/frame.txt'})
        self.assertEqual(set(pilot._retry_files(workspace)), {'case/frame.txt'})
        (workspace / 'case/link').symlink_to(workspace / 'case/frame.txt')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            pilot.snapshot(workspace)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            pilot._retry_files(workspace)

    def test_unexpected_checker_exception_fails_closed(self):
        self.plan()
        def broken(workspace, complete):
            raise TypeError('malformed native report')
        state = self.run_round(checker=broken)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertIn('TypeError', state['findings'][0])
        self.assertEqual(pilot.status(self.root)['status'], 'needs_revision')

    def test_frozen_pipeline_change_is_rejected(self):
        with patch.object(pilot, 'pipeline_sha', return_value='different source'):
            with self.assertRaisesRegex(ValueError, 'Pipeline/checker source changed'):
                pilot.status(self.root)

    def test_changed_shared_git_metadata_cannot_enter_a_revised_round(self):
        self.plan()
        config = self.root / 'workspace/.git/config'
        config.write_text(config.read_text() + '\n[pilot]\n altered = true\n')
        with self.assertRaisesRegex(ValueError, 'shared Git metadata changed'):
            pilot.plan(self.root, execute=True, revise=True, runner=self.fake)
        self.assertFalse((self.root / 'workers/r002-board').exists())

    def test_print_binding_uses_existing_validator_and_rejects_stale_project(self):
        import hashlib
        import zipfile
        workspace = self.root / 'workspace'
        stl = put(workspace, 'case/out/frame.stl', 'reference STL bytes')
        project = workspace / 'case/print/production.3mf'
        project.parent.mkdir(parents=True)
        with zipfile.ZipFile(project, 'w') as archive:
            archive.writestr('3D/3dmodel.model', '<model/>')
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        manifest = {'parts': [{'name': 'frame', 'role': 'bracket', 'stl': 'frame.stl'}],
                    'printProject': {'file': '../print/production.3mf', 'report': '../print/check.json'}}
        pilot.write(workspace / 'case/out/parts.json', manifest)
        receipt = {'passed': True, 'file': 'case/print/production.3mf', 'sha256': sha(project),
                   'meshes': [{'name': 'frame.stl', 'canonicalSTLSha256': sha(stl), 'printRotateDeg': [0, 0, 0]}],
                   'plates': [{'index': 1, 'parts': ['frame.stl']}]}
        pilot.write(workspace / 'case/print/check.json', receipt)
        pilot.check_plates(workspace)  # binding only: geometry is covered by native case/slicer checks
        stl.write_text('changed after project verification')
        with self.assertRaisesRegex(ValueError, 'meshes or orientations changed'):
            pilot.check_plates(workspace)
        del manifest['printProject']
        pilot.write(workspace / 'case/out/parts.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Missing native arranged'):
            pilot.check_plates(workspace)

    def test_worker_shared_git_change_rejects_round(self):
        self.plan()
        def defect(stage, workspace, result):
            if stage == 'case':
                config = self.root / 'workspace/.git/config'
                config.write_text(config.read_text() + '\n[pilot]\n touched = true\n')
        self.fake.defect = defect
        state = self.run_round()
        self.assertEqual(state['status'], 'needs_revision')
        self.assertIn('shared Git metadata', state['findings'][0])
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertFalse((self.root / 'workspace/case/frame.txt').exists())

    def test_single_writer_lock(self):
        with pilot.locked(self.root):
            with self.assertRaisesRegex(ValueError, 'Another pilot'):
                self.plan()

    def test_seed_is_read_only_clean_revision_and_does_not_inherit_flash_policy(self):
        seed = self.base / 'seed'
        seed.mkdir()
        pilot.git(seed, 'init', '-q', '-b', 'pilot')
        put(seed, 'firmware/main.c', 'known source')
        put(seed, 'FLASH-POLICY.md', 'automatic flashing allowed')
        pilot.commit(seed, ['firmware/main.c', 'FLASH-POLICY.md'], 'seed')
        before = pilot.snapshot(seed)
        target = self.base / 'seeded'
        self.init(target, seed=seed)
        self.assertEqual(pilot.snapshot(seed), before)
        self.assertTrue((target / 'workspace/firmware/main.c').is_file())
        self.assertFalse((target / 'workspace/FLASH-POLICY.md').exists())
        with self.assertRaisesRegex(ValueError, 'outside the seed'):
            self.init(seed / 'new-pilot', seed=seed)
        put(seed, 'firmware/main.c', 'uncommitted source')
        with self.assertRaisesRegex(ValueError, 'clean Git'):
            self.init(self.base / 'dirty-seed', seed=seed)

    def test_native_project_input_closure_fits_board_ownership(self):
        import shutil
        from kicadpy.project import Project, manifest
        from kicadpy.parallel_contract import owns
        workspace = self.root / 'workspace'
        fixture = Path(__file__).parent / 'fixtures/tiny'
        shutil.copytree(fixture, workspace / 'design')
        put(workspace, 'manufacturing.json', '{}')
        put(workspace, 'engineering/power.md', 'reference calculations')
        project = Project(workspace / 'design/tiny.kicad_pro')
        self.assertEqual(project.root, workspace)
        names = manifest(project.root)
        self.assertIn('manufacturing.json', names)
        self.assertTrue(all(owns('board', name) for name in names))
        revision, snapshot = project.snapshot()
        self.assertTrue(revision)
        self.assertTrue((snapshot / 'manufacturing.json').is_file())
        self.assertTrue(project.store.is_relative_to(self.root / '.kicadpy'))

    def test_passed_board_check_is_reused_until_a_board_file_changes(self):
        workspace = self.root / 'workspace'
        put(workspace, 'design/main.kicad_pcb', 'pcb v1')
        put(workspace, 'firmware/crab.ino', 'fw v1')
        calls = []

        def publish(ws):
            calls.append(1)
            put(ws, 'boards/main/gerbers.zip', 'out')
            put(ws, '.harness/verdict.json', '{"ready": true}')
            return True, []

        with patch('kicadpy.autofinish.inspect_board', side_effect=publish):
            self.assertEqual(pilot.checked_board(workspace), (True, []))
            self.assertEqual(pilot.checked_board(workspace), (True, []))
            self.assertEqual(len(calls), 1)
            put(workspace, 'firmware/crab.ino', 'fw v2')
            self.assertEqual(pilot.checked_board(workspace), (True, []))
            self.assertEqual(len(calls), 1)
            put(workspace, 'design/main.kicad_pcb', 'pcb v2')
            pilot.checked_board(workspace)
            self.assertEqual(len(calls), 2)
            put(workspace, 'boards/main/gerbers.zip', 'edited by hand')
            pilot.checked_board(workspace)
            self.assertEqual(len(calls), 3)
        self.assertTrue(pilot.owns('board', pilot.BOARD_CHECK_RECORD))

    def test_passed_board_check_reruns_when_its_verdict_is_missing(self):
        workspace = self.root / 'workspace'
        put(workspace, 'design/main.kicad_pcb', 'pcb v1')
        calls = []

        def publish(ws):
            calls.append(1)
            put(ws, '.harness/verdict.json', '{"ready": true}')
            return True, []

        with patch('kicadpy.autofinish.inspect_board', side_effect=publish):
            self.assertEqual(pilot.checked_board(workspace), (True, []))
            (workspace / '.harness/verdict.json').unlink()
            self.assertEqual(pilot.checked_board(workspace), (True, []))
            self.assertEqual(len(calls), 2)
        self.assertTrue((workspace / '.harness/verdict.json').is_file())

    def test_failed_board_check_is_never_reused(self):
        workspace = self.root / 'workspace'
        put(workspace, 'design/main.kicad_pcb', 'pcb')
        with patch('kicadpy.autofinish.inspect_board', return_value=(False, ['DRC'])) as run:
            self.assertEqual(pilot.checked_board(workspace), (False, ['DRC']))
            self.assertEqual(pilot.checked_board(workspace), (False, ['DRC']))
            self.assertEqual(run.call_count, 2)
        self.assertFalse((workspace / pilot.BOARD_CHECK_RECORD).exists())

    def test_board_check_record_is_ignored_when_kicadpy_code_changes(self):
        workspace = self.root / 'workspace'
        put(workspace, 'design/main.kicad_pcb', 'pcb')
        with patch('kicadpy.autofinish.inspect_board', return_value=(True, [])) as run:
            pilot.checked_board(workspace)
            record = workspace / pilot.BOARD_CHECK_RECORD
            saved = json.loads(record.read_text())
            record.write_text(json.dumps({**saved, 'key': 'older kicadpy'}))
            pilot.checked_board(workspace)
            self.assertEqual(run.call_count, 2)

    def test_native_checker_consumes_tuple_case_status_and_real_firmware_gate(self):
        workspace = self.root / 'workspace'
        put(workspace, 'firmware/WIRING.md', 'pins')
        put(workspace, 'case/README.md', 'guide')
        with patch('kicadpy.autofinish.inspect_board', return_value=(True, [])), \
             patch('kicadpy.firmware.firmware_written', return_value=True), \
             patch('kicadpy.firmware.firmware_built', return_value=True), \
             patch('kicadpy.case.status', return_value=('passed', 'checked')), \
             patch.object(pilot, 'check_plates'):
            self.assertTrue(pilot.native_check(workspace, True)['passed'])
            with patch('kicadpy.firmware.firmware_built', return_value=False):
                self.assertFalse(pilot.native_check(workspace, True)['passed'])
            with patch('kicadpy.case.status', return_value=('stale', 'board changed')):
                self.assertFalse(pilot.native_check(workspace, True)['passed'])


class ContractTests(unittest.TestCase):
    def test_reject_conflicting_pins_nonfinite_geometry_and_degenerate_outline(self):
        validate_contract(contract())
        variants = []
        c = contract()
        c['pins'].append({**c['pins'][0], 'net': 'VCC'})
        variants.append(c)
        c = contract()
        c['board']['outlineMm'] = [[0, 0], [1, 0], [2, 0]]
        variants.append(c)
        c = contract()
        c['modules'][0]['sizeMm'][0] = float('nan')
        variants.append(c)
        c = contract()
        c['unexpected'] = 'field'
        variants.append(c)
        for c in variants:
            with self.subTest(contract=c), self.assertRaises(ValueError):
                validate_contract(c)

    def test_usage_unknown_is_not_zero_and_invalid_counts_are_ignored(self):
        self.assertIsNone(pilot.usage_of('not json\n{"type":"turn.completed","usage":{}}'))
        self.assertIsNone(pilot.usage_of(json.dumps({'type': 'turn.completed', 'usage': {
            'input_tokens': 1, 'cached_input_tokens': 2, 'output_tokens': 0}})))
        text = json.dumps({'type': 'turn.completed', 'usage': {
            'input_tokens': 10, 'cached_input_tokens': 6, 'output_tokens': 3}})
        self.assertEqual(pilot.usage_of(text + '\n' + text)['input_tokens'], 20)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'pilot'
        prompt = put(self.base, 'brief.txt', 'reference board')
        self.state = pilot.initialize(self.root, prompt, instructions=CARD)
        self.workspace = self.root / 'workspace'
        self.state['planAttempts'] = 1
        stub = put(self.base, 'bin/codex', '#!' + sys.executable + '\n' + r'''
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
workspace = pathlib.Path(args[args.index('-C') + 1])
prompt = sys.stdin.read()
if not os.environ.get('PILOT_STUB_SKIP_INVOCATION'):
    (workspace / 'invocation.json').write_text(json.dumps({
        'args': args, 'prompt': prompt,
        'advisor': os.environ.get('KICAD_ADVISOR'),
        'hostWorkspace': os.environ.get('HARNESS_WORKSPACE'),
        'thread': os.environ.get('CODEX_THREAD_ID'),
        'blocks': os.environ.get('KICAD_HARNESS_BLOCKS'),
        'cmake': os.environ.get('KICAD_HARNESS_CMAKE'),
        'pioCore': os.environ.get('PLATFORMIO_CORE_DIR'),
    }))
print(json.dumps({'type':'item.started','item':{'id':'item_1','type':'command_execution','command':'make check'}}), flush=True)
print(json.dumps({'type':'item.completed','item':{'id':'item_1','type':'command_execution','command':'make check','exit_code':0}}), flush=True)
print(json.dumps({'type':'turn.completed','usage':{
    'input_tokens':100,'cached_input_tokens':60,'output_tokens':20}}), flush=True)
for tick in range(int(os.environ.get('PILOT_STUB_TICKS', '0'))):
    time.sleep(0.4)
    print(json.dumps({'type':'item.completed','item':{'id':f'tick_{tick}','type':'reasoning'}}), flush=True)
if os.environ.get('PILOT_STUB_NOISY'):
    sys.stderr.write('x' * 300000)
    sys.stderr.flush()
if os.environ.get('PILOT_STUB_HANG'):
    code = "import pathlib,time;time.sleep(3);pathlib.Path('orphan-marker').write_text('orphan')"
    subprocess.Popen([sys.executable, '-c', code], cwd=workspace)
    time.sleep(20)
if os.environ.get('PILOT_STUB_CAPACITY'):
    message = 'Selected model is at capacity. Please try a different model.'
    print(json.dumps({'type':'error','message':message}), flush=True)
    print(json.dumps({'type':'turn.failed','error':{'message':message}}), flush=True)
    sys.exit(1)
if os.environ.get('PILOT_STUB_PLAIN_CAPACITY'):
    print('Selected model is at capacity. Please try a different model.', file=sys.stderr)
    sys.exit(1)
pathlib.Path(args[args.index('-o') + 1]).write_text(json.dumps({
    'contractSha256':'hash','stage':'worker','summary':'done','blockers':[]}))
''')
        stub.chmod(0o755)
        self.env = patch.dict(os.environ, {'PATH': str(stub.parent) + os.pathsep + os.environ['PATH'],
                                          'HARNESS_WORKSPACE': '/live-pet', 'CODEX_THREAD_ID': 'live-thread',
                                          'KICAD_ADVISOR': 'codex:real-model'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def worker_worktree(self):
        worker = self.root / 'workers/test-board'
        worker.parent.mkdir(exist_ok=True)
        pilot.git(self.workspace, 'worktree', 'add', '--detach', str(worker),
                  pilot.git(self.workspace, 'rev-parse', 'HEAD'))
        return worker

    def test_real_subprocess_adapter_pins_flags_and_isolates_host_context(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                              'scoped task', RESULT_SCHEMA, False)
        self.assertIsNone(receipt['error'])
        self.assertEqual(receipt['usage']['cached_input_tokens'], 60)
        invocation = json.loads((self.workspace / 'invocation.json').read_text())
        args = invocation['args']
        self.assertEqual(args[args.index('-m') + 1], pilot.WORKER)
        self.assertEqual(args[args.index('-s') + 1], 'danger-full-access')
        self.assertIn('--ignore-user-config', args)
        self.assertNotIn('--skip-git-repo-check', args)
        self.assertIn('--ephemeral', args)
        self.assertIn('approval_policy="never"', args)
        self.assertEqual(invocation['advisor'], 'off')
        self.assertIsNone(invocation['hostWorkspace'])
        self.assertIsNone(invocation['thread'])
        self.assertEqual(Path(invocation['blocks']), ROOT / 'packages/golden-blocks/blocks')
        self.assertEqual(Path(invocation['cmake']), ROOT / 'toolchain/pico/tools/bin/cmake')
        self.assertEqual(Path(invocation['pioCore']), pilot.FIRMWARE_CACHE / 'platformio')
        self.assertNotIn(str(self.root), invocation['pioCore'])
        lead = pilot.codex(self.root, self.state, 'review', pilot.LEAD, self.workspace,
                           'read only', RESULT_SCHEMA, True)
        self.assertIsNone(lead['error'])
        invocation = json.loads((self.workspace / 'invocation.json').read_text())
        self.assertEqual(invocation['args'][invocation['args'].index('-s') + 1], 'read-only')
        self.assertIn('--skip-git-repo-check', invocation['args'])

    def claude_stub(self):
        stub = put(self.base, 'bin/claude', '#!' + sys.executable + '\n' + r"""
import json, os, pathlib, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
blocked = False
try:
    pathlib.Path('tester-wrote-this').write_text('x')
except OSError:
    blocked = True
protected_blocked = None
if os.environ.get('PILOT_STUB_PROTECTED'):
    try:
        pathlib.Path(os.environ['PILOT_STUB_PROTECTED']).write_text('x')
        protected_blocked = False
    except OSError:
        protected_blocked = True
pathlib.Path(os.environ['PILOT_STUB_OUT']).write_text(json.dumps({
    'args': args, 'prompt': prompt, 'blocked': blocked, 'protectedBlocked': protected_blocked,
    'nested': os.environ.get('CLAUDECODE'), 'memory': os.environ.get('CLAUDE_CODE_DISABLE_CLAUDE_MDS'),
    'advisor': os.environ.get('KICAD_ADVISOR')}))
with open(os.environ['PILOT_STUB_OUT'] + '.all', 'a') as calls:
    calls.write(json.dumps(args) + '\n')
if '--resume' in args and os.environ.get('PILOT_STUB_LOST_SESSION'):
    print(json.dumps({'type': 'result', 'is_error': True,
                      'result': 'No conversation found with session ID: ' + args[args.index('--resume') + 1]}))
    sys.exit(1)
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'sess-' + str(len(prompt))}))
if os.environ.get('PILOT_STUB_OVERLOADED'):
    print(json.dumps({'type': 'result', 'is_error': True, 'api_error_status': 529, 'result': 'Overloaded'}))
    sys.exit(1)
if os.environ.get('PILOT_STUB_LOGGED_OUT'):
    print(json.dumps({'type': 'result', 'is_error': True, 'api_error_status': None,
                      'result': 'Failed to authenticate: OAuth session expired and could not be refreshed'}))
    sys.exit(1)
if os.environ.get('PILOT_STUB_NO_ANSWER'):
    print(json.dumps({'type': 'result', 'is_error': False, 'result': 'prose only'}))
    sys.exit(0)
print('{"type":"result","note":"not json for the last line"')
print(json.dumps({'type': 'assistant', 'message': 'overloaded'}))
print(json.dumps({'type': 'result', 'is_error': False, 'total_cost_usd': 1.5,
                  'structured_output': {'contractSha256': 'hash', 'stage': 'worker', 'summary': 'done', 'blockers': []},
                  'modelUsage': {'claude-opus-5-5': {'inputTokens': 4, 'outputTokens': 20, 'thinkingTokens': 7,
                                                     'cacheReadInputTokens': 60, 'cacheCreationInputTokens': 36}}}))
""")
        stub.chmod(0o755)
        out = self.base / 'claude-invocation.json'
        env = patch.dict(os.environ, {'PILOT_STUB_OUT': str(out), 'CLAUDECODE': '1'})
        env.start()
        self.addCleanup(env.stop)
        return out

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').is_file(), 'needs macOS sandbox-exec')
    def test_claude_adapter_is_read_only_isolated_and_reports_codex_shaped_usage(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        out = self.claude_stub()
        receipt = pilot.agent(self.root, self.state, 'review', 'claude-opus-5-5', self.workspace,
                              'read only', RESULT_SCHEMA, True)
        self.assertIsNone(receipt['error'])
        self.assertEqual(receipt['result']['summary'], 'done')
        self.assertEqual(receipt['usage'], {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 20,
                                            'reasoning_output_tokens': 7, 'cost_usd': 1.5})
        seen = json.loads(out.read_text())
        self.assertTrue(seen['blocked'])
        self.assertFalse((self.workspace / 'tester-wrote-this').exists())
        self.assertEqual(seen['prompt'], 'read only')
        self.assertIsNone(seen['nested'])
        self.assertEqual((seen['memory'], seen['advisor']), ('1', 'off'))
        args = seen['args']
        self.assertEqual(args[args.index('--model') + 1], 'claude-opus-5-5')
        self.assertEqual(args[args.index('--setting-sources') + 1], '')
        self.assertEqual(args[args.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(args[args.index('--tools') + 1], 'Read,Grep,Glob,Bash')
        self.assertNotIn('--dangerously-skip-permissions', args)

    def test_agent_keeps_gpt_models_on_codex(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        pilot.agent(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                    'scoped task', RESULT_SCHEMA, False)
        self.assertTrue((self.workspace / 'invocation.json').is_file())

    def test_a_claude_builder_never_writes_the_integration_workspace(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        with self.assertRaisesRegex(ValueError, 'own worker worktree'):
            pilot.agent(self.root, self.state, 'worker', 'claude-opus-5-5', self.workspace,
                        'scoped task', RESULT_SCHEMA, False)

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').is_file(), 'needs macOS sandbox-exec')
    def test_a_claude_builder_writes_its_worktree_only(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        out = self.claude_stub()
        worker = self.worker_worktree()
        with patch.dict(os.environ, {'PILOT_STUB_PROTECTED': str(self.workspace / 'builder-wrote-this')}):
            receipt = pilot.agent(self.root, self.state, 'case', 'claude-opus-5-5', worker,
                                  'build the case', RESULT_SCHEMA, False)
        self.assertIsNone(receipt['error'])
        seen = json.loads(out.read_text())
        self.assertFalse(seen['blocked'])
        self.assertTrue((worker / 'tester-wrote-this').is_file())
        self.assertTrue(seen['protectedBlocked'])
        self.assertFalse((self.workspace / 'builder-wrote-this').exists())
        args = seen['args']
        self.assertEqual(args[args.index('--tools') + 1], 'Read,Edit,Write,Grep,Glob,Bash')
        # The builder keeps its session so the team's next job can resume it; it starts fresh here.
        self.assertNotIn('--no-session-persistence', args)
        self.assertNotIn('--resume', args)
        self.assertTrue(receipt['sessionId'].startswith('sess-'))
        # High effort let an Opus case builder think for 34 minutes without writing a part.
        self.assertEqual(args[args.index('--effort') + 1], 'medium')
        self.assertIn('--no-sandbox', seen['prompt'])
        self.assertIn('first runnable version within your first ten minutes', seen['prompt'])
        self.assertIn('Do not read the harness source code', seen['prompt'])

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').is_file(), 'needs macOS sandbox-exec')
    def test_a_claude_builder_resumes_the_team_session_and_starts_fresh_when_it_is_gone(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        out = self.claude_stub()
        worker = self.worker_worktree()
        receipt = pilot.agent(self.root, {**self.state, 'resumeSession': 'sess-old'}, 'case',
                              'claude-opus-5-5', worker, 'fix the lid', RESULT_SCHEMA, False)
        self.assertIsNone(receipt['error'])
        args = json.loads(out.read_text())['args']
        self.assertEqual(args[args.index('--resume') + 1], 'sess-old')
        with patch.dict(os.environ, {'PILOT_STUB_LOST_SESSION': '1'}):
            receipt = pilot.agent(self.root, {**self.state, 'resumeSession': 'sess-gone'}, 'case',
                                  'claude-opus-5-5', worker, 'fix the lid', RESULT_SCHEMA, False)
        self.assertIsNone(receipt['error'])
        calls = [json.loads(line) for line in Path(str(out) + '.all').read_text().splitlines()]
        self.assertIn('sess-gone', calls[-2])
        self.assertNotIn('--resume', calls[-1])
        seen = json.loads(out.read_text())
        self.assertEqual(seen['prompt'].count('macOS sandbox'), 1)

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').is_file(), 'needs macOS sandbox-exec')
    def test_a_claude_reader_never_keeps_or_resumes_a_session(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        out = self.claude_stub()
        pilot.agent(self.root, {**self.state, 'resumeSession': 'sess-old'}, 'review', 'claude-opus-5-5',
                    self.workspace, 'read only', RESULT_SCHEMA, True)
        args = json.loads(out.read_text())['args']
        self.assertIn('--no-session-persistence', args)
        self.assertNotIn('--resume', args)

    def test_call_hands_the_team_session_to_its_next_claude_job_in_the_same_revision(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        seen = []

        def runner(root, state, stage, model, workspace, prompt, schema, readonly):
            seen.append(state.get('resumeSession'))
            return {'stage': stage, 'model': model, 'startedAt': 0, 'seconds': 1, 'usage': None,
                    'result': {'contractSha256': 'h', 'stage': stage, 'summary': 's', 'blockers': []},
                    'error': None, 'sessionId': f'sess-{len(seen)}'}

        state = {**self.state, 'builderModels': {'case': 'claude-opus-5-5'}, 'jobs': [], 'revision': 1}
        pilot.call(self.root, state, 'case', self.workspace, 'build', RESULT_SCHEMA, False, runner)
        pilot.call(self.root, state, 'case', self.workspace, 'repair', RESULT_SCHEMA, False, runner)
        pilot.call(self.root, state, 'board', self.workspace, 'build', RESULT_SCHEMA, False, runner)
        self.assertEqual(seen[:2], [None, 'sess-1'])
        self.assertIsNone(seen[2], 'a session belongs to one team')
        state['revision'] = 2
        pilot.call(self.root, state, 'case', self.workspace, 'build', RESULT_SCHEMA, False, runner)
        self.assertIsNone(seen[3], 'a new contract revision starts fresh')

    def test_repair_context_pastes_cited_lines_and_names_the_handoff(self):
        tools = self.workspace / 'tools/case'
        tools.mkdir(parents=True, exist_ok=True)
        (tools / 'crab_geom.py').write_text(''.join(f'line {n}\n' for n in range(1, 301)))
        (self.base / 'secret.txt').write_text('outside')
        findings = [{'path': 'case/out/lid.step',
                     'message': 'shelf blocks the board (tools/case/crab_geom.py:150-156); '
                                'see ../secret.txt:1 and missing/file.py:3'}]
        text = pilot._repair_context(self.workspace, 'case', findings)
        self.assertIn('Read case/HANDOFF.md first', text)
        self.assertIn('--- tools/case/crab_geom.py:142-164', text)
        self.assertIn('  150  line 150', text)
        self.assertNotIn('line 141', text)
        self.assertNotIn('outside', text)
        self.assertNotIn('missing/file.py', text)
        self.assertEqual(pilot._cited_code(self.workspace, [{'message': 'no line cited'}]), '')

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').is_file(), 'needs macOS sandbox-exec')
    def test_claude_overload_is_capacity_and_missing_answer_is_an_error(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        self.claude_stub()
        with patch.dict(os.environ, {'PILOT_STUB_OVERLOADED': '1'}):
            busy = pilot.agent(self.root, self.state, 'review', 'claude-opus-5-5', self.workspace,
                               'read only', RESULT_SCHEMA, True)
        self.assertEqual(busy['failureKind'], 'model_capacity')
        with patch.dict(os.environ, {'PILOT_STUB_NO_ANSWER': '1'}):
            empty = pilot.agent(self.root, self.state, 'review', 'claude-opus-5-5', self.workspace,
                                'read only', RESULT_SCHEMA, True)
        self.assertIn('no structured answer', empty['error'])
        self.assertIsNone(empty['failureKind'])

    @unittest.skipUnless(Path('/usr/bin/sandbox-exec').is_file(), 'needs macOS sandbox-exec')
    def test_claude_logout_is_not_a_design_failure_and_says_why(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        self.claude_stub()
        with patch.dict(os.environ, {'PILOT_STUB_LOGGED_OUT': '1'}):
            receipt = pilot.agent(self.root, self.state, 'review', 'claude-opus-5-5', self.workspace,
                                  'read only', RESULT_SCHEMA, True)
        self.assertEqual(receipt['failureKind'], 'model_capacity')
        self.assertIn('Failed to authenticate', receipt['error'])

    def test_claude_sandbox_lets_a_refreshed_login_reach_the_keychain(self):
        profile = pilot._claude_sandbox(self.workspace)
        self.assertIn(f'(subpath "{Path.home() / "Library/Keychains"}")', profile)
        self.assertTrue(profile.endswith(f'(deny file-write* (subpath "{self.workspace.resolve()}"))'))

    def test_team_models_defaults_override_and_bad_file(self):
        project = self.base / 'project'
        defaults = {'lead': pilot.LEAD, 'tester': pilot.WORKER, 'integrationTester': None,
                    'advisor': None, 'builders': {}, 'testers': {}, 'caseDocs': False}
        self.assertEqual(pilot.team_models(project), defaults)
        put(project, str(pilot.TEAM_MODELS), json.dumps({'tester': 'claude-opus-5-5'}))
        self.assertEqual(pilot.team_models(project), {**defaults, 'tester': 'claude-opus-5-5'})
        chosen = {'builders': {'case': 'claude-opus-5-5'}, 'testers': {'case': pilot.WORKER},
                  'integrationTester': 'claude-opus-5-5', 'advisor': 'claude-fable-5-1'}
        put(project, str(pilot.TEAM_MODELS), json.dumps(chosen))
        self.assertEqual(pilot.team_models(project), {**defaults, **chosen})
        for bad in ({'worker': 'claude-opus-5-5'}, {'lead': ''}, ['claude-opus-5-5'],
                    {'builders': {'lid': 'claude-opus-5-5'}}, {'testers': {'case': ''}},
                    {'builders': 'claude-opus-5-5'}, {'advisor': ''}, {'caseDocs': ''},
                    {'caseDocs': 3}, {'builders': {'case-docs': 'claude-opus-5-5'}}):
            put(project, str(pilot.TEAM_MODELS), json.dumps(bad))
            with self.assertRaisesRegex(ValueError, 'integrationTester'):
                pilot.team_models(project)

    def test_case_docs_member_is_off_unless_chosen(self):
        project = self.base / 'project'
        self.assertIsNone(pilot.case_docs_model(pilot.team_models(project)))
        put(project, str(pilot.TEAM_MODELS), json.dumps({'caseDocs': False}))
        self.assertIsNone(pilot.case_docs_model(pilot.team_models(project)))
        # true keeps the case builder's model, so only the split changes
        put(project, str(pilot.TEAM_MODELS), json.dumps({'caseDocs': True,
                                                         'builders': {'case': 'claude-opus-5-5'}}))
        self.assertEqual(pilot.case_docs_model(pilot.team_models(project)), 'claude-opus-5-5')
        put(project, str(pilot.TEAM_MODELS), json.dumps({'caseDocs': True}))
        self.assertEqual(pilot.case_docs_model(pilot.team_models(project)), pilot.WORKER)
        put(project, str(pilot.TEAM_MODELS), json.dumps({'caseDocs': 'claude-sonnet-5-5'}))
        self.assertEqual(pilot.case_docs_model(pilot.team_models(project)), 'claude-sonnet-5-5')

    def test_job_logs_each_command_with_its_time(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                              'scoped task', RESULT_SCHEMA, False)
        self.assertEqual(receipt['commands']['count'], 1)
        logged = (self.root / receipt['logDirectory'] / 'commands.jsonl').read_text().splitlines()
        entry = json.loads(logged[0])
        self.assertEqual((entry['command'], entry['exitCode']), ('make check', 0))
        self.assertIn('turn.completed', (self.root / receipt['logDirectory'] / 'events.jsonl').read_text())

    def command_clock(self):
        job = self.base / 'clock'
        job.mkdir()
        return pilot._CommandClock(job / 'events.jsonl', job / 'commands.jsonl'), job / 'commands.jsonl'

    def test_command_clock_times_codex_commands(self):
        clock, log = self.command_clock()
        item = {'id': 'i', 'type': 'command_execution', 'command': 'python check.py'}
        clock.feed(json.dumps({'type': 'item.started', 'item': item}), 100)
        clock.feed('not json', 101)
        clock.feed(json.dumps({'type': 'item.completed', 'item': {**item, 'exit_code': 2}}), 160.5)
        entry = json.loads(log.read_text())
        self.assertEqual((entry['command'], entry['seconds'], entry['exitCode']), ('python check.py', 60.5, 2))

    def test_command_clock_times_claude_tools(self):
        clock, log = self.command_clock()
        use = lambda block: json.dumps({'type': 'assistant', 'message': {'content': [block]}})
        clock.feed(use({'type': 'tool_use', 'id': 'a', 'name': 'Bash', 'input': {'command': 'ls'}}), 10)
        clock.feed(use({'type': 'tool_use', 'id': 'b', 'name': 'Read', 'input': {'file_path': 'x.md'}}), 10)
        clock.feed(json.dumps({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'b'}, {'type': 'tool_result', 'tool_use_id': 'a'}]}}), 13)
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([(e['command'], e['seconds']) for e in entries], [('Read x.md', 3), ('ls', 3)])

    def test_command_clock_keeps_the_command_a_timeout_was_waiting_on(self):
        clock, log = self.command_clock()
        clock.feed(json.dumps({'type': 'item.started', 'item': {
            'id': 'i', 'type': 'command_execution', 'command': 'sweep'}}), time.time() - 30)
        clock.start()
        self.assertEqual(clock.finish()['count'], 1)
        entry = json.loads(log.read_text())
        self.assertTrue(entry['unfinished'])
        self.assertGreaterEqual(entry['seconds'], 29)

    def test_timeout_retains_partial_usage_and_kills_child_process(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        # Allow interpreter startup under host load; the child marker is delayed past this bound.
        self.state['timeoutSeconds'] = 2
        with patch.dict(os.environ, {'PILOT_STUB_HANG': '1'}):
            receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                  'scoped task', RESULT_SCHEMA, False)
        self.assertIsNotNone(receipt['error'])
        self.assertEqual(receipt['failureKind'], 'timeout')
        self.assertEqual(receipt['usage']['input_tokens'], 100)
        time.sleep(3.1)
        self.assertFalse((self.workspace / 'orphan-marker').exists())
        self.assertTrue((self.root / receipt['logDirectory'] / 'receipt.json').is_file())

    def test_a_silent_job_is_stopped_as_a_stalled_timeout(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        self.state['timeoutSeconds'] = 60
        with patch.dict(os.environ, {'PILOT_STUB_HANG': '1'}), patch.object(pilot, 'STALL_SECONDS', 1.5):
            receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                  'scoped task', RESULT_SCHEMA, False)
        self.assertEqual(receipt['failureKind'], 'timeout')
        self.assertIn('Stalled', receipt['error'])
        self.assertLess(receipt['seconds'], 15)

    def test_a_job_that_keeps_writing_events_is_not_a_stall(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        self.state['timeoutSeconds'] = 60
        with patch.dict(os.environ, {'PILOT_STUB_TICKS': '8'}), patch.object(pilot, 'STALL_SECONDS', 1.5):
            receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                  'scoped task', RESULT_SCHEMA, False)
        self.assertIsNone(receipt['error'])
        self.assertGreater(receipt['seconds'], 3)

    def test_heavy_stderr_does_not_block_the_job(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        self.state['timeoutSeconds'] = 30
        with patch.dict(os.environ, {'PILOT_STUB_NOISY': '1'}):
            receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                  'scoped task', RESULT_SCHEMA, False)
        self.assertIsNone(receipt['error'])
        self.assertEqual(len((self.root / receipt['logDirectory'] / 'stderr.log').read_text()), 300000)

    def test_structured_capacity_failure_is_classified_and_attempt_logs_are_distinct(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        with patch.dict(os.environ, {'PILOT_STUB_CAPACITY': '1'}):
            first = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                'scoped task', RESULT_SCHEMA, False)
            second = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                 'scoped task', RESULT_SCHEMA, False)
        self.assertEqual(first['failureKind'], 'model_capacity')
        self.assertEqual(second['failureKind'], 'model_capacity')
        self.assertNotEqual(first['logDirectory'], second['logDirectory'])
        for receipt in (first, second):
            directory = self.root / receipt['logDirectory']
            self.assertEqual(receipt['usage']['input_tokens'], 100)
            self.assertEqual(json.loads((directory / 'receipt.json').read_text())['failureKind'],
                             'model_capacity')
            self.assertIn('turn.failed', (directory / 'events.jsonl').read_text())

    def test_plain_capacity_text_does_not_trigger_retry_classification(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        self.assertFalse(pilot.capacity_event(json.dumps({
            'type': 'item.completed', 'item': {'text': 'Selected model is at capacity'}})))
        self.assertFalse(pilot.capacity_event('Selected model is at capacity'))
        with patch.dict(os.environ, {'PILOT_STUB_PLAIN_CAPACITY': '1'}):
            receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                  'scoped task', RESULT_SCHEMA, False)
        self.assertIsNone(receipt['failureKind'])
        self.assertIsNotNone(receipt['error'])

    def test_retry_decisions_are_written_to_each_attempt_receipt(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        with patch.dict(os.environ, {'PILOT_STUB_CAPACITY': '1', 'PILOT_STUB_SKIP_INVOCATION': '1'}), \
             patch.object(pilot.time, 'sleep'):
            receipt = pilot.call(self.root, self.state, 'worker', self.workspace,
                                 'scoped task', RESULT_SCHEMA, False, pilot.codex)
        self.assertEqual(receipt['failureKind'], 'model_capacity')
        self.assertEqual(len(self.state['jobs']), 3)
        self.assertEqual([job.get('retryScheduled', False) for job in self.state['jobs']],
                         [True, True, False])
        for job in self.state['jobs']:
            recorded = json.loads((self.root / job['logDirectory'] / 'receipt.json').read_text())
            self.assertEqual(recorded, job)

    def test_interrupted_cli_receipt_is_never_classified_as_capacity(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA

        class InterruptedProcess:
            pid = 424242
            args = ['codex']
            returncode = None
            calls = 0
            stdin = io.StringIO()

            def wait(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt
                return 0

        with patch.object(pilot.subprocess, 'Popen', return_value=InterruptedProcess()), \
             patch.object(pilot.os, 'killpg'):
            receipt = pilot.codex(self.root, self.state, 'worker', pilot.WORKER, self.workspace,
                                  'scoped task', RESULT_SCHEMA, False)
        self.assertEqual(receipt['failureKind'], 'interrupted')
        self.assertIsNotNone(receipt['error'])

    def test_capacity_retry_is_bounded_and_interruptible_with_all_usage_retained(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        calls = []

        def capacity(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(prompt)
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 1},
                    'result': None, 'error': 'Codex exited 1', 'failureKind': 'model_capacity'}

        with patch.object(pilot.time, 'sleep') as sleep:
            receipt = pilot.call(self.root, self.state, 'worker', self.workspace,
                                 'scoped task', RESULT_SCHEMA, False, capacity)
        self.assertEqual(receipt['failureKind'], 'model_capacity')
        self.assertEqual(len(calls), 3)
        self.assertEqual([j['attempt'] for j in self.state['jobs']], [1, 2, 3])
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(pilot.metrics(self.state)['reportedUsage']['input_tokens'], 30)
        self.assertIn('same sealed stage', calls[1])
        self.state['jobs'].clear()
        calls.clear()
        with patch.object(pilot.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                pilot.call(self.root, self.state, 'worker', self.workspace,
                           'scoped task', RESULT_SCHEMA, False, capacity)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.state['jobs']), 1)
        self.assertEqual(self.state['jobs'][0]['retrySuppressed'],
                         'Operator interrupted before retry')

    def test_partial_timeout_continues_once_with_shorter_per_attempt_limit(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        self.state['timeoutSeconds'] = 1800
        worker = self.worker_worktree()
        calls = []

        def timeout_then_finish(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append((state['timeoutSeconds'], prompt))
            if len(calls) == 1:
                put(workspace, 'firmware/partial.c', 'owned draft')
                return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                        'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 1},
                        'result': None, 'error': 'Command timed out after 1800 seconds',
                        'failureKind': 'timeout'}
            self.assertEqual((workspace / 'firmware/partial.c').read_text(), 'owned draft')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': {'input_tokens': 12, 'cached_input_tokens': 0, 'output_tokens': 2},
                    'result': {'done': True}, 'error': None}

        receipt = pilot.call(self.root, self.state, 'firmware', worker,
                             'scoped task', RESULT_SCHEMA, False, timeout_then_finish)
        self.assertIsNone(receipt['error'])
        self.assertEqual([limit for limit, _ in calls], [1800, 600])
        self.assertIn('time limit was reached after partial work', calls[1][1])
        self.assertEqual(self.state['timeoutSeconds'], 1800)
        self.assertEqual(self.state['jobs'][0]['error'], 'Command timed out after 1800 seconds')
        self.assertEqual(self.state['jobs'][0]['retryReason'], 'timeout')
        self.assertEqual(self.state['jobs'][1]['attemptTimeoutSeconds'], 600)
        self.assertEqual(pilot.metrics(self.state)['reportedUsage']['input_tokens'], 22)

    # Continuation rules, measured on the old half-hour attempt.
    @patch.object(pilot, 'LONG_STAGE_ATTEMPT_SECONDS', 1800)
    def test_board_timeout_continues_on_fresh_owned_progress(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        clock, limits = [0.0], []

        def progressing(root, state, stage, model, workspace, prompt, schema, readonly):
            limits.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            put(workspace, f'design/pass-{len(limits)}.kicad_pro', 'owned progress')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 1},
                    'result': {'done': True} if len(limits) == 4 else None,
                    'error': None if len(limits) == 4 else 'Command timed out',
                    'failureKind': None if len(limits) == 4 else 'timeout'}

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]):
            receipt = pilot.call(self.root, self.state, 'board', worker,
                                 'scoped task', RESULT_SCHEMA, False, progressing)
        self.assertIsNone(receipt['error'])
        self.assertEqual(limits, [1800, 1800, 1800, 1800])
        self.assertEqual([job['attempt'] for job in self.state['jobs']], [1, 2, 3, 4])
        self.assertTrue(all(job['continuedPartialWork'] for job in self.state['jobs'][:3]))
        self.assertEqual(pilot.metrics(self.state)['reportedUsage']['input_tokens'], 40)

    # Continuation rules, measured on the old half-hour attempt.
    @patch.object(pilot, 'LONG_STAGE_ATTEMPT_SECONDS', 1800)
    def test_board_timeout_stops_when_next_attempt_makes_no_new_progress(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        clock, limits = [0.0], []

        def stalls(root, state, stage, model, workspace, prompt, schema, readonly):
            limits.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            if len(limits) == 1:
                put(workspace, 'design/partial.kicad_pro', 'owned progress')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]):
            receipt = pilot.call(self.root, self.state, 'board', worker,
                                 'scoped task', RESULT_SCHEMA, False, stalls)
        self.assertEqual(limits, [1800, 1800])
        self.assertEqual(receipt['failureKind'], 'timeout')
        self.assertEqual(receipt['retrySuppressed'],
                         'Timed-out worker made no owned file progress')

    # Continuation rules, measured on the old half-hour attempt.
    @patch.object(pilot, 'LONG_STAGE_ATTEMPT_SECONDS', 1800)
    def test_board_timeout_does_not_count_same_bytes_with_new_mtime(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        clock, calls = [0.0], []

        def rewrites_same_file(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            path = put(workspace, 'design/partial.kicad_pro', 'unchanged bytes')
            os.utime(path, ns=(len(calls) * 1_000_000_000,) * 2)
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]):
            receipt = pilot.call(self.root, self.state, 'board', worker,
                                 'scoped task', RESULT_SCHEMA, False, rewrites_same_file)
        self.assertEqual(calls, [1800, 1800])
        self.assertEqual(receipt['retrySuppressed'],
                         'Timed-out worker made no owned file progress')

    # Continuation rules, measured on the old half-hour attempt.
    @patch.object(pilot, 'LONG_STAGE_ATTEMPT_SECONDS', 1800)
    def test_board_continuation_obeys_three_hour_wall_budget(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        clock, limits = [0.0], []

        def keeps_progressing(root, state, stage, model, workspace, prompt, schema, readonly):
            limits.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            put(workspace, f'design/pass-{len(limits)}.kicad_pro', 'owned progress')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]):
            receipt = pilot.call(self.root, self.state, 'board', worker,
                                 'scoped task', RESULT_SCHEMA, False, keeps_progressing)
        self.assertEqual(limits, [1800] * 6)
        self.assertEqual(clock[0], pilot.BOARD_STAGE_WALL_SECONDS)
        self.assertEqual(receipt['retrySuppressed'], 'Stage execution budget exhausted')
        self.assertEqual(len(self.state['jobs']), 6)

    @patch.object(pilot, 'LONG_STAGE_ATTEMPT_SECONDS', 10)
    def test_board_continuation_attempt_cap_is_explicit(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        self.state['timeoutSeconds'] = 10
        clock, calls = [0.0], []

        def keeps_progressing(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            put(workspace, f'design/pass-{len(calls)}.kicad_pro', 'owned progress')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]):
            receipt = pilot.call(self.root, self.state, 'board', worker,
                                 'scoped task', RESULT_SCHEMA, False, keeps_progressing)
        self.assertEqual(calls, [10] * (1 + pilot.BOARD_MAX_TIMEOUT_CONTINUATIONS))
        self.assertEqual(receipt['retrySuppressed'],
                         'Board continuation attempt limit exhausted')
        self.assertEqual(len(self.state['jobs']), len(calls))

    @patch.object(pilot, 'LONG_STAGE_ATTEMPT_SECONDS', 10)
    def test_case_gets_long_continuations_for_owned_progress(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        self.state['timeoutSeconds'] = 10
        clock, calls = [0.0], []

        def keeps_progressing(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            put(workspace, f'case/pass-{len(calls)}.py', 'owned progress')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]):
            receipt = pilot.call(self.root, self.state, 'case', worker,
                                 'scoped task', RESULT_SCHEMA, False, keeps_progressing)
        self.assertEqual(calls, [10] * (1 + pilot.BOARD_MAX_TIMEOUT_CONTINUATIONS))
        self.assertEqual(receipt['retrySuppressed'], 'Case continuation attempt limit exhausted')

    def test_firmware_still_gets_one_short_continuation(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        calls = []

        def keeps_progressing(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            put(workspace, f'firmware/pass-{len(calls)}.c', 'owned progress')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        pilot.call(self.root, self.state, 'firmware', worker,
                   'scoped task', RESULT_SCHEMA, False, keeps_progressing)
        self.assertEqual(calls, [1800, pilot.TIMEOUT_CONTINUATION_SECONDS])

    def test_firmware_prompts_point_at_shared_toolchain_cache(self):
        for mode in ('teams', 'parallel'):
            state = {**self.state, 'mode': mode, 'contractSha256': 'hash'}
            self.assertIn('PLATFORMIO_CORE_DIR', pilot.worker_prompt(state, 'firmware', 'firmware'))
            self.assertNotIn('PLATFORMIO_CORE_DIR', pilot.worker_prompt(state, 'case', 'case'))

    def test_team_builders_keep_a_handoff_note_for_the_next_job(self):
        state = {**self.state, 'mode': 'teams', 'contractSha256': 'hash'}
        for role, note in (('board', 'engineering/HANDOFF.md'), ('firmware', 'firmware/HANDOFF.md'),
                           ('case', 'case/HANDOFF.md')):
            self.assertIn(f'Keep {note} current', pilot.worker_prompt(state, role, role))

    def test_no_progress_timeout_is_terminal_and_combined_retries_are_capped(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        calls = []

        def no_progress(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        first = pilot.call(self.root, self.state, 'firmware', worker,
                           'scoped task', RESULT_SCHEMA, False, no_progress)
        self.assertEqual(first['failureKind'], 'timeout')
        self.assertEqual(first['retrySuppressed'], 'Timed-out worker made no owned file progress')
        self.assertEqual(calls, [1800])
        self.state['jobs'].clear()
        calls.clear()

        def mixed(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            if len(calls) == 3:
                put(workspace, 'firmware/partial.c', 'owned draft')
                kind, error = 'timeout', 'Command timed out after 1800 seconds'
            else:
                kind, error = 'model_capacity', 'Codex exited 1'
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': error, 'failureKind': kind}

        with patch.object(pilot.time, 'sleep') as sleep:
            last = pilot.call(self.root, self.state, 'firmware', worker,
                              'scoped task', RESULT_SCHEMA, False, mixed)
        self.assertEqual(last['failureKind'], 'model_capacity')
        self.assertEqual(calls, [1800, 1800, 1800, 600])
        self.assertEqual(len(self.state['jobs']), 4)
        self.assertEqual(sleep.call_count, 2)

    def test_capacity_retries_share_one_stage_wall_budget(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        self.state['timeoutSeconds'] = 1800
        clock = [0.0]
        limits = []

        def capacity(root, state, stage, model, workspace, prompt, schema, readonly):
            limits.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Codex exited 1',
                    'failureKind': 'model_capacity'}

        def backoff(seconds):
            clock[0] += seconds

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]), \
             patch.object(pilot.time, 'sleep', side_effect=backoff):
            receipt = pilot.call(self.root, self.state, 'firmware', worker,
                                 'scoped task', RESULT_SCHEMA, False, capacity)
        self.assertEqual(receipt['failureKind'], 'model_capacity')
        self.assertEqual(receipt['retrySuppressed'], 'Stage execution budget exhausted')
        self.assertEqual(len(limits), 2)
        self.assertEqual(limits[0], 1800)
        self.assertEqual(limits[1], 595)
        self.assertEqual(clock[0], 2400)

    # Continuation rules, measured on the old half-hour attempt.
    @patch.object(pilot, 'LONG_STAGE_ATTEMPT_SECONDS', 1800)
    def test_board_capacity_retries_keep_original_wall_budget(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        clock, limits = [0.0], []

        def capacity(root, state, stage, model, workspace, prompt, schema, readonly):
            limits.append(state['timeoutSeconds'])
            clock[0] += state['timeoutSeconds']
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Codex exited 1',
                    'failureKind': 'model_capacity'}

        with patch.object(pilot, '_stage_clock', side_effect=lambda: clock[0]), \
             patch.object(pilot.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            receipt = pilot.call(self.root, self.state, 'board', worker,
                                 'scoped task', RESULT_SCHEMA, False, capacity)
        self.assertEqual(receipt['failureKind'], 'model_capacity')
        self.assertEqual(receipt['retrySuppressed'], 'Stage execution budget exhausted')
        self.assertEqual(limits, [1800, 595])
        self.assertEqual(clock[0], 2400)


if __name__ == '__main__':
    unittest.main()
