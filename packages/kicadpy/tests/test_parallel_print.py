"""Offline print-team evidence and bridge tests; no printer or account is contacted."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from kicadpy import parallel, parallel_addendum, parallel_print, parallel_tile
from test_parallel_addendum import interfaces
from test_parallel_teams import TeamRunner, checker, put


ROOT = Path(__file__).resolve().parents[3]
H = lambda value: hashlib.sha256(value.encode()).hexdigest()


def receipt(stage, model, result):
    return {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': .01,
            'usage': None, 'result': result, 'error': None}


class PrintRunner:
    def __init__(self, action='ask_print', *, confirmation='none', observation='none',
                 reject=False):
        self.action = action
        self.confirmation = confirmation
        self.observation = observation
        self.reject = reject
        self.visits = []

    def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
        self.visits.append(stage)
        if stage.startswith('lead-addendum-'):
            result = {'routes': ['print'], 'note': 'Track the print with the person.',
                      'changedFields': []}
        elif stage.startswith('print-plan-'):
            result = {'contractSha256': state['contractSha256'], 'action': self.action,
                      'summary': 'Ask about the checked plate' if self.action == 'ask_print'
                      else 'Record exactly what the person reported',
                      'question': 'Print checked plate 1?' if self.action == 'ask_print' else '-',
                      'plateIndex': 0 if self.action in ('observe', 'wait') else 1,
                      'parts': [] if self.action in ('observe', 'wait') else
                      [{'name': 'shell', 'quantity': 1, 'instanceIds': []}],
                      'confirmationStatus': self.confirmation,
                      'observationStatus': self.observation,
                      'printerJobId': 'lan-42' if self.action == 'observe' else '-'}
        elif stage.startswith('print-review-'):
            result = {'contractSha256': state['contractSha256'],
                      'decision': 'revise' if self.reject else 'approve',
                      'summary': 'Independent print review',
                      'findings': ['No physical confirmation in user message'] if self.reject else []}
        else:
            raise AssertionError(f'Unexpected model stage {stage}')
        return receipt(stage, model, result)


class PrintTeamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.visible = self.base / 'visible'
        self.visible.mkdir()
        self.prompt = put(self.base, 'prompt.txt', 'Design a sensor case.')
        self.pilot = self.base / 'pilot'
        parallel.initialize(self.pilot, self.prompt, instructions=ROOT / 'harness/kicad', mode='teams')
        root, state = parallel.load(self.pilot)
        parallel.seal(root, state, interfaces())
        parallel.write(root / '.pilot/state.json', state)
        self.case = {'revision': 'r1', 'manifestSha256': H('manifest'),
                     'printProjectSha256': H('3mf'),
                     'parts': {'shell': {'name': 'shell', 'revision': 'r1',
                                         'sha256STL': H('shell-stl'), 'quantity': 1}},
                     'plates': {1: ['shell']}}
        self.case_patch = patch.object(parallel_print, '_case_snapshot', return_value=self.case)
        self.case_patch.start()
        self.addCleanup(self.case_patch.stop)

    def note(self, text):
        file = put(self.base, 'message.txt', text)
        return parallel_addendum.record_addendum(self.pilot, file, runner=PrintRunner())

    def test_ask_proposal_is_bound_to_case_but_does_not_claim_printed(self):
        note = self.note('Can you help me print the case?')
        runner = PrintRunner()
        result = parallel_print.run_print_team(self.visible, self.pilot, note, runner=runner,
                                               account_snapshot={'status': 'unavailable', 'jobs': []})
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['question'], 'Print checked plate 1?')
        self.assertEqual([v.split('-')[0] for v in runner.visits], ['print', 'print'])
        event = parallel_print._read_ledger(self.visible, note['contractSha256'])[0]
        self.assertEqual(event['case']['printProjectSha256'], H('3mf'))
        self.assertEqual(event['parts'][0]['sha256STL'], H('shell-stl'))
        self.assertEqual(event['kind'], 'proposal')

    def test_exact_user_confirmation_is_incremental_and_idempotent(self):
        note = self.note('I printed one shell from checked plate 1.')
        runner = PrintRunner('record_confirmation', confirmation='printed')
        first = parallel_print.run_print_team(self.visible, self.pilot, note, runner=runner,
                                              account_snapshot={'status': 'unavailable', 'jobs': []})
        second = parallel_print.run_print_team(self.visible, self.pilot, note, runner=runner,
                                               account_snapshot={'status': 'unavailable', 'jobs': []})
        self.assertEqual(first['event'], second['event'])
        self.assertEqual(len(parallel_print._read_ledger(self.visible, note['contractSha256'])), 1)
        self.assertEqual(len(first['event']['parts'][0]['instanceIds']), 1)
        self.assertEqual(first['event']['evidence']['sha256'], note['userTextSha256'])

    def test_physical_confirmation_requires_verified_plate(self):
        note = self.note('I printed one shell.')

        class MissingPlateRunner(PrintRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                answer = super().__call__(root, state, stage, model, workspace, prompt,
                                          schema, readonly)
                if stage.startswith('print-plan-'):
                    answer['result']['plateIndex'] = 0
                return answer

        with self.assertRaisesRegex(ValueError, 'verified print plate'):
            parallel_print.run_print_team(
                self.visible, self.pilot, note,
                runner=MissingPlateRunner('record_confirmation', confirmation='printed'),
                account_snapshot={'status': 'unavailable', 'jobs': []})
        self.assertFalse((self.visible / parallel_print.LEDGER).exists())

    def test_lan_completed_job_is_observation_without_physical_parts(self):
        note = self.note('Check the printer status.')
        runner = PrintRunner('observe', observation='completed')
        result = parallel_print.run_print_team(
            self.visible, self.pilot, note, runner=runner,
            account_snapshot={'status': 'connected', 'jobs': [
                {'id': 'lan-42', 'status': 'completed', 'fileName': 'case',
                 'plateIndex': None, 'projectSha256': None}]})
        event = result['event']
        self.assertEqual(event['kind'], 'printer_observation')
        self.assertEqual(event['status'], 'completed')
        self.assertEqual(event['parts'], [])
        self.assertIsNone(event['plateIndex'])
        self.assertEqual(event['evidence']['kind'], 'printer_lan')

    def test_reviewer_rejection_does_not_write_a_physical_claim(self):
        note = self.note('Check whether the print is done.')
        runner = PrintRunner('record_confirmation', confirmation='printed', reject=True)
        with self.assertRaisesRegex(ValueError, 'reviewer rejected'):
            parallel_print.run_print_team(self.visible, self.pilot, note, runner=runner,
                                          account_snapshot={'status': 'unavailable', 'jobs': []})
        self.assertFalse((self.visible / parallel_print.LEDGER).exists())

    def test_ledger_tampering_is_detected(self):
        note = self.note('Can you help me print the case?')
        parallel_print.run_print_team(self.visible, self.pilot, note, runner=PrintRunner(),
                                      account_snapshot={'status': 'unavailable', 'jobs': []})
        path = self.visible / parallel_print.LEDGER
        path.write_text(path.read_text().replace('proposed', 'printed'))
        with self.assertRaisesRegex(ValueError, 'ledger chain'):
            parallel_print._read_ledger(self.visible, note['contractSha256'])

    def test_torn_ledger_line_is_rejected(self):
        note = self.note('Can you help me print the case?')
        parallel_print.run_print_team(self.visible, self.pilot, note, runner=PrintRunner(),
                                      account_snapshot={'status': 'unavailable', 'jobs': []})
        path = self.visible / parallel_print.LEDGER
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        with self.assertRaisesRegex(ValueError, 'incomplete final line'):
            parallel_print._read_ledger(self.visible, note['contractSha256'])


class PrintCaseEvidenceTests(unittest.TestCase):
    def test_local_printer_snapshot_exposes_progress_without_credentials_or_part_match(self):
        observed = {'account': {'state': 'connected', 'accessCode': 'secret'}, 'jobs': [{
            'jobId': 'lan-42', 'status': 'printing', 'jobName': 'shell',
            'progressPct': 42, 'remainingMinutes': 17, 'currentLayer': 20,
            'totalLayers': 48, 'observedAt': '2026-10-28T00:00:00Z',
            'accessCode': 'secret'}]}
        process = subprocess.CompletedProcess([], 0, stdout=json.dumps(observed), stderr='')
        with patch.object(parallel_print.subprocess, 'run', return_value=process):
            snapshot = parallel_print._account_snapshot(parallel_print._local_account_snapshot())
        self.assertEqual(snapshot['jobs'][0]['progressPct'], 42)
        self.assertEqual(snapshot['jobs'][0]['remainingMinutes'], 17)
        self.assertEqual(snapshot['jobs'][0]['currentLayer'], 20)
        self.assertIsNone(snapshot['jobs'][0]['projectSha256'])
        self.assertIsNone(snapshot['jobs'][0]['plateIndex'])
        self.assertNotIn('secret', json.dumps(snapshot))

    def test_verified_case_uses_actual_stl_hash_and_accepts_case_print_directory(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        stl = put(workspace, 'case/out/shell.stl', 'solid shell\nendsolid shell\n')
        project = put(workspace, 'case/print/production.3mf', 'fake 3mf checked by mocked native gate')
        put(workspace, 'case/print/production-check.json', json.dumps({
            'plates': [{'index': 1, 'parts': ['shell.stl']}]}))
        manifest = {'revision': 'r1', 'parts': [{'name': 'shell', 'revision': 'r1',
                                                'stl': 'shell.stl', 'quantity': 1,
                                                'printable': True}],
                    'printProject': {'file': '../print/production.3mf',
                                     'report': '../print/production-check.json'}}
        put(workspace, 'case/out/parts.json', json.dumps(manifest))
        put(workspace, '.harness/verdict.json', '{"ready":true}')
        passed = subprocess.CompletedProcess([], 0, stdout='{"state":"passed","boardReady":true}', stderr='')
        with patch.object(parallel_print.subprocess, 'run', return_value=passed), \
                patch.object(parallel_print.parallel, 'check_plates'):
            case = parallel_print._case_snapshot(workspace)
            self.assertEqual(case['parts']['shell']['sha256STL'],
                             hashlib.sha256(stl.read_bytes()).hexdigest())
            self.assertEqual(case['printProjectSha256'], hashlib.sha256(project.read_bytes()).hexdigest())
            manifest['parts'][0]['sha256STL'] = H('wrong')
            put(workspace, 'case/out/parts.json', json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'mesh hash differs'):
                parallel_print._case_snapshot(workspace)
            stale = subprocess.CompletedProcess([], 0, stdout='{"state":"stale"}', stderr='')
            with patch.object(parallel_print.subprocess, 'run', return_value=stale):
                self.assertIsNone(parallel_print._case_snapshot(workspace))
            stale_board = subprocess.CompletedProcess([], 0, stdout='{"state":"passed","boardReady":false}', stderr='')
            with patch.object(parallel_print.subprocess, 'run', return_value=stale_board):
                self.assertIsNone(parallel_print._case_snapshot(workspace))


class PrintOnlyBridgeTests(unittest.TestCase):
    def project(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        visible = base / 'project'
        visible.mkdir()
        for name in ('product.json', 'project.json'):
            shutil.copy2(ROOT / 'harness/kicad/template' / name, visible / name)
        put(visible, '.circuit/team-auto', '')
        return base, visible, put(base, 'first.md', 'Build board, firmware and case.')

    def test_print_only_followup_preserves_ready_and_next_design_can_build(self):
        base, visible, first_prompt = self.project()

        class RoutedTeamRunner(TeamRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage.startswith('lead-addendum-'):
                    routes = ['print'] if 'print' in prompt.lower() else ['case']
                    return receipt(stage, model, {'routes': routes, 'note': 'Route the exact change.',
                                                   'changedFields': []})
                return super().__call__(root, state, stage, model, workspace, prompt, schema,
                                        readonly)

        runner = RoutedTeamRunner()
        runner.wait_for_three = False
        with patch.dict(os.environ, {'CIRCUIT_TEAM_MIN_FREE_GB': '0',
                                     'KICAD_HARNESS_ROOT': str(ROOT)}), \
                patch.object(parallel_print, 'run_print_team',
                             return_value={'status': 'proposed', 'question': 'Print plate 1?'}) as print_run:
            first = parallel_tile.start(visible, first_prompt, execute=True, teams=True,
                                        runner=runner, checker=checker)
            self.assertTrue(first['teamReady'], first.get('error'))
            contract = (visible / '.circuit/team-runs/r001/workspace/PILOT_CONTRACT.json').read_bytes()
            print_note = put(base, 'print.md', 'Please check the print plan.')
            before = len([stage for stage, *_ in runner.visits if stage in ('board', 'firmware', 'case')])
            second = parallel_tile.start(visible, print_note, execute=True, teams=True,
                                         runner=runner, checker=checker)
            after = len([stage for stage, *_ in runner.visits if stage in ('board', 'firmware', 'case')])
            self.assertTrue(second['teamReady'], second.get('error'))
            self.assertEqual(before, after)
            self.assertEqual(second['stage'], 'print')
            self.assertEqual(print_run.call_count, 1)
            self.assertEqual((visible / '.circuit/team-runs/r002/workspace/PILOT_CONTRACT.json').read_bytes(),
                             contract)
            self.assertEqual(len(parallel_addendum.load_addenda(
                visible / '.circuit/team-runs/r002')['entries']), 1)
            design_note = put(base, 'case.md', 'Please change the case frame.')
            third = parallel_tile.start(visible, design_note, execute=True, teams=True,
                                        runner=runner, checker=checker)
            self.assertTrue(third['teamReady'], third.get('error'))
            self.assertEqual(len(parallel_addendum.load_addenda(
                visible / '.circuit/team-runs/r003')['entries']), 2)
            self.assertEqual((visible / '.circuit/team-runs/r003/workspace/PILOT_CONTRACT.json').read_bytes(),
                             contract)

    def test_failed_print_only_keeps_design_ready_and_cli_reports_failure(self):
        base, visible, first_prompt = self.project()

        class RoutedRunner(TeamRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage.startswith('lead-addendum-'):
                    return receipt(stage, model, {'routes': ['print'], 'note': 'Print status.',
                                                   'changedFields': []})
                return super().__call__(root, state, stage, model, workspace, prompt,
                                        schema, readonly)

        runner = RoutedRunner()
        runner.wait_for_three = False
        with patch.dict(os.environ, {'CIRCUIT_TEAM_MIN_FREE_GB': '0',
                                     'KICAD_HARNESS_ROOT': str(ROOT)}), \
                patch.object(parallel_print, '_case_snapshot', return_value=None):
            first = parallel_tile.start(visible, first_prompt, execute=True, teams=True,
                                        runner=runner, checker=checker)
            self.assertTrue(first['teamReady'], first.get('error'))
            note = put(base, 'print.md', 'Check my print status.')
            with patch.object(parallel_print, 'run_print_team', side_effect=ValueError('review rejected')):
                failed = parallel_tile.start(visible, note, execute=True, teams=True,
                                             runner=runner, checker=checker)
            self.assertEqual(failed['status'], 'candidate_ready')
            self.assertTrue(failed['teamReady'])
            self.assertFalse(failed['printTeam']['ok'])
            self.assertEqual(len(failed['pendingPrintNotes']), 1)
            with patch.object(parallel_tile, 'start', return_value=failed), redirect_stdout(io.StringIO()):
                self.assertEqual(parallel_tile.main(['start', str(visible), '--prompt', str(note),
                                                     '--execute']), 2)
            with patch.object(parallel_print, 'run_print_team',
                              return_value={'status': 'unavailable', 'summary': 'Still waiting'}) as print_run:
                resumed = parallel_tile.resume(visible, execute=True, runner=runner, checker=checker)
            self.assertTrue(resumed['teamReady'], resumed.get('error'))
            self.assertTrue(resumed['printTeam']['ok'])
            self.assertEqual(resumed['pendingPrintNotes'], [])
            self.assertEqual(print_run.call_count, 1)
            self.assertEqual(len([stage for stage, *_ in runner.visits
                                  if stage in ('board', 'firmware', 'case')]), 3)

    def test_failed_initial_print_handoff_resumes_without_new_design(self):
        base, visible, first_prompt = self.project()
        runner = TeamRunner()
        runner.wait_for_three = False
        case = {'revision': 'r1', 'manifestSha256': H('manifest'),
                'printProjectSha256': H('3mf'),
                'parts': {'shell': {'name': 'shell', 'revision': 'r1',
                                    'sha256STL': H('shell-stl'), 'quantity': 1}},
                'plates': {1: ['shell']}}
        with patch.dict(os.environ, {'CIRCUIT_TEAM_MIN_FREE_GB': '0',
                                     'KICAD_HARNESS_ROOT': str(ROOT)}), \
                patch.object(parallel_print, '_case_snapshot', return_value=case), \
                patch.object(parallel_print, 'run_print_team', side_effect=ValueError('capacity')):
            first = parallel_tile.start(visible, first_prompt, execute=True, teams=True,
                                        runner=runner, checker=checker)
        self.assertTrue(first['teamReady'])
        self.assertFalse(first['printTeam']['ok'])
        contract_sha = json.loads((visible / '.circuit/team-runs/r001/.pilot/state.json').read_text())['contractSha256']
        self.assertEqual(first['pendingPrintNotes'], [contract_sha])
        print_runner = PrintRunner()
        with patch.dict(os.environ, {'CIRCUIT_TEAM_MIN_FREE_GB': '0',
                                     'KICAD_HARNESS_ROOT': str(ROOT)}), \
                patch.object(parallel_print, '_case_snapshot', return_value=case), \
                patch.object(parallel_print, '_local_account_snapshot', return_value=None):
            resumed = parallel_tile.resume(visible, execute=True, runner=print_runner,
                                           checker=checker)
        self.assertTrue(resumed['teamReady'], resumed.get('error'))
        self.assertTrue(resumed['printTeam']['ok'], resumed['printTeam'])
        self.assertEqual(resumed['pendingPrintNotes'], [])
        self.assertEqual(resumed['status'], 'candidate_ready')
        self.assertEqual(len(print_runner.visits), 2)
        self.assertEqual(len([stage for stage, *_ in runner.visits
                              if stage in ('board', 'firmware', 'case')]), 3)

    def test_mixed_change_waits_for_case_review_before_print_team(self):
        base, visible, first_prompt = self.project()

        class MixedRunner(TeamRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage.startswith('lead-addendum-'):
                    return receipt(stage, model, {'routes': ['case', 'print'],
                                                   'note': 'Revise case before printing.',
                                                   'changedFields': []})
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = MixedRunner()
        runner.wait_for_three = False
        with patch.dict(os.environ, {'CIRCUIT_TEAM_MIN_FREE_GB': '0',
                                     'KICAD_HARNESS_ROOT': str(ROOT)}), \
                patch.object(parallel_print, '_case_snapshot', return_value=None):
            first = parallel_tile.start(visible, first_prompt, execute=True, teams=True,
                                        runner=runner, checker=checker)
            self.assertTrue(first['teamReady'], first.get('error'))
            change = put(base, 'mixed.md', 'Change the case, then plan its print.')

            def reject_case(workspace, complete):
                result = checker(workspace, complete)
                if complete:
                    result['passed'] = False
                    result['findings'] = ['Case review needs revision']
                return result

            with patch.object(parallel_print, 'run_print_team') as print_run:
                failed = parallel_tile.start(visible, change, execute=True, teams=True,
                                             runner=runner, checker=reject_case)
                print_run.assert_not_called()
            self.assertFalse(failed['teamReady'])
            self.assertFalse(failed['printTeam']['ok'])
            self.assertEqual(failed['printTeam']['results'][0]['status'], 'pending')
            self.assertEqual(len(failed['pendingPrintNotes']), 1)

            with patch.object(parallel_print, 'run_print_team',
                              return_value={'status': 'proposed', 'question': 'Print new plate?'}) as print_run:
                resumed = parallel_tile.resume(visible, execute=True, runner=runner,
                                               checker=checker)
            self.assertTrue(resumed['teamReady'], resumed.get('error'))
            self.assertTrue(resumed['printTeam']['ok'])
            self.assertEqual(resumed['pendingPrintNotes'], [])
            print_run.assert_called_once()

    def test_print_only_between_design_rounds_preserves_draft_origin(self):
        base, visible, first_prompt = self.project()

        class RoutedRunner(TeamRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage.startswith('lead-addendum-'):
                    routes = ['print'] if 'print status' in prompt.lower() else ['case']
                    return receipt(stage, model, {'routes': routes, 'note': 'Route the change.',
                                                   'changedFields': []})
                return super().__call__(root, state, stage, model, workspace,
                                        prompt, schema, readonly)

        runner = RoutedRunner()
        runner.wait_for_three = False
        with patch.dict(os.environ, {'CIRCUIT_TEAM_MIN_FREE_GB': '0',
                                     'KICAD_HARNESS_ROOT': str(ROOT)}), \
                patch.object(parallel_print, '_case_snapshot', return_value=None):
            first = parallel_tile.start(visible, first_prompt, execute=True, teams=True,
                                        runner=runner, checker=checker)
            self.assertTrue(first['teamReady'], first.get('error'))

            def reject_case(workspace, complete):
                result = checker(workspace, complete)
                if complete:
                    result['passed'] = False
                    result['findings'] = ['Case pending']
                return result

            change = put(base, 'change.md', 'Change the case shell.')
            failed = parallel_tile.start(visible, change, execute=True, teams=True,
                                         runner=runner, checker=reject_case)
            self.assertFalse(failed['teamReady'])
            print_note = put(base, 'print.md', 'Check print status.')
            with patch.object(parallel_print, 'run_print_team',
                              return_value={'status': 'unavailable'}):
                only = parallel_tile.start(visible, print_note, execute=True, teams=True,
                                           runner=runner, checker=checker)
            self.assertEqual(only['stage'], 'print')
            self.assertFalse(only['teamReady'])
            self.assertEqual(json.loads((visible / '.circuit/team-runs/r003/.pilot/state.json').read_text())
                             ['draftOriginRun'], 'r002')
            resumed = parallel_tile.resume(visible, execute=True, runner=runner,
                                           checker=checker)
            self.assertTrue(resumed['teamReady'], resumed.get('error'))
            self.assertEqual(json.loads((visible / '.circuit/team-runs/r004/.pilot/state.json').read_text())
                             ['draftOriginRun'], 'r002')

    def test_lead_question_sees_current_ledger_and_sanitized_printer_progress(self):
        base, visible, first_prompt = self.project()
        runner = TeamRunner()
        runner.wait_for_three = False
        with patch.dict(os.environ, {'CIRCUIT_TEAM_MIN_FREE_GB': '0',
                                     'KICAD_HARNESS_ROOT': str(ROOT)}), \
                patch.object(parallel_print, '_case_snapshot', return_value=None):
            first = parallel_tile.start(visible, first_prompt, execute=True, teams=True,
                                        runner=runner, checker=checker)
        self.assertTrue(first['teamReady'])
        pilot = visible / '.circuit/team-runs/r001'
        note = parallel_print.initial_print_note(pilot)
        case = {'revision': 'r1', 'manifestSha256': H('manifest'),
                'printProjectSha256': H('3mf'),
                'parts': {'shell': {'name': 'shell', 'revision': 'r1',
                                    'sha256STL': H('shell-stl'), 'quantity': 1}},
                'plates': {1: ['shell']}}
        with patch.object(parallel_print, '_case_snapshot', return_value=case):
            parallel_print.run_print_team(visible, pilot, note, runner=PrintRunner(),
                                          account_snapshot={'status': 'unavailable', 'jobs': []})

        class AskRunner(TeamRunner):
            def __call__(self, root, state, stage, model, workspace, prompt, schema, readonly):
                if stage == 'ask':
                    ledger = workspace / 'PROJECT_ARTIFACTS/print/events.jsonl'
                    assert ledger.is_file()
                    assert 'proposed' in ledger.read_text()
                    inventory = json.loads((workspace / 'PRINT_INVENTORY.json').read_text())
                    assert inventory['status'] == 'verified_user_reports'
                    assert inventory['eventCount'] == 1
                    assert inventory['parts'] == []
                    account = json.loads((workspace / 'PRINT_ACCOUNT_STATUS.json').read_text())
                    assert account['jobs'][0]['progressPct'] == 42
                    assert account['jobs'][0]['currentLayer'] == 20
                    assert 'accessCode' not in json.dumps(account)
                    assert 'secret' not in json.dumps(account)
                return super().__call__(root, state, stage, model, workspace, prompt,
                                        schema, readonly)

        observation = {'status': 'connected', 'jobs': [{
            'id': 'lan-42', 'status': 'printing', 'fileName': 'shell',
            'plateIndex': None, 'projectSha256': None,
            'progressPct': 42, 'remainingMinutes': 17,
            'currentLayer': 20, 'totalLayers': 48,
            'observedAt': '2026-10-28T00:00:00Z', 'accessCode': 'secret'}],
            'accessCode': 'secret'}
        with patch.object(parallel_print, '_local_account_snapshot', return_value=observation):
            answer = parallel_tile.ask(visible, put(base, 'ask.md', 'In tới đâu rồi?'),
                                       execute=True, runner=AskRunner())
        self.assertTrue(answer['ok'])


if __name__ == '__main__':
    unittest.main()
