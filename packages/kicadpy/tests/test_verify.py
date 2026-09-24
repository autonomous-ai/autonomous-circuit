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


if __name__ == '__main__':
    unittest.main()
