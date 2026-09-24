"""kicadpy.knowledge: what earlier runs measured stays readable, and learn never overwrites a disagreement."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kicadpy import knowledge


class TablesTest(unittest.TestCase):
    def test_the_shipped_tables_parse_and_carry_the_three_runs(self):
        rot, ids = knowledge.rotation_rows(), knowledge.parts_rows()
        self.assertGreaterEqual(len(rot), 40)
        self.assertGreaterEqual(len(ids), 40)
        # the offsets three runs agreed on
        self.assertEqual(knowledge.rotation_offset('C97521'), 270)   # W25Q128 SOIC-8
        self.assertEqual(knowledge.rotation_offset('C6186'), 180)    # AMS1117 SOT-223
        self.assertEqual(knowledge.rotation_offset('C2040'), 0)      # RP2040
        self.assertIsNone(knowledge.rotation_offset('C000000'))
        # a part is keyed by code, not package: two SOT-23-6 parts differ
        self.assertEqual(rot['C2687116']['offsetDeg'], 270)
        self.assertEqual(rot['C131941']['offsetDeg'], 180)
        self.assertIn('clone', knowledge.part('C2687116')['note'])
        self.assertTrue(all(row['sources'] for row in rot.values()))


class LearnTest(unittest.TestCase):
    def test_learn_appends_new_rows_and_records_a_conflict_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rot, ids = tmp / 'rot.json', tmp / 'ids.json'
            rot.write_text(json.dumps({'_about': '', 'parts': {'C1': {'footprint': 'SOT-23', 'package': 'SOT-23', 'offsetDeg': 180, 'sources': ['harness-9 2026-09-21']}}}))
            ids.write_text(json.dumps({'_about': '', 'parts': {}}))
            ws = tmp / 'harness-99'
            (ws / 'design').mkdir(parents=True)
            (ws / 'manufacturing.json').write_text(json.dumps({'assembly': {
                'Q1': {'method': 'factory', 'rotationOffsetDeg': 0},      # disagrees with the table: conflict, not overwrite
                'U9': {'method': 'factory', 'rotationOffsetDeg': 270},    # new part
                'J2': {'method': 'manual'}}}))                            # no offset: ignored
            (ws / 'parts.json').write_text(json.dumps({'parts': [
                {'refdes': ['Q1'], 'lcsc': 'C1', 'manufacturer': 'CJ', 'mpn': 'S8550', 'package': 'SOT-23'},
                {'refdes': ['U9'], 'lcsc': 'C9', 'manufacturer': 'TI', 'mpn': 'X', 'package': 'SOIC-8'},
                {'refdes': ['J2'], 'manufacturer': 'XFCN', 'mpn': 'PZ254'}]}))
            (ws / 'design' / 'main.kicad_pcb').write_text('(kicad_pcb (footprint "Pkg:SOT-23" (property "Reference" "Q1")) (footprint "Pkg:SOIC-8" (property "Reference" "U9")))')
            with patch.object(knowledge, 'ROTATION', rot), patch.object(knowledge, 'PARTS', ids):
                added = knowledge.learn(ws, today='2026-09-30')
                self.assertEqual(added['rotation'], ['C9'])
                self.assertEqual(added['conflicts'], ['C1'])
                self.assertEqual(sorted(added['parts']), ['C1', 'C9'])
                table = knowledge.rotation_rows()
                self.assertEqual(table['C1']['offsetDeg'], 180)
                self.assertEqual(table['C1']['conflicts'], [{'source': 'harness-99 2026-09-30', 'offsetDeg': 0}])
                self.assertIsNone(knowledge.rotation_offset('C1'))   # a disputed offset is no answer
                self.assertEqual(table['C9'], {'footprint': 'SOIC-8', 'package': 'SOIC-8', 'offsetDeg': 270, 'sources': ['harness-99 2026-09-30']})
                self.assertEqual(knowledge.part('C9')['mpn'], 'X')
                # a second learn of the same run adds nothing new
                again = knowledge.learn(ws, today='2026-09-30')
                self.assertEqual(again, {'rotation': [], 'parts': [], 'conflicts': ['C1']})


if __name__ == '__main__':
    unittest.main()
