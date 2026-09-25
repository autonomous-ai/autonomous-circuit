"""kicadpy.verify: the measurements an agent runs instead of trusting its own summary.

Offline: the netlist and PCB are inline S-expressions, the EasyEDA and JLCPCB answers are the
shapes those services returned on 2026-09-24 (trimmed). The pcbnew `islands` worker needs
KiCad and is exercised only when it is installed.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from kicadpy import verify

NETLIST = '''(export (version "E")
  (components
    (comp (ref "R1") (value "1k"))
    (comp (ref "U2") (value "AMS1117"))
    (comp (ref "C1") (value "10uF"))
    (comp (ref "H1") (value "MountingHole")))
  (nets
    (net (code "1") (name "/GND") (node (ref "R1") (pin "2")) (node (ref "C1") (pin "2")))
    (net (code "2") (name "V3_3") (node (ref "R1") (pin "1")) (node (ref "C1") (pin "1")))))'''

PCB = '''(kicad_pcb (version 20250120) (generator "test")
  (footprint "Resistor_SMD:R_0402_1005Metric" (layer "F.Cu") (at 10 10 90)
    (property "Reference" "R1" (at 0 0 0))
    (pad "1" smd roundrect (at -0.51 0) (size 0.54 0.64) (net "V3_3"))
    (pad "2" smd roundrect (at 0.51 0) (size 0.54 0.64) (net "GND")))
  (footprint "Capacitor_SMD:C_0402_1005Metric" (layer "F.Cu") (at 12 10)
    (property "Reference" "C1" (at 0 0 0))
    (pad "1" smd roundrect (at -0.48 0) (size 0.56 0.62) (net 2 "V3_3"))
    (pad "2" smd roundrect (at 0.48 0) (size 0.56 0.62) (net 1 "VBUS")))
  (footprint "Package_TO_SOT_SMD:SOT-223-3_TabPin2" (layer "F.Cu") (at 20 10)
    (property "Reference" "U2" (at 0 0 0))
    (pad "1" smd rect (at -2.3 3.15) (size 1.2 2) (net "GND"))
    (pad "2" smd rect (at 0 3.15) (size 1.2 2) (net "V3_3"))
    (pad "2" smd rect (at 0 -3.15) (size 3.3 2) (net "V3_3"))
    (pad "3" smd rect (at 2.3 3.15) (size 1.2 2) (net "VBUS")))
  (footprint "MountingHole:MountingHole_2.2mm_M2" (layer "F.Cu") (at 3 3)
    (property "Reference" "H1" (at 0 0 0))
    (pad "" np_thru_hole circle (at 0 0) (size 2.2 2.2) (drill 2.2))))'''


class NetlistTest(unittest.TestCase):
    def test_pcb_pads_reads_both_net_forms_and_the_footprint_frame(self):
        pads, footprints = verify.pcb_pads(PCB)
        self.assertEqual(pads['R1.2'], 'GND')
        self.assertEqual(pads['C1.2'], 'VBUS')          # numbered (net 1 "VBUS") form
        self.assertEqual({f['reference'] for f in footprints}, {'R1', 'C1', 'U2', 'H1'})
        r1 = next(f for f in footprints if f['reference'] == 'R1')
        self.assertEqual(r1['rotationDeg'], 90.0)
        self.assertEqual(r1['pads'][0]['x'], -0.51)     # pad positions stay in the footprint's own frame

    def test_compare_finds_the_mismatch_and_the_hollow_symbol(self):
        pins, refs = verify.schematic_pins(NETLIST)
        pads, _ = verify.pcb_pads(PCB)
        out = verify.compare_netlists(pins, refs, pads)
        # /GND (a sheet-local label) and GND are the same net once normalised: no false alarm
        self.assertNotIn({'pad': 'R1.2', 'schematic': '/GND', 'pcb': 'GND'}, out['differences'])
        # C1.2 is VBUS on copper but GND in the drawing: the real conflict
        self.assertIn({'pad': 'C1.2', 'schematic': '/GND', 'pcb': 'VBUS'}, out['differences'])
        # U2 exists in the schematic but no pin of it is wired while its pads carry nets: hollow,
        # like Grok's four ICs. H1 has no pins and a net-less hole: not hollow.
        self.assertEqual(out['hollowSymbols'], ['U2'])
        # U2's pads exist on the PCB with nets the schematic never gave them
        self.assertTrue(any(d['pad'] == 'U2.1' and d['schematic'] == '' for d in out['differences']))
        # ... and the drawing used a sheet-local label for GND: named, so the agent switches to global labels
        self.assertEqual(out['sheetLocalNets'], ['/GND'])


class HollowFindingsTest(unittest.TestCase):
    def test_one_error_per_reference_in_plain_words(self):
        rows = verify.hollow_findings(['U1', 'U4'])
        self.assertEqual([r['part'] for r in rows], ['U1', 'U4'])
        self.assertTrue(all(r['severity'] == 'error' and r['kind'] == 'schematic_hollow_symbol' for r in rows))
        self.assertIn('U4: no pin of it is wired', rows[1]['message'])
        self.assertEqual(verify.hollow_findings([]), [])


class RotationTest(unittest.TestCase):
    def test_offset_is_the_rotation_that_maps_easyeda_pads_onto_kicad_pads(self):
        # A three-pad SOT-23 as KiCad draws it (pads 1,2 left column, 3 right) ...
        kicad = [{'number': '1', 'x': -1.0, 'y': -0.95}, {'number': '2', 'x': -1.0, 'y': 0.95}, {'number': '3', 'x': 1.0, 'y': 0.0}]
        footprints = [{'reference': 'Q1', 'lib': 'SOT-23', 'pads': kicad}]
        # ... and the same pads as EasyEDA holds them, turned by 270 degrees.
        eda_pads = [{'number': n, 'x': x, 'y': y} for n, (x, y) in
                    zip(('1', '2', '3'), (verify._rot(p['x'], p['y'], 90) for p in kicad))]
        rows = verify.rotation_offsets(footprints, {'Q1': 'C20917'}, {'C20917': {'pads': eda_pads}})
        self.assertEqual(rows[0]['rotationOffsetDeg'], 270)
        self.assertLess(rows[0]['meanErrorMm'], 0.01)
        self.assertEqual(rows[0]['method'], 'pad number')
        self.assertFalse(rows[0]['poorMatch'])

    def test_numbering_styles_fall_back_to_geometry_and_a_missing_part_is_skipped(self):
        kicad = [{'number': '1', 'x': -3.0, 'y': -2.0}, {'number': '1', 'x': -3.0, 'y': 2.0},
                 {'number': '2', 'x': 3.0, 'y': -2.0}, {'number': '2', 'x': 3.0, 'y': 2.0}]
        eda = [{'number': n, 'x': x, 'y': y} for n, x, y in (('1', -3, -2), ('2', -3, 2), ('3', 3, -2), ('4', 3, 2))]
        rows = verify.rotation_offsets([{'reference': 'SW1', 'lib': 'TS-1187A', 'pads': kicad},
                                        {'reference': 'R9', 'lib': 'R_0402', 'pads': kicad}],
                                       {'SW1': 'C318884'}, {'C318884': {'pads': eda}})
        self.assertEqual([r['reference'] for r in rows], ['SW1'])
        self.assertEqual(rows[0]['rotationOffsetDeg'], 0)
        self.assertIn('geometry', rows[0]['method'])

    def test_the_knowledge_table_fills_an_unfetched_part_and_flags_a_disagreement(self):
        kicad = [{'number': '1', 'x': -1.0, 'y': -0.95}, {'number': '2', 'x': -1.0, 'y': 0.95}, {'number': '3', 'x': 1.0, 'y': 0.0}]
        eda = [{'number': n, 'x': x, 'y': y} for n, (x, y) in zip(('1', '2', '3'), (verify._rot(p['x'], p['y'], 90) for p in kicad))]
        footprints = [{'reference': 'Q1', 'lib': 'SOT-23', 'pads': kicad}, {'reference': 'U9', 'lib': 'SOIC-8', 'pads': kicad},
                      {'reference': 'R1', 'lib': 'R_0402', 'pads': kicad}]
        rows = verify.rotation_offsets(footprints, {'Q1': 'C1', 'U9': 'C9', 'R1': 'C5'}, {'C1': {'pads': eda}},
                                       table={'C1': 180, 'C9': 270})
        by = {r['reference']: r for r in rows}
        # Q1 was measured (270) and the table says 180: both numbers shown, disagreement flagged, nothing overwritten
        self.assertEqual((by['Q1']['rotationOffsetDeg'], by['Q1']['tableOffsetDeg'], by['Q1']['agreesWithTable']), (270, 180, False))
        # U9 was never fetched from EasyEDA: the table answers, and says so
        self.assertEqual((by['U9']['rotationOffsetDeg'], by['U9']['method'], by['U9']['meanErrorMm']), (270, 'knowledge table', None))
        # R1: no fetch, no row — the CLI lists it under `unknown`
        self.assertNotIn('R1', by)


class IdentityTest(unittest.TestCase):
    def test_stock_rows_carry_what_earlier_runs_verified_and_flag_a_different_part(self):
        live = {'C2687116': {'componentCode': 'C2687116', 'componentModelEn': 'USBLC6-2SC6', 'componentBrandEn': 'UMW'},
                'C7519': {'componentCode': 'C7519', 'componentModelEn': 'SOMETHING-ELSE', 'componentBrandEn': 'X'},
                'C000': {'error': 'not found'}}
        table = {'C2687116': {'manufacturer': 'UMW', 'mpn': 'USBLC6-2SC6', 'note': 'UMW clone, not ST'},
                 'C7519': {'manufacturer': 'ST', 'mpn': 'USBLC6-2SC6'}}
        out = verify.attach_identity(live, part_of=table.get)
        self.assertEqual(out['C2687116']['known']['note'], 'UMW clone, not ST')
        self.assertFalse(out['C2687116']['identityMismatch'])
        self.assertTrue(out['C7519']['identityMismatch'])
        self.assertNotIn('known', out['C000'])


class EasyedaAndJlcTest(unittest.TestCase):
    def test_easyeda_pads_are_scaled_to_mm_and_centred(self):
        result = {'title': 'AO3400A', 'package': 'SOT-23', 'updateTime': 1,
                  'dataStr': {'head': {'c_para': {'Manufacturer': 'AOS', 'Manufacturer Part': 'AO3400A'}}},
                  'packageDetail': {'title': 'SOT-23-3_L2.9-W1.6-P0.95-LS2.8-BR', 'dataStr': {
                      'head': {'x': 4000, 'y': 3000},
                      'shape': ['PAD~RECT~3996.26~3003.74~3.5~4.7~1~~1~0~~90~gge1~0~~Y~0~0~0.2~',
                                'PAD~RECT~4003.74~3003.74~3.5~4.7~1~~2~0~~90~gge2~0~~Y~0~0~0.2~',
                                'PAD~RECT~4000~2996.26~3.5~4.7~1~~3~0~~90~gge3~0~~Y~0~0~0.2~',
                                'TRACK~1~3~~4000 2996 4001 2997~gge4~0']}}}
        entry = verify.easyeda_entry(result)
        self.assertEqual(entry['mpn'], 'AO3400A')
        self.assertEqual(entry['footprint'], 'SOT-23-3_L2.9-W1.6-P0.95-LS2.8-BR')
        pads = entry['pads']
        self.assertEqual([p['number'] for p in pads], ['1', '2', '3'])
        self.assertAlmostEqual(pads[0]['w'], 3.5 * 0.254, places=4)
        self.assertAlmostEqual(sum(p['x'] for p in pads), 0, places=3)
        self.assertAlmostEqual(pads[1]['x'] - pads[0]['x'], 7.48 * 0.254, places=3)

    def test_jlc_row_matches_exactly_this_code_or_names_the_candidates(self):
        data = {'data': {'componentPageInfo': {'list': [
            {'componentCode': 'C20917', 'componentLibraryType': 'base', 'stockCount': 442600,
             'componentBrandEn': 'AOS', 'componentModelEn': 'AO3400A', 'urlSuffix': '/x/C20917', 'componentPrices': []},
            {'componentCode': 'C209170', 'erpComponentName': 'other', 'urlSuffix': '/x/C209170'}]}}}
        row = verify.jlc_row(data, 'C20917')
        self.assertEqual(row['componentLibraryType'], 'base')
        self.assertEqual(row['stockCount'], 442600)
        missing = verify.jlc_row(data, 'C99999')
        self.assertEqual(missing['error'], 'not found')
        self.assertIn(('C20917', None), missing['candidates'])


class GateTest(unittest.TestCase):
    def test_gate_summary_reads_the_sidecar_check_and_manufacturing_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'boards').mkdir()
            (ws / 'boards' / 'main.board.json').write_text(json.dumps({
                'fab': {'ready': False}, 'validation': {'warnings': [
                    {'severity': 'error', 'kind': 'unconnected_items'}, {'severity': 'warning', 'kind': 'net_conflict'},
                    {'severity': 'warning', 'kind': 'net_conflict'}]}}))
            rev = ws / 'boards' / 'main_review' / 'abc-123'
            (rev / 'reports').mkdir(parents=True)
            (rev / 'manufacturing').mkdir()
            (rev / 'reports' / 'check.json').write_text(json.dumps({'passed': False, 'findings': [
                {'stage': 'drc', 'type': 'clearance', 'severity': 'error'},
                {'stage': 'erc', 'type': 'endpoint_off_grid', 'severity': 'warning'},
                {'stage': 'erc', 'type': 'endpoint_off_grid', 'severity': 'warning'}]}))
            (rev / 'manufacturing' / 'manufacturing-report.json').write_text(json.dumps({'prototypeReady': False, 'findings': [
                {'severity': 'error', 'kind': 'bom_identity'}]}))
            out = verify.gate_summary(ws)
        self.assertFalse(out['sidecar']['fabReady'])
        self.assertEqual((out['sidecar']['errors'], out['sidecar']['warnings']), (1, 2))
        self.assertEqual(out['sidecar']['byKind'][0], {'severity': 'warning', 'kind': 'net_conflict', 'count': 2})
        self.assertEqual(out['check']['findings'], 3)
        self.assertEqual(out['check']['byType'][0]['type'], 'endpoint_off_grid')
        self.assertEqual(out['manufacturing']['byKind'], [{'severity': 'error', 'kind': 'bom_identity', 'count': 1}])

    def test_gate_summary_on_an_unpublished_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = verify.gate_summary(tmp)
        self.assertIsNone(out['sidecar'])
        self.assertIsNone(out['check'])


@unittest.skipUnless(os.path.isfile('/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3')
                     or os.environ.get('KICADPY_PYTHON'), 'KiCad Python not installed')
class PowerTest(unittest.TestCase):
    def test_capacitor_values_parse_and_resistors_do_not(self):
        self.assertAlmostEqual(verify.cap_farads('10uF'), 10e-6)
        self.assertAlmostEqual(verify.cap_farads('100nF'), 100e-9)
        self.assertAlmostEqual(verify.cap_farads('4.7u'), 4.7e-6)
        self.assertAlmostEqual(verify.cap_farads('0.1µF'), 100e-9)
        self.assertAlmostEqual(verify.cap_farads('15pF'), 15e-12)
        self.assertIsNone(verify.cap_farads('10k'))
        self.assertIsNone(verify.cap_farads('AMS1117-3.3'))

    def test_rail_totals_and_decoupling_distance(self):
        # harness-17's shape: three caps hang on VBUS (30 uF against a 10 uF limit); the IC's
        # DVDD pin has its cap 7 mm away, its IOVDD pin has one 1.5 mm away, VREG_IN has none.
        fps = [
            {'ref': 'C1', 'value': '10uF', 'pads': [{'number': '1', 'net': 'VBUS', 'x': 0, 'y': 0}, {'number': '2', 'net': 'GND', 'x': 1, 'y': 0}]},
            {'ref': 'C2', 'value': '10uF', 'pads': [{'number': '1', 'net': '/VBUS', 'x': 5, 'y': 0}, {'number': '2', 'net': '/GND', 'x': 6, 'y': 0}]},
            {'ref': 'C21', 'value': '10u', 'pads': [{'number': '1', 'net': 'VBUS', 'x': 9, 'y': 0}, {'number': '2', 'net': 'GND', 'x': 10, 'y': 0}]},
            {'ref': 'C12', 'value': '1uF', 'pads': [{'number': '1', 'net': 'DVDD', 'x': 20, 'y': 7}, {'number': '2', 'net': 'GND', 'x': 21, 'y': 7}]},
            {'ref': 'C4', 'value': '100nF', 'pads': [{'number': '1', 'net': 'V3_3', 'x': 21.5, 'y': 0}, {'number': '2', 'net': 'GND', 'x': 22.5, 'y': 0}]},
            {'ref': 'C9', 'value': '100nF', 'pads': [{'number': '1', 'net': 'V3_3', 'x': 40, 'y': 0}, {'number': '2', 'net': 'LCD_CS', 'x': 41, 'y': 0}]},   # series cap: not a rail total
            {'ref': 'R1', 'value': '10k', 'pads': [{'number': '1', 'net': 'VBUS', 'x': 0, 'y': 5}, {'number': '2', 'net': 'GND', 'x': 1, 'y': 5}]},
            {'ref': 'U3', 'value': 'RP2040', 'pads': [{'number': '23', 'net': 'DVDD', 'x': 20, 'y': 0}, {'number': '42', 'net': 'V3_3', 'x': 20, 'y': 0},
                                                      {'number': '44', 'net': 'VREG_IN', 'x': 20, 'y': 1}, {'number': '19', 'net': 'GND', 'x': 20, 'y': 2},
                                                      {'number': '30', 'net': 'LCD_CS', 'x': 20, 'y': 3}]},
        ]
        out = verify.power_report(fps, rails={'VBUS': 10.0}, limit_mm=3.0)
        self.assertEqual(out['rails']['VBUS']['uF'], 30.0)
        self.assertEqual(out['overLimit'], ['VBUS'])
        self.assertNotIn('LCD_CS', out['rails'])
        by = {f"{q['ref']}.{q['pad']}": q for q in out['pins']}
        self.assertEqual(set(by), {'U3.23', 'U3.42', 'U3.44'})          # GND and signal pads are not power pins
        self.assertEqual(by['U3.23']['distanceMm'], 7.0); self.assertTrue(by['U3.23']['overLimit'])
        self.assertEqual(by['U3.42']['distanceMm'], 1.5); self.assertFalse(by['U3.42']['overLimit'])
        self.assertIsNone(by['U3.44']['distanceMm']); self.assertTrue(by['U3.44']['overLimit'])
        self.assertEqual(out['pinsOverLimit'], ['U3.23', 'U3.44'])
        kinds = [f['kind'] for f in verify.power_findings(out)]
        self.assertEqual(kinds, ['rail_capacitance', 'decoupling_distance', 'decoupling_distance'])
        self.assertEqual(verify.power_findings(out)[0]['severity'], 'error')
        # an ESD array's VBUS pin is a clamp reference: the knowledge table's noSupplyDecoupling skips it
        fps.append({'ref': 'U1', 'value': 'USBLC6-2SC6', 'pads': [{'number': '5', 'net': 'VBUS', 'x': 60, 'y': 0}]})
        self.assertIn('U1.5', verify.power_report(fps, limit_mm=3.0)['pinsOverLimit'])
        self.assertNotIn('U1.5', verify.power_report(fps, limit_mm=3.0, skip_refs=['U1'])['pinsOverLimit'])
        from kicadpy import knowledge
        self.assertTrue(knowledge.part('C2687116').get('noSupplyDecoupling'))


class EnablesTest(unittest.TestCase):
    def test_a_pin_rule_from_the_table_is_checked_against_the_copper(self):
        table = {'C7484': {'mpn': 'SN74AHCT1G125', 'pinRules': [{'pin': '1', 'name': '/OE', 'require': 'GND', 'why': 'OE high = Hi-Z'}]}}
        pads = {'U6.1': '/VBUS', 'U6.2': 'LED_DATA', 'U7.1': 'GND'}
        out = verify.enables_check(pads, {'U6': 'C7484', 'U7': 'C7484', 'U1': 'C2687116'}, table.get)
        self.assertEqual(out['violations'], ['U6.1'])
        self.assertEqual([r['ok'] for r in out['rows']], [False, True])
        # the shipped table carries the rule that would have caught harness-17
        from kicadpy import knowledge
        self.assertEqual(knowledge.part('C7484')['pinRules'][0]['require'], 'GND')


class ModulesTest(unittest.TestCase):
    CARD = """# st7789
| Header pin | Name | Board side | Notes |
|---|---|---|---|
| 1 | GND | GND | |
| 2 | VCC | 3V3 | |
| 3 | SCL | GPIO SCK | |
| 4 | SDA | GPIO MOSI | |
| 5 | RES | GPIO | |
| 6 | DC | GPIO | |
| 7 | CS | GPIO | |
| 8 | BLK | see below | |
"""

    def test_header_order_is_compared_pin_by_pin_with_aliases(self):
        pins = verify.module_card_pins(self.CARD)
        self.assertEqual([n for n, _ in pins], ['1', '2', '3', '4', '5', '6', '7', '8'])
        # harness-14's order: DC on 5, CS on 6, RST on 7 — a straight cable will not work
        pads = {'J2.1': 'GND', 'J2.2': '/V3_3', 'J2.3': '/LCD_SCK', 'J2.4': '/LCD_MOSI', 'J2.5': '/LCD_DC', 'J2.6': '/LCD_CS', 'J2.7': '/LCD_RST', 'J2.8': '/LCD_BLK'}
        out = verify.modules_check(pads, 'J2', pins)
        self.assertEqual(out['mismatches'], ['5', '6', '7'])
        # harness-18's order matches the module
        pads = {'J2.1': 'GND', 'J2.2': 'V3_3', 'J2.3': 'LCD_SCK', 'J2.4': 'LCD_MOSI', 'J2.5': 'LCD_RST', 'J2.6': 'LCD_DC', 'J2.7': 'LCD_CS', 'J2.8': 'LCD_BLK'}
        self.assertEqual(verify.modules_check(pads, 'J2', pins)['mismatches'], [])


class StaleTest(unittest.TestCase):
    def test_sources_newer_than_the_sidecar_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'design').mkdir(); (ws / 'boards').mkdir(); (ws / 'engineering').mkdir()
            (ws / 'design' / 'main.kicad_pcb').write_text('(kicad_pcb)')
            (ws / 'parts.json').write_text('{}')
            self.assertEqual(verify.stale(ws)['sidecar'], None)
            side = ws / 'boards' / 'main.board.json'; side.write_text('{}')
            old = side.stat().st_mtime - 100
            os.utime(ws / 'design' / 'main.kicad_pcb', (old, old)); os.utime(ws / 'parts.json', (old, old))
            self.assertEqual(verify.stale(ws)['stale'], [])
            new = side.stat().st_mtime + 100
            os.utime(ws / 'design' / 'main.kicad_pcb', (new, new))
            (ws / 'engineering' / 'thermal.md').write_text('# t'); os.utime(ws / 'engineering' / 'thermal.md', (new, new))
            self.assertEqual(verify.stale(ws)['stale'], ['design/main.kicad_pcb', 'engineering/thermal.md'])


class IslandsNetNameTest(unittest.TestCase):
    def test_a_missing_net_is_retried_with_the_sheet_local_spelling(self):
        calls = []
        def fake_worker(op, pcb, net, near):
            calls.append(net)
            return {'net': net, 'pads': [{'ref': 'C1', 'pad': '2'}] if net == '/GND' else [], 'layers': {}, 'sites': []}
        with patch.object(verify.toolchain, 'worker', fake_worker):
            out = verify.islands('design/main.kicad_pro', net='GND')
        self.assertEqual(calls, ['GND', '/GND'])
        self.assertEqual((out['net'], out['requestedNet'], len(out['pads'])), ('/GND', 'GND', 1))
        self.assertIn('sheet-local', out['note'])


class IslandsWorkerTest(unittest.TestCase):
    def test_islands_reports_the_fixture_without_writing(self):
        fixtures = Path(__file__).parent / 'fixtures' / 'tiny'
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(fixtures, Path(tmp) / 'tiny')
            pcb = Path(tmp) / 'tiny' / 'tiny.kicad_pcb'
            before = pcb.read_bytes()
            out = verify.islands(pcb.with_suffix('.kicad_pro'), net='GND', near=[15, 10, 1.0])
            self.assertEqual(pcb.read_bytes(), before)
        self.assertEqual(out['net'], 'GND')
        self.assertIn('layers', out)
        self.assertIn('strandedIslands', out)
        self.assertIsInstance(out['sites'], list)

    def test_pads_come_back_in_board_coordinates_and_copper_area_is_zero_without_zones(self):
        fixtures = Path(__file__).parent / 'fixtures' / 'tiny'
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(fixtures, Path(tmp) / 'tiny')
            pcb = Path(tmp) / 'tiny' / 'tiny.kicad_pcb'
            fps = verify.toolchain.worker('pads', pcb)['footprints']
            self.assertEqual(sorted(f['ref'] for f in fps), ['TP1', 'TP2', 'TP3', 'TP4'])
            self.assertTrue(all('x' in pd and 'net' in pd and 'layers' in pd for f in fps for pd in f['pads']))
            area = verify.thermal(pcb.with_suffix('.kicad_pro'), 'TP1', fps[0]['pads'][0]['number'] if fps[0]['ref'] == 'TP1' else next(f for f in fps if f['ref'] == 'TP1')['pads'][0]['number'])
            self.assertTrue(all(info['areaMm2'] == 0 for info in area['layers'].values()))


if __name__ == '__main__':
    unittest.main()
