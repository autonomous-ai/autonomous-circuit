"""The machine-run case checks: declared scripts and the native checker, levels from reports."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

from kicadpy import case_checks


def put(root, name, text):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class FakeRunner:
    """Stands in for the subprocess: writes what each script would have reported."""

    def __init__(self, reports=None, timeouts=()):
        self.reports = reports or {}
        self.timeouts = set(timeouts)
        self.calls = []

    def __call__(self, argv, cwd, env, timeout):
        self.calls.append((argv, timeout, env.get('MPLCONFIGDIR')))
        key = argv[1] if argv[1] != '-m' else 'native:' + argv[3]
        if key in self.timeouts:
            return None, 'still sweeping'
        for name, report in self.reports.get(key, {}).items():
            put(cwd, name, json.dumps(report))
        return 0, 'done'


class CaseChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def declare(self, *checks):
        for check in checks:
            if check.get('script', '').endswith('.py') and '..' not in check['script'] \
                    and not check['script'].startswith('/'):
                put(self.root, check['script'], 'pass\n')
        put(self.root, case_checks.CHECKS_FILE, json.dumps({'checks': list(checks)}))

    def run_checks(self, runner, **kwargs):
        return case_checks.run(self.root, {}, case_python='case-python', runner=runner, **kwargs)

    def by_name(self, summary):
        return {entry['name']: entry for entry in summary['checks']}

    def test_each_declared_report_gives_its_level_and_the_worst_wins(self):
        self.declare({'name': 'mech', 'script': 'tools/case/mech.py', 'args': ['--coarse'],
                      'report': 'case/reports/mech.json'},
                     {'name': 'route', 'script': 'tools/case/route.py', 'report': 'case/reports/route.json'})
        runner = FakeRunner({'tools/case/mech.py': {'case/reports/mech.json': {'verdict': 'review'}},
                             'tools/case/route.py': {'case/reports/route.json': {'passed': True}}})
        summary = self.run_checks(runner)
        levels = {name: entry['level'] for name, entry in self.by_name(summary).items()}
        self.assertEqual(levels, {'mech': 'review', 'route': 'pass', 'native': 'unverified'})
        # nothing exported yet: the native checker is not measured, so the case is not a pass
        self.assertEqual(summary['verdict'], 'unverified')
        argv, _, mpl = runner.calls[0]
        self.assertEqual(argv, ['case-python', 'tools/case/mech.py', '--coarse'])
        self.assertEqual(mpl, str(self.root / 'build/case/mpl'))
        self.assertEqual(len(runner.calls), 2)  # no subprocess for the native check without parts

    def test_a_failed_report_fails_the_case(self):
        self.declare({'name': 'mech', 'script': 'tools/case/mech.py', 'report': 'case/reports/mech.json'})
        runner = FakeRunner({'tools/case/mech.py': {'case/reports/mech.json': {
            'passed': False, 'errors': ['leg hits body at hip 40 deg']}}})
        summary = self.run_checks(runner)
        self.assertEqual(summary['verdict'], 'fail')
        self.assertEqual(self.by_name(summary)['mech']['message'], '1 error(s)')

    def test_a_report_not_rewritten_by_this_run_is_unverified(self):
        self.declare({'name': 'mech', 'script': 'tools/case/mech.py', 'report': 'case/reports/mech.json'})
        old = put(self.root, 'case/reports/mech.json', json.dumps({'passed': True}))
        past = time.time() - 3600
        import os
        os.utime(old, (past, past))
        summary = self.run_checks(FakeRunner())
        entry = self.by_name(summary)['mech']
        self.assertEqual(entry['level'], 'unverified')
        self.assertIn('not rewritten', entry['message'])

    def test_missing_or_shapeless_report_is_unverified_not_pass(self):
        self.declare({'name': 'a', 'script': 'tools/case/a.py', 'report': 'case/reports/a.json'},
                     {'name': 'b', 'script': 'tools/case/b.py', 'report': 'case/reports/b.json'})
        summary = self.run_checks(FakeRunner({'tools/case/b.py': {'case/reports/b.json': {'ok': 1}}}))
        entries = self.by_name(summary)
        self.assertEqual((entries['a']['level'], entries['b']['level']), ('unverified', 'unverified'))
        self.assertIn('not written', entries['a']['message'])
        self.assertIn('no verdict', entries['b']['message'])

    def test_a_timeout_is_unverified_and_the_total_budget_bounds_later_checks(self):
        self.declare({'name': 'sweep', 'script': 'tools/case/sweep.py', 'report': 'case/reports/s.json',
                      'timeoutSeconds': 99999})
        runner = FakeRunner(timeouts={'tools/case/sweep.py'})
        summary = self.run_checks(runner, total_seconds=50)
        entry = self.by_name(summary)['sweep']
        self.assertEqual(entry['level'], 'unverified')
        self.assertIn('timed out', entry['message'])
        self.assertLessEqual(runner.calls[0][1], 50)

    def test_no_declared_checks_is_unverified(self):
        summary = self.run_checks(FakeRunner())
        self.assertEqual(self.by_name(summary)['checks.json']['level'], 'unverified')
        self.assertEqual(summary['verdict'], 'unverified')

    def test_a_bad_declaration_fails_and_is_not_run(self):
        self.declare({'name': 'out', 'script': '../escape.py', 'report': 'case/reports/x.json'},
                     {'name': 'abs', 'script': '/usr/bin/true.py', 'report': 'case/reports/x.json'},
                     {'name': 'rep', 'script': 'tools/case/r.py', 'report': 'design/x.json'},
                     {'name': 'native', 'script': 'tools/case/n.py', 'report': 'case/reports/n.json'})
        runner = FakeRunner()
        summary = self.run_checks(runner)
        self.assertEqual(summary['verdict'], 'fail')
        self.assertEqual(sum(entry['name'] == 'checks.json' for entry in summary['checks']), 4)
        self.assertEqual(runner.calls, [])
        put(self.root, case_checks.CHECKS_FILE, '{nope')
        self.assertEqual(self.run_checks(FakeRunner())['verdict'], 'fail')

    def test_native_check_of_a_pcb_case_uses_the_final_gate_freshness(self):
        put(self.root, 'case/out/parts.json', json.dumps({'parts': []}))
        runner = FakeRunner({'native:check': {'case/reports/case-check.json': {'passed': True, 'verdict': 'pass'}}})
        summary = self.run_checks(runner)
        native = self.by_name(summary)['native']
        self.assertEqual(runner.calls[-1][0], ['case-python', '-m', 'kicadpy.case', 'check', '.'])
        # a report without the surface validation and board binding is stale for the gate: not a pass
        self.assertEqual(native['level'], 'unverified')
        self.assertIn('recheck', native['message'])

    def test_native_check_of_a_handwired_case_is_check_print(self):
        put(self.root, 'case/out/parts.json', json.dumps(
            {'parts': [], 'verification': {'nativePCBCheckerApplies': False}}))
        runner = FakeRunner({'native:check-print': {'case/reports/print-check.json': {'passed': True,
                                                                                      'verdict': 'pass'}}})
        native = self.by_name(self.run_checks(runner))['native']
        self.assertEqual(runner.calls[-1][0][3], 'check-print')
        self.assertEqual(native['level'], 'pass')

    def test_native_timeout_is_unverified(self):
        put(self.root, 'case/out/parts.json', json.dumps({'parts': []}))
        native = self.by_name(self.run_checks(FakeRunner(timeouts={'native:check'})))['native']
        self.assertEqual(native['level'], 'unverified')

    def test_real_subprocess_timeout_kills_the_script(self):
        script = put(self.root, 'tools/case/slow.py', 'import time\ntime.sleep(30)\n')
        began = time.monotonic()
        code, _ = case_checks.run_process([sys.executable, str(script)], self.root, {}, 1)
        self.assertIsNone(code)
        self.assertLess(time.monotonic() - began, 10)
        code, output = case_checks.run_process([sys.executable, '-c', 'print("hi")'], self.root, {}, 10)
        self.assertEqual((code, output.strip()), (0, 'hi'))

    def test_each_check_carries_its_waterfall_stage_and_kind(self):
        self.declare({'name': 'print', 'script': 'tools/case/p.py', 'report': 'case/reports/p.json'},
                     {'name': 'sweep', 'script': 'tools/case/s.py', 'report': 'case/reports/s.json',
                      'stage': 'moving', 'kind': 'motion'})
        entries = self.by_name(self.run_checks(FakeRunner()))
        self.assertEqual((entries['print']['stage'], entries['print']['kind']), ('simple', 'check'))
        self.assertEqual((entries['sweep']['stage'], entries['sweep']['kind']), ('moving', 'motion'))
        self.declare({'name': 'a', 'script': 'tools/case/a.py', 'report': 'case/reports/a.json', 'stage': 'covers'},
                     {'name': 'b', 'script': 'tools/case/b.py', 'report': 'case/reports/b.json', 'kind': 'fit'})
        summary = self.run_checks(FakeRunner())
        self.assertEqual(summary['verdict'], 'fail')
        messages = [entry['message'] for entry in summary['checks'] if entry['name'] == 'checks.json']
        self.assertIn('stage must be one of', messages[0])
        self.assertIn('kind must be print or motion', messages[1])

    def test_a_reused_check_is_not_run_again(self):
        self.declare({'name': 'frame', 'script': 'tools/case/f.py', 'report': 'case/reports/f.json', 'parts': ['frame']},
                     {'name': 'tray', 'script': 'tools/case/t.py', 'report': 'case/reports/t.json'})
        runner = FakeRunner({'tools/case/t.py': {'case/reports/t.json': {'passed': True}}})
        summary = self.run_checks(runner, reuse={'frame': ('pass', 'fits')})
        entries = self.by_name(summary)
        self.assertEqual((entries['frame']['level'], entries['frame']['message']), ('pass', 'reused: fits'))
        self.assertTrue(entries['frame']['reused'])
        self.assertEqual([call[0][1] for call in runner.calls], ['tools/case/t.py'])
        self.declare({'name': 'x', 'script': 'tools/case/x.py', 'report': 'case/reports/x.json', 'parts': 'frame'})
        self.assertEqual(self.run_checks(FakeRunner())['verdict'], 'fail')

    def print_project(self, plates, objects=('frame',)):
        put(self.root, 'case/out/parts.json', json.dumps(
            {'parts': [], 'printProject': {'file': 'print/all.3mf', 'report': 'print/all.json'}}))
        config = '<config>' + ''.join(
            f'<object id="{i}"><metadata key="name" value="{name}"/></object>'
            for i, name in enumerate(objects)) + ''.join(
            f'<plate><metadata key="plater_id" value="{i + 1}"/>'
            f'<metadata key="plater_name" value="{name}"/></plate>' for i, name in enumerate(plates)) + '</config>'
        (self.root / 'case/out/print').mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.root / 'case/out/print/all.3mf', 'w') as archive:
            archive.writestr('Metadata/model_settings.config', config)

    def test_a_plate_name_bambu_refuses_fails(self):
        self.print_project(['ĐÃ IN — GIỮ LẠI', 'MỚI/SỬA — CẦN IN'], objects=('frame', 'leg:1'))
        entry = self.by_name(self.run_checks(FakeRunner()))['plate-names']
        self.assertEqual(entry['level'], 'fail')
        self.assertIn('plate "MỚI/SỬA — CẦN IN"', entry['message'])
        self.assertIn('object "leg:1"', entry['message'])
        self.assertNotIn('ĐÃ IN', entry['message'])

    def test_clean_plate_names_pass(self):
        self.print_project(['ĐÃ IN — GIỮ LẠI', 'MỚI-SỬA — CẦN IN — Khung A1V4-r5'])
        entry = self.by_name(self.run_checks(FakeRunner()))['plate-names']
        self.assertEqual(entry['level'], 'pass')

    def test_no_print_project_is_skipped_not_failed(self):
        self.assertNotIn('plate-names', self.by_name(self.run_checks(FakeRunner())))
        put(self.root, 'case/out/parts.json', json.dumps(
            {'parts': [], 'printProject': {'file': 'print/missing.3mf', 'report': 'r.json'}}))
        self.assertIsNone(case_checks.plate_names(self.root))
        put(self.root, 'case/out/print/broken.3mf', 'not a zip')
        put(self.root, 'case/out/parts.json', json.dumps(
            {'parts': [], 'printProject': {'file': 'print/broken.3mf', 'report': 'r.json'}}))
        self.assertEqual(case_checks.plate_names(self.root)['level'], 'unverified')

    def test_brief_keeps_only_what_a_prompt_needs(self):
        summary = self.run_checks(FakeRunner())
        brief = case_checks.brief({**summary, 'report': '.pilot/case-checks/x.json'})
        self.assertEqual(set(brief), {'verdict', 'cached', 'report', 'checks'})
        self.assertEqual(set(brief['checks'][0]), {'name', 'level', 'message', 'seconds'})


if __name__ == '__main__':
    unittest.main()
