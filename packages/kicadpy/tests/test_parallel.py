"""Offline orchestration tests: no real models, native CAD, network or hardware."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from kicadpy import parallel as pilot
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
        self.assertLess(stages.index('board'), stages.index('firmware'))
        self.assertLess(stages.index('board'), stages.index('case'))
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
            receipt = json.loads((self.root / f".pilot/rounds/{state['revision']}/{stage}-check.json").read_text())
            seen.append({
                'stage': stage,
                'workspace': workspace,
                'marker': (workspace / 'boards/checkpoint.txt').read_text(),
                'receipt': receipt,
                'commit': pilot.git(workspace, 'log', '-1', '--format=%s'),
                'dirty': pilot.git(workspace, 'status', '--porcelain'),
                'status': state['status'],
                'reviewed': [name for name, _, _ in self.fake.visits if name.endswith('-review')],
                'verdictExists': (workspace / '.harness/verdict.json').exists(),
            })

        state = self.run_round(checker=checked, on_checkpoint=checkpoint)
        self.assertEqual(state['status'], 'candidate_ready', state['findings'])
        self.assertEqual([event['stage'] for event in seen], ['board', 'final'])
        self.assertEqual([event['marker'] for event in seen], ['board native', 'final native'])
        self.assertEqual([event['commit'] for event in seen],
                         ['Record native board publication', 'Record final native checks'])
        self.assertEqual([event['reviewed'] for event in seen], [[], ['structure-review']])
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
        self.assertEqual([stage for stage, _, _ in seen], ['board'])
        self.assertFalse(seen[0][1]['ready'])
        self.assertFalse(seen[0][2]['passed'])
        self.assertNotIn('firmware', [name for name, _, _ in self.fake.visits])
        self.assertNotIn('case', [name for name, _, _ in self.fake.visits])
        self.assertNotIn('structure-review', [name for name, _, _ in self.fake.visits])
        self.assertNotIn('case-final', [name for name, _, _ in self.fake.visits])

    def test_checkpoint_error_fails_closed_before_lead_review(self):
        self.plan()

        def broken(workspace, state, stage):
            raise RuntimeError('bridge copy failed')

        state = self.run_round(on_checkpoint=broken)
        self.assertEqual(state['status'], 'needs_revision')
        self.assertIn('bridge copy failed', state['findings'][0])
        self.assertNotIn('structure-review', [name for name, _, _ in self.fake.visits])

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

    def test_cross_role_edit_prevents_both_consumer_imports(self):
        self.plan()
        def defect(stage, workspace, result):
            if stage == 'case':
                put(workspace, 'firmware/main.c', 'wrong owner')
        self.fake.defect = defect
        result = self.run_round()
        self.assertEqual(result['status'], 'needs_revision')
        self.assertIn('unowned', result['findings'][0])
        self.assertTrue((self.root / 'workspace/design/main.kicad_pro').exists())
        self.assertFalse((self.root / 'workspace/firmware/main.c').exists())
        self.assertFalse((self.root / 'workspace/case/frame.txt').exists())
        self.assertEqual(len(result['jobs']), 4)  # all costs retained despite rejecting imports

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
        self.assertFalse((self.root / 'workspace/case/frame.txt').exists())

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
        self.assertTrue((self.root / 'workspace/case/frame.txt').is_file())
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
        self.assertFalse((self.root / 'workspace/firmware/main.c').exists())

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

    def test_worker_shared_git_change_is_rejected_before_import(self):
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
        self.assertFalse((self.root / 'workspace/firmware/main.c').exists())

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
    }))
print(json.dumps({'type':'turn.completed','usage':{
    'input_tokens':100,'cached_input_tokens':60,'output_tokens':20}}), flush=True)
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
        self.assertIn('--ephemeral', args)
        self.assertIn('approval_policy="never"', args)
        self.assertEqual(invocation['advisor'], 'off')
        self.assertIsNone(invocation['hostWorkspace'])
        self.assertIsNone(invocation['thread'])
        self.assertEqual(Path(invocation['blocks']), ROOT / 'packages/golden-blocks/blocks')
        self.assertEqual(Path(invocation['cmake']), ROOT / 'toolchain/pico/tools/bin/cmake')
        lead = pilot.codex(self.root, self.state, 'review', pilot.LEAD, self.workspace,
                           'read only', RESULT_SCHEMA, True)
        self.assertIsNone(lead['error'])
        invocation = json.loads((self.workspace / 'invocation.json').read_text())
        self.assertEqual(invocation['args'][invocation['args'].index('-s') + 1], 'read-only')

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
            returncode = None
            calls = 0

            def communicate(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt
                return '', ''

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
                put(workspace, 'design/partial.kicad_pro', 'owned draft')
                return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                        'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 1},
                        'result': None, 'error': 'Command timed out after 1800 seconds',
                        'failureKind': 'timeout'}
            self.assertEqual((workspace / 'design/partial.kicad_pro').read_text(), 'owned draft')
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': {'input_tokens': 12, 'cached_input_tokens': 0, 'output_tokens': 2},
                    'result': {'done': True}, 'error': None}

        receipt = pilot.call(self.root, self.state, 'board', worker,
                             'scoped task', RESULT_SCHEMA, False, timeout_then_finish)
        self.assertIsNone(receipt['error'])
        self.assertEqual([limit for limit, _ in calls], [1800, 600])
        self.assertIn('time limit was reached after partial work', calls[1][1])
        self.assertEqual(self.state['timeoutSeconds'], 1800)
        self.assertEqual(self.state['jobs'][0]['error'], 'Command timed out after 1800 seconds')
        self.assertEqual(self.state['jobs'][0]['retryReason'], 'timeout')
        self.assertEqual(self.state['jobs'][1]['attemptTimeoutSeconds'], 600)
        self.assertEqual(pilot.metrics(self.state)['reportedUsage']['input_tokens'], 22)

    def test_no_progress_timeout_is_terminal_and_combined_retries_are_capped(self):
        from kicadpy.parallel_contract import RESULT_SCHEMA
        worker = self.worker_worktree()
        calls = []

        def no_progress(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': 'Command timed out',
                    'failureKind': 'timeout'}

        first = pilot.call(self.root, self.state, 'board', worker,
                           'scoped task', RESULT_SCHEMA, False, no_progress)
        self.assertEqual(first['failureKind'], 'timeout')
        self.assertEqual(first['retrySuppressed'], 'Timed-out worker made no owned file progress')
        self.assertEqual(calls, [1800])
        self.state['jobs'].clear()
        calls.clear()

        def mixed(root, state, stage, model, workspace, prompt, schema, readonly):
            calls.append(state['timeoutSeconds'])
            if len(calls) == 3:
                put(workspace, 'design/partial.kicad_pro', 'owned draft')
                kind, error = 'timeout', 'Command timed out after 1800 seconds'
            else:
                kind, error = 'model_capacity', 'Codex exited 1'
            return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
                    'usage': None, 'result': None, 'error': error, 'failureKind': kind}

        with patch.object(pilot.time, 'sleep') as sleep:
            last = pilot.call(self.root, self.state, 'board', worker,
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
            receipt = pilot.call(self.root, self.state, 'board', worker,
                                 'scoped task', RESULT_SCHEMA, False, capacity)
        self.assertEqual(receipt['failureKind'], 'model_capacity')
        self.assertEqual(receipt['retrySuppressed'], 'Stage execution budget exhausted')
        self.assertEqual(len(limits), 2)
        self.assertEqual(limits[0], 1800)
        self.assertEqual(limits[1], 595)
        self.assertEqual(clock[0], 2400)


if __name__ == '__main__':
    unittest.main()
