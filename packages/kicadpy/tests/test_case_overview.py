"""case/overview.html is rendered from data by one shared template (stdlib only, no network)."""
import contextlib
import io
import json
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from kicadpy import case_overview  # noqa: E402

V17G = Path('/Users/macbookpro/harnesses/crab-robot-v17g')


def cube_tris(x0, y0, z0, s):
    p = [(x0 + dx * s, y0 + dy * s, z0 + dz * s) for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a, b, c, d in faces:
        tris += [(p[a], p[b], p[c]), (p[a], p[c], p[d])]
    return tris


def write_binary_stl(path, tris):
    out = bytearray(80) + struct.pack('<I', len(tris))
    for t in tris:
        out += struct.pack('<3f', 0, 0, 0) + struct.pack('<9f', *[c for v in t for c in v]) + b'\0\0'
    path.write_bytes(bytes(out))


def write_ascii_stl(path, tris):
    lines = ['solid x']
    for t in tris:
        lines += ['facet normal 0 0 0', 'outer loop'] + [f'vertex {v[0]} {v[1]} {v[2]}' for v in t] + ['endloop', 'endfacet']
    path.write_text('\n'.join(lines + ['endsolid x']) + '\n')


STEPS = [
    {'title': 'Bước 1 — Khung dưới', 'show': ['frame_lower', 'hw_B*'], 'focus': ['frame_lower'],
     'hw': [{'ids': ['B1', 'B2'], 'what': 'vít M3x6', 'count': 2, 'entry': 'từ trên xuống'}],
     'action': 'Đặt board.', 'camera': 'iso'},
    {'title': 'Bước 2 — Nắp', 'show': ['*'], 'focus': ['frame_upper'], 'hw': [], 'action': 'Đậy nắp.', 'camera': 'top'},
    {'title': 'Bước 3 — Dây', 'show': ['frame_*', 'wire:*'], 'focus': [], 'hw': [], 'action': '', 'camera': 'weird'},
]


def make_workspace(root: Path, steps=STEPS, drawings=None):
    case = root / 'case'
    out = case / 'out'
    out.mkdir(parents=True)
    write_binary_stl(out / 'frame_lower.stl', cube_tris(0, 0, 0, 40))
    write_ascii_stl(out / 'frame_upper.stl', cube_tris(0, 0, 40, 40))
    write_binary_stl(out / 'ref_DO_NOT_PRINT_hardware.stl', cube_tris(5, 5, 45, 3))
    write_binary_stl(out / 'coupon_TEST_ONLY.stl', cube_tris(100, 0, 0, 5))
    parts = {'schema': 1, 'revision': 'R1', 'parts': [
        {'name': 'frame_lower', 'role': 'shell', 'stl': 'frame_lower.stl', 'material': 'PETG'},
        {'name': 'frame_upper', 'role': 'lid', 'stl': 'frame_upper.stl', 'material': 'PETG'},
        {'name': 'ref_DO_NOT_PRINT_hardware', 'role': 'other', 'stl': 'ref_DO_NOT_PRINT_hardware.stl', 'printable': False},
        {'name': 'coupon_TEST_ONLY', 'role': 'coupon', 'stl': 'coupon_TEST_ONLY.stl'},
    ]}
    (out / 'parts.json').write_text(json.dumps(parts))
    if steps is not None:
        (case / 'assembly_steps.json').write_text(json.dumps(steps, ensure_ascii=False))
    if drawings is not None:
        (out / 'drawings').mkdir()
        (out / 'drawings' / 'index.json').write_text(drawings if isinstance(drawings, str) else json.dumps(drawings))
    return root


def blob(page: str) -> dict:
    m = re.search(r'<script type="application/json" id="case-data">(.*?)</script>', page, re.S)
    assert m, 'no case-data blob'
    return json.loads(m.group(1))


class RenderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_renders_fixture_with_chip_row_and_no_select(self):
        page = case_overview.render(make_workspace(self.root), title='Hộp thử')
        self.assertNotIn('<select', page)
        self.assertNotIn('__CASE_DATA__', page)
        self.assertIn('<title>Hộp thử — Tổng quan ráp</title>', page)
        for label in ('Vít/tán', 'Từ trên', 'Từ trước', 'Xiên', 'Vừa khung', 'Trước', 'Sau', 'Đủ bộ', 'Chi tiết'):
            self.assertIn(label, page)
        self.assertRegex(page, r'<details id="detail">')          # collapsed: no "open" attribute
        data = blob(page)
        self.assertEqual(len(data['steps']), 3)
        chips = re.findall(r'<button class="chip"[^>]*data-step="(\d+)" title="([^"]*)"[^>]*>([^<]*)</button>', page)
        self.assertEqual([c[0] for c in chips], ['0', '1', '2', '3'])        # N+1 chips
        self.assertEqual([c[2] for c in chips], ['Đủ bộ', '1', '2', '3'])
        self.assertEqual(chips[1][1], 'Bước 1 — Khung dưới')                  # hover shows the step name
        self.assertEqual(data['rev'], 'R1')
        ids = [m['id'] for m in data['meshes']]
        self.assertEqual(ids, ['frame_lower', 'frame_upper', 'ref_DO_NOT_PRINT_hardware'])   # coupon not drawn
        self.assertIn('coupon_TEST_ONLY', [p['name'] for p in data['parts']])
        hw = data['meshes'][2]
        self.assertEqual(hw['cat'], 'hw')
        self.assertIn('hw_*', hw['covers'])
        s1, s2, s3 = data['steps']
        self.assertEqual(s1['showIdx'], [0, 2])                   # hw_B* -> the merged hardware mesh
        self.assertEqual(s1['focusIdx'], [0])
        self.assertEqual(s2['showIdx'], [0, 1, 2])
        self.assertEqual(s2['camera'], 'top')
        self.assertEqual(s3['showIdx'], [0, 1])                   # wire:* is ignored
        self.assertEqual(s3['camera'], 'iso')                     # unknown camera -> iso
        self.assertEqual(s1['hw'][0]['ids'], ['B1', 'B2'])
        self.assertEqual(hw['n'], 12)

    def test_mesh_round_trip_and_edges(self):
        data = case_overview.build_data(make_workspace(self.root))
        import base64
        raw = base64.b64decode(data['meshes'][0]['tri'])
        vals = struct.unpack(f'<{len(raw) // 2}h', raw)
        self.assertEqual(len(vals), 12 * 9)
        self.assertAlmostEqual(max(vals) / data['scale'], 40.0, places=2)
        self.assertTrue(data['meshes'][0]['edge'])                # a cube has 12 feature edges
        self.assertEqual(len(base64.b64decode(data['meshes'][0]['edge'])) // 2, 12 * 6)

    def test_data_blob_escaping(self):
        steps = [dict(STEPS[0], title='</script><!-- <b>x</b> & y', action='a </SCRIPT> b')]
        page = case_overview.render(make_workspace(self.root, steps=steps), title='T </script> & <i>')
        m = re.search(r'<script type="application/json" id="case-data">(.*?)</script>', page, re.S)
        self.assertNotIn('<', m.group(1))
        data = json.loads(m.group(1))
        self.assertEqual(data['steps'][0]['title'], '</script><!-- <b>x</b> & y')
        self.assertEqual(data['steps'][0]['action'], 'a </SCRIPT> b')
        self.assertEqual(data['title'], 'T </script> & <i>')
        self.assertIn('<title>T &lt;/script&gt; &amp; &lt;i&gt; — Tổng quan ráp</title>', page)
        self.assertEqual(page.count('</script>'), 2)               # the data blob and the app script only
        self.assertIn('title="&lt;/script&gt;&lt;!-- &lt;b&gt;x&lt;/b&gt; &amp; y"', page)   # chip title escaped

    def test_drawings_present(self):
        idx = {'schema': 1, 'project': 'p', 'version': '1', 'sheets': [
            {'name': 'assembly', 'title': 'Lắp ráp', 'kind': 'assembly', 'svg': 'assembly.svg', 'quantity': 1},
            {'name': 'frame_lower', 'title': 'Khung dưới', 'kind': 'part', 'svg': 'frame_lower.svg', 'quantity': 2},
            {'name': 'evil', 'title': 'x', 'svg': '../../secret.svg'},
            {'name': 'abs', 'title': 'x', 'svg': '/etc/passwd'},
        ]}
        data = blob(case_overview.render(make_workspace(self.root, drawings=idx)))
        self.assertEqual([d['href'] for d in data['drawings']], ['out/drawings/assembly.svg', 'out/drawings/frame_lower.svg'])
        self.assertEqual(data['drawings'][1]['quantity'], 2)

    def test_drawings_absent_or_invalid(self):
        data = blob(case_overview.render(make_workspace(self.root)))
        self.assertEqual(data['drawings'], [])
        other = Path(self._tmp.name) / 'b'
        data = blob(case_overview.render(make_workspace(other, drawings='{not json')))
        self.assertEqual(data['drawings'], [])

    def test_bad_steps_raise_value_error(self):
        cases = {'missing': None, 'empty': [], 'notlist': {'a': 1}, 'notitle': [{'show': ['*']}],
                 'numtitle': [{'title': 3}], 'badshow': [{'title': 'x', 'show': [1]}]}
        for name, steps in cases.items():
            ws = make_workspace(self.root / name, steps=steps)
            with self.subTest(name), self.assertRaises(ValueError) as cm:
                case_overview.render(ws)
            self.assertIn('assembly_steps.json', str(cm.exception))
        ws = self.root / 'garbage'
        make_workspace(ws)
        (ws / 'case' / 'assembly_steps.json').write_text('[{')
        with self.assertRaisesRegex(ValueError, 'not valid JSON'):
            case_overview.render(ws)

    def test_steps_object_form_accepted(self):
        data = case_overview.build_data(make_workspace(self.root, steps={'steps': STEPS[:1]}))
        self.assertEqual(len(data['steps']), 1)

    def test_write_and_cli_line(self):
        ws = make_workspace(self.root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = case_overview.main([str(ws)])
        self.assertEqual(rc, 0)
        line = buf.getvalue().strip().splitlines()[-1]
        self.assertEqual(line, '{"ok":true,"overview":"case/overview.html","steps":3}')
        self.assertTrue((ws / 'case' / 'overview.html').is_file())
        self.assertEqual(case_overview.write(ws), ws / 'case' / 'overview.html')
        bad = make_workspace(self.root / 'bad', steps=[])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = case_overview.main([str(bad)])
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(buf.getvalue().strip())['ok'])

    def test_template_requirements(self):
        page = case_overview.TEMPLATE.read_text(encoding='utf-8')
        self.assertIn("get('theme')==='dark'", page)              # light without a hash
        self.assertIn("get('step')", page)                         # #step=N
        self.assertIn('replaceState', page)                        # keeps theme when writing step
        self.assertIn('hashchange', page)
        self.assertNotRegex(page, r'<script[^>]+src=|<link[^>]+href=|fetch\(')   # self-contained
        self.assertNotIn('target=', page)                          # sandbox: no popups

    @unittest.skipUnless((V17G / 'case' / 'assembly_steps.json').is_file(), 'v17g reference run not on this machine')
    def test_real_v17g_data(self):
        page = case_overview.render(V17G)                        # read-only: render() writes nothing
        data = blob(page)
        self.assertEqual(len(data['steps']), 8)
        self.assertEqual(data['rev'], 'A1V4-r5')
        names = {m['id'] for m in data['meshes']}
        self.assertIn('frame_lower', names)
        self.assertNotIn('MATING_TEST_leg_FL', names)
        for s in data['steps']:
            self.assertTrue(s['showIdx'], s['title'])
        self.assertLess(len(page), 8_000_000)


if __name__ == '__main__':
    unittest.main()
