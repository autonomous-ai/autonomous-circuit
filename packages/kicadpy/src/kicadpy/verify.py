"""Measure before you claim: the checks an agent runs instead of trusting its own summary.

Born from harness-12 (2026-09-23): Grok 4.7 spent 5 h 33 and $90 on a board it called
"ERC 0, DRC 0" while the gate counted 630 findings, four ICs had no wires in the schematic,
the firmware pin table disagreed with the copper, and every factory rotation was guessed at 0.
Claude's cross-review (harness-14) found all of it with six small scripts; these are those
scripts, made generic, so any engine runs them instead of writing its own.

    python -m kicadpy.verify gate     [workspace]                       # what the publisher actually counted
    python -m kicadpy.verify netlist  design/main.kicad_pro             # schematic pin->net vs PCB pad->net, hollow symbols
    python -m kicadpy.verify islands  design/main.kicad_pro [--net GND] [--near X Y R]   # zone islands, via sites (pcbnew)
    python -m kicadpy.verify easyeda  build/easyeda_fp.json C123 C456   # fetch EasyEDA footprint pads (network)
    python -m kicadpy.verify rotation design/main.kicad_pro parts.json build/easyeda_fp.json   # factory rotation offsets
    python -m kicadpy.verify stock    parts.json build/jlc_stock.json   # JLCPCB stock / library type (network)

Every command prints a readable report and, as its LAST line, one JSON object.
Host Python, read-only: nothing here edits a source file.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import toolchain
from .sexp import child, parse, value


# ---------------------------------------------------------------- gate

def gate_summary(workspace):
    """The publisher's own numbers: the sidecar, the last check.json, the last manufacturing report."""
    root = Path(workspace)
    out = {'workspace': str(root), 'sidecar': None, 'check': None, 'manufacturing': None}
    sidecars = sorted(root.glob('boards/*.board.json'))
    if sidecars:
        side = json.loads(sidecars[-1].read_text())
        warnings = side.get('validation', {}).get('warnings', [])
        by = collections.Counter((w.get('severity'), w.get('kind')) for w in warnings)
        out['sidecar'] = {'file': str(sidecars[-1].relative_to(root)), 'fabReady': bool(side.get('fab', {}).get('ready')),
                          'errors': sum(1 for w in warnings if w.get('severity') == 'error'),
                          'warnings': sum(1 for w in warnings if w.get('severity') == 'warning'),
                          'byKind': [{'severity': s, 'kind': k, 'count': n} for (s, k), n in by.most_common()]}
    reviews = sorted((d for d in root.glob('boards/*_review/*') if d.is_dir()), key=lambda d: d.stat().st_mtime)
    if reviews:
        latest = reviews[-1]
        check = latest / 'reports' / 'check.json'
        if check.is_file():
            data = json.loads(check.read_text())
            findings = data.get('findings', [])
            by = collections.Counter((f.get('stage'), f.get('type'), f.get('severity')) for f in findings)
            out['check'] = {'file': str(check.relative_to(root)), 'passed': bool(data.get('passed')),
                            'findings': len(findings),
                            'errors': sum(1 for f in findings if f.get('severity') == 'error'),
                            'warnings': sum(1 for f in findings if f.get('severity') == 'warning'),
                            'byType': [{'stage': s, 'type': t, 'severity': v, 'count': n} for (s, t, v), n in by.most_common()]}
        report = latest / 'manufacturing' / 'manufacturing-report.json'
        if report.is_file():
            data = json.loads(report.read_text())
            findings = data.get('findings', [])
            by = collections.Counter((f.get('severity'), f.get('kind')) for f in findings)
            out['manufacturing'] = {'file': str(report.relative_to(root)), 'prototypeReady': bool(data.get('prototypeReady')),
                                    'findings': len(findings),
                                    'byKind': [{'severity': s, 'kind': k, 'count': n} for (s, k), n in by.most_common()]}
    return out


def print_gate(out):
    print(f"gate summary for {out['workspace']}")
    side, check, mfg = out['sidecar'], out['check'], out['manufacturing']
    if not side:
        print('  no sidecar under boards/: nothing has been published yet')
    else:
        print(f"  sidecar {side['file']}: fab.ready={side['fabReady']}  errors={side['errors']}  warnings={side['warnings']}")
        for row in side['byKind'][:20]:
            print(f"    {row['count']:5d}  {row['severity']:8s} {row['kind']}")
    if check:
        print(f"  native check {check['file']}: passed={check['passed']}  findings={check['findings']} "
              f"(errors {check['errors']}, warnings {check['warnings']})")
        for row in check['byType'][:20]:
            print(f"    {row['count']:5d}  {row['stage']:4s} {row['severity']:8s} {row['type']}")
    if mfg:
        print(f"  manufacturing {mfg['file']}: prototypeReady={mfg['prototypeReady']}  findings={mfg['findings']}")
        for row in mfg['byKind'][:20]:
            print(f"    {row['count']:5d}  {row['severity']:8s} {row['kind']}")
    print('  The gate counts EVERY finding at --severity-all. "0 errors" in KiCad\'s default DRC is not a pass;')
    print('  the number that matters is check.findings above, and it must reach 0.')


# ---------------------------------------------------------------- netlist

def _norm_net(name):
    name = name or ''
    return name[1:] if name.startswith('/') else name


def schematic_pins(netlist_text):
    """kicad-cli `sch export netlist --format kicadsexpr` -> ({'REF.PIN': net}, {refs})."""
    root = parse(netlist_text)
    pins, refs = {}, set()
    comps = child(root, 'components') or []
    for comp in comps[1:]:
        if isinstance(comp, list) and comp[0] == 'comp':
            ref = child(comp, 'ref')
            if ref:
                refs.add(value(ref[1]))
    nets = child(root, 'nets') or []
    for net in nets[1:]:
        if not (isinstance(net, list) and net[0] == 'net'):
            continue
        name = value(child(net, 'name')[1])
        for node in net[1:]:
            if isinstance(node, list) and node[0] == 'node':
                ref, pin = value(child(node, 'ref')[1]), value(child(node, 'pin')[1])
                pins[f'{ref}.{pin}'] = name
    return pins, refs


def pcb_pads(pcb_text):
    """A .kicad_pcb -> ({'REF.PAD': net}, [footprints with pads in their own frame])."""
    root = parse(pcb_text)
    if root[0] != 'kicad_pcb':
        raise ValueError('not a KiCad PCB')
    pads, footprints = {}, []
    for node in root[1:]:
        if not (isinstance(node, list) and node and node[0] == 'footprint'):
            continue
        ref = None
        for prop in node[1:]:
            if isinstance(prop, list) and prop[0] == 'property' and len(prop) > 2 and value(prop[1]) == 'Reference':
                ref = value(prop[2])
        at = child(node, 'at') or ['at', '0', '0']
        fp = {'reference': ref, 'lib': value(node[1]) if len(node) > 1 and isinstance(node[1], str) else '',
              'at': [float(at[1]), float(at[2])], 'rotationDeg': float(at[3]) if len(at) > 3 else 0.0, 'pads': []}
        for pad in node[1:]:
            if not (isinstance(pad, list) and pad[0] == 'pad'):
                continue
            number = value(pad[1])
            net = child(pad, 'net')
            net_name = value(net[-1]) if net and len(net) > 1 else ''
            pat = child(pad, 'at') or ['at', '0', '0']
            fp['pads'].append({'number': number, 'net': net_name, 'x': float(pat[1]), 'y': float(pat[2])})
            if ref and number:
                pads[f'{ref}.{number}'] = net_name
        footprints.append(fp)
    return pads, footprints


def compare_netlists(sch_pins, sch_refs, pcb_pad_nets):
    """Differences pad by pad, plus symbols the schematic never wires ("hollow")."""
    diffs = []
    for pad, net in sorted(pcb_pad_nets.items()):
        s = sch_pins.get(pad, '')
        if _norm_net(s) != _norm_net(net):
            diffs.append({'pad': pad, 'schematic': s, 'pcb': net})
    for pin, net in sorted(sch_pins.items()):
        if pin not in pcb_pad_nets and not pin.startswith('#'):
            diffs.append({'pad': pin, 'schematic': net, 'pcb': None})
    wired = {pin.split('.', 1)[0] for pin in sch_pins}
    netted = {pad.split('.', 1)[0] for pad, net in pcb_pad_nets.items() if net}
    # Hollow = the drawing wires nothing on it while the copper gives its pads nets. A mounting
    # hole (no pins, net-less pad) is not hollow; an IC whose pads say GND/V3_3 on the PCB is.
    hollow = sorted(r for r in sch_refs if r not in wired and r in netted and not r.startswith('#'))
    return {'differences': diffs, 'hollowSymbols': hollow,
            'schematicPins': len(sch_pins), 'pcbPads': len(pcb_pad_nets)}


def hollow_findings(hollow):
    """Sidecar findings for symbols the schematic never wires: one error per reference, in plain words."""
    return [{'kind': 'schematic_hollow_symbol', 'severity': 'error', 'part': ref,
             'message': f'{ref}: no pin of it is wired in the schematic, yet its pads carry nets on the PCB — '
                        'the drawing does not describe the board (derived symbol not resolved, or wires missing)'}
            for ref in hollow]


def netlist(project):
    project = Path(project)
    root, stem = project.parent, project.stem
    sch, pcb = root / f'{stem}.kicad_sch', root / f'{stem}.kicad_pcb'
    with tempfile.TemporaryDirectory() as tmp:
        net_file = Path(tmp) / 'schematic.net'
        toolchain.run([toolchain.executable('cli'), 'sch', 'export', 'netlist', '--format', 'kicadsexpr',
                       '-o', str(net_file), str(sch)])
        pins, refs = schematic_pins(net_file.read_text())
    pad_nets, _ = pcb_pads(pcb.read_text())
    return compare_netlists(pins, refs, pad_nets)


def print_netlist(out):
    print(f"schematic pins {out['schematicPins']}, pcb pads {out['pcbPads']}, differences {len(out['differences'])}, "
          f"hollow symbols {len(out['hollowSymbols'])}")
    for d in out['differences'][:60]:
        print(f"  {d['pad']:10s} schematic={d['schematic']!r:24} pcb={d['pcb']!r}")
    if out['hollowSymbols']:
        print('  symbols with NO wired pin in the schematic (the copper was netted by hand, the drawing says nothing):',
              ', '.join(out['hollowSymbols']))


# ---------------------------------------------------------------- islands (pcbnew worker)

def islands(project, net='GND', near=None):
    pcb = Path(project).with_suffix('.kicad_pcb')
    return toolchain.worker('islands', pcb, net=net, near=near)


def print_islands(out):
    print(f"net {out['net']}:")
    for layer, info in out['layers'].items():
        print(f"  {layer}: {info['islands']} filled island(s), areas mm2 {info['areasMm2'][:8]}")
    stranded = [p for p in out['pads'] if p.get('stranded')]
    print(f"  pads on the net: {len(out['pads'])}; islands without a path to the plane: {out.get('strandedIslands')}; "
          f"stranded pads: {len(stranded)}")
    for p in stranded[:30]:
        print(f"    {p['ref']}.{p['pad']} at ({p['x']:.2f},{p['y']:.2f}) {p['layer']} island {p['island']}  <- needs a via or a track to the plane")
    if out.get('sites'):
        print('  via sites (clearance to non-net copper on both layers, mm):')
        for s in out['sites'][:15]:
            print(f"    {s['clearanceMm']:6.3f} at ({s['x']:.2f},{s['y']:.2f}) islands F={s['islandF']} B={s['islandB']}")


# ---------------------------------------------------------------- rotation

def _rot(x, y, deg):
    a = math.radians(deg)
    return (x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a))


def _centred(pads):
    if not pads:
        return []
    mx = sum(p[1] for p in pads) / len(pads)
    my = sum(p[2] for p in pads) / len(pads)
    return [(n, round(x - mx, 4), round(y - my, 4)) for n, x, y in pads]


def _score(kp, ep, theta, by_number=True):
    err = 0.0
    er = [(n, *_rot(x, y, theta)) for n, x, y in ep]
    for n, x, y in kp:
        cands = [(ex, ey) for en, ex, ey in er if (en == n) or not by_number]
        if not cands:
            return 99.0
        err += min(math.hypot(x - ex, y - ey) for ex, ey in cands)
    return err / len(kp)


def rotation_offsets(footprints, lcsc_of, easyeda):
    """Per footprint: the theta in {0, 90, 180, 270} that maps the EasyEDA pads onto the KiCad pads.

    JLCPCB rotates by its own library's zero; the CPL angle it expects is KiCad angle + theta,
    so theta is the `rotationOffsetDeg` to record. Pads are matched by number, then by geometry
    alone when the numbering styles differ (TS-1187A: 1/1/2/2 vs 1/2/3/4).
    """
    rows = []
    for fp in sorted(footprints, key=lambda f: f['reference'] or ''):
        code = lcsc_of.get(fp['reference'])
        eda = easyeda.get(code) if code else None
        if not code or not eda or not eda.get('pads'):
            continue
        kp = _centred([(p['number'], p['x'], p['y']) for p in fp['pads'] if p['number']])
        ep = _centred([(p['number'], p['x'], p['y']) for p in eda['pads']])
        if not kp or not ep:
            continue
        method = 'pad number'
        results = {t: _score(kp, ep, t) for t in (0, 90, 180, 270)}
        if min(results.values()) > 0.35:
            method = 'geometry only (pad numbering differs)'
            results = {t: _score(kp, ep, t, by_number=False) for t in (0, 90, 180, 270)}
        theta = min(results, key=results.get)
        rows.append({'reference': fp['reference'], 'lcsc': code, 'footprint': fp['lib'],
                     'rotationOffsetDeg': theta, 'meanErrorMm': round(results[theta], 3), 'method': method,
                     'poorMatch': results[theta] > 0.35})
    return rows


def rotation(project, parts_file, easyeda_file):
    _, footprints = pcb_pads(Path(project).with_suffix('.kicad_pcb').read_text())
    parts = json.loads(Path(parts_file).read_text())
    lcsc_of = {ref: part['lcsc'] for part in parts.get('parts', []) if part.get('lcsc') for ref in part.get('refdes', [])}
    easyeda = json.loads(Path(easyeda_file).read_text())
    return {'rows': rotation_offsets(footprints, lcsc_of, easyeda)}


def print_rotation(out):
    print(f"{'ref':6s} {'lcsc':10s} {'kicad footprint':44s} offset  err(mm)  method")
    for r in out['rows']:
        flag = '  <- POOR MATCH, check by hand' if r['poorMatch'] else ''
        print(f"{r['reference']:6s} {r['lcsc']:10s} {r['footprint'][:44]:44s} {r['rotationOffsetDeg']:5d}   {r['meanErrorMm']:.3f}   {r['method']}{flag}")
    print('  offsets are what manufacturing.json assembly.<ref>.rotationOffsetDeg wants; 0 is a measurement too, not a default')


# ---------------------------------------------------------------- easyeda (network)

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'


def _get_json(url, headers=None, data=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': 'application/json, text/plain, */*', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def easyeda_pads(result):
    """EasyEDA `products/<code>/components` result -> (footprint title, pads in mm, y down, centred)."""
    pk = result.get('packageDetail', {}) or {}
    ds = pk.get('dataStr', {}) or {}
    head = ds.get('head', {}) or {}
    ox, oy = float(head.get('x', 0) or 0), float(head.get('y', 0) or 0)
    pads = []
    for s in ds.get('shape', []) or []:
        if not isinstance(s, str) or not s.startswith('PAD~'):
            continue
        f = s.split('~')
        # PAD~shape~x~y~w~h~layer~net~number~holeRadius~points~rotation~id~...
        pads.append({'number': f[8], 'shape': f[1], 'x': (float(f[2]) - ox) * 0.254, 'y': (float(f[3]) - oy) * 0.254,
                     'w': round(float(f[4]) * 0.254, 4), 'h': round(float(f[5]) * 0.254, 4), 'layer': f[6],
                     'rot': float(f[11]) if len(f) > 11 and f[11] else 0.0,
                     'hole_r': round(float(f[9]) * 0.254, 4) if len(f) > 9 and f[9] else 0.0})
    if pads:
        cx = sum(p['x'] for p in pads) / len(pads)
        cy = sum(p['y'] for p in pads) / len(pads)
        for p in pads:
            p['x'], p['y'] = round(p['x'] - cx, 4), round(p['y'] - cy, 4)
    return pk.get('title'), pads


def easyeda_entry(result):
    title, pads = easyeda_pads(result)
    para = ((result.get('dataStr', {}) or {}).get('head', {}) or {}).get('c_para', {}) or {}
    return {'title': result.get('title'), 'package': result.get('package'), 'footprint': title,
            'manufacturer': para.get('Manufacturer'), 'mpn': para.get('Manufacturer Part'),
            'pads': pads, 'updated': result.get('updateTime')}


def easyeda(out_file, codes):
    out = {}
    for code in codes:
        try:
            data = _get_json(f'https://easyeda.com/api/products/{code}/components?version=6.5.22',
                             headers={'Referer': 'https://easyeda.com/'})
            out[code] = easyeda_entry(data.get('result', {}) or {})
        except Exception as exc:  # network is the one thing here that fails for reasons outside the board
            out[code] = {'error': str(exc)}
        time.sleep(0.8)
    Path(out_file).write_text(json.dumps(out, indent=1))
    return {'file': str(out_file), 'fetched': sum(1 for v in out.values() if 'pads' in v), 'failed': sorted(k for k, v in out.items() if 'error' in v)}


# ---------------------------------------------------------------- stock (network)

JLC_URL = 'https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList'
JLC_FIELDS = ('componentCode', 'componentLibraryType', 'stockCount', 'erpComponentName', 'componentBrandEn',
              'componentModelEn', 'componentSpecificationEn', 'componentTypeEn', 'describe', 'urlSuffix', 'componentPrices')


def jlc_row(data, code):
    """The JLCPCB search answer -> the row for exactly this code, or why not."""
    rows = ((data.get('data') or {}).get('componentPageInfo') or {}).get('list') or []
    for row in rows:
        if row.get('componentCode') == code or str(row.get('urlSuffix', '')).endswith('/' + code):
            return {k: row.get(k) for k in JLC_FIELDS}
    return {'error': 'not found', 'candidates': [(r.get('componentCode'), r.get('erpComponentName')) for r in rows]}


def stock(parts_file, out_file, extra=()):
    parts = json.loads(Path(parts_file).read_text())
    codes = sorted({p.get('lcsc') for p in parts.get('parts', []) if p.get('lcsc')} | set(extra))
    out = {}
    for code in codes:
        body = {'currentPage': 1, 'pageSize': 5, 'keyword': code, 'firstSortName': '', 'secondSortName': '',
                'componentBrand': '', 'componentSpecification': '', 'componentAttributes': [],
                'componentLibraryType': '', 'stockFlag': False, 'searchSource': 'search'}
        try:
            data = _get_json(JLC_URL, headers={'Content-Type': 'application/json'}, data=json.dumps(body).encode('utf-8'), timeout=30)
            out[code] = jlc_row(data, code)
        except Exception as exc:
            out[code] = {'error': str(exc)}
        time.sleep(0.7)
    Path(out_file).write_text(json.dumps(out, indent=1))
    return {'file': str(out_file), 'codes': len(codes), 'found': sum(1 for v in out.values() if 'componentCode' in v),
            'missing': sorted(k for k, v in out.items() if 'error' in v),
            'rows': [{'lcsc': k, 'library': v.get('componentLibraryType'), 'stock': v.get('stockCount'),
                      'brand': v.get('componentBrandEn'), 'model': v.get('componentModelEn')} for k, v in out.items() if 'componentCode' in v]}


def print_stock(out):
    for r in out['rows']:
        print(f"  {r['lcsc']:10s} {str(r['library']):9s} stock {str(r['stock']):>7s}  {r['brand']}  {r['model']}")
    if out['missing']:
        print('  not found / failed:', ', '.join(out['missing']))


# ---------------------------------------------------------------- cli

def main(argv=None):
    parser = argparse.ArgumentParser(prog='kicadpy.verify', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('gate'); s.add_argument('workspace', nargs='?', default='.')
    s = sub.add_parser('netlist'); s.add_argument('project')
    s = sub.add_parser('islands'); s.add_argument('project'); s.add_argument('--net', default='GND')
    s.add_argument('--near', nargs=3, type=float, metavar=('X', 'Y', 'R'))
    s = sub.add_parser('easyeda'); s.add_argument('out'); s.add_argument('codes', nargs='+')
    s = sub.add_parser('rotation'); s.add_argument('project'); s.add_argument('parts'); s.add_argument('easyeda')
    s = sub.add_parser('stock'); s.add_argument('parts'); s.add_argument('out'); s.add_argument('codes', nargs='*')
    args = parser.parse_args(argv)
    try:
        if args.command == 'gate':
            out = gate_summary(args.workspace); print_gate(out)
        elif args.command == 'netlist':
            out = netlist(args.project); print_netlist(out)
        elif args.command == 'islands':
            out = islands(args.project, args.net, args.near); print_islands(out)
        elif args.command == 'easyeda':
            out = easyeda(args.out, args.codes); print(f"fetched {out['fetched']}, failed {out['failed']}")
        elif args.command == 'rotation':
            out = rotation(args.project, args.parts, args.easyeda); print_rotation(out)
        else:
            out = stock(args.parts, args.out, args.codes); print_stock(out)
        print(json.dumps({'ok': True, 'command': args.command, 'result': out}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'command': args.command, 'error': str(exc), 'kind': type(exc).__name__}))
        sys.exit(1)


if __name__ == '__main__':
    main()
