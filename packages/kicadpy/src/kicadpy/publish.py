"""Publish checked native previews and the revision-bound prototype packet.

This tool reports; it never promotes. `fab.ready` is written false here and
only the app's native review loop (nativeEngine.mjs) sets it true, after an
independent attestation for this exact source. `native.manufacturing.prototypeReady`
carries the packet verdict on its own.
"""
import argparse
import json
from pathlib import Path
import shutil
import struct
import tempfile
from . import checks, toolchain, manufacture
from .project import Project, manifest, revision, write_json, digest


def publish(path, manufacturing=False):
    project = Project(path)
    workspace = project.file.parent.parent if project.file.parent.name == 'design' else project.root
    boards = workspace / 'boards'
    boards.mkdir(exist_ok=True)
    stem = project.file.stem
    metadata = boards / (stem + '.board.json')
    with project.lock():
        source_revision, snapshot = project.snapshot()
        inputs = {str((project.root / name).relative_to(workspace)): sha for name, sha in manifest(project.root).items()}
        base = {'schemaVersion': 1, 'source': {'engine': 'kicad-native', 'file': str(project.pcb.relative_to(workspace)), 'fingerprint': source_revision},
                'fab': {'ready': False}, 'native': {'inputRoot': str(project.root.relative_to(workspace)), 'inputs': inputs, 'publication': 'running', 'checksPassed': False,
                         'assemblyReady': False, 'hardwareTested': False},
                'validation': {'warnings': [{'kind': 'native_pending', 'severity': 'warning', 'message': 'Native CAD checks are running.'}]}}
        write_json(metadata, base)
        try:
            with tempfile.TemporaryDirectory(prefix='publish-', dir=project.store) as temp:
                temp = Path(temp)
                root = temp / 'design'
                shutil.copytree(snapshot, root)
                report = checks.check(root, project.stem, temp / 'reports')
                render = temp / 'render'
                render.mkdir()
                pcb = root / project.pcb_name
                sch = root / (project.stem + '.kicad_sch')
                cli = toolchain.executable('cli')
                for filename, layers, mirror in [('_pcb.svg', 'F.Cu,F.SilkS,Edge.Cuts', False), ('_pcb_bottom.svg', 'B.Cu,B.SilkS,Edge.Cuts', True)]:
                    args = [cli, 'pcb', 'export', 'svg', '--layers', layers, '--mode-single', '--page-size-mode', '2', '--exclude-drawing-sheet', '-o', str(render / filename)]
                    if mirror: args.append('--mirror')
                    toolchain.run(args + [str(pcb)])
                sheets = render / 'sheets'
                toolchain.run([cli, 'sch', 'export', 'svg', '--exclude-drawing-sheet', '-o', str(sheets), str(sch)])
                primary = sheets / (stem + '.svg')
                if not primary.exists():
                    raise RuntimeError('schematic exporter did not produce the root sheet')
                shutil.copyfile(primary, render / '_schematic.svg')
                for filename in ('_pcb.svg', '_pcb_bottom.svg', '_schematic.svg'):
                    if '<svg' not in (render / filename).read_text():
                        raise RuntimeError('invalid SVG preview')
                model_warning = None
                model = render / 'board.glb'
                try:
                    toolchain.run([cli, 'pcb', 'export', 'glb', '--force', '--subst-models', '--include-pads',
                                   '--include-silkscreen', '--include-soldermask', '-o', str(model), str(pcb)])
                    data = model.read_bytes()
                    if len(data) < 20 or struct.unpack('<4sII', data[:12]) != (b'glTF', 2, len(data)):
                        raise ValueError('invalid GLB preview')
                    chunk_size, chunk_type = struct.unpack('<II', data[12:20])
                    scene = json.loads(data[20:20 + chunk_size]) if chunk_type == 0x4E4F534A else {}
                    if not scene.get('meshes'):
                        raise ValueError('GLB preview contains no meshes')
                except Exception as exc:
                    model.unlink(missing_ok=True)
                    model_warning = str(exc)
                packet = None
                if manufacturing:
                    packet = manufacture.export(root, project.stem, render / 'manufacturing', report,
                                                manufacture.design_inputs(snapshot))
                if revision(root) != report['revision'] or revision(project.root) != source_revision:
                    raise ValueError('source changed during publication')
                # Revision-named bundles avoid partial replacement of visible artifacts.
                render_hash = digest(json.dumps({f.relative_to(render).as_posix(): digest(f.read_bytes()) for f in render.rglob('*') if f.is_file()}, sort_keys=True).encode())
                bundle = boards / (stem + '_review') / (report['revision'] + '-' + render_hash[:16])
                bundle.parent.mkdir(exist_ok=True)
                if not bundle.exists():
                    shutil.copytree(render, bundle)
                    shutil.copytree(temp / 'reports', bundle / 'reports')
                base['native'].update(publication='complete', checksPassed=report['passed'], checkedRevision=report['revision'],
                                      previewDir=str(bundle.relative_to(workspace)), ignoredChecks=report['ignoredChecks'])
                base['validation']['warnings'] = [
                    {'kind': f.get('type', 'native_finding'), 'severity': 'error' if f['severity'] == 'error' else 'warning', 'message': f.get('description', ''), 'native': f}
                    for f in report['findings']]
                base['validation']['warnings'].append({'kind': 'native_coverage', 'severity': 'warning',
                    'message': 'Experimental v2: native CAD checks only; engineering checks, PCBA data and hardware validation are incomplete.'})
                if model_warning:
                    base['validation']['warnings'].append({'kind': 'native_3d_failed', 'severity': 'warning', 'message': model_warning})
                if packet is not None:
                    base['native']['manufacturing'] = packet
                    base['validation']['warnings'] = [w for w in base['validation']['warnings'] if w['kind'] != 'native_coverage'] + packet['findings']
                    # Not `fab.ready`: the contract's order gate is the review loop's to set.
                for warning in base['validation']['warnings']:
                    warning.setdefault('detail', warning.get('message', ''))
                write_json(metadata, base)
                return {'metadata': str(metadata), 'checksPassed': report['passed'], 'findings': len(report['findings']),
                        'manufacturingFindings': len(packet['findings']) if packet else None,
                        'prototypeReady': packet['prototypeReady'] if packet else None, 'fabricationReady': base['fab']['ready']}
        except Exception as exc:
            base['native']['publication'] = 'failed'
            base['validation']['warnings'] = [{'kind': 'native_check_failed', 'severity': 'error', 'message': str(exc), 'detail': str(exc)}]
            write_json(metadata, base)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--manufacturing', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps({'ok': True, 'result': publish(args.project, manufacturing=args.manufacturing)}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
