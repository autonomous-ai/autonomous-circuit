"""`.harness/verdict.json` for a KiCad-native workspace — the Harness DSH verdict (spec 1).

The `.board.json` sidecar is the app's machine contract; the verdict is the same fact in the one
shape every domain harness shares, with a phase strip (Build / Checks / Fab) so the pane header
can say where the work is. Pure derivation is tested here without KiCad; the publisher's hook is
tested by pointing `write_verdict` at a workspace with sidecars on disk.

Runs under plain `python3 -m unittest` (no pytest on every machine) and under pytest.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from kicadpy import harness  # noqa: E402


def _sidecar(*, ready: bool, publication: str = 'complete', warnings: list[dict] | None = None) -> dict:
    payload: dict = {
        'schemaVersion': 1,
        'source': {'engine': 'kicad-native', 'file': 'design/main.kicad_pcb', 'fingerprint': 'abc123'},
        'fab': {'ready': ready},
        'native': {'publication': publication, 'checksPassed': ready},
        'validation': {'warnings': warnings or []},
    }
    return payload


def _states(v: dict) -> list[str]:
    return [p['state'] for p in v['phases']]


class VerdictDerivationTest(unittest.TestCase):

    def test_no_sidecar_yet_is_pending_everywhere_and_not_ready(self):
        v = harness.verdict([])
        self.assertEqual(v['spec'], 1)
        self.assertIs(v['ready'], False)
        self.assertEqual(v['summary'], 'No board yet')
        self.assertEqual(v['findings'], [])
        self.assertEqual([p['id'] for p in v['phases']], ['build', 'checks', 'fab'])
        self.assertEqual(_states(v), ['pending', 'pending', 'pending'])
        self.assertNotIn('artifact', v)
        self.assertRegex(v['updatedAt'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

    def test_the_agent_can_mark_build_active_before_the_first_publish(self):
        v = harness.verdict([], active='build')
        self.assertEqual(_states(v), ['active', 'pending', 'pending'])
        self.assertEqual(v['summary'], 'Designing the board')

    def test_an_unknown_phase_is_the_callers_bug(self):
        with self.assertRaises(ValueError):
            harness.verdict([], active='ship')

    def test_ready_is_fab_ready_and_nothing_weaker(self):
        v = harness.verdict([('boards/main.board.json', _sidecar(ready=True))])
        self.assertIs(v['ready'], True)
        self.assertEqual(v['summary'], 'Prototype-ready — physical hardware untested')
        self.assertEqual(_states(v), ['done', 'done', 'done'])
        self.assertEqual(v['artifact'], 'boards/main.board.json')

    def test_a_clean_publication_that_is_not_ready_keeps_fab_active(self):
        # Zero error findings but the packet is not ready (e.g. published without --manufacturing).
        v = harness.verdict([('boards/main.board.json', _sidecar(ready=False))])
        self.assertIs(v['ready'], False)
        self.assertEqual(_states(v), ['done', 'done', 'active'])
        self.assertEqual(v['summary'], 'Not prototype-ready')

    def test_findings_keep_severity_and_the_open_kind_and_count_into_the_summary(self):
        warnings = [
            {'kind': 'drc_clearance', 'severity': 'error', 'message': 'short', 'detail': 'Track too close to pad U3.7',
             'native': {'reference': 'U3'}},
            {'kind': 'erc_pin_not_connected', 'severity': 'error', 'message': 'pin 4 floats', 'part': 'U1.4'},
            {'kind': 'assembly_rotation_unverified', 'severity': 'warning', 'message': 'R1 rotation'},
            {'kind': 'native_coverage', 'severity': 'info', 'message': 'experimental'},
        ]
        v = harness.verdict([('boards/main.board.json', _sidecar(ready=False, warnings=warnings))])
        self.assertIs(v['ready'], False)
        self.assertEqual(v['summary'], '2 errors, 1 warning')
        self.assertEqual(_states(v), ['done', 'failed', 'pending'])
        self.assertEqual(v['findings'][0],
                         {'severity': 'error', 'kind': 'drc_clearance', 'message': 'Track too close to pad U3.7', 'ref': 'U3'})
        self.assertEqual(v['findings'][1]['ref'], 'U1.4')
        self.assertEqual(v['findings'][2]['ref'], '')
        self.assertEqual([f['severity'] for f in v['findings']], ['error', 'error', 'warning', 'info'])

    def test_a_running_publication_keeps_checks_active(self):
        v = harness.verdict([('boards/main.board.json', _sidecar(
            ready=False, publication='running',
            warnings=[{'kind': 'native_pending', 'severity': 'warning', 'message': 'Native CAD checks are running.'}]))])
        self.assertEqual(_states(v), ['done', 'active', 'pending'])
        self.assertEqual(v['summary'], 'Native checks running')

    def test_a_failed_publication_says_why(self):
        v = harness.verdict([('boards/main.board.json', _sidecar(
            ready=False, publication='failed',
            warnings=[{'kind': 'native_check_failed', 'severity': 'error', 'message': 'KiCad cli unavailable; set KICADPY_CLI'}]))])
        self.assertEqual(_states(v), ['done', 'failed', 'pending'])
        self.assertEqual(v['summary'], 'Native checks failed: KiCad cli unavailable; set KICADPY_CLI')

    def test_an_unknown_severity_degrades_to_info_rather_than_breaking_the_reader(self):
        v = harness.verdict([('boards/main.board.json', _sidecar(
            ready=False, warnings=[{'kind': 'k', 'severity': 'fatal', 'message': 'd'}]))])
        self.assertEqual(v['findings'][0]['severity'], 'info')
        self.assertEqual(_states(v), ['done', 'done', 'active'])

    def test_two_boards_fold_to_the_worst_and_ready_needs_both(self):
        v = harness.verdict([
            ('boards/a.board.json', _sidecar(ready=True)),
            ('boards/b.board.json', _sidecar(ready=False, warnings=[{'kind': 'x', 'severity': 'error', 'message': 'm'}])),
        ])
        self.assertIs(v['ready'], False)
        self.assertEqual(_states(v), ['done', 'failed', 'pending'])
        self.assertEqual(v['artifact'], 'boards/a.board.json')

    def test_a_long_summary_is_cut_to_the_spec_limit(self):
        v = harness.verdict([('boards/main.board.json', _sidecar(
            ready=False, publication='failed',
            warnings=[{'kind': 'native_check_failed', 'severity': 'error', 'message': 'x' * 500}]))])
        self.assertLessEqual(len(v['summary']), harness.SUMMARY_MAX)


class VerdictOnDiskTest(unittest.TestCase):

    def test_only_native_sidecars_count_and_a_broken_one_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'boards').mkdir()
            (ws / 'boards' / 'main.board.json').write_text(json.dumps(_sidecar(ready=True)))
            (ws / 'boards' / 'old.board.json').write_text(json.dumps({'source': {'engine': 'tscircuit'}, 'fab': {'ready': False}}))
            (ws / 'boards' / 'half.board.json').write_text('{"source": {"eng')
            found = harness.native_sidecars(ws)
            self.assertEqual([p for p, _ in found], ['boards/main.board.json'])

    def test_write_lands_atomically_under_dot_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'boards').mkdir()
            (ws / 'boards' / 'main.board.json').write_text(json.dumps(_sidecar(ready=True)))
            target = harness.write_verdict(ws)
            self.assertEqual(target, ws / '.harness' / 'verdict.json')
            on_disk = json.loads(target.read_text(encoding='utf-8'))
            self.assertIs(on_disk['ready'], True)
            self.assertEqual(on_disk['artifact'], 'boards/main.board.json')
            self.assertFalse((ws / '.harness' / 'verdict.json.tmp').exists())

    def test_an_empty_workspace_still_gets_a_seed_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = harness.write_verdict(tmp, active='build')
            on_disk = json.loads(target.read_text(encoding='utf-8'))
            self.assertIs(on_disk['ready'], False)
            self.assertEqual(_states(on_disk), ['active', 'pending', 'pending'])

    def test_write_never_raises_on_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / '.harness').write_text('a file where the directory should be')
            self.assertIsNone(harness.write_verdict(ws))

    def test_the_cli_prints_one_json_line(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = harness.main([tmp, '--active', 'build'])
            self.assertEqual(code, 0)
            lines = out.getvalue().strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[-1])
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['summary'], 'Designing the board')


if __name__ == '__main__':
    unittest.main()
