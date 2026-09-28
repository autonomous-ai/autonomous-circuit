"""Optional real-project acceptance run; copies the supplied release first."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
from kicadpy import checks, toolchain
from kicadpy.engine import Engine
from kicadpy.project import manifest, revision

parser = argparse.ArgumentParser()
parser.add_argument('project', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = Path(tempfile.mkdtemp(prefix='kicadpy-desk-cube-')) / 'design'
shutil.copytree(args.project.parent, root)
project = root / args.project.name
engine = Engine(project)
state = engine.inspect()
track = next(t for t in state['copper'] if t['kind'] == 'track' and not t['locked'] and t['widthMm'] < .3)
region = [-1000, -1000, 1000, 1000]
scope = {'uuids': [track['uuid']], 'nets': [track['net']], 'regionMm': region,
         'refillZones': [z['uuid'] for z in state['zones']]}
# Create the faulty fixture only in this temporary copy. Width 3 mm creates
# a local clearance/short problem on a fine-pitch routing segment.
toolchain.worker('apply', root / engine.project.pcb.name, scope=scope,
                 edits=[{'op': 'set_width', 'uuid': track['uuid'], 'widthMm': 3}])
bad_revision = revision(root)
bad_manifest = manifest(root)
# Check another copy because refill must not alter the fixture baseline.
bad_copy = root.parent / 'baseline-check'
shutil.copytree(root, bad_copy)
bad_report = checks.check(bad_copy, project.stem, root.parent / 'bad-reports')
assert not bad_report['passed'], 'injected defect was not detected'
candidate = engine.apply(bad_revision, scope, [{'op': 'set_width', 'uuid': track['uuid'], 'widthMm': track['widthMm']}])
report = engine.check(candidate['candidate'])
assert report['passed'], report['findings']
assert not report['diff']['dependenciesChanged'] and not report['diff']['settingsChanged']
assert len(report['diff']['objects']) == 1
committed = engine.commit(candidate['candidate'], bad_revision)
engine.undo(bad_revision, committed['revision'])
assert manifest(root) == bad_manifest
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps({'source': str(args.project), 'workspace': str(root),
    'sourceRevision': state['revision'], 'footprints': len(state['footprints']), 'copperItems': len(state['copper']),
    'injectedTrack': track, 'beforeFindings': len(bad_report['findings']), 'afterFindings': len(report['findings']),
    'diff': report['diff'], 'undoByteExact': True, 'tools': report['tools'],
    'ignoredChecks': report['ignoredChecks'], 'fabricationReady': False, 'hardwareTested': False}, indent=2)+'\n')
print(args.output)
