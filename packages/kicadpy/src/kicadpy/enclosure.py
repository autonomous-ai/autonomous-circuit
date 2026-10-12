"""The case contract for a KiCad-native board — `boards/<stem>.enclosure.json`.

A case that fits the board is not enough: it has to know what the person touches and sees. A
screen needs a window over its *active area*, a button needs a plunger, a light needs a window
or a thin skin, a USB-C needs a corridor the size of the *plug*, a touch pad needs thin plastic,
an antenna needs no metal. On these boards most of that sits on plug-in modules (a display on a
socket, a SuperMini whose USB-C is the board's only port), not on the carrier itself.

This module derives that from the published board, the same way Copper's
`circuitpy.enclosure` does for v1 (same top-level shape, `schema: 2` adds the rest):

- the **frame**: millimetres, origin at the board outline's centre, +x right, +y up, viewed from
  the top (KiCad's y-down is flipped), z = 0 on the top copper surface, the board spans -t..0;
- every footprint with its side, centre, rotation, body box and an **estimated height**
  (vendored KiCad has no 3D models, so heights come from a per-class table and module data and
  say so);
- the **interfaces**: user-facing elements found from refdes, footprint, value and the module
  library (`packages/golden-blocks/modules/<id>/module.json`), each with its access direction
  and the opening rule for its kind; plug-in modules are placed on the board from their header
  pads;
- the agent's **intent**, `case/interfaces.json`: it confirms, overrides or ignores what was
  found, adds what was missed, and says how the product stands (flat or upright, which face looks
  at the person). Generated geometry plus declared intent is the contract, as macropad's
  `board-interface.json` + `interface.meta.json`.

Never part of the manufacturing packet (its hashed member list would change) and never a design
input: the intent lives under `case/`, not `design/`, `engineering/` or `product.json`.

    python -m kicadpy.enclosure [workspace]     # regenerate and print what the case must do
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys

SCHEMA = 2
INTENT = Path('case') / 'interfaces.json'
EDGE_BAND_MM = 4.0

#: Opening rule per interface kind — what the case must provide. Read by the case designer and,
#: later, by the case checker.
RULES = {
    'display': 'window over the active area (+0.5 mm each side), not the module outline; bezel or ledge holds the module, never the glass',
    'button': 'clear hole over the actuator with a plunger or a flexible membrane; travel must reach the switch',
    'keyswitch': 'plate cutout 14.0 x 14.0 mm at 1.5 mm plate thickness; 19 x 19 mm keycap clearance',
    'knob': 'shaft hole about 7 mm; leave room for the knob above the case',
    'led': 'light window over the LED or a skin of 0.8 mm or less; light-block between neighbouring LEDs',
    'buzzer': 'sound holes over the buzzer, about 10 mm pattern; do not seal it',
    'usb': 'corridor sized to the plug overmold (about 12.5 x 7 mm), flush with the case wall',
    'connector': 'opening or cable path to the connector; plug overmold, not the receptacle',
    'touch': 'plastic over the pad at most the module maxWallMm (about 2 mm); no metal, no air gap',
    'antenna': 'no metal, screws or thick walls over the antenna end',
    'sensor': 'clear line of sight from the sensor face',
    'ultrasonic': 'two round openings, about 16.5 mm, over the transducers; the face must not be recessed',
    'cable': 'cable path and strain relief from the header to where the off-board part lives',
    'mcu': 'held by its socket; reachable for re-plugging when the back opens',
}

#: (pattern on "REF|VALUE|FOOTPRINT", kind, module id) — first match wins.
CLASSIFY = [
    (r'SUPERMINI|C3[ _-]?(SOCKET|IO|PWR|LEFT|RIGHT|ROW)|ESP32[-_ ]?C3', 'mcu', 'esp32-c3-supermini'),
    (r'ST7789|TFT|LCD|1\.54', 'display', 'st7789-1.54-module'),
    (r'OLED|SSD1306|SH1106', 'display', 'oled-0.96-i2c-module'),
    (r'TTP223|TOUCH|BELLY_TOUCH', 'touch', 'ttp223-module'),
    (r'SERVO|SG90', 'cable', 'sg90-continuous-module'),
    (r'HC[-_ ]?SR04|ULTRASONIC|SONAR', 'ultrasonic', None),
    (r'CHERRY_MX|\bMX\b|KAILH|GATERON', 'keyswitch', None),
    (r'^(ENC|ROT)|\|ENC\b|ENCODER|EC11|ROTARY', 'knob', None),
    (r'^SW|^BTN|^S\d|BUTTON|TACT|PTS6|BELLY', 'button', None),
    (r'^BZ|BUZZER|PIEZO|SPEAKER|^LS\d', 'buzzer', None),
    (r'^(J|CN|P|USB)\d*\|.*USB', 'usb', None),
    (r'WS2812|SK6812|NEOPIXEL|^LED|STATUS[_ ]?LED|\|LED\b', 'led', None),
]
MOUNTING = re.compile(r'^(H|MH)\d|MOUNTINGHOLE|HOLE_M\d|NPTH', re.I)

#: Height above the board surface by footprint class: (pattern on footprint name, mm).
HEADER = re.compile(r'PINHEADER|PINSOCKET|HEADER_1X|SOCKET_1X|CONN_01X', re.I)

HEIGHTS = [
    (r'PINSOCKET|SOCKET_1X', 8.5), (r'PINHEADER|HEADER_1X', 8.5), (r'CHERRY_MX', 11.6),
    (r'USB[_-]?C', 3.3), (r'WS2812|SK6812', 1.6), (r'BELLY|PTS645|SW_PUSH|TACT|6X6', 4.3),
    (r'PIEZO|BUZZER', 7.0), (r'BULK|ELEC|CP_', 16.0), (r'SOT|SOIC|QFN|TSSOP|MSOP', 1.2),
    (r'0402|0603|0805|1206|SOD|SMA|SMB', 1.3), (r'TESTPAD|TESTPOINT|HOLE|MOUNTING', 0.0),
]
DEFAULT_HEIGHT = 3.0


def modules_dir():
    env = os.environ.get('KICAD_HARNESS_BLOCKS')
    if env:
        return Path(env).parent / 'modules'
    return Path(__file__).resolve().parents[3] / 'golden-blocks' / 'modules'


def load_modules(root=None):
    root = Path(root) if root else modules_dir()
    out = {}
    for path in sorted(root.glob('*/module.json')):
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            out[doc['id']] = doc
        except (OSError, ValueError, KeyError):
            continue
    return out


def classify(fp):
    ref = fp['reference'].upper()
    key = f"{ref}|{fp['value']}|{fp['footprint']}".upper()
    if MOUNTING.search(ref) or MOUNTING.search(fp['footprint'].upper()):
        return 'mounting', None
    # A test point's value is a net name (3V3_LCD, OLED_SCL): never a thing the person touches.
    if re.match(r'^(TP|FID)\d', ref) or re.search(r'TESTPOINT|TESTPAD|FIDUCIAL', key):
        return None, None
    plug = bool(HEADER.search(fp['footprint'])) or bool(re.match(r'^(J|CN|P|U|M|A)\d', ref))
    for pattern, kind, module in CLASSIFY:
        if re.search(pattern, key, re.I):
            if module and not plug:
                continue      # a module sits on a header or socket, never on a passive or a pad
            return kind, module
    return None, None


def height(fp):
    name = fp['footprint'].upper()
    for pattern, mm in HEIGHTS:
        if re.search(pattern, name):
            return mm, 'class table'
    return DEFAULT_HEIGHT, 'default (unknown class)'


class Frame:
    """KiCad absolute (y down) -> contract (origin at the outline centre, y up)."""

    def __init__(self, bounds):
        x0, y0, x1, y1 = bounds
        self.cx, self.cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.w, self.h = x1 - x0, y1 - y0

    def pt(self, p):
        return [round(p[0] - self.cx, 3), round(self.cy - p[1], 3)]

    def box(self, b):
        a, c = self.pt((b[0], b[3])), self.pt((b[2], b[1]))
        return [a[0], a[1], c[0], c[1]]

    def edge(self, box):
        """Nearest board edge to a body box and the gap to it (Copper's rule: the body, not the centre)."""
        gaps = {'left': box[0] + self.w / 2, 'right': self.w / 2 - box[2],
                'bottom': box[1] + self.h / 2, 'top': self.h / 2 - box[3]}
        edge, gap = min(gaps.items(), key=lambda kv: kv[1])
        return (edge if gap <= EDGE_BAND_MM else None), round(gap, 3)


def _norm(v):
    n = math.hypot(*v) or 1.0
    return (v[0] / n, v[1] / n)


def _pads(fp, frame):
    pads = sorted(fp['pads'], key=lambda q: (len(str(q['number'])), str(q['number'])))
    return [frame.pt(q['at']) for q in pads]


def place_module(module, headers, frame, toward=None):
    """Module centre, axes and side on the board from its header footprints.

    One header: +x is pin 1 -> last pin; +y points to the module body, toward the board centre
    unless `toward` says 'away'. Two headers: +y runs from the first header to the second.
    """
    first = _pads(headers[0], frame)
    u = _norm((first[-1][0] - first[0][0], first[-1][1] - first[0][1]))
    c0 = (sum(p[0] for p in first) / len(first), sum(p[1] for p in first) / len(first))
    spec = module['headers']
    if len(headers) >= 2 and len(spec) >= 2:
        second = _pads(headers[1], frame)
        c1 = (sum(p[0] for p in second) / len(second), sum(p[1] for p in second) / len(second))
        n = _norm((c1[0] - c0[0], c1[1] - c0[1]))
    else:
        n = (-u[1], u[0])
        to_centre = (-c0[0], -c0[1])
        inward = n[0] * to_centre[0] + n[1] * to_centre[1] >= 0
        if inward == (toward == 'away'):
            n = (-n[0], -n[1])
    hx, hy = spec[0]['centerMm']
    centre = (c0[0] - u[0] * hx - n[0] * hy, c0[1] - u[1] * hx - n[1] * hy)
    return centre, u, n


def _to_board(centre, u, n, local):
    return [round(centre[0] + u[0] * local[0] + n[0] * local[1], 3),
            round(centre[1] + u[1] * local[0] + n[1] * local[1], 3)]


def _side(fp):
    return 'bottom' if fp['layer'].startswith('B.') else 'top'


def _face_vector(face, side, u, n):
    up = 1.0 if side == 'top' else -1.0
    return {'front': [0.0, 0.0, up], 'back': [0.0, 0.0, -up],
            'edge-x': [-u[0], -u[1], 0.0], 'edge+x': [u[0], u[1], 0.0],
            'edge-y': [-n[0], -n[1], 0.0], 'edge+y': [n[0], n[1], 0.0]}[face]


def _edge_name(vec, frame_note=None):
    x, y = vec[0], vec[1]
    if abs(vec[2]) > 0.5:
        return 'top' if vec[2] > 0 else 'bottom'
    if abs(x) >= abs(y):
        return 'right' if x > 0 else 'left'
    return 'top-edge' if y > 0 else 'bottom-edge'


def parts_heights(parts_doc):
    """ref -> {'heightMm', 'seatHeightMm'} from parts.json entries that record them."""
    out = {}
    items = (parts_doc or {}).get('parts', parts_doc) if isinstance(parts_doc, dict) else parts_doc
    for entry in (items.values() if isinstance(items, dict) else items or []):
        if not isinstance(entry, dict):
            continue
        refs = entry.get('refdes') or entry.get('refs') or []
        refs = [refs] if isinstance(refs, str) else refs
        got = {k: float(entry[k]) for k in ('heightMm', 'seatHeightMm') if isinstance(entry.get(k), (int, float))}
        for ref in refs:
            if got:
                out[str(ref)] = got
    return out


def _bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _overlap(a, b, margin=0.5):
    return a[0] < b[2] - margin and b[0] < a[2] - margin and a[1] < b[3] - margin and b[1] < a[3] - margin


def placement_findings(placed, parts, frame):
    """A module placed where it cannot physically be: off the board, on another module, on a tall part.

    The generator guesses which side of a single header the body hangs (toward the board centre);
    these findings are how a wrong guess surfaces instead of a case built around it.
    """
    out = []
    onboard = [m for m in placed if not m['offboard']]
    for m in onboard:
        box = _bbox(m['outlineCornersMm'])
        half_w, half_h = frame.w / 2, frame.h / 2
        if box[0] < -half_w - 1 or box[2] > half_w + 1 or box[1] < -half_h - 1 or box[3] > half_h + 1:
            out.append({'kind': 'enclosure_module_outside_board', 'severity': 'warning', 'ref': m['refs'][0],
                        'message': f"{m['id']} on {', '.join(m['refs'])} reaches past the board outline; if it really "
                                   'overhangs, say so; otherwise set bodyToward (board-center | away) or offboard: true '
                                   'for that ref in case/interfaces.json'})
        for p in parts:
            if p['side'] != m['side'] or p['ref'] in m['refs'] or p['heightMm'] <= m['seatHeightMm']:
                continue
            if _overlap(box, p['boxMm']):
                out.append({'kind': 'enclosure_module_on_tall_part', 'severity': 'warning', 'ref': m['refs'][0],
                            'message': f"{m['id']} on {', '.join(m['refs'])} sits {m['seatHeightMm']} mm up, over "
                                       f"{p['ref']} ({p['heightMm']} mm tall)"})
    for i, a in enumerate(onboard):
        for b in onboard[i + 1:]:
            if a['side'] == b['side'] and _overlap(_bbox(a['outlineCornersMm']), _bbox(b['outlineCornersMm'])):
                out.append({'kind': 'enclosure_modules_overlap', 'severity': 'warning', 'ref': a['refs'][0],
                            'message': f"{a['id']} ({', '.join(a['refs'])}) and {b['id']} ({', '.join(b['refs'])}) "
                                       'land on top of each other; one of them hangs the other way (bodyToward) or '
                                       'lives off the board (offboard: true) — declare it in case/interfaces.json'})
    return out


def build(state, *, parts_doc=None, intent=None, modules=None, stem='main'):
    """The contract from a worker `inspect` result. Pure: no files, no KiCad.

    Heights, most trusted first: the agent's intent (`heightMm` / `seatHeightMm` per ref), then
    parts.json entries that record one, then module.json / the class table (an assumption).
    """
    modules = load_modules() if modules is None else modules
    intent = intent or {}
    declared = intent.get('interfaces') or {}
    recorded = parts_heights(parts_doc)
    frame = Frame(state['boardBoundsMm'])
    t = float(state.get('thicknessMm') or 1.6)
    footprints = [f for f in state['footprints'] if not f.get('dnp')]

    parts, holes, connectors, candidates = [], [], [], []
    findings, assumptions = [], set()
    for fp in footprints:
        kind, module = classify(fp)
        box = frame.box(fp.get('courtyardMm') or fp.get('bboxMm') or [fp['at'][0], fp['at'][1]] * 2)
        hmm, hsrc = height(fp)
        ref_decl = declared.get(fp['reference']) or {}
        if isinstance(ref_decl.get('heightMm'), (int, float)):
            hmm, hsrc = float(ref_decl['heightMm']), 'case/interfaces.json'
        elif 'heightMm' in recorded.get(fp['reference'], {}):
            hmm, hsrc = recorded[fp['reference']]['heightMm'], 'parts.json'
        side = _side(fp)
        parts.append({'ref': fp['reference'], 'value': fp['value'], 'footprint': fp['footprint'], 'side': side,
                      'centerMm': frame.pt(fp['at']), 'rotationDeg': fp['rotationDeg'], 'boxMm': box,
                      'heightMm': hmm, 'heightSource': hsrc})
        if kind == 'mounting':
            drill = max((max(q.get('drillMm') or [0, 0]) for q in fp['pads']), default=0.0)
            diameter = drill or 2.2
            holes.append({'name': fp['reference'], 'xMm': frame.pt(fp['at'])[0], 'yMm': frame.pt(fp['at'])[1],
                          'diameterMm': round(diameter, 3), 'fastener': 'M3' if diameter >= 3.0 else 'M2'})
            continue
        if kind is None:
            if re.match(r'^(J|CN|P)\d', fp['reference'], re.I) and fp['reference'] not in declared:
                findings.append({'kind': 'enclosure_unclassified_connector', 'severity': 'warning', 'ref': fp['reference'],
                                 'message': f"{fp['reference']} ({fp['value']}) is a connector the contract cannot place; "
                                            'declare it in case/interfaces.json (kind, or ignore: true)'})
            continue
        candidates.append({'fp': fp, 'kind': kind, 'module': module, 'box': box, 'side': side})
        edge, gap = frame.edge(box)
        if kind in ('usb', 'connector', 'cable') and module is None:
            connectors.append({'refdes': fp['reference'], 'xMm': frame.pt(fp['at'])[0], 'yMm': frame.pt(fp['at'])[1],
                               'bodyMm': [round(box[2] - box[0], 3), round(box[3] - box[1], 3)], 'edge': edge,
                               'edgeGapMm': gap, 'rotationDeg': fp['rotationDeg']})

    interfaces, placed_modules = [], []
    by_module = {}
    for c in candidates:
        if c['module'] and c['module'] in modules:
            by_module.setdefault(c['module'], []).append(c)
    for mid, group in by_module.items():
        module = modules[mid]
        per = len(module['headers'])
        group = sorted(group, key=lambda c: c['fp']['reference'])
        instances = [group[i:i + per] for i in range(0, len(group), per)] if per > 1 else [[c] for c in group]
        for inst in instances:
            refs = [c['fp']['reference'] for c in inst]
            toward = (declared.get(refs[0]) or {}).get('bodyToward')
            centre, u, n = place_module(module, [c['fp'] for c in inst], frame, toward)
            side = inst[0]['side']
            mdecl = declared.get(refs[0]) or {}
            seat, seat_src = module.get('seatHeightMm', 0.0), 'module.json (assumed)'
            for r in refs:
                if 'seatHeightMm' in recorded.get(r, {}):
                    seat, seat_src = recorded[r]['seatHeightMm'], 'parts.json'
            if isinstance(mdecl.get('seatHeightMm'), (int, float)):
                seat, seat_src = float(mdecl['seatHeightMm']), 'case/interfaces.json'
            offboard_module = bool(mdecl.get('offboard')) or module.get('mount') == 'cable'
            z_face = seat + module.get('bodyHeightMm', 0.0)
            ow, oh = module['outlineMm']
            corners = [_to_board(centre, u, n, (sx * ow / 2, sy * oh / 2)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            placed_modules.append({'id': mid, 'refs': refs, 'side': side, 'centerMm': [round(centre[0], 3), round(centre[1], 3)],
                                   'axisX': [round(u[0], 4), round(u[1], 4)], 'axisY': [round(n[0], 4), round(n[1], 4)],
                                   'outlineCornersMm': corners, 'seatHeightMm': seat, 'seatSource': seat_src,
                                   'offboard': offboard_module, 'frontZMm': round(z_face if side == 'top' else -t - z_face, 3),
                                   'mount': module.get('mount'), 'holesMm': [_to_board(centre, u, n, h) for h in module.get('mountingHolesMm', [])],
                                   'assumptions': module.get('assumptions', [])})
            assumptions.update(f'{mid}: {a}' for a in module.get('assumptions', []))
            if seat_src.endswith('(assumed)'):
                assumptions.add(f'{mid} ({", ".join(refs)}): seat height {seat} mm assumed; record the socket height '
                                'in parts.json (seatHeightMm) or case/interfaces.json')
            for feat in module.get('features', []):
                vec = _face_vector(feat['face'], side, u, n)
                item = {'name': f"{mid} {feat['name']}", 'key': f"{refs[0]}#{feat['kind']}", 'kind': feat['kind'],
                        'refs': refs, 'module': mid, 'side': side,
                        'centerMm': _to_board(centre, u, n, feat['centerMm']), 'sizeMm': feat['sizeMm'],
                        'angleDeg': round(math.degrees(math.atan2(u[1], u[0])), 2), 'access': vec,
                        'accessFace': _edge_name(vec), 'zMm': round(z_face if side == 'top' else -t - z_face, 3),
                        'rule': RULES.get(feat['kind'], ''), 'note': feat.get('note', ''), 'source': 'module.json'}
                for key in ('plugMm', 'maxWallMm'):
                    if key in feat:
                        item[key] = feat[key]
                if mdecl.get('offboard') and module.get('mount') != 'cable':
                    item['offboard'], item['accessFace'] = True, 'off-board'
                interfaces.append(item)

    for c in candidates:
        if c['module'] and c['module'] in modules:
            continue
        fp, side = c['fp'], c['side']
        hmm, _ = height(fp)
        vec = [0.0, 0.0, 1.0 if side == 'top' else -1.0]
        edge, gap = frame.edge(c['box'])
        if c['kind'] in ('usb', 'connector') and edge:
            vec = {'left': [-1.0, 0.0, 0.0], 'right': [1.0, 0.0, 0.0], 'top': [0.0, 1.0, 0.0], 'bottom': [0.0, -1.0, 0.0]}[edge]
        item = {'name': f"{fp['reference']} {fp['value']}", 'key': fp['reference'], 'kind': c['kind'],
                'refs': [fp['reference']], 'module': c['module'],
                'side': side, 'centerMm': frame.pt(fp['at']),
                'sizeMm': [round(c['box'][2] - c['box'][0], 3), round(c['box'][3] - c['box'][1], 3)],
                'angleDeg': fp['rotationDeg'], 'access': vec, 'accessFace': _edge_name(vec),
                'zMm': round(hmm if side == 'top' else -t - hmm, 3), 'rule': RULES.get(c['kind'], ''), 'note': '',
                'source': 'footprint'}
        if c['kind'] in ('usb',):
            item['plugMm'] = [12.5, 7.0]
        if HEADER.search(fp['footprint']) and c['kind'] not in ('usb', 'connector', 'cable'):
            # A light, button, buzzer or knob named on a header hangs off it on wires: the case
            # holds the part where the person reaches it, and the header is only a cable exit.
            item['offboard'] = True
            item['access'], item['accessFace'] = [0.0, 0.0, 0.0], 'off-board'
            item['note'] = (f"the {c['kind']} hangs off this header on wires; place it in the case and declare where "
                            'in case/interfaces.json (extra), the header is a cable exit').strip()
        if c['module'] and c['module'] not in modules:
            item['note'] = f"module {c['module']} has no module.json; its geometry is not known"
        interfaces.append(item)

    # Intent: confirm, override, ignore, add.
    for item in interfaces:
        ref = item['refs'][0]
        # 'J1#usb' targets one feature of a module; plain 'J1' applies to all of them.
        decl = declared.get(item.get('key')) or declared.get(ref) or {}
        item['status'] = 'proposed'
        if decl.get('ignore'):
            item['status'] = 'ignored'
        elif decl:
            if decl.get('kind') and decl['kind'] != item['kind']:
                item['kind'], item['rule'], item['status'] = decl['kind'], RULES.get(decl['kind'], ''), 'overridden'
            elif decl.get('confirm'):
                item['status'] = 'confirmed'
            if decl.get('note'):
                item['note'] = (item['note'] + ' ' + decl['note']).strip()
    for extra in intent.get('extra') or []:
        interfaces.append({**extra, 'status': 'declared', 'source': 'case/interfaces.json', 'rule': RULES.get(extra.get('kind'), '')})
    known_refs = {r for i in interfaces for r in i.get('refs', [])} | {p['ref'] for p in parts} | \
        {i['key'] for i in interfaces if i.get('key')}
    for ref in declared:
        if ref not in known_refs:
            findings.append({'kind': 'enclosure_intent_unknown_ref', 'severity': 'warning', 'ref': ref,
                             'message': f'case/interfaces.json names {ref}, which is not on the board'})
    for item in interfaces:
        if item['status'] == 'proposed':
            findings.append({'kind': 'enclosure_interface_unconfirmed', 'severity': 'warning', 'ref': item['refs'][0] if item.get('refs') else '',
                             'message': f"{item['name']} ({item['kind']}) was found but not confirmed in case/interfaces.json"})
    findings += placement_findings(placed_modules, parts, frame)
    tallest = {'top': max([p['heightMm'] for p in parts if p['side'] == 'top'] + [0.0]),
               'bottom': max([p['heightMm'] for p in parts if p['side'] == 'bottom'] + [0.0])}
    for m in placed_modules:
        key = 'top' if m['side'] == 'top' else 'bottom'
        tallest[key] = max(tallest[key], abs(m['frontZMm']) - (t if key == 'bottom' else 0.0))
    if any(p['heightSource'] != 'module.json' for p in parts):
        assumptions.add('component heights come from a per-class table: vendored KiCad has no 3D models')
    orientation = {'mount': 'flat', 'faceToUser': 'top', 'upEdge': 'top', **(intent.get('orientation') or {})}

    holes.sort(key=lambda h: (h['xMm'], h['yMm']))
    connectors.sort(key=lambda c: c['refdes'])
    return {
        'schema': SCHEMA, 'generator': 'kicadpy', 'entryKind': 'enclosure',
        'frame': {'units': 'mm', 'origin': 'board outline centre', 'x': 'right', 'y': 'up, viewed from the top',
                  'z': 'up from the top copper surface; the board spans -thickness..0',
                  'kicadToContract': {'subtract': [round(frame.cx, 3), round(frame.cy, 3)], 'flipY': True}},
        'board': {'name': stem, 'widthMm': round(frame.w, 3), 'heightMm': round(frame.h, 3), 'thicknessMm': t,
                  'centerMm': [0.0, 0.0], 'outline': state.get('boardBoundsSource', 'outline')},
        'orientation': orientation,
        'mountingHoles': holes,
        'connectors': connectors,
        'clearance': {'topMm': round(tallest['top'], 3), 'bottomMm': round(tallest['bottom'], 3), 'confidence': 'table'},
        'interfaces': interfaces,
        'modules': placed_modules,
        'parts': parts,
        'assumptions': sorted(assumptions),
        'findings': findings,
        'notes': ['Interfaces with status "proposed" are what the generator found; the agent confirms, overrides or ignores '
                  'each in case/interfaces.json and adds what it missed under "extra".',
                  f'{len(holes)} mounting hole(s).'],
    }


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def write(workspace, pcb=None, inspect=None):
    """Regenerate `boards/<stem>.enclosure.json` from the board on disk. Returns its path."""
    from . import toolchain
    workspace = Path(workspace)
    pcb = Path(pcb) if pcb else next((workspace / 'design').glob('*.kicad_pcb'))
    state = inspect(pcb) if inspect else toolchain.worker('inspect', pcb)
    parts_doc = _json(workspace / 'parts.json') or _json(workspace / 'design' / 'parts.json')
    intent = _json(workspace / INTENT)
    doc = build(state, parts_doc=parts_doc, intent=intent, stem=pcb.stem)
    target = workspace / 'boards' / f'{pcb.stem}.enclosure.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, target)
    return target


def summary(doc):
    lines = [f"board {doc['board']['widthMm']} x {doc['board']['heightMm']} mm, {len(doc['mountingHoles'])} holes, "
             f"tallest top {doc['clearance']['topMm']} mm / bottom {doc['clearance']['bottomMm']} mm"]
    for i in doc['interfaces']:
        lines.append(f"  [{i['status']:10}] {i['kind']:10} {i['name'][:44]:44} {i['accessFace']:11} at {i['centerMm']}")
    for f in doc['findings']:
        lines.append(f"  ! {f['kind']}: {f['message']}")
    return '\n'.join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    workspace = Path(argv[0] if argv else '.').resolve()
    target = write(workspace)
    doc = json.loads(target.read_text(encoding='utf-8'))
    print(summary(doc))
    print(json.dumps({'ok': True, 'enclosure': str(target), 'interfaces': len(doc['interfaces']),
                      'unconfirmed': sum(1 for i in doc['interfaces'] if i['status'] == 'proposed')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
