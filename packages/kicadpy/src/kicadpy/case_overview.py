"""case/overview.html from data: one shared template, filled from the case outputs (stdlib only).

The docs agent no longer hand-writes the overview page; it writes data and runs this.

Inputs (all relative to the workspace):

- ``case/assembly_steps.json`` (required): a non-empty list of steps (or ``{"steps": [...]}``).
  Each step: ``title`` (str, required), ``show`` / ``focus`` (lists of part globs, ``*`` allowed;
  ``wire:*`` globs are accepted and ignored), ``hw`` (list of ``{ids, what, count, entry}``
  fastener groups), ``action`` (str), ``camera`` (``iso`` | ``top`` | ``front`` | ``fit``).
- ``case/out/parts.json`` (required): ``{"revision", "parts": [{"name", "role", "stl", ...}]}``.
  Every part with an STL in ``case/out/`` is embedded in the page as a quantised mesh
  (int16, base64), so the page works offline and inside the viewer's sandboxed iframe, where
  fetching sibling files is not allowed. Parts with ``role: "coupon"`` are listed, not drawn.
  A part may carry ``covers: [globs]`` when its STL is a merged stand-in for many step names
  (e.g. ``ref_DO_NOT_PRINT_hardware`` covers ``hw_*`` and ``nut_*``); ``ref_*`` parts without
  it get defaults inferred from their name (hardware / servo / electronics). Optional
  ``label`` and ``color`` (``#rrggbb``) override the defaults.
- ``case/out/drawings/index.json`` (optional): ``{"sheets": [{"name", "title", "kind", "svg",
  "quantity"}]}`` with ``svg`` relative to ``case/out/drawings/``; shown as the "Bản vẽ" list in
  the details. Missing or invalid -> the section is omitted.

API::

    render(workspace, *, title=None) -> str    # the HTML; ValueError on bad steps/parts
    write(workspace, *, title=None) -> Path    # writes case/overview.html, returns its path

CLI: ``python -m kicadpy.case_overview [workspace]`` prints
``{"ok":true,"overview":"case/overview.html","steps":N}`` (or ``{"ok":false,"error":...}``, exit 1).

The page reads ``#step=N`` (0 = Đủ bộ) and ``#theme=dark|light`` (dark by default) from the hash.
"""
from __future__ import annotations

import base64
import fnmatch
import html
import json
import math
import struct
import sys
from array import array
from pathlib import Path

TEMPLATE = Path(__file__).parent / 'assets' / 'case-overview.html'
CAMERAS = ('iso', 'top', 'front', 'fit')
EDGE_TRI_LIMIT = 60000          # feature edges are skipped on meshes larger than this (speed)

COLORS = {'shell': '#d6cfc1', 'lid': '#eee9df', 'print': '#9ebcca', 'hw': '#8a98a2',
          'servo': '#4d6fa8', 'elec': '#4c806c', 'ref': '#7d8a94'}
# name keyword -> (category, default covers) for merged reference meshes
REF_KINDS = (
    ('hardware', 'hw', ['hw_*', 'nut_*', 'screw*', 'nut*', 'insert*']),
    ('fastener', 'hw', ['hw_*', 'nut_*', 'screw*', 'nut*', 'insert*']),
    ('servo', 'servo', ['*servo*', '*horn*', 'motor*']),
    ('electronics', 'elec', ['board', 'part_*', 'display', 'led', 'bank', 'adapter', 'usb*', 'pcb*', 'battery*']),
)


# ---------------------------------------------------------------- inputs
def _read_json(path: Path, what: str):
    if not path.is_file():
        raise ValueError(f'{what} not found: {path}')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{what} is not valid JSON ({path}): {exc}') from None


def _globs(value, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(g, str) for g in value):
        raise ValueError(f'assembly_steps.json: {where} must be a list of strings')
    return list(value)


def load_steps(path: Path) -> list[dict]:
    raw = _read_json(path, 'assembly_steps.json')
    if isinstance(raw, dict):
        raw = raw.get('steps')
    if not isinstance(raw, list) or not raw:
        raise ValueError(f'assembly_steps.json: steps must be a non-empty list ({path})')
    steps = []
    for i, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            raise ValueError(f'assembly_steps.json: step {i} must be an object')
        title = s.get('title')
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f'assembly_steps.json: step {i} needs a string title')
        hw = s.get('hw') or []
        if not isinstance(hw, list) or not all(isinstance(g, dict) for g in hw):
            raise ValueError(f'assembly_steps.json: step {i} hw must be a list of objects')
        groups = []
        for g in hw:
            ids = [str(x) for x in (g.get('ids') or []) if isinstance(x, (str, int))]
            count = g.get('count')
            groups.append({'ids': ids, 'what': str(g.get('what') or ''),
                           'count': count if isinstance(count, int) and count >= 0 else len(ids),
                           'entry': str(g.get('entry') or ''), 'nutOnly': bool(g.get('nutOnly'))})
        cam = s.get('camera')
        action = s.get('action')
        steps.append({'title': title.strip(), 'show': _globs(s.get('show', ['*']), f'step {i} show') or ['*'],
                      'focus': _globs(s.get('focus'), f'step {i} focus'), 'hw': groups,
                      'action': action if isinstance(action, str) else '',
                      'camera': cam if cam in CAMERAS else 'iso'})
    return steps


def load_parts(path: Path) -> dict:
    raw = _read_json(path, 'parts.json')
    if not isinstance(raw, dict) or not isinstance(raw.get('parts'), list):
        raise ValueError(f'parts.json: needs a "parts" list ({path})')
    for i, p in enumerate(raw['parts'], 1):
        if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not p['name']:
            raise ValueError(f'parts.json: part {i} needs a string name')
    return raw


def load_drawings(case_dir: Path) -> list[dict]:
    try:
        raw = json.loads((case_dir / 'out' / 'drawings' / 'index.json').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    sheets = raw.get('sheets') if isinstance(raw, dict) else raw
    out = []
    for s in sheets if isinstance(sheets, list) else []:
        if not isinstance(s, dict) or not isinstance(s.get('svg'), str):
            continue
        rel = s['svg'].replace('\\', '/')
        if not rel or rel.startswith('/') or ':' in rel or '..' in rel.split('/'):
            continue
        q = s.get('quantity')
        out.append({'name': str(s.get('name') or rel), 'title': str(s.get('title') or s.get('name') or rel),
                    'kind': str(s.get('kind') or ''), 'quantity': q if isinstance(q, int) else None,
                    'href': 'out/drawings/' + rel})
    return out


# ---------------------------------------------------------------- geometry
def read_stl(path: Path) -> array:
    """Flat float array of triangle vertices (9 per triangle), binary or ASCII STL."""
    data = path.read_bytes()
    if len(data) >= 84:
        n = struct.unpack_from('<I', data, 80)[0]
        if 84 + 50 * n == len(data):
            out = array('f')
            for i in range(n):
                out.extend(struct.unpack_from('<9f', data, 84 + 50 * i + 12))
            return out
    out = array('f')
    for line in data.decode('ascii', 'replace').splitlines():
        t = line.split()
        if len(t) == 4 and t[0] == 'vertex':
            out.extend(float(x) for x in t[1:])
    if len(out) % 9:
        raise ValueError(f'unreadable STL: {path}')
    return out


def feature_edges(P, angle: float = 28.0) -> list[float]:
    """Boundary edges and edges whose dihedral angle exceeds ``angle``, as flat segment coords."""
    cmin = math.cos(math.radians(angle))
    edges: dict = {}
    normals = []
    for t in range(len(P) // 9):
        a = P[9 * t:9 * t + 9]
        ux, uy, uz = a[3] - a[0], a[4] - a[1], a[5] - a[2]
        vx, vy, vz = a[6] - a[0], a[7] - a[1], a[8] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln < 1e-12:
            normals.append(None)
            continue
        normals.append((nx / ln, ny / ln, nz / ln))
        k = [(round(a[0], 3), round(a[1], 3), round(a[2], 3)), (round(a[3], 3), round(a[4], 3), round(a[5], 3)),
             (round(a[6], 3), round(a[7], 3), round(a[8], 3))]
        for i, j in ((0, 1), (1, 2), (2, 0)):
            e = (k[i], k[j]) if k[i] < k[j] else (k[j], k[i])
            edges.setdefault(e, []).append(t)
    out: list[float] = []
    for (pa, pb), fs in edges.items():
        if len(fs) == 1:
            out += [*pa, *pb]
        else:
            n0, n1 = normals[fs[0]], normals[fs[1]]
            if n0[0] * n1[0] + n0[1] * n1[1] + n0[2] * n1[2] < cmin:
                out += [*pa, *pb]
    return out


def _pack(values, scale: float) -> str:
    q = array('h', (max(-32767, min(32767, round(v * scale))) for v in values))
    if sys.byteorder != 'little':
        q.byteswap()
    return base64.b64encode(q.tobytes()).decode('ascii')


# ---------------------------------------------------------------- matching
def globs_overlap(a: str, b: str) -> bool:
    """True when glob ``a`` and glob ``b`` can match a common name (approximation, ``*`` only)."""
    if a == '*' or b == '*':
        return True
    return fnmatch.fnmatchcase(a.replace('*', ''), b) or fnmatch.fnmatchcase(b.replace('*', ''), a)


def _matches(mesh: dict, pattern: str) -> bool:
    if pattern.startswith('wire:'):
        return False
    if fnmatch.fnmatchcase(mesh['id'], pattern):
        return True
    return any(globs_overlap(c, pattern) for c in mesh['covers'])


def _category(part: dict) -> tuple[str, list[str]]:
    name = part['name']
    low = name.lower()
    covers = part.get('covers')
    covers = [c for c in covers if isinstance(c, str)] if isinstance(covers, list) else None
    cat = None
    for key, kcat, kcovers in REF_KINDS:
        if key in low:
            cat = kcat
            if covers is None and (low.startswith('ref') or part.get('printable') is False):
                covers = list(kcovers)
            break
    if cat is None:
        if low.startswith(('hw_', 'nut_', 'screw')):
            cat = 'hw'
        elif 'servo' in low:
            cat = 'servo'
        elif low in ('board', 'pcb', 'display', 'led') or low.startswith('part_'):
            cat = 'elec'
        elif part.get('role') == 'shell':
            cat = 'shell'
        elif part.get('role') == 'lid':
            cat = 'lid'
        elif part.get('printable') is False:
            cat = 'ref'
        else:
            cat = 'print'
    return cat, covers or []


# ---------------------------------------------------------------- render
def _title(workspace: Path, manifest: dict) -> str:
    for f in ('product.json', 'project.json'):
        try:
            d = json.loads((workspace / f).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            for k in ('title', 'name'):
                if isinstance(d.get(k), str) and d[k].strip():
                    return d[k].strip()
    return workspace.resolve().name or 'Case'


def build_data(workspace, *, title=None) -> dict:
    """The JSON blob the template reads (exposed for tests and tooling)."""
    ws = Path(workspace)
    case = ws / 'case'
    steps = load_steps(case / 'assembly_steps.json')
    manifest = load_parts(case / 'out' / 'parts.json')
    meshes, tris, listed = [], [], []
    for p in manifest['parts']:
        role = p.get('role') or 'other'
        listed.append({'name': p['name'], 'role': role, 'material': str(p.get('material') or ''),
                       'printable': p.get('printable') is not False, 'supports': p.get('supports'),
                       'note': str(p.get('note') or '')})
        stl = p.get('stl')
        if role == 'coupon' or not isinstance(stl, str):
            continue
        f = case / 'out' / stl
        if not f.is_file():
            continue
        P = read_stl(f)
        if not len(P):
            continue
        cat, covers = _category(p)
        color = p.get('color') if isinstance(p.get('color'), str) and len(p['color']) == 7 else COLORS[cat]
        meshes.append({'id': p['name'], 'label': str(p.get('label') or p['name']), 'cat': cat, 'role': role,
                       'color': color, 'covers': covers, 'n': len(P) // 9})
        tris.append(P)
    if not meshes:
        raise ValueError(f'parts.json: no part has a readable STL in {case / "out"}')
    big = max((max(abs(min(P)), abs(max(P))) for P in tris), default=1.0)
    scale = 50.0 if big * 50 < 32000 else 32000.0 / big
    for m, P in zip(meshes, tris):
        m['tri'] = _pack(P, scale)
        m['edge'] = _pack(feature_edges(P), scale) if m['n'] <= EDGE_TRI_LIMIT else ''
    for s in steps:
        s['showIdx'] = [i for i, m in enumerate(meshes) if any(_matches(m, g) for g in s['show'])]
        s['focusIdx'] = [i for i, m in enumerate(meshes) if any(_matches(m, g) for g in s['focus'])]
    return {'schema': 1, 'title': title or _title(ws, manifest), 'rev': str(manifest.get('revision') or ''),
            'scale': scale, 'meshes': meshes, 'steps': steps, 'parts': listed,
            'drawings': load_drawings(case)}


def embed_json(data) -> str:
    """JSON safe inside <script>: no raw '<', '>' or '&' (cannot close the tag or open a comment)."""
    s = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return s.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026') \
        .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


FULL = 'Đủ bộ — toàn bộ đã ráp'


def chips_html(steps: list[dict]) -> str:
    """The step row: Đủ bộ, then one numbered chip per step (title on hover)."""
    out = []
    for i, label, name in [(0, 'Đủ bộ', FULL)] + [(k, str(k), s['title']) for k, s in enumerate(steps, 1)]:
        t = html.escape(name, quote=True)
        out.append(f'<button class="chip" type="button" role="tab" data-step="{i}" title="{t}" aria-label="{t}">{label}</button>')
    return ''.join(out)


def render(workspace, *, title=None) -> str:
    data = build_data(workspace, title=title)
    page = TEMPLATE.read_text(encoding='utf-8')
    return page.replace('__TITLE__', html.escape(data['title'])).replace('__CHIPS__', chips_html(data['steps'])) \
        .replace('__CASE_DATA__', embed_json(data))


def write(workspace, *, title=None) -> Path:
    target = Path(workspace) / 'case' / 'overview.html'
    target.write_text(render(workspace, title=title), encoding='utf-8')
    return target


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ws = Path(argv[0]) if argv else Path('.')
    try:
        write(ws)
        n = len(load_steps(ws / 'case' / 'assembly_steps.json'))
    except ValueError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False, separators=(',', ':')))
        return 1
    print(json.dumps({'ok': True, 'overview': 'case/overview.html', 'steps': n}, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
