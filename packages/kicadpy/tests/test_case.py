"""The Case phase: the template case passes on real boards, and every way a case can be wrong is caught.

Runs with CadQuery (the tile's toolchain/case-python, or any Python that has it); skipped
otherwise. The boards are the real contract fixtures of test_enclosure.
"""
import json
from pathlib import Path
import tempfile
import unittest

from kicadpy import case, enclosure

try:
    import cadquery as cq
except ImportError:  # the plain test runner without the case toolchain
    cq = None

FIX = Path(__file__).parent / 'fixtures' / 'enclosure'
MODULES = Path(__file__).resolve().parents[2] / 'golden-blocks' / 'modules'

INTENTS = {
    'pet': {'interfaces': {'J2': {'confirm': True, 'seatHeightMm': 5.08}, 'J1#usb': {'confirm': True},
                           'J1#button': {'ignore': True}, 'SW1': {'confirm': True}}},
    'rover': {'interfaces': {'J6': {'offboard': True}, 'J8': {'confirm': True, 'bodyToward': 'away'},
                             'J10': {'ignore': True}, 'J11': {'ignore': True}, 'J7': {'ignore': True},
                             'J2#button': {'ignore': True}}},
    'deck': {'interfaces': {'J3': {'offboard': True}, 'J1#button': {'ignore': True}}},
}


def workspace(name, tmp, intent=None):
    """A workspace holding only what the case phase reads: the contract and the intent."""
    ws = Path(tmp) / name
    (ws / 'boards').mkdir(parents=True)
    (ws / 'case').mkdir()
    intent = INTENTS[name] if intent is None else intent
    (ws / 'case' / 'interfaces.json').write_text(json.dumps(intent))
    doc = enclosure.build(json.loads((FIX / f'{name}.json').read_text()), intent=intent,
                          modules=enclosure.load_modules(MODULES))
    (ws / 'boards' / 'main.enclosure.json').write_text(json.dumps(doc))
    return ws, doc


def export(ws, parts):
    from kicadpy.case_template import _sha
    out = ws / 'case' / 'out'
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'contractSha': _sha(ws / 'boards' / 'main.enclosure.json'), 'parts': []}
    for name, (role, solid) in parts.items():
        cq.exporters.export(solid, str(out / f'{name}.stl'))
        cq.exporters.export(solid, str(out / f'{name}.step'))
        manifest['parts'].append({'name': name, 'role': role, 'stl': f'{name}.stl', 'step': f'{name}.step',
                                  'printRotateDeg': [180, 0, 0] if name == 'front' else [0, 0, 0]})
    (out / 'parts.json').write_text(json.dumps(manifest))


def kinds(report):
    return sorted({e['kind'] for e in report['errors']})


class StatusWithoutCadQueryTest(unittest.TestCase):
    def test_none_stale_failed_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = workspace('pet', tmp)
            self.assertEqual(case.status(ws)[0], 'none')
            out = ws / 'case' / 'out'
            out.mkdir()
            (out / 'parts.json').write_text('{}')
            (out / 'front.stl').write_text('solid')
            self.assertEqual(case.status(ws)[0], 'stale', 'built but never checked')
            report = {'passed': True, 'contractSha': case.sha(ws / 'boards' / 'main.enclosure.json'),
                      'partShas': {'front.stl': case.sha(out / 'front.stl')}}
            (ws / 'case' / 'reports').mkdir()
            (ws / 'case' / 'reports' / 'case-check.json').write_text(json.dumps(report))
            self.assertEqual(case.status(ws)[0], 'passed')
            (out / 'front.stl').write_text('solid changed')
            self.assertEqual(case.status(ws), ('stale', 'case/out/front.stl changed since the case was checked'))
            (out / 'front.stl').write_text('solid')
            (ws / 'boards' / 'main.enclosure.json').write_text('{"changed": true}')
            self.assertEqual(case.status(ws)[0], 'stale', 'a new board makes the case stale')
            (ws / 'case' / 'reports' / 'case-check.json').write_text(json.dumps({**report, 'passed': False, 'errors': [{}],
                                                                                 'contractSha': case.sha(ws / 'boards' / 'main.enclosure.json')}))
            self.assertEqual(case.status(ws)[0], 'failed')


@unittest.skipIf(cq is None, 'CadQuery is not installed (run with toolchain/case-python)')
class TemplateCasePassesTest(unittest.TestCase):
    def test_every_board_gets_a_case_that_fits_and_opens_for_every_interface(self):
        from kicadpy.case_template import build_box
        for name in ('pet', 'rover', 'deck'):
            with self.subTest(board=name), tempfile.TemporaryDirectory() as tmp:
                ws, doc = workspace(name, tmp)
                build_box(ws)
                report = case.check(ws)
                self.assertTrue(report['passed'], (name, report['errors']))
                self.assertEqual(case.status(ws)[0], 'passed', name)
                parts = json.loads((ws / 'case' / 'out' / 'parts.json').read_text())['parts']
                self.assertTrue(all(p['stl'] and (ws / 'case' / 'out' / p['stl']).exists() for p in parts))
                buttons = [i for i in doc['interfaces'] if i['kind'] == 'button' and i['status'] != 'ignored' and not i.get('offboard')]
                self.assertEqual(sum(p['role'] == 'plunger' for p in parts), len(buttons), f'{name}: a plunger per button')


@unittest.skipIf(cq is None, 'CadQuery is not installed (run with toolchain/case-python)')
class EveryWrongCaseIsCaughtTest(unittest.TestCase):
    def setUp(self):
        from kicadpy.case_template import shapes
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws, self.doc = workspace('pet', self.tmp.name)
        self.parts = shapes(self.doc)

    def run_check(self):
        export(self.ws, self.parts)
        return case.check(self.ws)

    def test_a_covered_screen(self):
        display = next(i for i in self.doc['interfaces'] if i['kind'] == 'display')
        role, front = self.parts['front']
        top = front.val().BoundingBox().zmax
        cx, cy = display['centerMm']
        plug = cq.Workplane('XY').box(30, 30, 1.6, centered=(True, True, False)).translate((cx, cy, top - 1.6))
        self.parts['front'] = (role, front.union(plug))
        self.assertEqual(kinds(self.run_check()), ['case_interface_blocked'])

    def test_a_case_that_goes_into_a_module(self):
        role, back = self.parts['back']
        mcu = next(m for m in self.doc['modules'] if m['id'] == 'esp32-c3-supermini')
        cx, cy = mcu['centerMm']
        spike = cq.Workplane('XY').box(4, 4, 6, centered=(True, True, False)).translate((cx, cy, mcu['frontZMm'] - 1))
        self.parts['back'] = (role, back.union(spike))
        report = self.run_check()
        self.assertIn('case_collides', kinds(report))
        self.assertTrue(any('esp32-c3-supermini' in e.get('with', '') for e in report['errors']))

    def test_a_button_without_a_plunger(self):
        del self.parts['plunger_SW1']
        role, front = self.parts['front']
        sw = next(i for i in self.doc['interfaces'] if i['refs'] == ['SW1'])
        top = front.val().BoundingBox().zmax
        cap = cq.Workplane('XY').circle(3).extrude(1.6).translate((sw['centerMm'][0], sw['centerMm'][1], top - 1.6))
        self.parts['front'] = (role, front.union(cap))
        self.assertEqual(kinds(self.run_check()), ['case_interface_blocked'])

    def test_a_part_too_big_for_the_bed_and_a_case_built_for_another_board(self):
        intent = json.loads((self.ws / 'case' / 'interfaces.json').read_text())
        intent['printer'] = {'bedMm': [40, 40, 40]}
        (self.ws / 'case' / 'interfaces.json').write_text(json.dumps(intent))
        export(self.ws, self.parts)
        manifest = json.loads((self.ws / 'case' / 'out' / 'parts.json').read_text())
        manifest['contractSha'] = 'an older board'
        (self.ws / 'case' / 'out' / 'parts.json').write_text(json.dumps(manifest))
        self.assertEqual(kinds(case.check(self.ws)), ['case_built_from_old_board', 'case_part_too_big'])


@unittest.skipIf(cq is None, 'CadQuery is not installed (run with toolchain/case-python)')
class TouchWallTest(unittest.TestCase):
    def test_too_much_plastic_over_a_touch_pad(self):
        from kicadpy.case_template import shapes
        with tempfile.TemporaryDirectory() as tmp:
            ws, doc = workspace('rover', tmp)
            parts = shapes(doc)
            export(ws, parts)
            self.assertNotIn('case_touch_wall', kinds(case.check(ws)), '1.6 mm plate over the pad is within 2 mm')
            parts = shapes(doc, wall=3.2)
            export(ws, parts)
            self.assertIn('case_touch_wall', kinds(case.check(ws)))


if __name__ == '__main__':
    unittest.main()
