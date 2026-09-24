"""What earlier runs measured, kept where the next run can read it.

Two tables under `kicadpy/data/`, both keyed by LCSC code:

- `rotation-offsets.json` — the factory (JLCPCB/EasyEDA) zero-orientation offset of a part,
  measured with `kicadpy.verify rotation` or by hand. Keyed by part, not package: two SOT-23-6
  parts differed (C2687116 = 270°, C131941 = 180°).
- `parts-verified.json` — a part's identity (maker, MPN, package) as a run looked it up on the
  live supplier page and a review confirmed, plus the traps found (a clone, a wrong maker).
  Stock and price are not here; they change daily (`kicadpy.verify stock`).

    python -m kicadpy.knowledge show C97521 C6186      # what is known about these codes
    python -m kicadpy.knowledge learn <workspace>      # harvest a finished run's manufacturing.json + parts.json

Every row names the runs it came from. `learn` appends and never overwrites an offset that
disagrees: it records the conflict for a person to settle. Read-only for the workspace.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
import re
import sys

DATA = Path(__file__).with_name('data')
ROTATION = DATA / 'rotation-offsets.json'
PARTS = DATA / 'parts-verified.json'


def _load(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'_about': '', 'parts': {}}


def _save(path, doc):
    doc['parts'] = dict(sorted(doc['parts'].items()))
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')


def rotation_offset(lcsc):
    """The measured factory offset for this LCSC code, or None when no run has measured it."""
    row = _load(ROTATION)['parts'].get(lcsc)
    return None if row is None or row.get('conflicts') else row.get('offsetDeg')


def rotation_rows():
    return _load(ROTATION)['parts']


def part(lcsc):
    return _load(PARTS)['parts'].get(lcsc)


def parts_rows():
    return _load(PARTS)['parts']


def learn(workspace, today=None):
    """Harvest a finished workspace: assembly offsets and part identities, with the run named."""
    workspace = Path(workspace)
    today = today or datetime.date.today().isoformat()
    source = f'{workspace.name} {today}'
    manufacturing = json.loads((workspace / 'manufacturing.json').read_text(encoding='utf-8'))
    parts = json.loads((workspace / 'parts.json').read_text(encoding='utf-8'))['parts']
    pcb = (workspace / 'design' / 'main.kicad_pcb').read_text(encoding='utf-8')
    footprint_of = {m.group(2): m.group(1).split(':')[-1]
                    for m in re.finditer(r'\(footprint "([^"]+)"[\s\S]*?\(property "Reference" "([^"]+)"', pcb)}
    by_ref = {ref: p for p in parts for ref in p.get('refdes', [])}
    rot, ids = _load(ROTATION), _load(PARTS)
    added = {'rotation': [], 'parts': [], 'conflicts': []}
    for ref, decision in manufacturing.get('assembly', {}).items():
        p = by_ref.get(ref)
        code = p and p.get('lcsc')
        offset = decision.get('rotationOffsetDeg')
        if not code or offset is None:
            continue
        row = rot['parts'].get(code)
        if row is None:
            rot['parts'][code] = {'footprint': footprint_of.get(ref, ''), 'package': p.get('package') or p.get('native_footprint'),
                                  'offsetDeg': offset, 'sources': [source]}
            added['rotation'].append(code)
        elif row.get('offsetDeg') == offset:
            if source not in row['sources']:
                row['sources'].append(source)
        else:
            row.setdefault('conflicts', []).append({'source': source, 'offsetDeg': offset})
            added['conflicts'].append(code)
    for p in parts:
        code = p.get('lcsc')
        if not code:
            continue
        row = ids['parts'].get(code)
        if row is None:
            ids['parts'][code] = {'manufacturer': p.get('manufacturer'), 'mpn': p.get('mpn'),
                                  'package': p.get('package') or p.get('native_footprint'),
                                  'sources': [source], 'checkedDate': p.get('checkedDate') or today}
            added['parts'].append(code)
        elif source not in row['sources']:
            row['sources'].append(source)
    _save(ROTATION, rot)
    _save(PARTS, ids)
    return added


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ('show', 'learn'):
        print(__doc__)
        sys.exit(2)
    if argv[0] == 'learn':
        added = learn(argv[1])
        print(f"rotation rows added: {added['rotation']}\nparts rows added: {added['parts']}\nconflicts (settle by hand): {added['conflicts']}")
        print(json.dumps({'ok': True, 'result': added}))
        return
    rot, ids = rotation_rows(), parts_rows()
    out = {}
    for code in argv[1:] or sorted(set(rot) | set(ids)):
        out[code] = {'rotation': rot.get(code), 'identity': ids.get(code)}
        r, i = rot.get(code), ids.get(code)
        print(f"{code:10s} offset={r['offsetDeg'] if r else '?':>4}  {(i or {}).get('manufacturer') or '?'} {(i or {}).get('mpn') or ''}"
              + (f"  — {i['note']}" if i and i.get('note') else ''))
    print(json.dumps({'ok': True, 'result': out}))


if __name__ == '__main__':
    main()
