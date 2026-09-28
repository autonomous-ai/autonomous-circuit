"""Revision-bound prototype packet export and fail-closed manufacturing review."""
import csv
import io
import json
import math
from pathlib import Path
import re
import sys
import shutil
import tempfile
import zipfile

from . import toolchain
from .project import digest, manifest, write_json
from .sexp import parse, child, value

REVIEW_AREAS = ('power', 'protection', 'pinout', 'thermal', 'assembly', 'fabricator', 'bringup')


def fabrication_floor(root, stem, output, state):
    """Apply the fab floor AFTER project exceptions, on a separate check copy."""
    tree = parse((root/(stem+'.kicad_pcb')).read_text())
    stack = child(child(tree,'setup') or [],'stackup') or []
    copper = {}
    for layer in stack:
        if not isinstance(layer,list) or not layer or layer[0]!='layer': continue
        name=value(layer[1]);thickness=child(layer,'thickness')
        if name in state['copperLayers'] and thickness: copper[name]=float(thickness[1])*1000
    if set(copper) != set(state['copperLayers']) or any(v<=0 or v>70.001 for v in copper.values()):
        return [issue('fabricator_stackup','Explicit copper thickness on every layer is required; this profile supports up to 2 oz.')], copper
    # JLCPCB copper-weight guide, checked 2026-09-15: any 2 oz layer needs
    # 0.16 mm traces/spacing. Apply conservatively to all layers.
    minimum=.16 if max(copper.values())>35.001 else .10 if len(copper)<=2 else .09
    with tempfile.TemporaryDirectory(prefix='native-fab-check-') as temp:
        copy=Path(temp)/'source';shutil.copytree(root,copy)
        rules=copy/(stem+'.kicad_dru')
        current=rules.read_text() if rules.exists() else '(version 1)\n'
        rules.write_text(current+f'\n(rule "Verified factory floor" (constraint clearance (min {minimum}mm)) (constraint track_width (min {minimum}mm)))\n')
        pro=copy/(stem+'.kicad_pro');settings=json.loads(pro.read_text())
        severities=settings.setdefault('board',{}).setdefault('design_settings',{}).setdefault('rule_severities',{})
        for kind in ('clearance','track_width','shorting_items','invalid_outline','unconnected_items','drill_out_of_range','annular_width'):
            severities[kind]='error'
        pro.write_text(json.dumps(settings))
        report=output/'fabricator-drc.json'
        toolchain.run([toolchain.executable('cli'),'pcb','drc','--severity-all','--format','json','--units','mm','-o',str(report),str(copy/(stem+'.kicad_pcb'))])
        data=json.loads(report.read_text())
        if any(not isinstance(data.get(k),list) for k in ('violations','unconnected_items','schematic_parity','ignored_checks')):
            raise ValueError('incomplete fabrication DRC')
        findings=[issue('fabricator_'+f.get('type','drc'),f.get('description','Fabrication DRC finding')) for key in ('violations','unconnected_items','schematic_parity') for f in data[key] if f.get('severity')!='info']
        return findings,copper


def issue(kind, message, part='board'):
    return {'kind': kind, 'severity': 'error', 'message': message, 'detail': message, 'part': part}


def csv_file(path, columns, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def design_inputs(root):
    return {n: h for n, h in manifest(root).items()
            if n != 'manufacturing.json' and not n.startswith('engineering/')}


def engineering_review(root, source_inputs, ignored):
    findings = []
    path = root / 'manufacturing.json'
    if not path.exists():
        return [issue('engineering_missing', 'manufacturing.json is missing; engineering and assembly review has not been completed.')], {}
    data = json.loads(path.read_text())
    if data.get('schemaVersion') != 1 or data.get('designInputs') != source_inputs:
        findings.append(issue('engineering_stale', 'Engineering review does not match the current design inputs.'))
    if not str(data.get('reviewer', '')).strip():
        findings.append(issue('engineering_reviewer', 'Engineering review must identify its reviewer.'))
    for area in REVIEW_AREAS:
        check = data.get('checks', {}).get(area, {})
        evidence = check.get('evidence', [])
        if check.get('status') != 'pass' or not str(check.get('analysis', '')).strip() or not evidence:
            findings.append(issue('engineering_' + area, str(check.get('analysis') or f'{area} review is incomplete.')))
            continue
        for item in evidence:
            name = item.get('path', '')
            file = (root / name).resolve()
            if (not name.startswith('engineering/') or not file.is_relative_to(root.resolve()) or
                    not file.is_file() or file.is_symlink() or digest(file.read_bytes()) != item.get('sha256')):
                findings.append(issue('engineering_evidence', f'Missing or changed evidence for {area}: {name}'))
    if data.get('acceptedIgnoredChecks') != ignored:
        findings.append(issue('engineering_ignored_checks', 'Review must explicitly account for the exact ignored ERC/DRC categories.'))
    return findings, data


def assembly_rows(state, parts, review):
    findings, bom, cpl, manual = [], [], [], []
    by_ref = {}
    for part in parts.get('parts', []):
        for ref in part.get('refdes', []):
            if ref in by_ref:
                findings.append(issue('bom_duplicate', 'Reference occurs in more than one parts record.', ref))
            by_ref[ref] = part
    footprints = state['footprints']
    refs = [f['reference'] for f in footprints]
    if len(refs) != len(set(refs)):
        findings.append(issue('pcb_duplicate_reference', 'PCB references must be unique.'))
    for f in footprints:
        ref = f['reference']
        if f['dnp'] or f['excludedFromBOM']:
            continue
        part = by_ref.get(ref, {})
        identities=[str(part.get(k) or '').strip() for k in ('mpn','manufacturer')]
        if any(not v or v.lower() in ('unknown','tbd','n/a','generic','-','?') or v.lower().startswith(('see ','refer to ')) for v in identities):
            findings.append(issue('bom_identity', 'Exact manufacturer and MPN are required.', ref))
        expected = str(part.get('project_footprint') or part.get('native_footprint') or '').split(':')[-1]
        if expected != f['footprint']:
            findings.append(issue('bom_footprint', f'BOM footprint {expected!r} does not match PCB {f["footprint"]!r}.', ref))
        decision = review.get('assembly', {}).get(ref, {})
        method = decision.get('method')
        if method not in ('factory', 'manual'):
            findings.append(issue('assembly_method', 'Specify factory or manual assembly for this populated reference.', ref))
        row = {'Comment': f['value'], 'Designator': ref, 'Footprint': f['footprint'],
               'LCSC Part #': part.get('lcsc') or '', 'Manufacturer': part.get('manufacturer') or '',
               'MPN': part.get('mpn') or ''}
        if method == 'manual':
            manual.append(row)
            continue
        bom.append(row)
        if not re.fullmatch(r'C\d+', row['LCSC Part #']):
            findings.append(issue('bom_sourcing', 'Factory placement requires an exact JLCPCB/LCSC part identity.', ref))
        if f['excludedFromPosition']:
            findings.append(issue('position_excluded', 'Factory component is excluded from position files.', ref))
        rotation = decision.get('rotationOffsetDeg')
        if isinstance(rotation, bool) or not isinstance(rotation, (int, float)) or not math.isfinite(rotation):
            findings.append(issue('assembly_rotation', 'Factory orientation offset must be verified for this footprint and part.', ref))
            rotation = 0
        cpl.append({'Designator': ref, 'Mid X': f'{f["at"][0]:.6f}', 'Mid Y': f'{-f["at"][1]:.6f}',
                    'Layer': 'Top' if f['layer'] == 'F.Cu' else 'Bottom',
                    'Rotation': f'{(f["rotationDeg"] + rotation) % 360:.6f}'})
    return findings, bom, cpl, manual


def verify_positions(path, state, cpl, review):
    findings = []
    exported = list(csv.DictReader(path.open()))
    by_ref = {r['Ref']:r for r in exported}
    if len(by_ref) != len(exported):
        findings.append(issue('position_duplicate', 'Native position export contains duplicate references.'))
    for row in cpl:
        ref = row['Designator']
        native = by_ref.get(ref)
        if not native:
            findings.append(issue('position_missing', 'Factory CPL reference is missing from native position export.', ref))
            continue
        offset = review.get('assembly',{}).get(ref,{}).get('rotationOffsetDeg',0)
        if not isinstance(offset,(int,float)) or isinstance(offset,bool) or not math.isfinite(offset): offset = 0
        if (abs(float(row['Mid X'])-float(native['PosX'])) > .001 or
                abs(float(row['Mid Y'])-float(native['PosY'])) > .001 or
                abs((float(row['Rotation'])-float(native['Rot'])-offset+180)%360-180) > .001 or
                row['Layer'].lower() != native['Side'].lower()):
            findings.append(issue('position_mismatch', 'CPL position, side or corrected rotation differs from native export.',ref))
    return findings


def packet_geometry(folder, state):
    # Reuse the independent parser, never the KiCad exporter to parse itself.
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / 'packages/verify/src'
        if (candidate / 'verifylib').is_dir():
            sys.path.insert(0, str(candidate))
            break
    from verifylib.gerber import parse_gerber, parse_excellon, Packet
    from verifylib.model import Board, Rect, Component, Pad, Hole
    from verifylib.gerber_truth import check_parsed
    from .gerber_native import linearize
    packet = Packet(source=str(folder))
    findings, layers, hits = [], {}, []
    for file in folder.iterdir():
        if file.suffix == '.drl':
            parsed = parse_excellon(file.read_text(), path=file.name)
            hits.extend(parsed.hits)
            packet.drills.append(parsed)
        elif file.suffix == '.gbr':
            parsed = parse_gerber(linearize(file.read_text()), path=file.name)
            role = parsed.file_function
            if not role or role in layers:
                findings.append(issue('gerber_role', f'Missing or repeated layer function: {file.name}'))
            layers[role] = parsed
            role_map = {'Soldermask,Top': 'mask_top', 'Soldermask,Bot': 'mask_bottom',
                        'Legend,Top': 'silk_top', 'Legend,Bot': 'silk_bottom',
                        'Paste,Top': 'paste_top', 'Paste,Bot': 'paste_bottom'}
            canonical = role_map.get(role)
            if role and role.startswith('Profile,'): canonical = 'outline'
            if role and role.startswith('Copper,'):
                canonical = 'copper_top' if ',Top' in role else 'copper_bottom' if ',Bot' in role else role
            if canonical: packet.layers[canonical] = parsed
        else:
            continue
        if parsed.unsupported:
            findings.append(issue('packet_unsupported', f'{file.name}: {", ".join(sorted(set(parsed.unsupported)))}'))
    copper = [v for k, v in layers.items() if k and k.startswith('Copper,')]
    if len(copper) != len(state['copperLayers']):
        findings.append(issue('packet_layers', 'Exported copper layer count differs from the native board.'))
    for role in ('Soldermask,Top', 'Soldermask,Bot'):
        if role not in layers:
            findings.append(issue('packet_layer_missing', 'Required layer missing: ' + role))
    outline = next((v for k, v in layers.items() if k and k.startswith('Profile,')), None)
    bounds = outline.centreline_bounds if outline else None
    x0, y0, x1, y1 = state['boardBoundsMm']
    if not bounds or any(abs(a-b) > .11 for a,b in zip(
            [bounds.x0, bounds.y0, bounds.x1, bounds.y1] if bounds else [], [x0, -y1, x1, -y0])):
        findings.append(issue('packet_outline', 'Gerber outline does not match native board extents.'))
    expected = []
    for f in state['footprints']:
        for p in f['pads']:
            dx, dy = p['drillMm']
            if dx > 0:
                angle = math.radians(p['rotationDeg'])
                diameter=min(dx,dy)
                w = diameter+abs((dx-diameter)*math.cos(angle))+abs((dy-diameter)*math.sin(angle))
                h = diameter+abs((dx-diameter)*math.sin(angle))+abs((dy-diameter)*math.cos(angle))
                expected.append((p['at'][0], -p['at'][1], min(dx,dy), w, h, p['plated']))
    for via in state['copper']:
        if via['kind'] == 'via':
            expected.append((via['at'][0], -via['at'][1], via['drillMm'], via['drillMm'], via['drillMm'], True))
    remaining = list(hits)
    for x,y,diameter,w,height,plated in expected:
        found = next((h for h in remaining if math.hypot(h.center[0]-x,h.center[1]-y) < .01 and
                      abs(h.tool.diameter_mm-diameter) < .01 and abs(h.size[0]-w) < .01 and abs(h.size[1]-height) < .01), None)
        if found is None:
            findings.append(issue('packet_drill_missing', f'No matching drill at {x:.3f},{y:.3f}, diameter {diameter:.3f} mm.'))
        else:
            remaining.remove(found)
    if remaining:
        findings.append(issue('packet_drill_extra', f'{len(remaining)} drill hits could not be reconciled with native round holes.'))
    board = Board([])
    board.outline = Rect(x0, -y1, x1, -y0)
    board.layers = len(state['copperLayers'])
    board.holes = [Hole(x,y,d,plated,width=w,height=h) for x,y,d,w,h,plated in expected]
    for f in state['footprints']:
        c = Component(f['uuid'], f['uuid'], f['reference'], None,
                      'top' if f['layer'] == 'F.Cu' else 'bottom', (f['at'][0], -f['at'][1]), 0, 0)
        for p in f['pads']:
            if not p['plated']: continue
            for layer,side in [('F.Cu','top'),('B.Cu','bottom')]:
                if layer not in p['layers']: continue
                prefix = layer[0]
                c.pads.append(Pad(p['uuid'],f['uuid'],None,side,p['at'][0],-p['at'][1],*p['sizeMm'],
                                  plated_hole=p['drillMm'][0] > 0,
                                  mask_required=prefix+'.Mask' in p['layers'],
                                  paste_required=prefix+'.Paste' in p['layers']))
        board.components.append(c)
    result = check_parsed(board,packet,assembly=True)
    findings.extend(dict(issue(f['kind'],f['detail'],f['part']), severity='info' if f['severity'] == 'info' else 'error') for f in result.findings)
    return findings


def export(root, stem, output, native_report, source_inputs):
    output.mkdir()
    write_json(output / 'native-check.json', native_report)
    cli = toolchain.executable('cli')
    pcb = root / (stem + '.kicad_pcb')
    state = toolchain.worker('inspect', pcb)
    findings, review = engineering_review(root, source_inputs, native_report['ignoredChecks'])
    fab_findings,copper = fabrication_floor(root,stem,output,state)
    findings += fab_findings
    parts_path = root / 'parts.json'
    parts = json.loads(parts_path.read_text()) if parts_path.exists() else {}
    assembly_findings, bom, cpl, manual = assembly_rows(state, parts, review)
    findings += assembly_findings
    gerbers = output / 'gerbers'
    gerbers.mkdir()
    layers = state['copperLayers'] + ['F.Mask', 'B.Mask', 'F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS', 'Edge.Cuts']
    toolchain.run([cli, 'pcb', 'export', 'gerbers', '--no-protel-ext', '--subtract-soldermask',
                   '--layers', ','.join(layers), '-o', str(gerbers), str(pcb)])
    toolchain.run([cli, 'pcb', 'export', 'drill', '--format', 'excellon', '--excellon-units', 'mm',
                   '-o', str(gerbers), str(pcb)])
    toolchain.run([cli, 'pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'both',
                   '-o', str(output / 'native-positions.csv'), str(pcb)])
    findings += verify_positions(output / 'native-positions.csv', state, cpl, review)
    try:
        findings += packet_geometry(gerbers, state)
    except Exception as exc:
        findings.append(issue('packet_parser_failed', str(exc)))
    columns = ['Comment', 'Designator', 'Footprint', 'LCSC Part #', 'Manufacturer', 'MPN']
    csv_file(output / 'bom.csv', columns, bom)
    csv_file(output / 'manual-assembly.csv', columns, manual)
    csv_file(output / 'cpl.csv', ['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'], cpl)
    with zipfile.ZipFile(output / 'gerbers.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(gerbers.iterdir()):
            if file.suffix in ('.gbr', '.drl', '.gbrjob'):
                archive.write(file, file.name)
    with zipfile.ZipFile(output / 'kicad-project.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in manifest(root):
            archive.write(root / name, name)
    if not native_report['passed']:
        findings.append(issue('native_checks', 'Native ERC/DRC/parity has outstanding findings.'))
    result = {'schemaVersion': 1, 'checkedRevision': native_report['revision'], 'prototypeReady': not any(f['severity'] == 'error' for f in findings),
              'hardwareTested': False, 'findings': findings, 'factoryComponents': len(bom),
              'manualComponents': len(manual), 'copperLayers': state['copperLayers'], 'thicknessMm': state['thicknessMm']}
    result['copperUm'] = copper
    write_json(output / 'manufacturing-report.json', result)
    (output / 'ORDER.md').write_text('# Prototype manufacturing review\n\n' +
        ('Ready for prototype quotation.\n' if result['prototypeReady'] else '**BLOCKED — review packet only; do not submit for manufacture.**\n') +
        f'\nChecked revision: `{native_report["revision"]}`\n\n' +
        '\n'.join('- ' + f['message'] + (' [' + f['part'] + ']' if f['part'] != 'board' else '') for f in findings) +
        f'\n\n## Manufacturing settings\n\nLayers: {len(state["copperLayers"])}; thickness: {state["thicknessMm"]} mm; copper micrometres: {json.dumps(copper)}.\n' +
        f'\nFactory population: {len(bom)} components. Manual population: {len(manual)} components; see manual-assembly.csv.\n' +
        '\n## Prototype quotation, only after all blockers are resolved\n\n1. Upload gerbers.zip at https://jlcpcb.com/ and match the reviewed stackup and special processes.\n2. Upload bom.csv and cpl.csv for factory assembly. Procure manual-assembly.csv separately.\n3. Confirm every part identity, availability, side, location and pin-1/polarity in the factory placement preview.\n4. Review the factory DFM response, final price, quantity and shipping before payment.\n' +
        '\n\nHardware validation is separate from prototype manufacturing readiness. Follow the reviewed bring-up plan on current-limited power before connecting motors or the host computer.\n')
    result['files'] = {f.relative_to(output).as_posix(): digest(f.read_bytes()) for f in output.rglob('*') if f.is_file()}
    return result
