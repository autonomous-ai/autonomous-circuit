"""The advisor: off unless chosen, read-only, called at the two moments, never a blocker."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from kicadpy import advisor as _advisor_for_isolation  # # isolate-from-machine-advisor


def setUpModule():
    # The machine may have an advisor switched on (~/.harness/kicad-advisor); tests never call a real model.
    _advisor_for_isolation.CONFIG = Path('/nonexistent/kicad-advisor')
    import os as _os
    _os.environ['KICAD_ADVISOR'] = 'off'   # the default is on; tests opt in explicitly
from unittest.mock import patch

from kicadpy import advisor
from kicadpy import autofinish as af


def fake_run(answer, code=0, writes=True):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if writes and '-o' in cmd:
            Path(cmd[cmd.index('-o') + 1]).write_text(answer, encoding='utf-8')
        return subprocess.CompletedProcess(cmd, code, stdout='' if writes else answer, stderr='boom' if code else '')
    run.calls = calls
    return run


class AdvisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        (self.ws / 'engineering').mkdir()
        (self.ws / 'firmware').mkdir()
        (self.ws / 'product.json').write_text(json.dumps({'name': 'Desk Pet'}))
        (self.ws / 'engineering' / 'pinout.md').write_text('| TFT_SCK | 4 |\n' + 'x' * 9000)
        (self.ws / 'firmware' / 'flash.json').write_text('{"family": "esp32"}')
        cfg = patch.object(advisor, 'CONFIG', self.ws / 'no-config')
        cfg.start()
        self.addCleanup(cfg.stop)
        which = patch.object(advisor.shutil, 'which', lambda name: '/usr/bin/' + name)
        which.start()
        self.addCleanup(which.stop)

    def test_off_unless_chosen_and_the_spec_is_parsed(self):
        self.assertEqual(advisor.spec({}), ('codex', 'gpt-6-astra'), 'on by default')
        self.assertIsNone(advisor.spec({'KICAD_ADVISOR': 'off'}))
        self.assertIsNone(advisor.spec({'KICAD_ADVISOR': 'grok:x'}))
        self.assertIsNone(advisor.spec({'KICAD_ADVISOR': 'codex:'}))
        self.assertEqual(advisor.spec({'KICAD_ADVISOR': 'codex:gpt-6-astra'}), ('codex', 'gpt-6-astra'))
        self.assertEqual(advisor.spec({'KICAD_ADVISOR': 'claude:claude-opus-5-5'}), ('claude', 'claude-opus-5-5'))
        (self.ws / 'no-config').write_text('codex:gpt-6-astra\n')
        self.assertEqual(advisor.spec({}), ('codex', 'gpt-6-astra'))
        # One run can differ from the machine: the two sides of an A/B.
        (self.ws / '.circuit').mkdir(exist_ok=True)
        (self.ws / '.circuit' / 'advisor.conf').write_text('off\n')
        self.assertIsNone(advisor.spec({'KICAD_ADVISOR': 'codex:gpt-6-astra'}, self.ws))
        (self.ws / '.circuit' / 'advisor.conf').write_text('claude:claude-opus-5-5\n')
        self.assertEqual(advisor.spec({}, self.ws), ('claude', 'claude-opus-5-5'))
        (self.ws / '.circuit' / 'advisor.conf').unlink()
        self.assertIsNone(advisor.ask('stuck', self.ws, env={'KICAD_ADVISOR': 'off'}, run=fake_run('x')))

    def test_bundle_is_small_artifacts_not_the_transcript(self):
        b = advisor.bundle(self.ws, ['engineering_power: servo rail'])
        self.assertIn('===== product.json (product) =====', b)
        self.assertIn('===== engineering/pinout.md (pinout) =====', b)
        self.assertIn('…(truncated)', b)
        self.assertIn('engineering_power: servo rail', b)
        self.assertLessEqual(len(b), advisor.BUNDLE_MAX)

    def test_codex_runs_read_only_in_the_workspace_and_the_answer_is_logged(self):
        run = fake_run('1. engineering/pinout.md: GPIO9 is a strapping pin, move TFT_DC.')
        out = advisor.ask('stuck', self.ws, ['x'], env={'KICAD_ADVISOR': 'codex:gpt-6-astra'}, run=run)
        self.assertIn('GPIO9', out)
        cmd = run.calls[0][0]
        self.assertEqual(cmd[:4], ['codex', 'exec', '-m', 'gpt-6-astra'])
        self.assertIn('read-only', cmd)
        self.assertIn('model_reasoning_effort=medium', cmd)
        self.assertIn('--json', cmd)
        self.assertEqual(cmd[cmd.index('-C') + 1], str(self.ws))
        self.assertIn('Never ask the person anything', cmd[-1])
        logs = list((self.ws / '.circuit' / 'advisor').glob('*-stuck.md'))
        self.assertEqual(len(logs), 1)
        self.assertIn('used', logs[0].read_text())

    def test_advice_that_cites_nothing_real_is_dropped_and_failures_are_skipped(self):
        env = {'KICAD_ADVISOR': 'codex:gpt-6-astra'}
        self.assertIsNone(advisor.ask('ready', self.ws, env=env, run=fake_run('Looks risky overall, be careful.')))
        self.assertEqual(advisor.ask('ready', self.ws, env=env, run=fake_run('NO CONCERNS')), 'NO CONCERNS')
        self.assertIsNone(advisor.ask('ready', self.ws, env=env, run=fake_run('1. product.json: x', code=1)))

        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get('timeout'))
        self.assertIsNone(advisor.ask('ready', self.ws, env=env, run=slow))
        notes = ' '.join(p.read_text() for p in (self.ws / '.circuit' / 'advisor').glob('*.md'))
        self.assertIn('cites nothing', notes)
        self.assertIn('timed out', notes)

    def test_usage_and_time_are_logged_for_the_comparison(self):
        stream = '\n'.join([
            '{"type": "thread.started"}',
            '{"type": "turn.completed", "usage": {"input_tokens": 15812, "cached_input_tokens": 8064, "output_tokens": 5, "reasoning_output_tokens": 0}}',
        ])
        self.assertEqual(advisor.usage_of(stream), {'input_tokens': 15812, 'cached_input_tokens': 8064, 'output_tokens': 5, 'reasoning_output_tokens': 0})
        self.assertEqual(advisor.usage_of('not json'), {})

        def run(cmd, **kw):
            Path(cmd[cmd.index('-o') + 1]).write_text('NO CONCERNS')
            return subprocess.CompletedProcess(cmd, 0, stdout=stream, stderr='')
        advisor.ask('ready', self.ws, env={'KICAD_ADVISOR': 'codex:gpt-6-astra'}, run=run)
        log = next((self.ws / '.circuit' / 'advisor').glob('*-ready.md')).read_text()
        self.assertIn('input_tokens 15812', log)
        self.assertIn(' s · ', log)

    def test_moment(self):
        self.assertIsNone(advisor.moment({}, False, ['a'], True))
        self.assertIsNone(advisor.moment({'findings': ['a'], 'unchanged': 0}, False, ['a'], True))
        self.assertEqual(advisor.moment({'findings': ['a'], 'unchanged': 1}, False, ['a'], True), 'stuck')
        self.assertIsNone(advisor.moment({'findings': ['a'], 'unchanged': 1}, False, ['b'], True))
        self.assertEqual(advisor.moment({}, True, [], True), 'ready')
        self.assertIsNone(advisor.moment({}, True, [], False), 'firmware first')
        self.assertIsNone(advisor.moment({'advisor_ready': True}, True, [], True))
        self.assertIsNone(advisor.moment({'advisor_calls': 3, 'findings': ['a'], 'unchanged': 5}, False, ['a'], True))
        self.assertIsNone(advisor.moment({'continuations': 8, 'findings': ['a'], 'unchanged': 5}, False, ['a'], True),
                          'the reporting turn gets no advice')


class DecideWithAdviceTest(unittest.TestCase):
    base = {'continuations': 2, 'started': 0, 'findings': ['a'], 'unchanged': 1, 'status': 'active'}

    def test_stuck_advice_goes_into_the_repair_continuation(self):
        state, resp = af.decide(dict(self.base), False, ['a'], 1, advice=('stuck', '1. engineering/power.md: add 470 uF'))
        self.assertEqual(resp['decision'], 'block')
        self.assertIn('independent advisor', resp['reason'])
        self.assertIn('470 uF', resp['reason'])
        self.assertEqual(state['advisor_calls'], 1)
        # A failed advisor still counts as a call, so a broken one cannot stall every Stop.
        state, resp = af.decide(dict(self.base), False, ['a'], 1, advice=('stuck', None))
        self.assertNotIn('independent advisor', resp['reason'])
        self.assertEqual(state['advisor_calls'], 1)

    def test_ready_review_blocks_once_with_points_and_finishes_on_no_concerns(self):
        state, resp = af.decide({'status': 'active'}, True, [], 1, advice=('ready', '1. firmware/flash.json: wrong build dir'))
        self.assertEqual(resp['decision'], 'block')
        self.assertIn('.circuit/advisor-response.md', resp['reason'])
        self.assertIn('The gate is the referee', resp['reason'])
        self.assertTrue(state['advisor_ready'])
        self.assertEqual(state['status'], 'active')
        # The next ready Stop has no advice (advisor_ready is set): the run finishes.
        state, resp = af.decide(state, True, [], 2)
        self.assertNotIn('decision', resp)
        self.assertEqual(state['status'], 'ready')

        state, resp = af.decide({'status': 'active'}, True, [], 1, advice=('ready', 'NO CONCERNS'))
        self.assertNotIn('decision', resp)
        self.assertEqual(state['status'], 'ready')
        state, resp = af.decide({'status': 'active'}, True, [], 1, advice=('ready', None))
        self.assertNotIn('decision', resp, 'an advisor that failed never holds the board back')

    def test_no_advice_is_exactly_the_old_behaviour(self):
        a = af.decide(dict(self.base), False, ['a'], 1)
        b = af.decide(dict(self.base), False, ['a'], 1, advice=None)
        self.assertEqual(a, b)
        self.assertNotIn('advisor_calls', a[0])


class HandleCallsTheAdvisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        env = patch.dict(os.environ, {'HARNESS_WORKSPACE': str(self.ws), 'CODEX_THREAD_ID': ''})
        env.start()
        self.addCleanup(env.stop)
        self.event = {'hook_event_name': 'Stop', 'session_id': 's', 'cwd': str(self.ws)}
        af.arm(self.ws)

    def test_stuck_twice_then_the_advisor_is_asked_with_the_findings(self):
        asked = []
        with patch.object(af.advisor, 'spec', lambda env=None, workspace=None: ('codex', 'm')), \
             patch.object(af.advisor, 'ask', lambda when, ws, findings=(), **k: asked.append((when, list(findings))) or '1. product.json: x'):
            for _ in range(3):
                resp = af.handle(self.event, lambda _: (False, ['unconnected C6.1']))
        self.assertEqual(asked, [('stuck', ['unconnected C6.1'])])
        self.assertIn('independent advisor', resp['reason'])
        self.assertEqual(af.read_state(self.ws)['advisor_calls'], 1)

    def test_the_switch_and_the_logs_live_side_by_side(self):
        # 2026-10-08: the switch was a file named like the log folder, so every call failed to log
        # and was skipped. Switch on per run, the call must happen and leave its log.
        (self.ws / '.circuit' / 'advisor.conf').write_text('codex:gpt-6-astra\n')

        def run(cmd, **kw):
            Path(cmd[cmd.index('-o') + 1]).write_text('NO CONCERNS')
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
        with patch.object(af.advisor.shutil, 'which', lambda n: '/usr/bin/' + n), \
             patch.object(af.advisor.subprocess, 'run', run), \
             patch.object(af, 'firmware_built', lambda ws: True):
            af.handle(self.event, lambda _: (True, []))
        self.assertEqual(len(list((self.ws / '.circuit' / 'advisor').glob('*-ready.md'))), 1)

    def test_advisor_off_means_no_call_and_nothing_counted(self):
        with patch.object(af.advisor, 'spec', lambda env=None, workspace=None: None), \
             patch.object(af, 'firmware_built', lambda ws: True):
            af.handle(self.event, lambda _: (True, []))
        state = af.read_state(self.ws)
        self.assertNotIn('advisor_calls', state)
        self.assertNotIn('advisor_ready', state)

    def test_advisor_off_means_no_call(self):
        with patch.object(af.advisor, 'spec', lambda env=None, workspace=None: None):
            with patch.object(af.advisor.subprocess, 'run', side_effect=AssertionError('ran an advisor while off')):
                for _ in range(4):
                    af.handle(self.event, lambda _: (False, ['same']))


if __name__ == '__main__':
    unittest.main()
