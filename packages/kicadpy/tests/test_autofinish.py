"""Stop orchestration must neither accept stale passes nor loop after cancellation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kicadpy import autofinish as af
from kicadpy.project import write_json


class AutoFinishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'HARNESS_WORKSPACE': str(self.ws), 'CODEX_THREAD_ID': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.event = {'hook_event_name': 'Stop', 'session_id': 'test-session', 'cwd': str(self.ws)}

    def arm(self):
        af.arm(self.ws)

    def test_question_or_plan_does_not_run_checks(self):
        def forbidden(_):
            self.fail('unarmed hook ran publisher')
        self.assertEqual(af.handle(self.event, forbidden), {})

    def test_build_continues_then_fresh_ready_ends_loop(self):
        self.arm()
        response = af.handle(self.event, lambda _: (False, ['unconnected C6.1']))
        self.assertEqual(response['decision'], 'block')
        self.assertIn('C6.1', response['reason'])
        af.arm(self.ws)
        self.assertEqual(af.read_state(self.ws)['continuations'], 1)
        response = af.handle(self.event, lambda _: (True, []))
        self.assertNotIn('decision', response)
        self.assertEqual(af.read_state(self.ws)['status'], 'ready')

    def test_stagnation_changes_strategy_and_budget_ends(self):
        self.arm()
        for i in range(af.MAX_CONTINUATIONS):
            response = af.handle(self.event, lambda _: (False, ['unconnected']))
            if i:
                self.assertIn('different repair strategy', response['reason'])
        response = af.handle(self.event, lambda _: (False, ['unconnected']))
        self.assertIn('NOT ready', response['reason'])
        self.assertEqual(af.read_state(self.ws)['status'], 'exhausted')
        self.assertEqual(af.handle(self.event), {})

    def test_third_continuation_asks_for_a_handoff_note_first(self):
        self.arm()
        reasons = [af.handle(self.event, lambda _: (False, [f'error {i}']))['reason'] for i in range(af.HANDOFF_AT + 1)]
        self.assertNotIn('handoff.md', reasons[0])
        self.assertIn('handoff.md', reasons[af.HANDOFF_AT - 1])
        self.assertTrue(reasons[af.HANDOFF_AT - 1].index('handoff.md') < reasons[af.HANDOFF_AT - 1].index('Current findings'))

    def test_time_budget_ends(self):
        self.arm()
        state = af.read_state(self.ws)
        state['started'] = 0
        write_json(self.ws / af.STATE, state)
        af.handle(self.event, lambda _: (False, ['error']))
        self.assertEqual(af.read_state(self.ws)['status'], 'exhausted')

    def test_interrupt_during_publication_cannot_be_revived(self):
        self.arm()
        def check(_):
            af.handle({**self.event, 'hook_event_name': 'Interrupt'})
            return False, ['error']
        self.assertEqual(af.handle(self.event, check), {})
        self.assertEqual(af.read_state(self.ws)['status'], 'interrupted')

    def test_other_session_cannot_continue_or_cancel_build(self):
        self.arm()
        af.handle(self.event, lambda _: (False, ['error']))
        self.assertEqual(af.handle({**self.event, 'session_id': 'other'}), {})
        af.handle({**self.event, 'session_id': 'other', 'hook_event_name': 'Interrupt'})
        self.assertEqual(af.read_state(self.ws)['status'], 'active')

    def test_grok_event_shape_session_id_and_stop_cancelled(self):
        """Grok Build sends `sessionId`, cancels with `StopCancelled`, and fires a teardown Stop."""
        self.arm()
        grok_stop = {'hook_event_name': 'Stop', 'sessionId': 'grok-session', 'cwd': str(self.ws), 'reason': 'end_turn'}
        response = af.handle(grok_stop, lambda _: (False, ['unconnected C6.1']))
        self.assertEqual(response['decision'], 'block')
        self.assertEqual(af.read_state(self.ws)['session'], 'grok-session')
        # The session-end Stop (reason channel_closed / shutdown) must not run the publisher.
        def forbidden(_):
            self.fail('teardown Stop ran publisher')
        self.assertEqual(af.handle({**grok_stop, 'reason': 'shutdown'}, forbidden), {})
        # A runtime cancel (max_turns, no_progress) keeps the run armed; the user's interrupt ends it.
        af.handle({**grok_stop, 'hook_event_name': 'StopCancelled', 'reason': 'max_turns', 'cancelledBy': 'runtime'})
        self.assertEqual(af.read_state(self.ws)['status'], 'active')
        af.handle({**grok_stop, 'hook_event_name': 'StopCancelled', 'reason': 'user_interrupt', 'cancelledBy': 'user'})
        self.assertEqual(af.read_state(self.ws)['status'], 'interrupted')

    def test_checker_exception_is_a_blocker(self):
        self.arm()
        def broken(_):
            raise RuntimeError('KiCad missing')
        response = af.handle(self.event, broken)
        self.assertIn('KiCad missing', response['reason'])
        self.assertEqual(af.read_state(self.ws)['status'], 'active')

    def test_explicit_build_in_new_session_recovers_abandoned_run(self):
        self.arm()
        af.handle(self.event, lambda _: (False, ['error']))
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'new-session'}):
            af.arm(self.ws)
        self.assertEqual(af.read_state(self.ws)['session'], 'new-session')
        self.assertEqual(af.read_state(self.ws)['continuations'], 0)

    def test_hook_command_reads_stdin_and_emits_codex_json(self):
        import subprocess
        import sys
        self.arm()
        output = subprocess.run([sys.executable, '-m', 'kicadpy.autofinish'],
                                input=json.dumps(self.event), text=True,
                                capture_output=True, check=True)
        response = json.loads(output.stdout)
        self.assertEqual(response['decision'], 'block')
        self.assertIn('No design/', response['reason'])

    def test_missing_project_is_not_ready(self):
        self.assertFalse(af.inspect_board(self.ws)[0])

    def publish_fixture(self, payload, result=None, returncode=0):
        (self.ws / 'design').mkdir()
        (self.ws / 'design/main.kicad_pro').touch()
        (self.ws / '.circuit').mkdir()
        (self.ws / 'boards').mkdir()
        write_json(self.ws / 'boards/main.board.json', payload)
        class Process:
            def __init__(self, *args, stdout, **kwargs):
                stdout.write(json.dumps(result or {'ok': True}) + '\n')
                stdout.flush()
                self.returncode = returncode
            def wait(self, **kwargs):
                return self.returncode
        return patch.object(af.subprocess, 'Popen', Process)

    def test_stale_ready_sidecar_cannot_hide_publisher_failure(self):
        with self.publish_fixture({'fab': {'ready': True}}, {'ok': False, 'error': 'DRC failed'}):
            ready, findings = af.inspect_board(self.ws)
        self.assertFalse(ready)
        self.assertIn('DRC failed', findings[0])

    def test_publisher_timeout_cannot_accept_stale_ready(self):
        import subprocess
        with self.publish_fixture({'fab': {'ready': True}}):
            with patch.object(af.subprocess, 'Popen') as popen, patch.object(af.os, 'killpg') as kill:
                popen.return_value.pid = 123
                popen.return_value.wait.side_effect = [subprocess.TimeoutExpired('publisher', 480), 0]
                ready, findings = af.inspect_board(self.ws)
                self.assertFalse(ready)
                self.assertIn('timed out', findings[0])
                kill.assert_called_once()

    def test_publisher_success_requires_complete_ready_without_errors(self):
        board = {'source': {'engine': 'kicad-native'}, 'native': {'publication': 'complete'},
                 'fab': {'ready': True}, 'validation': {'warnings': []}}
        with self.publish_fixture(board):
            self.assertTrue(af.inspect_board(self.ws)[0])
            board['validation']['warnings'] = [{'kind': 'short', 'severity': 'error', 'message': 'U1'}]
            write_json(self.ws / 'boards/main.board.json', board)
            self.assertFalse(af.inspect_board(self.ws)[0])
            board['validation']['warnings'] = []
            board['native']['publication'] = 'running'
            write_json(self.ws / 'boards/main.board.json', board)
            self.assertFalse(af.inspect_board(self.ws)[0])


if __name__ == '__main__':
    unittest.main()
