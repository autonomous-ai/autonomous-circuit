"""The case contract: every board says what the person touches and sees, where, and how to open for it.

Fixtures are the real worker `inspect` of three boards with different interfaces (2026-10-12):
- pet:   a display on a socket, a SuperMini under the board (its USB-C is the only port), a belly button;
- rover: OLED, touch, two servo leads, an ultrasonic sensor on a header, WS2812s, a status LED, a buzzer,
         a USB-C power input, and edge-sensor headers the generator cannot place;
- deck:  four MX keys, an OLED, an encoder and an LED strip on wires, a SuperMini; page coordinates.
"""
import json
from pathlib import Path
import tempfile
import unittest

from kicadpy import enclosure

FIX = Path(__file__).parent / 'fixtures' / 'enclosure'
MODULES = Path(__file__).resolve().parents[2] / 'golden-blocks' / 'modules'


def fixture(name):
    return json.loads((FIX / f'{name}.json').read_text())


def build(name, intent=None):
    return enclosure.build(fixture(name), intent=intent, modules=enclosure.load_modules(MODULES))


def by_kind(doc):
    out = {}
    for i in doc['interfaces']:
        out.setdefault(i['kind'], []).append(i)
    return out


class ModulesTest(unittest.TestCase):
    def test_every_module_has_a_machine_readable_card(self):
        mods = enclosure.load_modules(MODULES)
        dirs = {p.name for p in MODULES.iterdir() if p.is_dir()}
        self.assertEqual(set(mods), dirs, 'each modules/<id>/ carries a module.json')
        for mid, m in mods.items():
            self.assertEqual(m['schema'], 1, mid)
            self.assertEqual(len(m['outlineMm']), 2, mid)
            self.assertTrue(m['headers'], mid)
            self.assertTrue(m['sources'], f'{mid}: every number says where it came from')
            for feat in m['features']:
                self.assertIn(feat['face'], ('front', 'back', 'edge-x', 'edge+x', 'edge-y', 'edge+y'), mid)
                self.assertIn(feat['kind'], enclosure.RULES, f'{mid}: {feat["kind"]} has an opening rule')

    def test_displays_carry_an_active_area_and_the_supermini_its_usb(self):
        mods = enclosure.load_modules(MODULES)
        for mid in ('st7789-1.54-module', 'oled-0.96-i2c-module'):
            area = next(f for f in mods[mid]['features'] if f['kind'] == 'display')
            self.assertLess(area['sizeMm'][0], mods[mid]['outlineMm'][0], 'the window is the active area, not the module')
        usb = next(f for f in mods['esp32-c3-supermini']['features'] if f['kind'] == 'usb')
        self.assertEqual(usb['face'], 'edge-x', 'USB-C at the pin-1 end')
        self.assertEqual(usb['plugMm'], [12.5, 7.0], 'the corridor is the plug overmold')


class FrameTest(unittest.TestCase):
    def test_origin_at_the_outline_centre_with_y_up(self):
        f = enclosure.Frame([100.0, 50.0, 160.0, 114.0])
        self.assertEqual(f.pt((130.0, 82.0)), [0.0, 0.0])
        self.assertEqual(f.pt((100.0, 50.0)), [-30.0, 32.0], 'KiCad top-left is up and left')
        self.assertEqual(f.box([100.0, 50.0, 110.0, 60.0]), [-30.0, 22.0, -20.0, 32.0])
        self.assertEqual(f.edge([-30.0, 0.0, -28.0, 2.0]), ('left', 0.0))

    def test_page_coordinates_are_brought_to_the_board(self):
        doc = build('deck')
        self.assertEqual((doc['board']['widthMm'], doc['board']['heightMm']), (76.0, 70.0))
        for i in doc['interfaces']:
            self.assertLessEqual(abs(i['centerMm'][0]), 38.0 + 6, i['name'])
            self.assertLessEqual(abs(i['centerMm'][1]), 35.0 + 6, i['name'])


class PetTest(unittest.TestCase):
    def setUp(self):
        self.doc = build('pet')
        self.kinds = by_kind(self.doc)

    def test_the_face_the_belly_and_the_port(self):
        self.assertEqual(sorted(self.kinds), ['antenna', 'button', 'display', 'usb'])
        display, = self.kinds['display']
        self.assertEqual(display['refs'], ['J2'])
        self.assertEqual(display['accessFace'], 'top')
        self.assertEqual(display['centerMm'], [0.0, 6.15], 'the module body hangs from its header toward the board centre')
        self.assertEqual(display['sizeMm'], [27.72, 27.72])
        belly = next(b for b in self.kinds['button'] if b['refs'] == ['SW1'])
        self.assertEqual((belly['accessFace'], belly['centerMm']), ('top', [0.0, -23.0]))

    def test_the_usb_is_on_the_supermini_under_the_board(self):
        usb, = self.kinds['usb']
        self.assertEqual((usb['module'], usb['side'], usb['accessFace']), ('esp32-c3-supermini', 'bottom', 'bottom-edge'))
        self.assertLess(usb['zMm'], -1.6, 'below the board')
        mcu = next(m for m in self.doc['modules'] if m['id'] == 'esp32-c3-supermini')
        self.assertEqual(sorted(mcu['refs']), ['J1', 'J3'], 'two sockets, one module')

    def test_test_points_named_after_nets_are_not_screens(self):
        refs = {r for i in self.doc['interfaces'] for r in i['refs']}
        self.assertNotIn('TP3', refs, 'TP3 is the 3V3_LCD net, not a display')
        self.assertEqual(len(self.doc['mountingHoles']), 4)
        self.assertEqual({h['fastener'] for h in self.doc['mountingHoles']}, {'M2'})


class RoverTest(unittest.TestCase):
    def setUp(self):
        self.doc = build('rover')
        self.kinds = by_kind(self.doc)

    def test_every_kind_the_robot_shows(self):
        for kind in ('display', 'touch', 'cable', 'usb', 'led', 'buzzer', 'ultrasonic', 'antenna'):
            self.assertIn(kind, self.kinds, kind)
        self.assertEqual(len(self.kinds['led']), 3, 'two WS2812 and the status LED')
        self.assertEqual(len(self.kinds['cable']), 2, 'two servo leads')
        self.assertEqual(sorted(u['refs'][0] for u in self.kinds['usb']), ['J1', 'J2'], 'power USB-C and the SuperMini')

    def test_the_esd_chip_named_usb_is_not_a_port(self):
        self.assertFalse(any(i['refs'] == ['U1'] for i in self.doc['interfaces']), 'USBLC6 is an ESD array')

    def test_a_sensor_on_a_header_is_off_board_and_unknown_headers_are_reported(self):
        sonar, = self.kinds['ultrasonic']
        self.assertTrue(sonar['offboard'])
        self.assertEqual(sonar['accessFace'], 'off-board')
        unplaced = sorted(f['ref'] for f in self.doc['findings'] if f['kind'] == 'enclosure_unclassified_connector')
        self.assertEqual(unplaced, ['J10', 'J11', 'J7'])


class PlacementTest(unittest.TestCase):
    def test_modules_that_cannot_be_where_they_were_put_are_findings(self):
        rover = {(f['kind'], f['ref']) for f in build('rover')['findings']}
        self.assertIn(('enclosure_modules_overlap', 'J6'), rover, 'OLED and touch module land on each other')
        self.assertIn(('enclosure_module_on_tall_part', 'J6'), rover, 'over the 16 mm bulk capacitor')
        self.assertFalse(any(k.startswith('enclosure_module') for k, _ in {(f['kind'], f['ref']) for f in build('pet')['findings']}),
                         'the pet board is consistent')

    def test_offboard_closes_the_finding(self):
        deck = {f['kind'] for f in build('deck')['findings']}
        self.assertIn('enclosure_modules_overlap', deck, "the deck's OLED cannot sit on the SuperMini")
        fixed = build('deck', {'interfaces': {'J3': {'offboard': True, 'confirm': True}}})
        self.assertNotIn('enclosure_modules_overlap', {f['kind'] for f in fixed['findings']})
        oled = next(i for i in fixed['interfaces'] if i['kind'] == 'display')
        self.assertEqual((oled['accessFace'], oled.get('offboard')), ('off-board', True))


class DeckTest(unittest.TestCase):
    def test_keys_knob_and_light_on_wires(self):
        kinds = by_kind(build('deck'))
        self.assertEqual(sorted(k['refs'][0] for k in kinds['keyswitch']), ['SW1', 'SW2', 'SW3', 'SW4'])
        self.assertIn('14.0 x 14.0', kinds['keyswitch'][0]['rule'])
        knob, = kinds['knob']
        led, = kinds['led']
        self.assertTrue(knob['offboard'] and led['offboard'], 'encoder and LED strip hang off headers')
        usb, = kinds['usb']
        self.assertEqual(usb['accessFace'], 'top-edge')
        self.assertEqual(len(kinds['display']), 1, 'OLED_SCL/OLED_SDA test points are not displays')


class IntentTest(unittest.TestCase):
    def test_confirm_override_ignore_add_and_orientation(self):
        intent = {'orientation': {'mount': 'upright', 'faceToUser': 'top', 'upEdge': 'top'},
                  'interfaces': {'J2': {'confirm': True, 'note': 'face window'},
                                 'SW1': {'kind': 'touch'},
                                 'J1': {'ignore': True},
                                 'Q9': {'confirm': True}},
                  'extra': [{'name': 'lanyard slot', 'kind': 'cable', 'centerMm': [0, 30]}]}
        doc = build('pet', intent)
        status = {i['refs'][0] if i.get('refs') else i['name']: i['status'] for i in doc['interfaces']}
        self.assertEqual(status['J2'], 'confirmed')
        self.assertEqual(status['SW1'], 'overridden')
        self.assertEqual(status['J1'], 'ignored', 'the module features follow their first header ref')
        self.assertEqual(status['lanyard slot'], 'declared')
        self.assertEqual(doc['orientation']['mount'], 'upright')
        kinds = {f['kind'] for f in doc['findings']}
        self.assertIn('enclosure_intent_unknown_ref', kinds, 'Q9 is not on the board')
        self.assertNotIn('J2', [f.get('ref') for f in doc['findings'] if f['kind'] == 'enclosure_interface_unconfirmed'])

    def test_one_feature_of_a_module_can_be_ignored_without_hiding_the_port(self):
        # Ignoring the SuperMini's BOOT/RESET must not ignore its USB-C, the pet's only port.
        doc = build('pet', {'interfaces': {'J1#button': {'ignore': True}, 'J1#usb': {'confirm': True}}})
        status = {i['key']: i['status'] for i in doc['interfaces']}
        self.assertEqual(status['J1#button'], 'ignored')
        self.assertEqual(status['J1#usb'], 'confirmed')
        self.assertEqual(status['J1#antenna'], 'proposed')
        self.assertNotIn('enclosure_intent_unknown_ref', {f['kind'] for f in doc['findings']})

    def test_seat_height_from_intent_or_parts_json_moves_the_display_face(self):
        # The pet's sockets are Samtec CES, 5.08 mm, not the 8.5 mm table default.
        display = lambda doc: next(i for i in doc['interfaces'] if i['kind'] == 'display')
        assumed = display(build('pet'))
        self.assertEqual(assumed['zMm'], 13.0)
        declared = build('pet', {'interfaces': {'J2': {'seatHeightMm': 5.08}}})
        self.assertEqual(display(declared)['zMm'], 9.58)
        self.assertEqual(next(m for m in declared['modules'] if m['refs'] == ['J2'])['seatSource'], 'case/interfaces.json')
        recorded = enclosure.build(fixture('pet'), parts_doc={'parts': [{'refdes': ['J1', 'J2', 'J3'], 'mpn': 'CES-108-01-T-S', 'seatHeightMm': 5.08}]},
                                   modules=enclosure.load_modules(MODULES))
        self.assertEqual(display(recorded)['zMm'], 9.58)
        self.assertFalse(any('seat height' in a and 'st7789' in a for a in recorded['assumptions']))
        tall = enclosure.build(fixture('pet'), intent={'interfaces': {'SW1': {'heightMm': 5.0}}}, modules=enclosure.load_modules(MODULES))
        self.assertEqual(next(p for p in tall['parts'] if p['ref'] == 'SW1')['heightSource'], 'case/interfaces.json')

    def test_unconfirmed_interfaces_are_findings(self):
        doc = build('pet')
        self.assertTrue(all(i['status'] == 'proposed' for i in doc['interfaces']))
        self.assertEqual(sum(f['kind'] == 'enclosure_interface_unconfirmed' for f in doc['findings']), len(doc['interfaces']))


class WriteTest(unittest.TestCase):
    def test_written_beside_the_sidecar_never_into_design_or_the_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'design').mkdir()
            (ws / 'design' / 'main.kicad_pcb').write_text('(kicad_pcb)')
            (ws / 'case').mkdir()
            (ws / 'case' / 'interfaces.json').write_text(json.dumps({'interfaces': {'J2': {'confirm': True}}}))
            target = enclosure.write(ws, inspect=lambda pcb: fixture('pet'))
            self.assertEqual(target, ws / 'boards' / 'main.enclosure.json')
            doc = json.loads(target.read_text())
            self.assertEqual(doc['schema'], 2)
            self.assertEqual(doc['entryKind'], 'enclosure', "Copper's shape, extended")
            for key in ('board', 'mountingHoles', 'connectors', 'clearance', 'notes'):
                self.assertIn(key, doc, 'every v1 key is still there')
            self.assertEqual(next(i for i in doc['interfaces'] if i['refs'] == ['J2'])['status'], 'confirmed')
            self.assertEqual(sorted(p.name for p in (ws / 'design').iterdir()), ['main.kicad_pcb'])

    def test_case_folder_is_not_a_design_input(self):
        # kicadpy's revision manifest of a workspace: design/, engineering/ and root JSONs only.
        # Writing the intent after the board went green must not change it (it would mark the
        # board stale), while an engineering note does — so this test can fail.
        from kicadpy.project import manifest
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'design').mkdir()
            (ws / 'design' / 'main.kicad_pro').write_text('{}')
            (ws / 'design' / 'main.kicad_pcb').write_text('(kicad_pcb)')
            (ws / 'product.json').write_text('{}')
            before = manifest(ws)
            self.assertIn('design/main.kicad_pcb', before)
            (ws / 'case').mkdir()
            (ws / 'case' / 'interfaces.json').write_text('{"interfaces": {}}')
            (ws / 'boards').mkdir()
            (ws / 'boards' / 'main.enclosure.json').write_text('{}')
            self.assertEqual(manifest(ws), before, 'case/ and the contract are not inputs')
            (ws / 'engineering').mkdir()
            (ws / 'engineering' / 'power.md').write_text('x')
            self.assertNotEqual(manifest(ws), before, 'control: an engineering note is an input')

if __name__ == '__main__':
    unittest.main()
