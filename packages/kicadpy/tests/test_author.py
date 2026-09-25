"""kicadpy.author: a schematic from a net table, with the four holes every emitter fell into closed.

The symbol libraries come from KiCad's bundle, so the writing tests need KiCad on the machine;
the ERC/netlist round trip needs kicad-cli as well. Both skip elsewhere.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from kicadpy import author, verify
from kicadpy.sexp import parse

KICAD = Path('/Applications/KiCad/KiCad.app/Contents')
HAVE_LIBS = (KICAD / 'SharedSupport' / 'symbols' / 'Device.kicad_sym').is_file()

SPEC = {
    'title': 'author test', 'rev': 'T', 'lib': 't', 'stem': 'main',
    'libraries': ['Device', 'power', 'Regulator_Linear'],
    'parts': [
        {'ref': 'R1', 'value': '10k', 'symbol': 'R', 'footprint': 'Resistor_SMD:R_0402_1005Metric', 'at': [50.0, 50.3]},
        {'ref': 'C1', 'value': '100nF', 'symbol': 'C', 'footprint': 'Capacitor_SMD:C_0402_1005Metric', 'at': [70, 50]},
        {'ref': 'U1', 'value': 'AMS1117-3.3', 'symbol': 'AMS1117-3.3', 'footprint': 'Package_TO_SOT_SMD:SOT-223-3_TabPin2', 'at': [100, 50]},
        {'ref': '#FLG01', 'value': 'PWR_FLAG', 'symbol': 'PWR_FLAG', 'footprint': '', 'at': [130, 40]},
        {'ref': '#FLG02', 'value': 'PWR_FLAG', 'symbol': 'PWR_FLAG', 'footprint': '', 'at': [150, 40]},
    ],
    'nets': {
        'VBUS': [['U1', '3'], ['#FLG01', '1']],
        'V3_3': [['U1', '2'], ['R1', '1'], ['C1', '1']],
        'GND': [['U1', '1'], ['R1', '2'], ['C1', '2'], ['#FLG02', '1']],
    },
}


class SpecValidationTest(unittest.TestCase):
    def test_a_pin_in_two_nets_or_a_missing_pin_or_a_bare_footprint_is_refused(self):
        pins = {'R': {'1': (0, 0, 0), '2': (0, 0, 0)}}
        base = {'parts': [{'ref': 'R1', 'symbol': 'R', 'footprint': 'Lib:R'}], 'nets': {}}
        author.validate(base, pins)
        with self.assertRaises(ValueError):
            author.validate({**base, 'nets': {'A': [['R1', '1']], 'B': [['R1', '1']]}}, pins)
        with self.assertRaises(ValueError):
            author.validate({**base, 'nets': {'A': [['R1', '9']]}}, pins)
        with self.assertRaises(ValueError):
            author.validate({'parts': [{'ref': 'R1', 'symbol': 'R', 'footprint': 'R_0402'}], 'nets': {}}, pins)


@unittest.skipUnless(HAVE_LIBS, 'KiCad symbol libraries not installed')
class WriteTest(unittest.TestCase):
    def test_writes_a_flattened_gridded_globally_labelled_schematic(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = author.write(SPEC, tmp)
            sch = Path(out['schematic']).read_text(encoding='utf-8')
            lib = Path(out['library']).read_text(encoding='utf-8')
            self.assertEqual(out['symbols'], 5)
            self.assertEqual(out['nets'], 3)
            # AMS1117-3.3 extends AP1117-15 in Regulator_Linear: flattened, with pins, no `extends`
            self.assertNotIn('extends', lib)
            tree = parse(lib)
            ams = next(s for s in tree[1:] if isinstance(s, list) and s[0] == 'symbol' and s[1] == '"AMS1117-3.3"')
            self.assertEqual(sorted(author.pins_of(ams)), ['1', '2', '3'])
            # every symbol on the 2.54 grid (R1 was asked for at y = 50.3)
            self.assertIn('(at 50.8 50.8 0)', sch)
            # global labels named exactly like the nets, never sheet-local ones
            self.assertIn('(global_label "GND"', sch)
            self.assertNotIn('(label ', sch)
            self.assertIn('(lib_id "t:AMS1117-3.3")', sch)
            self.assertIn('(property "Footprint" "Package_TO_SOT_SMD:SOT-223-3_TabPin2"', sch)
            self.assertIn('(name "t")', (Path(tmp) / 'sym-lib-table').read_text(encoding='utf-8'))
            # uuids survive a rewrite when asked, so PCB footprint paths keep pointing at their symbols
            def uuid_of(text, ref):
                for s in parse(text)[1:]:
                    if isinstance(s, list) and s[0] == 'symbol':
                        props = {x[1]: x[2] for x in s if isinstance(x, list) and x[0] == 'property' and len(x) > 2}
                        if props.get('"Reference"') == json.dumps(ref):
                            return next(x for x in s if isinstance(x, list) and x[0] == 'uuid')[1]
                raise AssertionError(f'{ref} not in schematic')
            before = uuid_of(sch, 'R1')
            again = author.write({**SPEC, 'keep_uuids_from': out['schematic']}, tmp)
            self.assertEqual(again['reusedUuids'], 5)
            self.assertEqual(uuid_of(Path(out['schematic']).read_text(encoding='utf-8'), 'R1'), before)

    @unittest.skipUnless((KICAD / 'MacOS' / 'kicad-cli').is_file() or os.environ.get('KICADPY_CLI'), 'kicad-cli not installed')
    def test_the_exported_netlist_says_what_the_spec_says_and_erc_has_no_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = author.write(SPEC, tmp)
            project = Path(tmp) / 'main.kicad_pro'
            project.write_text('{"meta": {"filename": "main.kicad_pro", "version": 1}}', encoding='utf-8')
            result = author.check(project, SPEC)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['schematicPins'], result['specPins'])
            # ERC at every severity: errors 0 (single-use global labels are warnings the tile ignores)
            cli = os.environ.get('KICADPY_CLI') or str(KICAD / 'MacOS' / 'kicad-cli')
            report = Path(tmp) / 'erc.json'
            subprocess.run([cli, 'sch', 'erc', '--severity-all', '--format', 'json', '-o', str(report), out['schematic']],
                           capture_output=True, text=True, timeout=180)
            erc = json.loads(report.read_text(encoding='utf-8'))
            violations = [v for s in erc['sheets'] for v in s['violations']]
            self.assertEqual([v['type'] for v in violations if v['severity'] == 'error'], [], violations)
            self.assertEqual([v['type'] for v in violations if v['type'] in ('endpoint_off_grid', 'unconnected_wire_endpoint', 'lib_symbol_mismatch')], [])
            # and the hollow-symbol detector sees nothing hollow
            net = Path(tmp) / 'n.net'
            subprocess.run([cli, 'sch', 'export', 'netlist', '--format', 'kicadsexpr', '-o', str(net), out['schematic']],
                           capture_output=True, text=True, timeout=180)
            pins, refs = verify.schematic_pins(net.read_text(encoding='utf-8'))
            self.assertEqual({r for r in refs if not r.startswith('#')} - {p.split('.')[0] for p in pins}, set())


if __name__ == '__main__':
    unittest.main()
