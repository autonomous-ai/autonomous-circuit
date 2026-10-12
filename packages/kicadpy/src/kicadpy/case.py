"""The Case phase: check the agent's case against the board's contract.

The agent models the case in `case/build.py` (CadQuery), **in the contract's frame and already
assembled** (`boards/<stem>.enclosure.json`: mm, origin at the board outline centre, +x right,
+y up from the top, z = 0 on the top copper), and writes `case/out/`:

    case/out/parts.json   {"contractSha": "<sha256 of the enclosure.json it was built from>",
                           "parts": [{"name": "front", "role": "shell", "stl": "front.stl",
                                      "step": "front.step", "printRotateDeg": [180, 0, 0],
                                      "supports": false}, ...]}

`role` is shell, lid, plunger, membrane, diffuser, bracket, coupon (the test-fit piece, cut from a
shell and never assembled) or other. Plungers and membranes are
the parts a button may press through; everything else must leave each interface open.

The checker reads the STEP files (the mesh is what prints; the STEP is what can be measured),
never imports the agent's code, and writes `case/reports/case-check.json`, bound to the
contract's sha and each part's sha. A new board or a rebuilt part makes the report stale. It is
not run by the Stop hook (the hook's 1200 s are the publisher's and the advisor's); the hook and
the verdict only read whether a fresh report passed.

    "$KICAD_CASE_PYTHON" -m kicadpy.case check .     # CadQuery; writes the report, prints a summary

What a fresh, passing report means:
- every part listed exists in both formats and fits the bed in its print orientation;
- no part goes into the board, a part's body or a seated module (keep-outs from the contract);
- every interface the person uses has its opening: a display's active area, a light (or a skin
  of 0.8 mm or less), a USB-C plug corridor to the outside, a button reached by a plunger or a
  membrane, a key's plate cutout, a touch pad under at most its wall limit of plastic;
- every mounting hole has material around it (a boss or a clamp), or a warning says it does not.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
import time

OUT = Path('case') / 'out'
REPORT = Path('case') / 'reports' / 'case-check.json'
DEFAULT_BED = [220.0, 220.0, 250.0]
PRESSABLE = ('plunger', 'membrane')
#: (cross-section rule, what may be in the way) per interface kind for face access.
PROBE_KINDS = {'display', 'led', 'button', 'keyswitch', 'knob', 'buzzer', 'touch', 'sensor', 'ultrasonic', 'usb', 'connector'}
SKIN_MM = 0.8
CONTACT_MM3 = 2.0


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contract_path(workspace):
    found = sorted((Path(workspace) / 'boards').glob('*.enclosure.json'))
    return found[0] if found else None


def status(workspace):
    """Without CadQuery: does a fresh, passing check exist? -> (state, reason).

    state is 'none' (no case yet), 'stale' (board or parts changed since the check), 'failed', or
    'passed'. Read by the verdict and the Stop hook.
    """
    workspace = Path(workspace)
    report_path = workspace / REPORT
    if not (workspace / OUT / 'parts.json').exists():
        return 'none', 'no case/out/parts.json yet'
    try:
        report = json.loads(report_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'stale', 'the case has not been checked (no case/reports/case-check.json)'
    contract = contract_path(workspace)
    if not contract or report.get('contractSha') != sha(contract):
        return 'stale', 'the board changed since the case was checked'
    for name, digest in (report.get('partShas') or {}).items():
        path = workspace / OUT / name
        if not path.exists() or sha(path) != digest:
            return 'stale', f'case/out/{name} changed since the case was checked'
    if not report.get('passed'):
        return 'failed', f"{len(report.get('errors') or [])} case error(s)"
    return 'passed', 'the case fits the board and opens for every interface'


# ---- geometry (CadQuery only below) ---------------------------------------------------------

def _cq():
    import cadquery as cq  # noqa: PLC0415 — only the case python has it
    return cq


def _box(cq, cx, cy, sx, sy, z0, z1, angle=0.0):
    z0, z1 = min(z0, z1), max(z0, z1)
    solid = cq.Workplane('XY').box(max(sx, 0.01), max(sy, 0.01), max(z1 - z0, 0.01), centered=(True, True, False))
    if angle:
        solid = solid.rotate((0, 0, 0), (0, 0, 1), angle)
    return solid.translate((cx, cy, z0))


def _poly(cq, corners, z0, z1):
    z0, z1 = min(z0, z1), max(z0, z1)
    return cq.Workplane('XY').polyline([tuple(c) for c in corners]).close().extrude(max(z1 - z0, 0.01)).translate((0, 0, z0))


def _volume(a, b):
    try:
        inter = a.intersect(b)
        return sum(s.Volume() for s in inter.solids().vals())
    except Exception:  # an empty or degenerate intersection
        return 0.0


def keepouts(cq, contract):
    """What the case may not enter: the board, every part's body, every seated module."""
    b, t = contract['board'], contract['board']['thicknessMm']
    out = [('board', _box(cq, 0, 0, b['widthMm'], b['heightMm'], -t, 0))]
    module_refs = {r for m in contract['modules'] if not m.get('offboard') for r in m['refs']}
    for p in contract['parts']:
        if p['heightMm'] <= 0 or p['ref'] in module_refs:
            continue
        x0, y0, x1, y1 = p['boxMm']
        z0, z1 = (0, p['heightMm']) if p['side'] == 'top' else (-t - p['heightMm'], -t)
        if 'CHERRY_MX' in p['footprint'].upper():
            # A key's plate wraps the 14 mm body, inside the courtyard: keep out the body only.
            cx, cy = p['centerMm']
            out.append((p['ref'], _box(cq, cx, cy, 13.8, 13.8, z0, z1)))
            continue
        out.append((p['ref'], _box(cq, (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, z0, z1)))
    for m in contract['modules']:
        if m.get('offboard'):
            continue
        if m['side'] == 'top':
            z0, z1 = 0.0, m['frontZMm']
        else:
            z0, z1 = m['frontZMm'], -t
        out.append((f"{m['id']} ({', '.join(m['refs'])})", _poly(cq, m['outlineCornersMm'], z0, z1)))
    return out


def _module_of(contract, item):
    return next((m for m in contract['modules'] if item.get('refs') and m['refs'][0] == item['refs'][0]), None)


def probe(cq, contract, item, reach):
    """The column of air an interface needs, from its surface out past the case."""
    t = contract['board']['thicknessMm']
    cx, cy = item['centerMm']
    sx, sy = item.get('sizeMm') or [2.0, 2.0]
    kind, angle = item['kind'], item.get('angleDeg', 0.0)
    access = item.get('access') or [0, 0, 1]
    if abs(access[2]) < 0.5:
        # An edge: a corridor the size of the plug, at the height of the receptacle.
        w, h = item.get('plugMm') or [sx, 3.0]
        m = _module_of(contract, item)
        if m:
            zc = (m['seatHeightMm'] + m['frontZMm']) / 2 if m['side'] == 'top' else (m['frontZMm'] - t - m['seatHeightMm']) / 2
        else:
            zc = item['zMm'] / 2 if item['side'] == 'top' else (item['zMm'] - t) / 2
        vx, vy = access[0], access[1]
        heading = math.degrees(math.atan2(vy, vx))
        return _box(cq, cx + vx * reach / 2, cy + vy * reach / 2, reach, w, zc - h / 2, zc + h / 2, heading)
    size = {'button': (2.4, 2.4), 'keyswitch': (14.0, 14.0), 'knob': (7.0, 7.0),
            'led': (min(sx, 2.0), min(sy, 2.0)), 'buzzer': (min(sx, 6.0), min(sy, 6.0))}.get(kind, (sx, sy))
    z0 = item['zMm'] + (0.05 if access[2] > 0 else -0.05)
    if kind == 'keyswitch':
        size, z0 = (13.8, 13.8), (0.05 if access[2] > 0 else -t - 0.05)   # through the plate, from the board
    z1 = reach if access[2] > 0 else -reach
    return _box(cq, cx, cy, size[0], size[1], z0, z1, angle)


def check(workspace, write=True):
    """Run every check; write the report. Returns the report."""
    cq = _cq()
    workspace = Path(workspace)
    errors, warnings = [], []
    contract_file = contract_path(workspace)
    if not contract_file:
        raise SystemExit('no boards/*.enclosure.json: publish the board first (python -m kicadpy.enclosure .)')
    contract = json.loads(contract_file.read_text(encoding='utf-8'))
    manifest_path = workspace / OUT / 'parts.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    intent = {}
    try:
        intent = json.loads((workspace / 'case' / 'interfaces.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        warnings.append({'kind': 'case_intent_missing', 'message': 'case/interfaces.json is missing: no interface was confirmed'})
    bed = (intent.get('printer') or {}).get('bedMm') or DEFAULT_BED
    if manifest.get('contractSha') and manifest['contractSha'] != sha(contract_file):
        errors.append({'kind': 'case_built_from_old_board', 'message': 'case/out/parts.json was built from an older '
                       'enclosure.json; rebuild the case from the current board'})

    solids, part_shas = [], {'parts.json': sha(manifest_path)}   # a relabelled or added part is a new case
    for part in manifest.get('parts') or []:
        name = part.get('name', '?')
        for key in ('stl', 'step'):
            path = workspace / OUT / (part.get(key) or '')
            if not part.get(key) or not path.is_file():
                errors.append({'kind': 'case_part_missing', 'part': name, 'message': f'{name}: {key} file missing in case/out/'})
            else:
                part_shas[part[key]] = sha(path)
        step = workspace / OUT / (part.get('step') or '')
        if not step.is_file():
            continue
        try:
            shape = cq.importers.importStep(str(step))
        except Exception as exc:
            errors.append({'kind': 'case_part_unreadable', 'part': name, 'message': f'{name}: {exc}'})
            continue
        solids.append((name, part.get('role', 'other'), shape))
        rot = part.get('printRotateDeg') or [0, 0, 0]
        placed = shape
        for axis, deg in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), rot):
            if deg:
                placed = placed.rotate((0, 0, 0), axis, deg)
        bb = placed.val().BoundingBox()
        dims, limit = sorted([bb.xlen, bb.ylen]), sorted(bed[:2])
        if dims[0] > limit[0] or dims[1] > limit[1] or bb.zlen > bed[2]:
            errors.append({'kind': 'case_part_too_big', 'part': name,
                           'message': f'{name} is {bb.xlen:.0f} x {bb.ylen:.0f} x {bb.zlen:.0f} mm printed, the bed is '
                                      f'{bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f}'})
    if not solids:
        errors.append({'kind': 'case_empty', 'message': 'case/out/parts.json lists no readable part'})

    # 1. Nothing goes into the board, a part, or a module.
    for label, keep in keepouts(cq, contract):
        for name, role, shape in solids:
            vol = _volume(shape, keep)
            if vol > CONTACT_MM3:
                errors.append({'kind': 'case_collides', 'part': name, 'with': label,
                               'message': f'{name} goes {vol:.1f} mm3 into {label}'})

    # 2. Every interface the person uses has its opening.
    reach = 400.0
    for item in contract['interfaces']:
        if item.get('status') == 'ignored' or item.get('offboard') or item['kind'] not in PROBE_KINDS:
            continue
        column = probe(cq, contract, item, reach)
        sx, sy = item.get('sizeMm') or [2.0, 2.0]
        blocked = [(name, role, _volume(shape, column)) for name, role, shape in solids]
        blocked = [(n, r, v) for n, r, v in blocked if v > 0.05]
        label = f"{item['name']} ({item['kind']})"
        if item['kind'] == 'touch':
            area = max(sx * sy, 0.01)
            wall = sum(v for _, _, v in blocked) / area
            limit = item.get('maxWallMm', 2.0)
            if wall > limit + 0.05:
                errors.append({'kind': 'case_touch_wall', 'interface': label,
                               'message': f'{label}: {wall:.1f} mm of plastic over the pad, at most {limit} mm'})
            continue
        if item['kind'] == 'led' and blocked:
            area = max(min(sx, 2.0) * min(sy, 2.0), 0.01)
            skin = sum(v for _, _, v in blocked) / area
            if skin <= SKIN_MM + 0.05:
                continue
        if item['kind'] == 'button':
            blocked = [(n, r, v) for n, r, v in blocked if r not in PRESSABLE]
        if blocked:
            who = ', '.join(f'{n} ({v:.0f} mm3)' for n, _, v in blocked)
            errors.append({'kind': 'case_interface_blocked', 'interface': label,
                           'message': f"{label} at {item['centerMm']} is covered by {who}: {item.get('rule', '')}"})

    # 3. Mounting holes hold the board.
    t = contract['board']['thicknessMm']
    for hole in contract['mountingHoles']:
        ring = _box(cq, hole['xMm'], hole['yMm'], 6.0, 6.0, -t - 8.0, 8.0)
        if not any(_volume(shape, ring) > 0.5 for _, _, shape in solids):
            warnings.append({'kind': 'case_hole_unused', 'hole': hole['name'],
                             'message': f"{hole['name']} has no boss or clamp around it"})

    import cadquery
    report = {'schema': 1, 'passed': not errors, 'errors': errors, 'warnings': warnings,
              'contract': str(contract_file.relative_to(workspace)), 'contractSha': sha(contract_file),
              'partShas': part_shas, 'parts': [n for n, _, _ in solids], 'bedMm': bed,
              'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'cadquery': cadquery.__version__}
    if write:
        target = workspace / REPORT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ('check', 'status'):
        print('usage: python -m kicadpy.case check|status [workspace]', file=sys.stderr)
        return 2
    workspace = Path(argv[1] if len(argv) > 1 else '.').resolve()
    if argv[0] == 'status':
        state, reason = status(workspace)
        print(json.dumps({'ok': True, 'case': state, 'reason': reason}))
        return 0
    report = check(workspace)
    for e in report['errors']:
        print(f"  ✗ {e['kind']}: {e['message']}")
    for w in report['warnings']:
        print(f"  ! {w['kind']}: {w['message']}")
    print(json.dumps({'ok': True, 'passed': report['passed'], 'errors': len(report['errors']),
                      'warnings': len(report['warnings']), 'report': str(REPORT)}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
