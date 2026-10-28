"""Offline tests for contract-once lead routing and release-note integrity."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kicadpy import parallel as pilot
from kicadpy import parallel_addendum as notes


ROOT = Path(__file__).resolve().parents[3]


def interfaces():
    return {
        'name': 'Reference sensor',
        'coordinateFrame': {'units': 'mm', 'origin': 'board centre, top copper', 'axes': '+x right, +y up'},
        'board': {'outlineMm': [[-20, -15], [20, -15], [20, 15], [-20, 15]],
                  'thicknessMm': 1.6,
                  'mounts': [{'id': 'H1', 'xMm': -17, 'yMm': -12, 'holeDiameterMm': 2.2}]},
        'powerRails': [{'name': '3V3', 'voltageV': 3.3, 'source': 'module regulator'}],
        'pins': [{'signal': 'SDA', 'device': 'U1', 'pin': '4', 'net': 'SDA', 'voltageV': 3.3}],
        'modules': [{'id': 'U1', 'interface': 'I2C', 'positionMm': [0, 0, 2],
                     'sizeMm': [20, 15, 3]}],
        'mechanicalEnvelopeMm': [60, 50, 30],
        'firmwareBehavior': ['Read sensor over I2C'],
        'assumptions': ['Physical test pending'],
        'tasks': {'board': 'Build board', 'firmware': 'Build firmware', 'case': 'Build case'},
    }


class AddendumTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.prompt = self.base / 'prompt.txt'
        self.prompt.write_text('Build a sensor with a board, firmware, and case.')
        self.root = self.base / 'pilot'
        pilot.initialize(self.root, self.prompt, instructions=ROOT / 'harness/kicad')
        root, state = pilot.load(self.root)
        pilot.seal(root, state, interfaces())
        pilot.write(root / '.pilot/state.json', state)
        self.base_contract = (root / 'workspace/PILOT_CONTRACT.json').read_bytes()
        self.base_head = pilot.git(root / 'workspace', 'rev-parse', 'HEAD')

    def message(self, text):
        path = self.base / 'user-message.txt'
        path.write_bytes(text.encode('utf-8'))
        return path

    def runner(self, routes, note, fields):
        def run(root, state, stage, model, workspace, prompt, schema, readonly):
            self.assertTrue(readonly)
            self.assertTrue(stage.startswith('lead-addendum-'))
            self.assertEqual(model, state['leadModel'])
            self.assertEqual((workspace / 'PILOT_CONTRACT.json').read_bytes(), self.base_contract)
            self.assertIn('EXACT USER MESSAGE:', prompt)
            self.assertTrue((workspace / 'PILOT_RELEASE_NOTES.json').is_file())
            return {'stage': stage, 'model': model, 'startedAt': 0, 'seconds': .01,
                    'usage': None, 'result': {'routes': routes, 'note': note,
                                            'changedFields': fields}, 'error': None}
        return run

    def test_exact_messages_append_in_order_and_route_without_mutating_contract(self):
        first = 'Đổi board lên 50 mm.\r\n  Giữ lỗ bắt.  '
        second = 'Sửa firmware theo pin mới.'
        with pilot.locked(self.root):
            a = notes.record_addendum(
                self.root, self.message(first),
                runner=self.runner(['board', 'case'], 'Expand PCB and adjust case fit.', ['board']))
        b = notes.record_addendum(
            self.root, self.message(second),
            runner=self.runner(['firmware'], 'Update pin mapping in code.', ['pins']))
        chain = notes.load_addenda(self.root)
        self.assertEqual(chain['entries'], [a, b])
        self.assertEqual(chain['tipSha256'], b['sha256'])
        self.assertEqual(a['previousSha256'], pilot.load(self.root)[1]['contractSha256'])
        self.assertEqual(b['previousSha256'], a['sha256'])
        self.assertEqual(a['userText'], first)
        self.assertIn(first, notes.notes_for_role(self.root, 'board'))
        self.assertIn(first, notes.notes_for_role(self.root, 'case'))
        self.assertNotIn(second, notes.notes_for_role(self.root, 'case'))
        self.assertIn(second, notes.notes_for_role(self.root, 'firmware'))
        self.assertEqual((self.root / 'workspace/PILOT_CONTRACT.json').read_bytes(), self.base_contract)
        self.assertEqual(pilot.git(self.root / 'workspace', 'rev-parse', 'HEAD'), self.base_head)
        release_notes = (self.root / '.pilot/RELEASE_NOTES.md').read_text()
        self.assertIn('## 1. board, case', release_notes)
        self.assertIn('## 2. firmware', release_notes)

    def test_tampering_or_reordering_breaks_chain(self):
        self.first_note()
        path = next((self.root / '.pilot/addenda').glob('n*.json'))
        entry = json.loads(path.read_text())
        entry['note'] = 'Tampered instruction'
        pilot.write(path, entry)
        with self.assertRaisesRegex(ValueError, 'chain was changed'):
            notes.load_addenda(self.root)
        with self.assertRaisesRegex(ValueError, 'chain was changed'):
            notes.record_addendum(self.root, self.message('One more change'),
                                  runner=self.runner(['case'], 'Case only.', []))

    def test_bad_lead_result_does_not_record_a_note(self):
        with self.assertRaisesRegex(ValueError, 'unique responsible teams'):
            notes.record_addendum(self.root, self.message('Move the board'),
                                  runner=self.runner(['board', 'board'], 'Move it.', ['board']))
        self.assertEqual(notes.load_addenda(self.root)['entries'], [])

    def test_finalized_run_cannot_accept_a_late_note(self):
        root, state = pilot.load(self.root)
        state['status'] = 'candidate_ready'
        pilot.write(root / '.pilot/state.json', state)
        with self.assertRaisesRegex(ValueError, 'already finalized'):
            notes.record_addendum(self.root, self.message('Change firmware'),
                                  runner=self.runner(['firmware'], 'Change it.', ['firmwareBehavior']))
        self.assertEqual(notes.load_addenda(self.root)['entries'], [])

    def test_readonly_lead_mutation_is_rejected(self):
        def bad(root, state, stage, model, workspace, prompt, schema, readonly):
            (workspace / 'PILOT_CONTRACT.json').write_text('{}')
            return {'stage': stage, 'model': model, 'startedAt': 0, 'seconds': .01,
                    'usage': None, 'result': {'routes': ['board'], 'note': 'Move board.',
                                            'changedFields': ['board']}, 'error': None}

        with self.assertRaisesRegex(ValueError, 'Read-only lead changed'):
            notes.record_addendum(self.root, self.message('Move board'), runner=bad)
        self.assertEqual(notes.load_addenda(self.root)['entries'], [])

    def test_lead_can_read_new_image_and_reference_in_isolated_view(self):
        image = self.root / 'workspace/prompts/assets/new-case.png'
        drawing = self.root / 'workspace/references/assembly/outline.txt'
        image.parent.mkdir(parents=True)
        drawing.parent.mkdir(parents=True)
        image.write_bytes(b'\x89PNG\r\nnew user image')
        drawing.write_bytes(b'board datum at centre')
        expected = {
            'prompts/assets/new-case.png': image.read_bytes(),
            'references/assembly/outline.txt': drawing.read_bytes(),
        }
        regular_runner = self.runner(['case', 'board'], 'Use the new reference.', ['board'])

        def inspect(root, state, stage, model, workspace, prompt, schema, readonly):
            manifest = json.loads((workspace / 'PILOT_REFERENCE_ASSETS.json').read_text())
            self.assertEqual(manifest['schema'], 1)
            self.assertIn('PILOT_REFERENCE_ASSETS.json', prompt)
            self.assertIn('2 reference file(s)', prompt)
            for relative, data in expected.items():
                self.assertEqual((workspace / relative).read_bytes(), data)
                self.assertEqual(manifest['files'][relative], {
                    'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                })
            self.assertNotEqual(workspace, self.root / 'workspace')
            return regular_runner(root, state, stage, model, workspace, prompt, schema, readonly)

        entry = notes.record_addendum(
            self.root, self.message('Use the new image and outline.'), runner=inspect)
        self.assertEqual(entry['userText'], 'Use the new image and outline.')
        self.assertEqual(notes.load_addenda(self.root)['entries'], [entry])

    def test_lead_reference_symlink_and_oversize_are_rejected_before_model(self):
        outside = self.base / 'external.png'
        outside.write_bytes(b'external')
        linked = self.root / 'workspace/prompts/assets/latest.png'
        linked.parent.mkdir(parents=True)
        linked.symlink_to(outside)
        def no_model(*args):
            self.fail('Lead model must not run with an unsafe reference')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            notes.record_addendum(self.root, self.message('Use latest image'), runner=no_model)
        linked.unlink()
        linked.write_bytes(b'too-large')
        with patch.object(notes, 'MAX_REFERENCE_FILE_BYTES', 8):
            with self.assertRaisesRegex(ValueError, 'exceeds lead-view limit'):
                notes.record_addendum(self.root, self.message('Use latest image'), runner=no_model)
        self.assertEqual(notes.load_addenda(self.root)['entries'], [])

    def test_lead_reference_symlinked_parent_is_rejected(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'image.png').write_bytes(b'external image')
        (self.root / 'workspace/references').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            notes.record_addendum(
                self.root, self.message('Use the linked reference'),
                runner=lambda *args: self.fail('Lead model must not run'))
        self.assertEqual(notes.load_addenda(self.root)['entries'], [])

    def test_active_note_pins_new_visible_image_without_touching_integrated_workspace(self):
        root, state = pilot.load(self.root)
        state['status'] = 'running'
        pilot.write(root / '.pilot/state.json', state)
        visible = self.base / 'visible'
        image = visible / 'prompts/assets/new-image.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'\x89PNG\r\nvisible user update')
        expected = image.read_bytes()
        initial_head = pilot.git(self.root / 'workspace', 'rev-parse', 'HEAD')
        regular_runner = self.runner(['case'], 'Match the attached image.', ['mechanicalEnvelopeMm'])

        def inspect(root, state, stage, model, workspace, prompt, schema, readonly):
            self.assertEqual((workspace / 'prompts/assets/new-image.png').read_bytes(), expected)
            self.assertFalse((root / 'workspace/prompts/assets/new-image.png').exists())
            return regular_runner(root, state, stage, model, workspace, prompt, schema, readonly)

        entry = notes.record_addendum(
            self.root, self.message('Adjust case to this image.'),
            source_assets=visible, runner=inspect)
        relative = 'prompts/assets/new-image.png'
        self.assertEqual(entry['schema'], 2)
        self.assertEqual(entry['assets'][relative], {
            'sha256': hashlib.sha256(expected).hexdigest(), 'bytes': len(expected),
        })
        pinned = notes._asset_directory(self.root, entry['sequence'], entry['sha256']) / relative
        self.assertEqual(pinned.read_bytes(), expected)
        self.assertIn(str(pinned), notes.notes_for_role(self.root, 'case'))
        self.assertFalse((root / 'workspace' / relative).exists())
        self.assertEqual(pilot.git(root / 'workspace', 'rev-parse', 'HEAD'), initial_head)

        target = self.base / 'continued-run'
        pilot.initialize(target, self.prompt, instructions=ROOT / 'harness/kicad')
        next_root, next_state = pilot.load(target)
        pilot.seal(next_root, next_state, interfaces())
        pilot.write(next_root / '.pilot/state.json', next_state)
        carried = notes.carry_addenda(self.root, target)
        self.assertEqual(carried['entries'], [entry])
        next_pinned = notes._asset_directory(target, entry['sequence'], entry['sha256']) / relative
        self.assertEqual(next_pinned.read_bytes(), expected)
        self.assertIn(str(next_pinned), notes.notes_for_role(target, 'case'))
        next_pinned.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            notes.load_addenda(target)

    def test_visible_reference_changed_during_lead_call_rejects_note(self):
        visible = self.base / 'visible'
        image = visible / 'prompts/assets/new-image.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'first image')
        regular_runner = self.runner(['case'], 'Match image.', ['mechanicalEnvelopeMm'])

        def mutate(root, state, stage, model, workspace, prompt, schema, readonly):
            self.assertEqual((workspace / 'prompts/assets/new-image.png').read_bytes(), b'first image')
            image.write_bytes(b'second image')
            return regular_runner(root, state, stage, model, workspace, prompt, schema, readonly)

        with self.assertRaisesRegex(ValueError, 'Reference assets changed'):
            notes.record_addendum(
                self.root, self.message('Use image.'), source_assets=visible, runner=mutate)
        self.assertEqual(notes.load_addenda(self.root)['entries'], [])
        self.assertFalse((self.root / '.pilot/addenda/assets').exists())

    def test_capacity_retry_preserves_one_message_and_one_note(self):
        calls = []
        success = self.runner(['case'], 'Adjust case clearance.', ['mechanicalEnvelopeMm'])

        def flaky(*args):
            calls.append(args[2])
            if len(calls) < 3:
                return {'stage': args[2], 'model': args[3], 'startedAt': 0,
                        'seconds': .01, 'usage': None, 'result': None,
                        'error': 'Selected model is at capacity', 'failureKind': 'model_capacity'}
            return success(*args)

        with patch.object(notes.time, 'sleep') as sleep:
            entry = notes.record_addendum(self.root, self.message('Increase case clearance'),
                                          runner=flaky)
        self.assertEqual(sleep.call_args_list[0].args, (5,))
        self.assertEqual(sleep.call_args_list[1].args, (15,))
        self.assertEqual(len(calls), 3)
        self.assertEqual(notes.load_addenda(self.root)['entries'], [entry])

    def test_carry_chain_to_an_adopted_run_without_a_model_call(self):
        first = self.first_note()
        second = notes.record_addendum(
            self.root, self.message('Firmware needs a watchdog.'),
            runner=self.runner(['firmware'], 'Add a watchdog.', ['firmwareBehavior']))
        target = self.base / 'next-run'
        pilot.initialize(target, self.prompt, instructions=ROOT / 'harness/kicad')
        target_root, target_state = pilot.load(target)
        pilot.seal(target_root, target_state, interfaces())
        pilot.write(target_root / '.pilot/state.json', target_state)
        original_contract = (target / 'workspace/PILOT_CONTRACT.json').read_bytes()
        original_head = pilot.git(target / 'workspace', 'rev-parse', 'HEAD')

        copied = notes.carry_addenda(self.root, target)
        self.assertEqual(copied['entries'], [first, second])
        self.assertEqual(copied['tipSha256'], second['sha256'])
        self.assertEqual(notes.carry_addenda(self.root, target), copied)
        for entry in copied['entries']:
            filename = f"n{entry['sequence']:04}-{entry['sha256']}.json"
            self.assertEqual((self.root / '.pilot/addenda' / filename).read_bytes(),
                             (target / '.pilot/addenda' / filename).read_bytes())
        self.assertEqual((target / 'workspace/PILOT_CONTRACT.json').read_bytes(), original_contract)
        self.assertEqual(pilot.git(target / 'workspace', 'rev-parse', 'HEAD'), original_head)
        self.assertIn('watchdog', notes.notes_for_role(target, 'firmware'))

    def test_carry_rejects_a_different_original_contract(self):
        self.first_note()
        target = self.base / 'other-run'
        prompt = self.base / 'different-prompt.txt'
        prompt.write_text('Build a different sensor.')
        pilot.initialize(target, prompt, instructions=ROOT / 'harness/kicad')
        target_root, target_state = pilot.load(target)
        pilot.seal(target_root, target_state, interfaces())
        pilot.write(target_root / '.pilot/state.json', target_state)
        with self.assertRaisesRegex(ValueError, 'exact same original contract'):
            notes.carry_addenda(self.root, target)
        self.assertEqual(notes.load_addenda(target)['entries'], [])

    def first_note(self):
        return notes.record_addendum(self.root, self.message('Adjust board outline'),
                                     runner=self.runner(['board', 'case'],
                                                        'Adjust outline and frame.', ['board']))


if __name__ == '__main__':
    unittest.main()
