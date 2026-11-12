"""Engineering drawing sheets from the exported case parts (needs CadQuery: run with $KICAD_CASE_PYTHON)."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

try:
    import cadquery as cq
    from kicadpy import case_drawing
except ImportError:  # the host Python has no CadQuery; the case Python runs these
    cq = None


@unittest.skipIf(cq is None, 'CadQuery is not installed in this Python')
class CaseDrawingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        out = self.root / 'case/out'
        out.mkdir(parents=True)
        (self.root / 'product.json').write_text(json.dumps({'name': 'Desk Crab'}))
        frame = cq.Workplane('XY').box(80, 50, 20).faces('>Z').workplane().pushPoints(
            [(-30, -15), (30, 15)]).hole(3.4)
        foot = cq.Workplane('XY').box(10, 10, 30).translate((50, 0, 0))
        servo = cq.Workplane('XY').box(23, 12, 22).translate((0, 40, 0))
        coupon = cq.Workplane('XY').box(10, 10, 3).translate((0, -60, 0))
        for name, shape in (('frame', frame), ('foot_FL', foot), ('ref_DO_NOT_PRINT_servo', servo),
                            ('coupon_TEST_ONLY', coupon)):
            cq.exporters.export(shape, str(out / f'{name}.step'))
        # the same design twice: one sheet with its count
        shutil.copy(out / 'foot_FL.step', out / 'foot_FR.step')
        self.manifest = {'revision': 'r7', 'parts': [
            {'name': 'frame', 'role': 'shell', 'step': 'frame.step', 'stl': 'frame.stl'},
            {'name': 'foot_FL', 'role': 'other', 'step': 'foot_FL.step', 'stl': 'foot_FL.stl'},
            {'name': 'foot_FR', 'role': 'other', 'step': 'foot_FR.step', 'stl': 'foot_FR.stl'},
            {'name': 'ref_DO_NOT_PRINT_servo', 'role': 'other', 'step': 'ref_DO_NOT_PRINT_servo.step'},
            {'name': 'coupon_TEST_ONLY', 'role': 'coupon', 'step': 'coupon_TEST_ONLY.step'}]}
        self.write_manifest()

    def write_manifest(self):
        (self.root / 'case/out/parts.json').write_text(json.dumps(self.manifest))

    def test_one_assembly_sheet_and_one_per_printed_design(self):
        index = case_drawing.generate(self.root)
        sheets = {sheet['name']: sheet for sheet in index['sheets']}
        self.assertEqual(set(sheets), {'assembly', 'frame', 'foot_FL'})
        self.assertEqual(sheets['foot_FL']['quantity'], 2)
        self.assertEqual(sheets['foot_FL']['parts'], ['foot_FL', 'foot_FR'])
        self.assertNotIn('coupon_TEST_ONLY', sheets['assembly']['parts'])
        self.assertIn('ref_DO_NOT_PRINT_servo', sheets['assembly']['parts'])
        files = sorted(path.name for path in (self.root / 'case/out/drawings').iterdir())
        self.assertEqual(files, ['assembly.svg', 'foot_FL.svg', 'frame.svg', 'index.json'])
        saved = json.loads((self.root / 'case/out/drawings/index.json').read_text())
        self.assertEqual((saved['schema'], saved['project'], saved['version']), (1, 'Desk Crab', 'r7'))

    def test_sheets_carry_views_dimensions_parts_list_and_title_block(self):
        case_drawing.generate(self.root)
        assembly = (self.root / 'case/out/drawings/assembly.svg').read_text()
        self.assertIn('viewBox="0 0 420 297"', assembly)  # A3 landscape in mm
        for text in ('FRONT', 'TOP', 'RIGHT', 'ISO', 'SCALE', 'UNITS', '>mm<', 'VERSION', '>r7<', 'DATE',
                     'Desk Crab', 'ASSEMBLY', 'servo (bought)', '>2<'):
            self.assertIn(text, assembly)
        self.assertNotIn('DO NOT PRINT', assembly)
        part = (self.root / 'case/out/drawings/frame.svg').read_text()
        self.assertIn('PART OF DESK CRAB', part.upper())
        self.assertIn('>80<', part)  # overall length
        self.assertIn('⌀3.4', part)  # the hole note
        self.assertIn('2/3', (self.root / 'case/out/drawings/foot_FL.svg').read_text() +
                      part)

    def test_unchanged_parts_reuse_the_sheets_and_a_change_redraws(self):
        first = case_drawing.generate(self.root)
        self.assertFalse(first['cached'])
        self.assertTrue(case_drawing.generate(self.root)['cached'])
        self.assertFalse(case_drawing.generate(self.root, force=True)['cached'])
        self.manifest['revision'] = 'r8'
        self.write_manifest()
        self.assertFalse(case_drawing.generate(self.root)['cached'])
        # a part that is no longer printed loses its stale sheet
        self.manifest['parts'] = self.manifest['parts'][:1]
        self.write_manifest()
        case_drawing.generate(self.root)
        self.assertFalse((self.root / 'case/out/drawings/foot_FL.svg').exists())

    def test_missing_step_is_an_error_and_cli_reports_it(self):
        self.manifest['parts'][0]['step'] = 'gone.step'
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'missing'):
            case_drawing.generate(self.root)
        import contextlib
        import io
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(case_drawing.main([str(self.root)]), 1)
        self.assertFalse(json.loads(stream.getvalue())['ok'])

    def test_cli_prints_one_json_line(self):
        import contextlib
        import io
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(case_drawing.main([str(self.root)]), 0)
        line = json.loads(stream.getvalue().strip().splitlines()[-1])
        self.assertEqual((line['ok'], line['sheets'], line['index']), (True, 3, 'case/out/drawings/index.json'))

    def test_bought_parts(self):
        self.assertTrue(case_drawing.bought({'name': 'ref_DO_NOT_PRINT_x'}))
        self.assertTrue(case_drawing.bought({'name': 'servo', 'print': False}))
        self.assertTrue(case_drawing.bought({'name': 'servo', 'role': 'hardware'}))
        self.assertFalse(case_drawing.bought({'name': 'frame', 'role': 'shell'}))


if __name__ == '__main__':
    unittest.main()
