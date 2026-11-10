"""The Case phase: the template case passes on real boards, and every way a case can be wrong is caught.

Runs with CadQuery (the tile's toolchain/case-python, or any Python that has it); skipped
otherwise. The boards are the real contract fixtures of test_enclosure.
"""
import json
from pathlib import Path
import tempfile
import unittest

from kicadpy import case, case_measure, enclosure

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
                                  'printRotateDeg': [180, 0, 0] if name in ('front', 'coupon') else [0, 0, 0]})
    (out / 'parts.json').write_text(json.dumps(manifest))


def kinds(report):
    return sorted({e['kind'] for e in report['errors']})


class StatusWithoutCadQueryTest(unittest.TestCase):
    def report_state(self, report):
        return case._state_of(report)[0]

    def test_four_levels_reach_the_status(self):
        check = {'check': 'walls:lid', 'message': 'x'}
        self.assertEqual(self.report_state({'passed': True, 'verdict': 'pass', 'checks': []}), 'passed')
        self.assertEqual(self.report_state({'passed': True, 'verdict': 'review', 'checks': [{**check, 'level': 'review'}]}), 'passed')
        self.assertEqual(self.report_state({'passed': False, 'verdict': 'unverified', 'checks': [{**check, 'level': 'unverified'}]}), 'unverified')
        self.assertEqual(self.report_state({'passed': False, 'verdict': 'fail', 'errors': [{}]}), 'failed')

    def test_none_stale_failed_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = workspace('pet', tmp)
            self.assertEqual(case.status(ws)[0], 'none')
            out = ws / 'case' / 'out'
            out.mkdir()
            (out / 'parts.json').write_text('{}')
            (out / 'front.stl').write_text('solid')
            self.assertEqual(case.status(ws)[0], 'stale', 'built but never checked')
            report = {'passed': True, 'meshValidation': 'surface-v1', 'contractSha': case.sha(ws / 'boards' / 'main.enclosure.json'),
                      'partShas': {'front.stl': case.sha(out / 'front.stl'), 'parts.json': case.sha(out / 'parts.json')}}
            (ws / 'case' / 'reports').mkdir()
            (ws / 'case' / 'reports' / 'case-check.json').write_text(json.dumps(report))
            self.assertEqual(case.status(ws)[0], 'passed')
            old = {k: v for k, v in report.items() if k != 'meshValidation'}
            (ws / 'case' / 'reports' / 'case-check.json').write_text(json.dumps(old))
            self.assertEqual(case.status(ws)[0], 'stale', 'old bounding-box-only checks need a recheck')
            (ws / 'case' / 'reports' / 'case-check.json').write_text(json.dumps(report))
            (out / 'front.stl').write_text('solid changed')
            self.assertEqual(case.status(ws), ('stale', 'case/out/front.stl changed since the case was checked'))
            (out / 'front.stl').write_text('solid')
            (out / 'parts.json').write_text('{"parts": [{"name": "front", "role": "plunger"}]}')
            self.assertEqual(case.status(ws), ('stale', 'case/out/parts.json changed since the case was checked'),
                             'relabelling a shell as a plunger after the check is a new case')
            (out / 'parts.json').write_text('{}')
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

    def test_the_coupon_is_the_plate_around_the_screen_and_the_wall_around_the_port(self):
        from kicadpy.case_template import shapes
        with tempfile.TemporaryDirectory() as tmp:
            _, doc = workspace('pet', tmp)
            parts = shapes(doc)
            role, coupon = parts['coupon']
            self.assertEqual(role, 'coupon')
            front, back = parts['front'][1], parts['back'][1]
            volume = lambda w: sum(s.Volume() for s in w.solids().vals())
            self.assertLess(volume(coupon), (volume(front) + volume(back)) / 3, 'a few minutes of print, not the case')
            display = next(i for i in doc['interfaces'] if i['kind'] == 'display')
            usb = next(i for i in doc['interfaces'] if i['kind'] == 'usb')
            bb = coupon.val().BoundingBox()
            for item in (display, usb):
                x, y = item['centerMm']
                self.assertTrue(bb.xmin <= x <= bb.xmax and bb.ymin <= y <= bb.ymax, item['name'])
            self.assertGreaterEqual(len(coupon.solids().vals()), 1)
            # deck: no display on the board (the OLED is on wires) but a USB-C: the coupon is the port wall
            _, deck = workspace('deck', tmp)
            self.assertIn('coupon', shapes(deck))


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


class ContractIsStableTest(unittest.TestCase):
    def test_republishing_an_unchanged_board_keeps_the_case_fresh(self):
        # status() compares the contract's sha: the same board must give the same bytes every publish.
        state = json.loads((FIX / 'pet.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'design').mkdir()
            (ws / 'design' / 'main.kicad_pcb').write_text('')
            (ws / 'case').mkdir()
            (ws / 'case' / 'interfaces.json').write_text(json.dumps(INTENTS['pet']))
            first = enclosure.write(ws, inspect=lambda pcb: json.loads(json.dumps(state))).read_bytes()
            second = enclosure.write(ws, inspect=lambda pcb: json.loads(json.dumps(state))).read_bytes()
            self.assertEqual(first, second)


@unittest.skipIf(cq is None, 'CadQuery is not installed (run with toolchain/case-python)')
class MeshFrameTest(unittest.TestCase):
    def test_same_bounds_do_not_hide_a_closed_display_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'boards').mkdir()
            (ws / 'case/out').mkdir(parents=True)
            (ws / 'case/interfaces.json').write_text('{}')
            contract = {'board': {'widthMm': 20, 'heightMm': 20, 'thicknessMm': 1.6},
                        'parts': [], 'modules': [], 'mountingHoles': [],
                        'interfaces': [{'name': 'screen', 'kind': 'display', 'centerMm': [0, 0],
                                        'sizeMm': [4, 4], 'zMm': 1, 'side': 'top', 'access': [0, 0, 1], 'status': 'confirmed'}]}
            (ws / 'boards/main.enclosure.json').write_text(json.dumps(contract))
            closed = cq.Workplane('XY').box(30, 30, 2).translate((0, 0, 6))
            opened = closed.cut(cq.Workplane('XY').box(4.4, 4.4, 4).translate((0, 0, 6)))
            export(ws, {'front': ('shell', opened)})
            self.assertEqual(kinds(case.check(ws)), [])
            cq.exporters.export(closed, str(ws / 'case/out/front.stl'))
            report = case.check(ws)
            self.assertEqual(kinds(report), ['case_mesh_geometry_mismatch'])

    def test_reference_envelopes_are_hashed_but_not_assembled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = workspace('pet', tmp)
            from kicadpy.case_template import build_box
            out = build_box(ws)
            reference = cq.Workplane('XY').box(10, 10, 10)
            cq.exporters.export(reference, str(out / 'reference.stl'))
            cq.exporters.export(reference, str(out / 'reference.step'))
            manifest_path = out / 'parts.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['parts'].append({'name': 'ref_DO_NOT_PRINT', 'role': 'other', 'printable': False,
                                      'stl': 'reference.stl', 'step': 'reference.step'})
            manifest_path.write_text(json.dumps(manifest))
            report = case.check(ws)
            self.assertEqual(kinds(report), [])
            self.assertIn('reference.stl', report['partShas'])
            self.assertNotIn('ref_DO_NOT_PRINT', report['parts'])

    def test_an_stl_exported_in_print_orientation_is_caught(self):
        # 2026-10-12, claude-pet-case: STEP assembled, STL flipped face down and dropped to z = 0.
        # The checker read the STEP and passed; the Case tab drew the STL and the board sat outside.
        from kicadpy.case_template import build_box
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = workspace('pet', tmp)
            out = build_box(ws)
            self.assertEqual(kinds(case.check(ws)), [])
            front = cq.importers.importStep(str(out / 'front.step'))
            flipped = front.rotate((0, 0, 0), (1, 0, 0), 180)
            flipped = flipped.translate((0, 0, -flipped.val().BoundingBox().zmin))
            cq.exporters.export(flipped, str(out / 'front.stl'))
            report = case.check(ws)
            self.assertEqual(kinds(report), ['case_mesh_frame_mismatch'])
            self.assertIn('front', report['errors'][0]['message'])

    def test_stl_bounds_reads_binary_and_ascii(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = cq.Workplane('XY').box(2, 4, 6).translate((1, 2, 3))
            path = Path(tmp) / 'b.stl'
            cq.exporters.export(box, str(path))
            lo, hi = case.stl_bounds(path)
            self.assertEqual([round(v, 3) for v in lo + hi], [0, 0, 0, 2, 4, 6])
            path.write_text('solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 5 -2\n'
                            'endloop\nendfacet\nendsolid t\n')
            self.assertEqual(case.stl_bounds(path), ((0, 0, -2), (1, 5, 0)))


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


class VerdictTest(unittest.TestCase):
    def test_what_was_not_measured_outranks_a_review_and_a_failure_outranks_both(self):
        level = lambda *levels: case_measure.verdict([{'level': item} for item in levels])
        self.assertEqual(level(), 'pass')
        self.assertEqual(level('pass', 'review'), 'review')
        self.assertEqual(level('review', 'unverified'), 'unverified')
        self.assertEqual(level('unverified', 'fail'), 'fail')

    def test_export_tolerance_is_a_tenth_of_the_clearance_within_bounds(self):
        self.assertAlmostEqual(case_measure.tolerance_for(0.2), 0.02)
        self.assertEqual(case_measure.tolerance_for(0.05), 0.01)
        self.assertEqual(case_measure.tolerance_for(2.0), 0.05)


@unittest.skipIf(cq is None, 'CadQuery is not installed')
class MeasuredTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def stl(self, name, solid):
        path = self.dir / f'{name}.stl'
        cq.exporters.export(solid, str(path), tolerance=0.02, angularTolerance=0.1)
        return path

    def test_a_thick_block_passes_and_a_thin_web_is_located(self):
        block = cq.Workplane('XY').box(20, 20, 10)
        self.assertEqual(case_measure.check_walls('block', self.stl('block', block), 0.4)['level'], 'pass')
        slot = cq.Workplane('XY').box(18, 30, 30).translate((-0.8 - 9, 0, 6))
        result = case_measure.check_walls('web', self.stl('web', block.cut(slot)), 0.4)
        self.assertEqual(result['level'], 'pass', 'a 9 mm web')
        thin = block.cut(cq.Workplane('XY').box(19.4, 19.4, 30).translate((0, 0, 1.5)).intersect(cq.Workplane('XY').box(40, 40, 40)))
        result = case_measure.check_walls('thin', self.stl('thin', thin), 0.4)
        self.assertEqual(result['level'], 'review', result['message'])
        self.assertLess(result['thinnest'][0]['thicknessMm'], 0.75)

    def test_a_declared_skin_is_not_a_finding(self):
        block = cq.Workplane('XY').box(20, 20, 10)
        thin = block.cut(cq.Workplane('XY').box(19.4, 19.4, 30).translate((0, 0, 1.5)))
        skin = {'part': 'thin', 'centerMm': [0, 0, 0], 'radiusMm': 40, 'reason': 'light window'}
        self.assertEqual(case_measure.check_walls('thin', self.stl('thin', thin), 0.4, [skin])['level'], 'pass')

    def test_an_unreadable_mesh_is_unverified_not_passed(self):
        (self.dir / 'bad.stl').write_text('not a mesh')
        self.assertEqual(case_measure.check_walls('bad', self.dir / 'bad.stl', 0.4)['level'], 'unverified')

    def arm(self, gap):
        base = cq.Workplane('XY').box(20, 20, 10, centered=(True, True, False)).val()
        arm = cq.Workplane('XY').box(20, 4, 4, centered=(False, True, False)).translate((10 + gap, 0, 2)).val()
        return {'base': base, 'arm': arm}

    def sweep(self, gap, **joint):
        shapes = self.arm(gap)
        spec = {'name': 'j', 'originMm': [50, 0, 0], 'axis': [0, 0, 1], 'rangeDeg': [0, 0], 'moves': ['arm'],
                'clearOf': ['base'], 'minClearanceMm': 0.5, **joint}
        return case_measure.check_joints(cq, [spec], shapes.__getitem__)[0]

    def test_a_gap_is_failed_reviewed_or_passed_against_its_limit(self):
        self.assertEqual(self.sweep(0.2)['level'], 'fail')
        self.assertEqual(self.sweep(0.6)['level'], 'review')
        self.assertEqual(self.sweep(5.0)['level'], 'pass')

    def test_the_sweep_finds_a_collision_only_reached_at_the_end_of_travel(self):
        result = self.sweep(5.0, originMm=[15, 0, 0], rangeDeg=[0, 180])
        self.assertEqual(result['level'], 'fail', result['message'])
        self.assertEqual(result['closest']['gapMm'], 0.0)

    def test_a_declared_contact_is_not_a_collision(self):
        self.assertEqual(self.sweep(0.0, allowContact=[['arm', 'base']])['level'], 'unverified', 'nothing else to clear')

    def test_a_child_joint_rides_on_its_parent(self):
        shapes = self.arm(5.0)
        hip = {'name': 'hip', 'originMm': [100, 0, 0], 'axis': [0, 0, 1], 'rangeDeg': [0, 0], 'moves': [], 'clearOf': ['base']}
        knee = {'name': 'knee', 'parent': 'hip', 'originMm': [35, 0, 0], 'axis': [0, 0, 1], 'rangeDeg': [0, 0],
                'moves': ['arm'], 'clearOf': ['base']}
        moved = {**hip, 'rangeDeg': [-180, 180]}
        self.assertEqual(case_measure.check_joints(cq, [hip, knee], shapes.__getitem__)[1]['level'], 'pass')
        self.assertEqual(case_measure.check_joints(cq, [moved, knee], shapes.__getitem__)[1]['level'], 'pass', 'orbits clear of the base')

    def test_a_bad_declaration_is_unverified(self):
        self.assertEqual(self.sweep(5.0, moves=['missing'])['level'], 'unverified')

    def wires(self, *routes):
        box = cq.Workplane('XY').box(20, 20, 20).val()
        wire = {'name': 'w', 'clearDiameterMm': 8.0, 'routes': [{'name': str(i), 'pointsMm': r} for i, r in enumerate(routes)]}
        return case_measure.check_wires(cq, [wire], [('box', box)])[0]

    def test_a_wire_needs_a_second_open_route(self):
        through = [[-30, 0, 0], [30, 0, 0]]
        above, beside = [[-30, 0, 30], [30, 0, 30]], [[-30, 30, 0], [30, 30, 0]]
        self.assertEqual(self.wires(through)['level'], 'fail')
        self.assertEqual(self.wires(through, above)['level'], 'review')
        self.assertEqual(self.wires(above, beside)['level'], 'pass')
        self.assertEqual(self.wires([[0, 0, 30]])['level'], 'unverified')

    def test_a_fit_needs_a_coupon_in_the_test_kit(self):
        manifest = {'parts': [{'name': 'coupon', 'role': 'coupon'}, {'name': 'lid', 'role': 'lid'}]}
        levels = [r['level'] for r in case_measure.check_fits([{'name': 'a', 'testPart': 'coupon'}, {'name': 'b', 'testPart': 'lid'}], manifest)]
        self.assertEqual(levels, ['pass', 'review'])


@unittest.skipIf(cq is None, 'CadQuery is not installed')
class HandwiredPrintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws, _ = workspace('pet', self.tmp.name)
        export(self.ws, {'front': ('shell', cq.Workplane('XY').box(20, 20, 2))})
        self.out = self.ws / 'case/out'
        self.mechanical = self.ws / 'case/reports/mechanical.json'
        self.mechanical.parent.mkdir()
        manifest = json.loads((self.out / 'parts.json').read_text())
        manifest['verification'] = {'nativePCBCheckerApplies': False, 'report': '../reports/mechanical.json'}
        (self.out / 'parts.json').write_text(json.dumps(manifest))
        self.bind()

    def bind(self, **extra):
        self.mechanical.write_text(json.dumps({'passed': True, 'scope': 'handwired assembly', 'collisions': [],
            'partShas': {n: case.sha(self.out / n) for n in ('front.step', 'front.stl')}, **extra}))

    def test_print_passes_without_blessing_pcb_and_without_pcb_contract(self):
        self.assertFalse(case.check(self.ws, write=False)['passed'], 'this box collides with the native PCB')
        (self.ws / 'boards/main.enclosure.json').unlink()
        (self.ws / 'case/interfaces.json').write_text(json.dumps({'joints': [], 'wires': [], 'fits': []}))
        result = case.check_print(self.ws)
        self.assertTrue(result['passed'], result['errors'])
        self.assertEqual(result['verdict'], 'pass', result['checks'])
        self.assertEqual(result['validationScope'], 'handwired-print')
        self.assertEqual(result['verificationSha'], case.sha(self.mechanical))
        self.assertIn('parts.json', result['partShas'])
        self.assertFalse((self.ws / case.REPORT).exists(), 'the native Case report is untouched')

    def test_missing_changed_failed_and_collision_evidence_is_rejected(self):
        for changes in ({'passed': False}, {'collisions': [{}]}, {'partShas': {}}):
            self.bind(**changes)
            with self.assertRaises(ValueError): case.check_print(self.ws)
        self.bind()
        with (self.out / 'front.stl').open('a') as f: f.write('\nchanged')
        with self.assertRaises(ValueError): case.check_print(self.ws)

    def test_undeclared_joints_wires_and_fits_are_unverified(self):
        result = case.check_print(self.ws, write=False)
        self.assertFalse(result['passed'])
        self.assertEqual(result['verdict'], 'unverified')
        self.assertEqual(result['errors'], [])
        self.assertEqual({c['check'] for c in result['checks'] if c['level'] == 'unverified'}, {'joints', 'wires', 'fits'})

    def test_matching_bounds_cannot_hide_filled_stl_hole(self):
        box = cq.Workplane('XY').box(20, 20, 2)
        ring = box.cut(cq.Workplane('XY').circle(4).extrude(4,both=True))
        cq.exporters.export(ring, str(self.out / 'front.step'))
        cq.exporters.export(box, str(self.out / 'front.stl'))
        self.bind()
        result = case.check_print(self.ws)
        self.assertFalse(result['passed'])
        self.assertIn('case_mesh_geometry_mismatch', kinds(result))

    def test_escape_and_print_bed_changes(self):
        manifest = json.loads((self.out / 'parts.json').read_text())
        manifest['verification']['report'] = '../../outside.json'
        (self.out / 'parts.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): case.check_print(self.ws)
        manifest['verification']['report'] = '../reports/mechanical.json'
        (self.out / 'parts.json').write_text(json.dumps(manifest))
        (self.ws / 'case/interfaces.json').write_text(json.dumps({'printer': {'bedMm': [10,10,10]}}))
        result = case.check_print(self.ws)
        self.assertIn('case_part_too_big', kinds(result))
