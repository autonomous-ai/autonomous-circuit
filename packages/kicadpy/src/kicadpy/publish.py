"""Publish checked native previews for the experimental app; never fab output."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
from . import checks, toolchain
from .project import Project, manifest, revision, write_json


def publish(path):
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
                    args = [cli, 'pcb', 'export', 'svg', '--layers', layers, '--mode-single', '--page-size-mode', '2', '-o', str(render / filename)]
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
                if revision(root) != report['revision'] or revision(project.root) != source_revision:
                    raise ValueError('source changed during publication')
                # Revision-named bundles avoid partial replacement of visible artifacts.
                bundle = boards / (stem + '_review') / report['revision']
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
                write_json(metadata, base)
                return {'metadata': str(metadata), 'checksPassed': report['passed'], 'findings': len(report['findings']), 'fabricationReady': False}
        except Exception as exc:
            base['native']['publication'] = 'failed'
            base['validation']['warnings'] = [{'kind': 'native_check_failed', 'severity': 'error', 'message': str(exc)}]
            write_json(metadata, base)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    args = parser.parse_args()
    try:
        print(json.dumps({'ok': True, 'result': publish(args.project)}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
