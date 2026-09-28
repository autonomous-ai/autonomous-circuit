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
    python -m kicadpy.verify rotation design/main.kicad_pro parts.json [build/easyeda_fp.json]   # factory rotation offsets
    python -m kicadpy.verify stock    parts.json build/jlc_stock.json   # JLCPCB stock / library type (network)
    python -m kicadpy.verify power    design/main.kicad_pro [--rail VBUS=10] [--limit-mm 3]   # cap totals per rail, cap-to-pin distances (pcbnew)
    python -m kicadpy.verify enables  design/main.kicad_pro parts.json   # enable/strap pins against the knowledge table's pin rules
    python -m kicadpy.verify modules  design/main.kicad_pro J2 st7789-1.54-module   # header pin order against the module card
    python -m kicadpy.verify thermal  design/main.kicad_pro U2 2       # the copper island under a pad, per layer (pcbnew)
    python -m kicadpy.verify stale    [workspace]                       # sources newer than the published packet

The last five came from what a second engine kept finding behind a first engine's fab.ready
(harness-14, -15, -17, -18): a level shifter with its enable pin tied to a rail, 30 uF on a
rail limited to 10, decoupling 16 mm from the pin, a header wired in the wrong order, a
"heatsink pour" that was not on the board, and sources edited after the packet was made.

Every command prints a readable report and, as its LAST line, one JSON object.
Host Python, read-only: nothing here edits a source file.

`rotation` and `stock` read `kicadpy.knowledge` first (what earlier runs measured, keyed by LCSC
code): a rotation the table knows needs no EasyEDA fetch, a measurement that disagrees with the
table is flagged, and a part with a recorded trap (a clone, a wrong maker) says so next to its
live stock row.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import knowledge, toolchain
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
        ref, val = None, ''
        for prop in node[1:]:
            if isinstance(prop, list) and prop[0] == 'property' and len(prop) > 2:
                if value(prop[1]) == 'Reference':
                    ref = value(prop[2])
                elif value(prop[1]) == 'Value':
                    val = value(prop[2])
        at = child(node, 'at') or ['at', '0', '0']
        fp = {'reference': ref, 'value': val, 'lib': value(node[1]) if len(node) > 1 and isinstance(node[1], str) else '',
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
    # A net named "/GND" is a sheet-local label on the root sheet. The comparison above forgives
    # it, KiCad's parity check does not once the PCB says "GND", and every tool that takes a net
    # name (`islands --net GND`) misses it. harness-15 (Grok, 2026-09-24) drew every net that way.
    local = sorted({n for n in sch_pins.values() if n.startswith('/') and n.count('/') == 1})
    return {'differences': diffs, 'hollowSymbols': hollow, 'sheetLocalNets': local,
            'schematicPins': len(sch_pins), 'pcbPads': len(pcb_pad_nets)}


def hollow_findings(hollow):
    """Sidecar findings for symbols the schematic never wires: one error per reference, in plain words."""
    return [{'kind': 'schematic_hollow_symbol', 'severity': 'error', 'part': ref,
             'message': f'{ref}: no pin of it is wired in the schematic, yet its pads carry nets on the PCB — '
                        'the drawing does not describe the board (derived symbol not resolved, or wires missing). '
                        'Run `kicadpy.verify netlist design/main.kicad_pro` and fix the schematic, not the copper.'}
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
    if out.get('sheetLocalNets'):
        names = out['sheetLocalNets']
        print(f"  {len(names)} net(s) are sheet-local labels ({', '.join(names[:6])}{', …' if len(names) > 6 else ''}): "
              "use global labels — a PCB net named GND will not match /GND, and every --net argument needs the slash")
    for d in out['differences'][:60]:
        print(f"  {d['pad']:10s} schematic={d['schematic']!r:24} pcb={d['pcb']!r}")
    if out['hollowSymbols']:
        print('  symbols with NO wired pin in the schematic (the copper was netted by hand, the drawing says nothing):',
              ', '.join(out['hollowSymbols']))


# ---------------------------------------------------------------- islands (pcbnew worker)

def islands(project, net='GND', near=None):
    pcb = Path(project).with_suffix('.kicad_pcb')
    out = toolchain.worker('islands', pcb, net=net, near=near)
    if not out.get('pads') and not net.startswith('/'):
        # Sheet-local labels name the PCB nets "/GND": try that spelling before reporting nothing.
        again = toolchain.worker('islands', pcb, net='/' + net, near=near)
        if again.get('pads'):
            again['requestedNet'] = net
            again['note'] = (f'no net "{net}" on this board; "/{net}" (a sheet-local label) has {len(again["pads"])} pads '
                             '— measured that one; use global labels in the schematic')
            return again
    return out


def print_islands(out):
    print(f"net {out['net']}:")
    if out.get('note'):
        print(f"  note: {out['note']}")
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


def rotation_offsets(footprints, lcsc_of, easyeda, table=None):
    """Per footprint: the theta in {0, 90, 180, 270} that maps the EasyEDA pads onto the KiCad pads.

    JLCPCB rotates by its own library's zero; the CPL angle it expects is KiCad angle + theta,
    so theta is the `rotationOffsetDeg` to record. Pads are matched by number, then by geometry
    alone when the numbering styles differ (TS-1187A: 1/1/2/2 vs 1/2/3/4).

    `table` is what earlier runs measured ({lcsc: offset}). A part the table knows and EasyEDA
    was not fetched for gets its row from the table (`method` "knowledge table"); a measurement
    that disagrees with the table is flagged (`agreesWithTable` false) for a person to settle.
    """
    table = table or {}
    rows = []
    for fp in sorted(footprints, key=lambda f: f['reference'] or ''):
        code = lcsc_of.get(fp['reference'])
        eda = easyeda.get(code) if code else None
        known = table.get(code) if code else None
        if not code:
            continue
        if not eda or not eda.get('pads'):
            if known is not None:
                rows.append({'reference': fp['reference'], 'lcsc': code, 'footprint': fp['lib'],
                             'rotationOffsetDeg': known, 'meanErrorMm': None, 'method': 'knowledge table',
                             'poorMatch': False, 'tableOffsetDeg': known, 'agreesWithTable': True})
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
                     'poorMatch': results[theta] > 0.35, 'tableOffsetDeg': known,
                     'agreesWithTable': known is None or known == theta})
    return rows


def rotation(project, parts_file, easyeda_file=None):
    _, footprints = pcb_pads(Path(project).with_suffix('.kicad_pcb').read_text())
    parts = json.loads(Path(parts_file).read_text())
    lcsc_of = {ref: part['lcsc'] for part in parts.get('parts', []) if part.get('lcsc') for ref in part.get('refdes', [])}
    easyeda = json.loads(Path(easyeda_file).read_text()) if easyeda_file else {}
    table = {code: knowledge.rotation_offset(code) for code in set(lcsc_of.values())}
    table = {code: off for code, off in table.items() if off is not None}
    rows = rotation_offsets(footprints, lcsc_of, easyeda, table)
    coded = {fp['reference'] for fp in footprints if lcsc_of.get(fp['reference'])}
    return {'rows': rows, 'measured': sum(1 for r in rows if r['method'] != 'knowledge table'),
            'fromTable': sum(1 for r in rows if r['method'] == 'knowledge table'),
            'disagreements': [r['reference'] for r in rows if not r['agreesWithTable']],
            'unknown': sorted(coded - {r['reference'] for r in rows})}


def print_rotation(out):
    print(f"{'ref':6s} {'lcsc':10s} {'kicad footprint':44s} offset  err(mm)  method")
    for r in out['rows']:
        flag = '  <- POOR MATCH, check by hand' if r['poorMatch'] else ''
        if not r.get('agreesWithTable', True):
            flag += f"  <- DISAGREES with the knowledge table ({r['tableOffsetDeg']}), settle by hand"
        err = '  -  ' if r['meanErrorMm'] is None else f"{r['meanErrorMm']:.3f}"
        print(f"{r['reference']:6s} {r['lcsc']:10s} {r['footprint'][:44]:44s} {r['rotationOffsetDeg']:5d}   {err}   {r['method']}{flag}")
    if out.get('unknown'):
        print(f"  no measurement and no table row for: {', '.join(out['unknown'])} — fetch them with `verify easyeda` first")
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


def attach_identity(out, part_of=None):
    """Beside each live row, what earlier runs verified about that code (maker, MPN, the trap found)."""
    part_of = part_of or knowledge.part
    for code, row in out.items():
        known = part_of(code)
        if not known:
            continue
        row['known'] = {k: known.get(k) for k in ('manufacturer', 'mpn', 'note') if known.get(k)}
        model = str(row.get('componentModelEn') or '')
        mpn = str(known.get('mpn') or '')
        if model and mpn:
            row['identityMismatch'] = mpn.lower() not in model.lower() and model.lower() not in mpn.lower()
    return out


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
    attach_identity(out)
    Path(out_file).write_text(json.dumps(out, indent=1))
    return {'file': str(out_file), 'codes': len(codes), 'found': sum(1 for v in out.values() if 'componentCode' in v),
            'missing': sorted(k for k, v in out.items() if 'error' in v),
            'rows': [{'lcsc': k, 'library': v.get('componentLibraryType'), 'stock': v.get('stockCount'),
                      'brand': v.get('componentBrandEn'), 'model': v.get('componentModelEn'),
                      'known': v.get('known'), 'identityMismatch': v.get('identityMismatch', False)}
                     for k, v in out.items() if 'componentCode' in v]}


def print_stock(out):
    for r in out['rows']:
        print(f"  {r['lcsc']:10s} {str(r['library']):9s} stock {str(r['stock']):>7s}  {r['brand']}  {r['model']}")
        if r.get('known', {}).get('note'):
            print(f"             known: {r['known']['note']}")
        if r.get('identityMismatch'):
            print(f"             <- the live listing ({r['model']}) is not the part earlier runs verified ({r['known'].get('mpn')}): check the code")
    if out['missing']:
        print('  not found / failed:', ', '.join(out['missing']))


# ---------------------------------------------------------------- power (pcbnew positions)

CAP_UNITS = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6, 'm': 1e-3, 'f': 1.0}
RAIL_NAME = re.compile(r'^/?(V\d|V[A-Z0-9_]*|.*VDD.*|.*VCC.*|.*AVDD.*|.*IOVDD.*|3V3|5V|VBUS|VIN|VOUT|DVDD|VREG.*|.*_?V3_?3.*|LED_?V.*)$', re.I)


def cap_farads(text):
    """'10uF', '100nF', '4.7u', '0.1µF', '15pF', '1u' -> farads; None when the value is not a capacitance."""
    m = re.match(r'^\s*(\d+(?:[.,]\d+)?)\s*([pnuµμmf])?F?\b', str(text or ''), re.I)
    if not m:
        return None
    number = float(m.group(1).replace(',', '.'))
    unit = (m.group(2) or 'f').lower()
    if unit == 'f' and not re.search(r'\d\s*F', str(text), re.I):
        return None          # a bare number is a resistor or a voltage, not a capacitor value
    return number * CAP_UNITS.get(unit, 1.0)


def _is_rail(net):
    return bool(net) and net.upper() not in ('GND', '/GND', 'AGND', '/AGND') and bool(RAIL_NAME.match(net))


def power_report(footprints, rails=None, limit_mm=3.0, ic_prefixes=('U',), cap_prefix='C', skip_refs=()):
    """Two measurements a report tends to get wrong.

    1. Capacitance per rail: every capacitor (ref C*) with a pad on the rail and its other pad on
       GND, summed by value. `rails` = {'VBUS': 10.0} adds the limit in uF; a rail over its limit
       is listed under `overLimit`.
    2. Decoupling distance: for every IC pad (ref U*) on a rail, the nearest capacitor pad on the
       same rail, in mm; `limit_mm` is the block rule (rp2040-core: 3 mm). Pins with no capacitor
       on their rail at all are listed too — a rail with 8 caps 16 mm away is not decoupled.
    `footprints` is the worker's `pads` result (absolute positions).
    """
    rails = {k.lstrip('/'): v for k, v in (rails or {}).items()}
    def norm(n): return (n or '').lstrip('/')
    caps = []
    for fp in footprints:
        if not fp['ref'].startswith(cap_prefix):
            continue
        farads = cap_farads(fp.get('value'))
        nets = [norm(pd['net']) for pd in fp['pads']]
        if farads is None or len(fp['pads']) < 2:
            continue
        caps.append({'ref': fp['ref'], 'farads': farads, 'nets': nets, 'pads': fp['pads']})
    totals = {}
    for c in caps:
        rail_nets = [n for n in c['nets'] if _is_rail(n)]
        if len(rail_nets) == 1 and any(n.upper() in ('GND', 'AGND') for n in c['nets']):
            totals.setdefault(rail_nets[0], {'uF': 0.0, 'caps': []})
            totals[rail_nets[0]]['uF'] += c['farads'] * 1e6
            totals[rail_nets[0]]['caps'].append(f"{c['ref']}={c['farads'] * 1e6:g}u")
    for rail, info in totals.items():
        info['uF'] = round(info['uF'], 3)
        if rail in rails:
            info['limitUF'] = rails[rail]
    over = [rail for rail, info in totals.items() if 'limitUF' in info and info['uF'] > info['limitUF']]
    missing_rails = [rail for rail in rails if rail not in totals]
    cap_pads = {}
    for c in caps:
        for pd in c['pads']:
            n = norm(pd['net'])
            if _is_rail(n):
                cap_pads.setdefault(n, []).append((c['ref'], pd['number'], pd['x'], pd['y']))
    pins = []
    for fp in footprints:
        if not fp['ref'].startswith(tuple(ic_prefixes)) or fp['ref'] in set(skip_refs):
            continue
        for pd in fp['pads']:
            n = norm(pd['net'])
            if not _is_rail(n):
                continue
            best = None
            for ref, num, x, y in cap_pads.get(n, []):
                d = math.hypot(x - pd['x'], y - pd['y'])
                if best is None or d < best[0]:
                    best = (d, ref, num)
            pins.append({'ref': fp['ref'], 'pad': pd['number'], 'net': n,
                         'nearestCap': None if best is None else f'{best[1]}.{best[2]}',
                         'distanceMm': None if best is None else round(best[0], 3),
                         'overLimit': best is None or best[0] > limit_mm})
    return {'limitMm': limit_mm, 'rails': totals, 'overLimit': over, 'railsWithoutCaps': missing_rails,
            'pins': pins, 'pinsOverLimit': [f"{q['ref']}.{q['pad']}" for q in pins if q['overLimit']]}


def power(project, rails=None, limit_mm=3.0, parts_file=None):
    pcb = Path(project).with_suffix('.kicad_pcb')
    footprints = toolchain.worker('pads', pcb)['footprints']
    # An ESD array's VBUS pin is a clamp reference, not a supply: the knowledge table says which
    # parts need no capacitor of their own (`noSupplyDecoupling`), keyed by LCSC code.
    skip = []
    if parts_file and Path(parts_file).is_file():
        parts = json.loads(Path(parts_file).read_text())
        for part in parts.get('parts', []):
            known = knowledge.part(part.get('lcsc') or '') or {}
            if known.get('noSupplyDecoupling'):
                skip += part.get('refdes', [])
    out = power_report(footprints, rails, limit_mm, skip_refs=skip)
    out['skippedRefs'] = sorted(skip)
    return out


def print_power(out):
    for rail, info in sorted(out['rails'].items()):
        flag = f"  <- OVER the {info['limitUF']} uF limit" if rail in out['overLimit'] else ''
        lim = f" (limit {info['limitUF']} uF)" if 'limitUF' in info else ''
        print(f"  rail {rail:10s} {info['uF']:8.3f} uF{lim}: {', '.join(info['caps'])}{flag}")
    for rail in out['railsWithoutCaps']:
        print(f"  rail {rail:10s} has NO capacitor to GND")
    print(f"  decoupling: {len(out['pins'])} IC power pins, {len(out['pinsOverLimit'])} farther than {out['limitMm']} mm from a capacitor on their rail")
    for q in out['pins']:
        if q['overLimit']:
            d = 'no capacitor on this rail' if q['distanceMm'] is None else f"{q['distanceMm']:.3f} mm to {q['nearestCap']}"
            print(f"    {q['ref']}.{q['pad']:4s} {q['net']:12s} {d}")
    print('  totals count every capacitor between the rail and GND; the block limit for decoupling is per pin, not per BOM')


def power_findings(out):
    """Publisher findings: a rail over its declared limit, an IC power pin without a capacitor within the limit."""
    findings = []
    for rail in out['overLimit']:
        info = out['rails'][rail]
        findings.append({'kind': 'rail_capacitance', 'severity': 'error', 'net': rail,
                         'message': f"{rail}: {info['uF']:g} uF of capacitance to GND ({', '.join(info['caps'])}) exceeds the declared limit of {info['limitUF']:g} uF."})
    for q in out['pins']:
        if q['overLimit']:
            where = 'no capacitor on that rail' if q['distanceMm'] is None else f"nearest {q['nearestCap']} is {q['distanceMm']:g} mm away"
            findings.append({'kind': 'decoupling_distance', 'severity': 'warning', 'part': q['ref'],
                             'message': f"{q['ref']}.{q['pad']} ({q['net']}): {where}; the block rule is a capacitor within {out['limitMm']:g} mm of the pin it serves."})
    return findings


# ---------------------------------------------------------------- enables (knowledge pin rules)

def enables_check(pad_nets, lcsc_of, part_of):
    """Pins the knowledge table says must sit on a given net (an active-low /OE on GND, a strap pin)."""
    rows = []
    for ref, code in sorted(lcsc_of.items()):
        known = part_of(code) or {}
        for rule in known.get('pinRules', []):
            actual = pad_nets.get(f"{ref}.{rule['pin']}")
            want = rule.get('require')
            ok = actual is not None and actual.lstrip('/').upper() == str(want).lstrip('/').upper()
            rows.append({'ref': ref, 'lcsc': code, 'pin': rule['pin'], 'name': rule.get('name', ''),
                         'require': want, 'actual': actual, 'ok': ok, 'why': rule.get('why', '')})
    return {'rows': rows, 'violations': [f"{r['ref']}.{r['pin']}" for r in rows if not r['ok']]}


def enables(project, parts_file):
    pad_nets, _ = pcb_pads(Path(project).with_suffix('.kicad_pcb').read_text())
    parts = json.loads(Path(parts_file).read_text())
    lcsc_of = {ref: part['lcsc'] for part in parts.get('parts', []) if part.get('lcsc') for ref in part.get('refdes', [])}
    return enables_check(pad_nets, lcsc_of, knowledge.part)


def print_enables(out):
    if not out['rows']:
        print('  no part on this board has a pin rule in the knowledge table (nothing checked, nothing proven)')
    for r in out['rows']:
        mark = 'ok  ' if r['ok'] else 'BAD '
        print(f"  {mark}{r['ref']}.{r['pin']} {r['name']:8s} must be {r['require']}, is {r['actual']}   {r['why'] if not r['ok'] else ''}")


# ---------------------------------------------------------------- modules (header order vs the card)

PIN_ALIASES = {'GND': ('GND',), 'VCC': ('VCC', '3V3', 'V3_3', '3.3V', 'VDD', 'VBUS', '5V'),
               'SCL': ('SCL', 'SCK', 'CLK'), 'SDA': ('SDA', 'MOSI', 'SDI', 'DIN', 'DATA'),
               'RES': ('RES', 'RST', 'RESET'), 'DC': ('DC', 'D/C', 'RS'), 'CS': ('CS', 'SS'),
               'BLK': ('BLK', 'BL', 'LED', 'BACKLIGHT'), 'OUT': ('OUT', 'TOUCH', 'SIG')}


def module_card_pins(text):
    """The first pin table of a modules/<id>/BLOCK.md: [(pin number, name)] in card order."""
    pins = []
    for line in text.splitlines():
        m = re.match(r'^\|\s*(\d+)\s*\|\s*([A-Za-z0-9_/]+)\s*\|', line)
        if m:
            pins.append((m.group(1), m.group(2).upper()))
    return pins


def modules_check(pad_nets, ref, pins):
    rows = []
    for number, name in pins:
        net = pad_nets.get(f'{ref}.{number}')
        bare = (net or '').lstrip('/').upper()
        tokens = PIN_ALIASES.get(name, (name,))
        ok = bool(bare) and any(t in bare for t in tokens)
        rows.append({'pin': number, 'expected': name, 'net': net, 'ok': ok})
    return {'ref': ref, 'rows': rows, 'mismatches': [r['pin'] for r in rows if not r['ok']]}


def modules(project, ref, card):
    card_path = Path(card)
    if not card_path.is_file():
        blocks = os.environ.get('KICAD_HARNESS_BLOCKS')
        if blocks:
            card_path = Path(blocks).parent / 'modules' / card / 'BLOCK.md'
    if not card_path.is_file():
        raise FileNotFoundError(f'module card not found: {card} (a path, or an id under $KICAD_HARNESS_BLOCKS/../modules)')
    pins = module_card_pins(card_path.read_text(encoding='utf-8'))
    if not pins:
        raise ValueError(f'{card_path}: no pin table')
    pad_nets, _ = pcb_pads(Path(project).with_suffix('.kicad_pcb').read_text())
    out = modules_check(pad_nets, ref, pins)
    out['card'] = str(card_path)
    return out


def print_modules(out):
    for r in out['rows']:
        print(f"  {'ok ' if r['ok'] else 'BAD'} {out['ref']}.{r['pin']} card says {r['expected']:5s} board net {r['net']}")
    if out['mismatches']:
        print(f"  {len(out['mismatches'])} pin(s) do not match the card: a straight cable will not work — reorder the header or document the crossed cable")


# ---------------------------------------------------------------- thermal (pcbnew)

def thermal(project, ref, pad):
    pcb = Path(project).with_suffix('.kicad_pcb')
    return toolchain.worker('copper_area', pcb, ref=ref, pad=pad)


def print_thermal(out):
    for ln, info in out['layers'].items():
        print(f"  {out['ref']}.{out['pad']} ({out['net']}) on {ln}: {info['areaMm2']:.2f} mm2 of filled copper under the pad, {info['viasOnIsland']} via(s) of the net on that island")
    if all(info['areaMm2'] == 0 for info in out['layers'].values()):
        print('  0 mm2 everywhere: no pour touches this pad — a thermal claim about it is not true of this board')


# ---------------------------------------------------------------- stale (sources vs packet)

SOURCE_GLOBS = ('design/**/*', 'parts.json', 'product.json', 'manufacturing.json', 'engineering/*.md')


def stale(workspace='.'):
    """Files edited after the newest sidecar: the packet no longer describes the sources."""
    workspace = Path(workspace)
    sidecars = sorted(workspace.glob('boards/*.board.json'), key=lambda f: f.stat().st_mtime)
    if not sidecars:
        return {'sidecar': None, 'stale': [], 'note': 'no packet published yet'}
    sidecar = sidecars[-1]
    since = sidecar.stat().st_mtime
    newer = []
    for pattern in SOURCE_GLOBS:
        for f in workspace.glob(pattern):
            if f.is_file() and not f.name.startswith('~') and f.stat().st_mtime > since + 1:
                newer.append(str(f.relative_to(workspace)))
    return {'sidecar': str(sidecar.relative_to(workspace)), 'sidecarMtime': since, 'stale': sorted(newer)}


def print_stale(out):
    if out['sidecar'] is None:
        print('  ' + out['note'])
    elif out['stale']:
        print(f"  {len(out['stale'])} source file(s) newer than {out['sidecar']} — publish again before you report a number:")
        for f in out['stale'][:40]:
            print('    ' + f)
    else:
        print(f"  {out['sidecar']} is newer than every source: the packet describes what is on disk")


# ---------------------------------------------------------------- cli

def main(argv=None):
    parser = argparse.ArgumentParser(prog='kicadpy.verify', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('gate'); s.add_argument('workspace', nargs='?', default='.')
    s = sub.add_parser('netlist'); s.add_argument('project')
    s = sub.add_parser('islands'); s.add_argument('project'); s.add_argument('--net', default='GND')
    s.add_argument('--near', nargs=3, type=float, metavar=('X', 'Y', 'R'))
    s = sub.add_parser('easyeda'); s.add_argument('out'); s.add_argument('codes', nargs='+')
    s = sub.add_parser('rotation'); s.add_argument('project'); s.add_argument('parts'); s.add_argument('easyeda', nargs='?')
    s = sub.add_parser('stock'); s.add_argument('parts'); s.add_argument('out'); s.add_argument('codes', nargs='*')
    s = sub.add_parser('power'); s.add_argument('project'); s.add_argument('--rail', action='append', default=[], metavar='NET=MAX_UF')
    s.add_argument('--limit-mm', type=float, default=3.0); s.add_argument('--parts', default='parts.json')
    s = sub.add_parser('enables'); s.add_argument('project'); s.add_argument('parts')
    s = sub.add_parser('modules'); s.add_argument('project'); s.add_argument('ref'); s.add_argument('card')
    s = sub.add_parser('thermal'); s.add_argument('project'); s.add_argument('ref'); s.add_argument('pad')
    s = sub.add_parser('stale'); s.add_argument('workspace', nargs='?', default='.')
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
        elif args.command == 'power':
            rails = {}
            for item in args.rail:
                name, _, limit = item.partition('=')
                rails[name] = float(limit) if limit else float('inf')
            out = power(args.project, rails, args.limit_mm, args.parts); print_power(out)
        elif args.command == 'enables':
            out = enables(args.project, args.parts); print_enables(out)
        elif args.command == 'modules':
            out = modules(args.project, args.ref, args.card); print_modules(out)
        elif args.command == 'thermal':
            out = thermal(args.project, args.ref, args.pad); print_thermal(out)
        elif args.command == 'stale':
            out = stale(args.workspace); print_stale(out)
        else:
            out = stock(args.parts, args.out, args.codes); print_stock(out)
        print(json.dumps({'ok': True, 'command': args.command, 'result': out}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'command': args.command, 'error': str(exc), 'kind': type(exc).__name__}))
        sys.exit(1)


if __name__ == '__main__':
    main()
