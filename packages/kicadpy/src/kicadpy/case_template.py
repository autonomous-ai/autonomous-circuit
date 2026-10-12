"""A starting case for any board: a two-part box built from the contract, openings included.

Every board gets a printable, checkable case before anyone styles it. The agent's
`case/build.py` can be as short as

    from kicadpy.case_template import build_box
    build_box('.')

and is then reshaped (a character, rounded corners, a stand) as long as `kicadpy.case check`
keeps passing. Built in the contract frame (`boards/<stem>.enclosure.json`), assembled:

- **front**: walls and the face plate above the board, printed face down (no supports): a window
  over each display's active area, a light window over each LED, a cutout per key, a hole per
  knob, a sound grille per buzzer, a plunger hole per button; a touch pad keeps the plate
  (at most its wall limit);
- **back**: the floor and walls below the board, with a boss under every mounting hole;
- **plunger_<ref>**: one per tact button, its flange caught under the face plate;
- edge interfaces (a USB-C) get a corridor through the wall the size of the plug, at the
  height of the receptacle; interfaces facing the bottom go through the floor.

The split is the board's underside: the board rests on the bosses and the front holds it down.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

WALL = 1.6
CLEARANCE = 0.6      # board edge to wall, and above the tallest part
MARGIN = 0.5         # around every opening


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rect(cq, cx, cy, sx, sy, z0, z1, angle=0.0):
    z0, z1 = min(z0, z1), max(z0, z1)
    s = cq.Workplane('XY').box(sx, sy, z1 - z0, centered=(True, True, False))
    if angle:
        s = s.rotate((0, 0, 0), (0, 0, 1), angle)
    return s.translate((cx, cy, z0))


def _cyl(cq, cx, cy, d, z0, z1):
    z0, z1 = min(z0, z1), max(z0, z1)
    return cq.Workplane('XY').circle(d / 2).extrude(z1 - z0).translate((cx, cy, z0))


def shapes(contract, wall=WALL, clearance=CLEARANCE):
    """The parts as CadQuery solids, keyed by name -> (role, solid). No files."""
    import cadquery as cq
    b, t = contract['board'], contract['board']['thicknessMm']
    iw, ih = b['widthMm'] + 2 * clearance, b['heightMm'] + 2 * clearance
    ow, oh = iw + 2 * wall, ih + 2 * wall
    top = max([contract['clearance']['topMm']] + [m['frontZMm'] for m in contract['modules'] if m['side'] == 'top' and not m.get('offboard')]) + clearance
    bottom = -t - max([contract['clearance']['bottomMm']] + [-m['frontZMm'] - t for m in contract['modules']
                                                              if m['side'] == 'bottom' and not m.get('offboard')]) - clearance
    split = -t

    front = _rect(cq, 0, 0, ow, oh, split, top + wall).cut(_rect(cq, 0, 0, iw, ih, split, top))
    back = _rect(cq, 0, 0, ow, oh, bottom - wall, split).cut(_rect(cq, 0, 0, iw, ih, bottom, split))
    plungers = {}

    for hole in contract['mountingHoles']:
        d = hole['diameterMm']
        boss = _cyl(cq, hole['xMm'], hole['yMm'], d + 3.2, bottom, split).cut(_cyl(cq, hole['xMm'], hole['yMm'], max(d - 0.4, 1.0), bottom, split))
        back = back.union(boss)

    module_by_ref = {m['refs'][0]: m for m in contract['modules']}
    for item in contract['interfaces']:
        if item.get('status') == 'ignored' or item.get('offboard'):
            continue
        kind = item['kind']
        cx, cy = item['centerMm']
        sx, sy = item.get('sizeMm') or [2.0, 2.0]
        angle = item.get('angleDeg', 0.0)
        access = item.get('access') or [0, 0, 1]
        if abs(access[2]) < 0.5:
            if kind not in ('usb', 'connector'):
                continue
            w, h = item.get('plugMm') or [sx, 3.0]
            m = module_by_ref.get(item['refs'][0]) if item.get('refs') else None
            if m:
                zc = (m['seatHeightMm'] + m['frontZMm']) / 2 if m['side'] == 'top' else (m['frontZMm'] - t - m['seatHeightMm']) / 2
            else:
                zc = item['zMm'] / 2 if item['side'] == 'top' else (item['zMm'] - t) / 2
            reach = max(ow, oh)
            heading = math.degrees(math.atan2(access[1], access[0]))
            cut = _rect(cq, cx + access[0] * reach / 2, cy + access[1] * reach / 2, reach, w + 2 * MARGIN,
                        zc - h / 2 - MARGIN, zc + h / 2 + MARGIN, heading)
            front, back = front.cut(cut), back.cut(cut)
            continue
        up = access[2] > 0
        plate0, plate1 = (top, top + wall) if up else (bottom - wall, bottom)
        if kind == 'touch':
            continue                                     # the plate stays: wall <= its limit
        if kind == 'button':
            hole_d = 4.0
            cut = _cyl(cq, cx, cy, hole_d, plate0 - 0.01, plate1 + 0.01)
            z_act = item['zMm'] + (0.2 if up else -0.2)
            stem = _cyl(cq, cx, cy, hole_d - 0.6, z_act, plate1 + 1.5 if up else plate0 - 1.5)
            flange = _cyl(cq, cx, cy, hole_d + 3.0, plate0 - 1.0, plate0 - 0.2) if up else _cyl(cq, cx, cy, hole_d + 3.0, plate1 + 0.2, plate1 + 1.0)
            plungers[f"plunger_{item['refs'][0]}"] = ('plunger', stem.union(flange))
        elif kind == 'keyswitch':
            cut = _rect(cq, cx, cy, 18.6, 18.6, plate0 - 0.01, plate1 + 0.01, angle)
        elif kind == 'knob':
            cut = _cyl(cq, cx, cy, 7.5, plate0 - 0.01, plate1 + 0.01)
        elif kind == 'led':
            cut = _rect(cq, cx, cy, min(sx, 3.0) + 2 * MARGIN, min(sy, 3.0) + 2 * MARGIN, plate0 - 0.01, plate1 + 0.01, angle)
        elif kind == 'buzzer':
            cut = _rect(cq, cx, cy, min(sx, 8.0), min(sy, 8.0), plate0 - 0.01, plate1 + 0.01, angle)
        elif kind in ('display', 'sensor', 'ultrasonic', 'usb', 'connector'):
            cut = _rect(cq, cx, cy, sx + 2 * MARGIN, sy + 2 * MARGIN, plate0 - 0.01, plate1 + 0.01, angle)
        else:
            continue
        if up:
            front = front.cut(cut)
        else:
            back = back.cut(cut)
    parts = {'front': ('shell', front), 'back': ('shell', back)}
    parts.update(plungers)
    return parts


def build_box(workspace='.', wall=WALL, clearance=CLEARANCE):
    """Write case/out/ (STL + STEP per part + parts.json) for the board in `workspace`."""
    import cadquery as cq
    workspace = Path(workspace)
    contract_file = sorted((workspace / 'boards').glob('*.enclosure.json'))[0]
    contract = json.loads(contract_file.read_text(encoding='utf-8'))
    out = workspace / 'case' / 'out'
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'contractSha': _sha(contract_file), 'generator': 'kicadpy.case_template.build_box', 'parts': []}
    for name, (role, solid) in shapes(contract, wall, clearance).items():
        cq.exporters.export(solid, str(out / f'{name}.stl'))
        cq.exporters.export(solid, str(out / f'{name}.step'))
        rotate = [180, 0, 0] if name == 'front' else [0, 0, 0]       # face plate down, no supports
        manifest['parts'].append({'name': name, 'role': role, 'stl': f'{name}.stl', 'step': f'{name}.step',
                                  'printRotateDeg': rotate, 'supports': False})
    (out / 'parts.json').write_text(json.dumps(manifest, indent=1) + '\n', encoding='utf-8')
    return out
