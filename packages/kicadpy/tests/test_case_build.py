"""Per-part cached case builds: unchanged parts are skipped, changed ones (and only those) rebuilt."""
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest

from kicadpy import case_build

COUNTER = itertools.count()

# Shaped like the dog's case/build.py: coupons cut from frame(), trays on their own constants.
SCRIPT = '''
import helpers
from kicadpy.case_build import CaseBuild

BAR = 4.0
TRAY_W = 20.0
TABS = {"coupon_a": (1, 2), "coupon_b": (3, 4)}

def box(*size):
    return ("box",) + tuple(size)

def frame():
    return ("frame", box(126, 75, BAR))

def _tab(solid, name):
    return ("tab", solid, TABS[name])

def coupon_a():
    return _tab(("cut", frame(), 1), "coupon_a")

def coupon_b():
    return _tab(("cut", frame(), 2), "coupon_b")

def charger_tray():
    return ("tray", box(TRAY_W, 10, 2))

def buck_tray():
    return ("buck", box(15, 9, 2), helpers.ring(3))

PARTS = {
    "frame": dict(fn=frame, role="shell", note="floor down"),
    "charger_tray": dict(fn=charger_tray, role="bracket", glueOn="frame"),
    "buck_tray": dict(fn=buck_tray, role="bracket", glueOn="frame"),
    "coupon_a": dict(fn=coupon_a, role="coupon"),
    "coupon_b": dict(fn=coupon_b, role="coupon"),
}
'''

HELPERS = '''
def ring(n):
    return ("ring", n)
'''


class Exporter:
    """Writes the solid's repr as both files; counts what was exported."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, solid, step, stl):
        name = Path(step).name.split('.')[1]
        if name in self.fail:
            raise RuntimeError('kernel says no')
        self.calls.append(name)
        Path(step).write_text('STEP ' + repr(solid))
        Path(stl).write_text('STL ' + repr(solid))
        return [0, 0, 0, 1, 1, 1]


class CaseBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'case').mkdir()
        (self.root / 'tools/case').mkdir(parents=True)
        (self.root / 'PILOT_CONTRACT.json').write_text('{"revision": 1}')
        self.script = self.root / 'case/build.py'
        self.script.write_text(SCRIPT)
        (self.root / 'tools/case/helpers.py').write_text(HELPERS)
        sys.path.insert(0, str(self.root / 'tools/case'))
        self.addCleanup(sys.path.remove, str(self.root / 'tools/case'))
        self.addCleanup(sys.modules.pop, 'helpers', None)

    def load(self, **edits):
        """Load case/build.py fresh, after replacing text in it (old -> new)."""
        text = self.script.read_text()
        for old, new in edits.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        self.script.write_text(text)
        sys.modules.pop('helpers', None)
        for cache in self.root.rglob('__pycache__'):   # same-second, same-size edits: no stale bytecode
            for pyc in cache.iterdir():
                pyc.unlink()
        name = f'case_build_script_{next(COUNTER)}'
        spec = importlib.util.spec_from_file_location(name, self.script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(module)
        return module

    def build(self, module, exporter=None, **kwargs):
        exporter = exporter or Exporter()
        builder = case_build.CaseBuild(self.script, exporter=exporter, manifest={'stage': 'simple'})
        builder.add_parts(module.PARTS)
        result = builder.run(log=lambda *_: None, **kwargs)
        return result, exporter

    def manifest(self):
        return json.loads((self.root / 'case/out/parts.json').read_text())

    def test_first_build_builds_all_and_writes_the_manifest(self):
        result, exporter = self.build(self.load())
        self.assertEqual(sorted(exporter.calls), ['buck_tray', 'charger_tray', 'coupon_a', 'coupon_b', 'frame'])
        manifest = self.manifest()
        self.assertEqual([p['name'] for p in manifest['parts']],
                         ['frame', 'charger_tray', 'buck_tray', 'coupon_a', 'coupon_b'])
        tray = manifest['parts'][1]
        self.assertEqual(tray, {'name': 'charger_tray', 'role': 'bracket', 'stl': 'charger_tray.stl',
                                'step': 'charger_tray.step', 'printRotateDeg': [0, 0, 0], 'supports': False,
                                'glueOn': 'frame', 'bboxMm': [0, 0, 0, 1, 1, 1]})
        self.assertEqual(manifest['stage'], 'simple')
        self.assertEqual(manifest['contractSha'], case_build._sha_file(self.root / 'PILOT_CONTRACT.json'))
        self.assertFalse(list((self.root / 'case/out').glob('.*.tmp.*')))

    def test_unchanged_parts_are_skipped(self):
        self.build(self.load())
        result, exporter = self.build(self.load())
        self.assertEqual(exporter.calls, [])
        self.assertEqual(len(result['cached']), 5)
        self.assertEqual(len(self.manifest()['parts']), 5)

    def test_a_comment_or_moved_function_does_not_rebuild(self):
        self.build(self.load())
        _, exporter = self.build(self.load(**{'def frame():': '# the frame\n\n\ndef frame():'}))
        self.assertEqual(exporter.calls, [])

    def test_constant_used_by_one_part_rebuilds_only_that_part(self):
        self.build(self.load())
        _, exporter = self.build(self.load(**{'TRAY_W = 20.0': 'TRAY_W = 21.0'}))
        self.assertEqual(exporter.calls, ['charger_tray'])

    def test_changed_frame_rebuilds_frame_and_the_coupons_cut_from_it(self):
        self.build(self.load())
        _, exporter = self.build(self.load(**{'return ("frame", box(126, 75, BAR))':
                                              'return ("frame", box(126, 76, BAR))'}))
        self.assertEqual(sorted(exporter.calls), ['coupon_a', 'coupon_b', 'frame'])

    def test_changed_shared_helper_rebuilds_everything_that_calls_it(self):
        self.build(self.load())
        _, exporter = self.build(self.load(**{'return ("box",) + tuple(size)': 'return ("box2",) + tuple(size)'}))
        self.assertEqual(sorted(exporter.calls), ['buck_tray', 'charger_tray', 'coupon_a', 'coupon_b', 'frame'])

    def test_tab_table_change_rebuilds_only_coupons(self):
        self.build(self.load())
        _, exporter = self.build(self.load(**{'"coupon_b": (3, 4)': '"coupon_b": (3, 5)'}))
        self.assertEqual(sorted(exporter.calls), ['coupon_a', 'coupon_b'])

    def test_imported_project_module_change_rebuilds_its_users(self):
        self.build(self.load())
        (self.root / 'tools/case/helpers.py').write_text(HELPERS.replace('("ring", n)', '("ring", n + 1)'))
        _, exporter = self.build(self.load())
        self.assertEqual(exporter.calls, ['buck_tray'])

    def test_declared_input_file_change_rebuilds(self):
        self.build(self.load())
        (self.root / 'PILOT_CONTRACT.json').write_text('{"revision": 2}')
        _, exporter = self.build(self.load())
        self.assertEqual(len(exporter.calls), 5)
        self.assertEqual(self.manifest()['contractSha'], case_build._sha_file(self.root / 'PILOT_CONTRACT.json'))

    def test_missing_or_edited_output_rebuilds_that_part(self):
        self.build(self.load())
        (self.root / 'case/out/frame.stl').unlink()
        (self.root / 'case/out/buck_tray.step').write_text('tampered')
        _, exporter = self.build(self.load())
        self.assertEqual(sorted(exporter.calls), ['buck_tray', 'frame'])

    def test_only_rebuilds_just_the_named_parts(self):
        self.build(self.load())
        module = self.load(**{'TRAY_W = 20.0': 'TRAY_W = 21.0'})
        result, exporter = self.build(module, only=['frame'])
        self.assertEqual(exporter.calls, ['frame'])
        self.assertEqual(len(self.manifest()['parts']), 5)
        with self.assertRaises(SystemExit):
            self.build(module, only=['nope'])

    def test_force_rebuilds_everything(self):
        self.build(self.load())
        _, exporter = self.build(self.load(), force=True)
        self.assertEqual(len(exporter.calls), 5)

    def test_frozen_parts_are_not_rebuilt_unless_named(self):
        self.build(self.load())
        (self.root / 'case/stages.json').write_text(json.dumps(
            {'parts': {'frame': {'status': 'valid'}, 'coupon_a': {'status': 'note'},
                       'coupon_b': {'status': 'working'}}}))
        module = self.load(**{'return ("box",) + tuple(size)': 'return ("box2",) + tuple(size)'})
        result, exporter = self.build(module)
        self.assertEqual(sorted(exporter.calls), ['buck_tray', 'charger_tray', 'coupon_b'])
        self.assertEqual(sorted(result['frozen']), ['coupon_a', 'frame'])
        self.assertEqual(sorted(result['frozenChanged']), ['coupon_a', 'frame'])
        _, exporter = self.build(module)        # no loop: frozen parts stay put
        self.assertEqual(exporter.calls, [])
        _, exporter = self.build(module, only=['frame'])
        self.assertEqual(exporter.calls, ['frame'])

    def test_unchanged_frozen_part_is_plain_frozen_and_rework_parts_rebuild(self):
        self.build(self.load())
        (self.root / 'case/stages.json').write_text(json.dumps(
            {'parts': {'frame': {'status': 'done'}, 'charger_tray': {'status': 'done'}}}))
        result, _ = self.build(self.load())
        self.assertEqual(sorted(result['frozen']), ['charger_tray', 'frame'])
        self.assertEqual(result['frozenChanged'], [])
        (self.root / 'case/checks.json').write_text(json.dumps({'rework': ['charger_tray']}))
        result, exporter = self.build(self.load(**{'TRAY_W = 20.0': 'TRAY_W = 23.0'}))
        self.assertEqual(exporter.calls, ['charger_tray'])
        self.assertEqual(result['frozen'], ['frame'])

    def test_removed_part_loses_its_files_and_entry(self):
        self.build(self.load())
        module = self.load()
        del module.PARTS['coupon_b']
        self.build(module)
        self.assertFalse((self.root / 'case/out/coupon_b.step').exists())
        self.assertNotIn('coupon_b', [p['name'] for p in self.manifest()['parts']])

    def test_failed_part_is_dropped_and_others_still_build(self):
        self.build(self.load())
        module = self.load(**{'TRAY_W = 20.0': 'TRAY_W = 21.0', 'box(15, 9, 2)': 'box(16, 9, 2)'})
        result, exporter = self.build(module, exporter=Exporter(fail={'charger_tray'}))
        self.assertEqual(result['failed'], ['charger_tray'])
        self.assertEqual(exporter.calls, ['buck_tray'])
        self.assertFalse((self.root / 'case/out/charger_tray.stl').exists())
        self.assertNotIn('charger_tray', [p['name'] for p in self.manifest()['parts']])
        _, exporter = self.build(module)      # retried next run
        self.assertEqual(exporter.calls, ['charger_tray'])

    def test_manifest_keeps_keys_other_members_own(self):
        self.build(self.load())
        manifest = self.manifest()
        manifest['printProject'] = {'plates': [1]}
        manifest['overview'] = '../overview.html'
        (self.root / 'case/out/parts.json').write_text(json.dumps(manifest))
        self.build(self.load(**{'TRAY_W = 20.0': 'TRAY_W = 22.0'}))
        manifest = self.manifest()
        self.assertEqual(manifest['printProject'], {'plates': [1]})
        self.assertEqual(manifest['overview'], '../overview.html')

    def test_unreadable_global_falls_back_to_the_whole_file(self):
        module = self.load(**{'BAR = 4.0': 'BAR = 4.0\nSOLID = object()',
                              'return ("tray", box(TRAY_W, 10, 2))': 'return ("tray", box(TRAY_W, 10, 2), SOLID is None)'})
        self.build(module)
        # an unrelated edit anywhere in the file now rebuilds the tray (when unsure, rebuild) ...
        _, exporter = self.build(self.load(**{'"coupon_b": (3, 4)': '"coupon_b": (3, 5)'}))
        self.assertIn('charger_tray', exporter.calls)
        self.assertNotIn('buck_tray', exporter.calls)

    def test_copied_project_keeps_its_cache(self):
        import shutil
        self.build(self.load(**{'BAR = 4.0': 'BAR = 4.0\nSOLID = object()',
                                'return ("tray", box(TRAY_W, 10, 2))': 'return ("tray", box(TRAY_W, 10, 2), SOLID)'}))
        copy = Path(tempfile.mkdtemp()).resolve() / 'other-worktree'
        self.addCleanup(shutil.rmtree, copy.parent)
        shutil.copytree(self.root, copy)
        sys.path.remove(str(self.root / 'tools/case'))
        sys.path.insert(0, str(copy / 'tools/case'))
        self.addCleanup(sys.path.insert, 0, str(self.root / 'tools/case'))
        self.addCleanup(sys.path.remove, str(copy / 'tools/case'))
        self.root, self.script = copy, copy / 'case/build.py'
        _, exporter = self.build(self.load())
        self.assertEqual(exporter.calls, [])

    def test_decorator_registration_and_main(self):
        builder = case_build.CaseBuild(self.script, exporter=Exporter())

        @builder.part('lid', role='lid')
        def lid():
            return ('lid',)

        result = builder.main([])
        self.assertEqual(result['built'], ['lid'])
        self.assertEqual(builder.main(['--plan'])['plan'], {'lid': ('cached', 'unchanged')})
        self.assertEqual(builder.main(['--only', 'lid'])['built'], ['lid'])


if __name__ == '__main__':
    unittest.main()
