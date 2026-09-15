"""A candidate is never the live board until a checked single-file commit."""
import json
from pathlib import Path
import re
import shutil
import uuid
from . import checks, sexp, toolchain
from .project import Project, atomic, digest, manifest, revision, write_json


class Engine:
    def __init__(self, project):
        self.project = Project(project)

    def inspect(self):
        with self.project.lock():
            rev, root = self.project.snapshot()
            return dict(toolchain.worker('inspect', root / self.project.pcb_name), revision=rev)

    def view(self):
        from .viewer_adapter import board_view
        return board_view(self.inspect())

    def snapshot(self):
        with self.project.lock():
            rev, _ = self.project.snapshot()
            return {'revision': rev}

    def _candidate(self, identifier):
        if not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('invalid candidate ID')
        path = self.project.store / 'candidates' / identifier
        return path, json.loads((path / 'metadata.json').read_text())

    def apply(self, expected, scope, edits):
        if not edits or len(edits) > 32:
            raise ValueError('expected 1..32 edits')
        with self.project.lock():
            self.project.expect(expected)
            base, snapshot = self.project.snapshot()
            identifier = uuid.uuid4().hex
            path = self.project.store / 'candidates' / identifier
            root = path / 'design'
            shutil.copytree(snapshot, root)
            # Failure leaves evidence in an uncommittable candidate directory.
            changed = toolchain.worker('apply', root / self.project.pcb_name, scope=scope, edits=edits)
            metadata = {'base': base, 'revision': revision(root), 'scope': scope,
                        'allowed': changed['touched'] + changed['created']}
            write_json(path / 'metadata.json', metadata)
            delta = self._diff(path, metadata)
            self._guard(delta, metadata)
            return {'candidate': identifier, 'revision': metadata['revision'], 'diff': delta}

    def route(self, expected, scope):
        # Reuse v1's pinned launcher, but keep native DSN/SES conversion here.
        from circuitpy.toolchain import run_freerouting
        with self.project.lock():
            self.project.expect(expected)
            base, snapshot = self.project.snapshot()
            identifier = uuid.uuid4().hex
            path = self.project.store / 'candidates' / identifier
            root = path / 'design'
            shutil.copytree(snapshot, root)
            dsn, ses = path / 'route.dsn', path / 'route.ses'
            toolchain.worker('route_export', root / self.project.pcb_name, scope=scope, output=str(dsn))
            run = run_freerouting(dsn, ses, passes=5, threads=1, timeout=120)
            (path / 'router.log').write_text(run.output)
            if not ses.is_file():
                raise ValueError('router produced no SES')
            changed = toolchain.worker('route_import', root / self.project.pcb_name, scope=scope, input=str(ses))
            metadata = {'base': base, 'revision': revision(root), 'scope': scope,
                        'allowed': changed['touched'] + changed['created']}
            write_json(path / 'metadata.json', metadata)
            delta = self._diff(path, metadata)
            self._guard(delta, metadata)
            return {'candidate': identifier, 'revision': metadata['revision'], 'diff': delta}

    def _diff(self, path, metadata):
        base = self.project.store / 'snapshots' / metadata['base']
        root = path / 'design'
        if revision(base) != metadata['base']:
            raise ValueError('snapshot integrity failure')
        a, b = manifest(base), manifest(root)
        dependencies = sorted(k for k in set(a) | set(b) if k != self.project.pcb_name and a.get(k) != b.get(k))
        delta = sexp.diff((base / self.project.pcb_name).read_text(), (root / self.project.pcb_name).read_text())
        delta['dependenciesChanged'] = dependencies
        return delta

    @staticmethod
    def _guard(delta, metadata):
        if delta['settingsChanged'] or delta['dependenciesChanged']:
            raise ValueError('project settings or dependencies changed')
        for change in delta['objects']:
            if change['uuid'] not in metadata['allowed'] or change['kind'] not in ('segment', 'arc', 'via'):
                raise ValueError('out-of-scope object changed: ' + change['uuid'])
        if set(delta['zoneFillsChanged']) - set(metadata['scope'].get('refillZones', [])):
            raise ValueError('zone fill changed without explicit zone scope')

    def diff(self, identifier):
        with self.project.lock():
            path, metadata = self._candidate(identifier)
            return self._diff(path, metadata)

    def check(self, identifier):
        with self.project.lock():
            path, metadata = self._candidate(identifier)
            if revision(path / 'design') != metadata['revision']:
                raise ValueError('candidate integrity failure')
            report = path / 'reports'
            # Never reuse an older successful verdict if this check fails.
            if report.exists():
                shutil.rmtree(report)
            result = checks.check(path / 'design', self.project.stem, report)
            delta = self._diff(path, metadata)
            self._guard(delta, metadata)
            metadata['revision'] = result['revision']
            metadata['checkHash'] = digest((report / 'check.json').read_bytes())
            write_json(path / 'metadata.json', metadata)
            return dict(result, diff=delta, candidate=identifier)

    def commit(self, identifier, expected):
        with self.project.lock():
            self.project.expect(expected)
            path, metadata = self._candidate(identifier)
            if metadata['base'] != expected:
                raise ValueError('candidate was prepared against a different revision')
            raw = (path / 'reports/check.json').read_bytes()
            report = json.loads(raw)
            if digest(raw) != metadata.get('checkHash') or not report['passed']:
                raise ValueError('candidate lacks a passing check')
            if report['tools'] != toolchain.versions() or revision(path / 'design') != report['revision']:
                raise ValueError('stale candidate or toolchain check')
            self._guard(self._diff(path, metadata), metadata)
            # Only PCB is mutable in this spike. No multi-file partial commit.
            self.project.expect(expected)
            atomic(self.project.pcb, (path / 'design' / self.project.pcb_name).read_bytes())
            actual = revision(self.project.root)
            if actual != report['revision']:
                raise ValueError('external dependency edit during commit; re-inspect project')
            return {'revision': actual, 'undoRevision': expected, 'fabricationReady': False}

    def undo(self, target, expected):
        if not re.fullmatch('[0-9a-f]{64}', target):
            raise ValueError('invalid snapshot revision')
        with self.project.lock():
            self.project.expect(expected)
            root = self.project.store / 'snapshots' / target
            if not root.is_dir() or revision(root) != target:
                raise ValueError('snapshot integrity failure')
            current, old = manifest(self.project.root), manifest(root)
            if {k: v for k, v in current.items() if k != self.project.pcb_name} != {k: v for k, v in old.items() if k != self.project.pcb_name}:
                raise ValueError('undo would overwrite changed dependencies')
            self.project.snapshot()
            atomic(self.project.pcb, (root / self.project.pcb_name).read_bytes())
            return {'revision': revision(self.project.root), 'checkStatus': 'not_rechecked', 'fabricationReady': False}
