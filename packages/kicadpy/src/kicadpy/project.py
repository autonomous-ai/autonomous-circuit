"""Project-local input closure, immutable snapshots and process locks."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import tempfile

SUFFIXES = {'.kicad_pro', '.kicad_pcb', '.kicad_sch', '.kicad_dru', '.kicad_sym', '.kicad_mod', '.step', '.stp', '.wrl'}
NAMES = {'fp-lib-table', 'sym-lib-table', 'product.json', 'parts.json', 'manufacturing.json'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifest(root):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        if '.kicadpy' in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise ValueError(f'symlink dependency unsupported: {path}')
        if path.is_file() and (path.suffix in SUFFIXES or path.name in NAMES or path.relative_to(root).parts[0] == 'engineering'):
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def revision(root):
    return digest(json.dumps(manifest(root), sort_keys=True).encode())


def atomic(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.kicadpy-')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic(path, (json.dumps(data, indent=2, sort_keys=True) + '\n').encode())


class Project:
    def __init__(self, path):
        self.file = Path(path).absolute()
        if self.file.suffix != '.kicad_pro' or not self.file.is_file():
            raise ValueError('expected an existing .kicad_pro')
        self.root = self.file.parent
        if self.root.name == 'design' and any((self.root.parent / n).exists() for n in ('product.json', 'parts.json', 'manufacturing.json')):
            self.root = self.root.parent
        self.pcb = self.file.with_suffix('.kicad_pcb')
        self.sch = self.file.with_suffix('.kicad_sch')
        if not self.pcb.is_file() or not self.sch.is_file():
            raise ValueError('project requires matching PCB and schematic')
        self.pcb_name = self.pcb.relative_to(self.root).as_posix()
        self.stem = self.file.relative_to(self.root).with_suffix('').as_posix()
        self.store = self.root.parent / '.kicadpy' / (self.file.stem + '-' + digest(str(self.root).encode())[:12])
        self.store.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self):
        with (self.store / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def expect(self, expected):
        actual = revision(self.root)
        if actual != expected:
            raise ValueError(f'stale revision: expected {expected}, current {actual}')
        return actual

    def validate_dependencies(self):
        # Keep the spike's dependency closure local and reproducible. Embedded
        # schematic symbols are included; 3D rendering is not a checked capability.
        for name in manifest(self.root):
            path = self.root / name
            if path.name in ('fp-lib-table', 'sym-lib-table'):
                refs = re.findall(r'\(uri\s+"([^"\n]+)"', path.read_text())
            elif path.suffix == '.kicad_sch':
                refs = re.findall(r'\(property\s+"Sheetfile"\s+"([^"\n]+)"', path.read_text(), re.I)
            else:
                continue
            for ref in refs:
                ref = ref.replace('${KIPRJMOD}', str(self.file.parent))
                target = (path.parent / ref).resolve()
                if '$' in ref or not target.is_relative_to(self.root.resolve()) or not target.exists():
                    raise ValueError('missing or external project dependency: ' + ref)

    def snapshot(self):
        self.validate_dependencies()
        before = manifest(self.root)
        rev = revision(self.root)
        dest = self.store / 'snapshots' / rev
        if not dest.exists():
            dest.parent.mkdir(exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=dest.parent))
            try:
                for name in before:
                    target = staging / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(self.root / name, target)
                if manifest(staging) != before or manifest(self.root) != before:
                    raise ValueError('project changed while snapshotting')
                os.rename(staging, dest)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        if revision(dest) != rev:
            raise ValueError('snapshot integrity failure')
        return rev, dest
