"""Per-part cached case builds: only new or changed parts are rebuilt.

A project's `case/build.py` registers each printed part as a function that returns its solid
(a CadQuery Workplane or Shape, assembled in the contract frame) and calls `main()`:

    from kicadpy.case_build import CaseBuild

    build = CaseBuild(__file__, stl_tolerance=0.04, manifest={'stage': 'simple'})

    @build.part('frame', role='shell', note='prints floor-down, no supports')
    def frame():
        ...

    build.add('lid', make_lid, role='lid')          # or build.add_parts(PARTS) for a dict
    if __name__ == '__main__':
        build.main()

    "$KICAD_CASE_PYTHON" case/build.py               # rebuild only what changed (the default)
    "$KICAD_CASE_PYTHON" case/build.py --only frame  # rebuild just these parts (comma separated)
    "$KICAD_CASE_PYTHON" case/build.py --plan        # say what would be rebuilt, and why
    "$KICAD_CASE_PYTHON" case/build.py --force       # rebuild everything: only when asked

Each part has a cache key: the code of its function and of every function, constant, class and
project module it reaches (followed through calls, lambdas, defaults and closures), the
declared input files (the contract and case/interfaces.json by default, plus `inputs=`), the
export settings and the Python / CadQuery versions. A part whose key is unchanged and whose
case/out/<part>.step and .stl are the files it wrote last time is skipped. When the walk meets
something it cannot read (a module-level solid, an object from a library), the whole source
file that refers to it joins the key: when unsure, rebuild.

A part the team waterfall froze (case/stages.json: valid, done or note) is never rebuilt, even
when a shared helper changed; only `--only <part>` rebuilds it. case/out/parts.json is rewritten
on every run from the declarations, keeping the keys other members own (printProject,
overview). A part that fails to build loses its old files and its manifest entry, and the run
exits non-zero. A file this module cannot read is never assumed unchanged.
"""
from __future__ import annotations

import argparse
import functools
import glob
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import time
import traceback
import types

CACHE_VERSION = 1
CACHE_FILE = '.build-cache.json'
MANIFEST = 'parts.json'
STATUS_FILE = 'case/stages.json'
FROZEN_STATUSES = ('valid', 'done', 'note')
DEFAULT_INPUTS = ('PILOT_CONTRACT.json', 'boards/*.enclosure.json', 'case/interfaces.json')
OWNED_ENTRY_KEYS = ('name', 'stl', 'step', 'bboxMm')
_SIMPLE = (int, float, complex, str, bytes, bool, type(None))
_MAX_DEPTH = 40


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha_file(path):
    try:
        return _sha_bytes(Path(path).read_bytes())
    except OSError:
        return None


def _code_token(code):
    """A code object without its line numbers or file name: editing a comment or moving a
    function leaves it alone, changing what it does does not."""
    consts = tuple(_code_token(c) if isinstance(c, types.CodeType) else repr(c) for c in code.co_consts)
    return repr((code.co_code, consts, code.co_names, code.co_varnames, code.co_freevars,
                 code.co_cellvars, code.co_argcount, code.co_kwonlyargcount, code.co_flags))


def _code_names(code):
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


class _Fingerprint:
    """Walks what a part function reaches and hashes it."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.seen = {}
        self.coarse_files = set()
        self.unread = []

    def _local_file(self, filename):
        if not filename:
            return False
        try:
            path = Path(filename).resolve()
        except (OSError, ValueError):
            return False
        return path == self.root or self.root in path.parents

    def _name(self, path):
        """A file's name in the key: relative to the project, so a copied worktree keeps its cache."""
        path = Path(path)
        return path.relative_to(self.root).as_posix() if self.root in path.parents else path.name

    def _is_local_module(self, name):
        module = sys.modules.get(name or '')
        filename = getattr(module, '__file__', None)
        return (name or '').split('.')[0] == 'kicadpy' or self._local_file(filename)

    def _module_file(self, name):
        return getattr(sys.modules.get(name or ''), '__file__', None)

    def _coarse(self, filename, why):
        """Fallback: the whole file that refers to something unreadable joins the key."""
        if filename and _sha_file(filename):
            self.coarse_files.add(str(Path(filename).resolve()))
        else:
            self.unread.append(why)

    def token(self, obj, context=None, depth=0):
        if isinstance(obj, _SIMPLE):
            return repr(obj)
        if depth > _MAX_DEPTH:
            self._coarse(context, 'too deep')
            return '<deep>'
        if isinstance(obj, (list, tuple)):
            return type(obj).__name__ + '[' + ','.join(self.token(i, context, depth + 1) for i in obj) + ']'
        if isinstance(obj, (set, frozenset)):
            return 'set[' + ','.join(sorted(self.token(i, context, depth + 1) for i in obj)) + ']'
        if isinstance(obj, dict):
            items = sorted((self.token(k, context, depth + 1), self.token(v, context, depth + 1)) for k, v in obj.items())
            return 'dict{' + ','.join(f'{k}:{v}' for k, v in items) + '}'
        if isinstance(obj, functools.partial):
            return ('partial(' + self.token(obj.func, context, depth + 1) + ',' +
                    self.token(obj.args, context, depth + 1) + ',' + self.token(obj.keywords, context, depth + 1) + ')')
        if inspect.ismodule(obj):
            filename = getattr(obj, '__file__', None)
            if self._is_local_module(obj.__name__) and filename:
                return f'module:{obj.__name__}:{_sha_file(filename)}'
            return f"module:{obj.__name__}:{getattr(obj, '__version__', '')}"
        if inspect.ismethod(obj):
            return 'method(' + self.token(obj.__func__, context, depth + 1) + ',' + self.token(obj.__self__, context, depth + 1) + ')'
        if inspect.isfunction(obj):
            return self.function(obj, depth + 1)
        if isinstance(obj, type):
            if self._is_local_module(obj.__module__):
                try:
                    return f'class:{obj.__qualname__}:' + _sha_bytes(inspect.getsource(obj).encode())
                except (OSError, TypeError):
                    self._coarse(self._module_file(obj.__module__) or context, f'class {obj.__qualname__}')
                    return f'class:{obj.__qualname__}'
            return f'ext:{obj.__module__}.{obj.__qualname__}'
        if inspect.isbuiltin(obj):
            return f"builtin:{getattr(obj, '__module__', '')}.{getattr(obj, '__qualname__', obj.__name__)}"
        # Anything else (a module-level solid, a library object): the file that made it decides.
        self._coarse(context, f'object {type(obj).__name__}')
        return f'obj:{type(obj).__module__}.{type(obj).__qualname__}'

    def function(self, fn, depth=0, top=False):
        fn = inspect.unwrap(fn)
        ident = id(fn)
        name = f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', '')}"
        code = getattr(fn, '__code__', None)
        if code is None or not (top or self._local_file(code.co_filename) or self._is_local_module(fn.__module__)):
            return 'ext:' + name      # a library function: its version is in the key, not its code
        if ident in self.seen:        # None while in progress (recursion stops here)
            return 'fn:' + fn.__qualname__ + ':' + (self.seen[ident] or 'recursive')
        self.seen[ident] = None
        context = code.co_filename
        parts = [fn.__qualname__, _code_token(code)]   # not the module name: __main__ or not, same key
        scope = fn.__globals__
        for global_name in sorted(_code_names(code)):
            if global_name in scope:
                parts.append(global_name + '=' + self.token(scope[global_name], context, depth + 1))
        parts.append('defaults=' + self.token(fn.__defaults__, context, depth + 1))
        parts.append('kwdefaults=' + self.token(fn.__kwdefaults__, context, depth + 1))
        for var, cell in zip(code.co_freevars, fn.__closure__ or ()):
            try:
                parts.append(var + '~' + self.token(cell.cell_contents, context, depth + 1))
            except ValueError:   # empty cell
                parts.append(var + '~<empty>')
        self.seen[ident] = _sha_bytes('\0'.join(parts).encode())
        return 'fn:' + fn.__qualname__ + ':' + self.seen[ident]

    def digest(self, fn):
        self.function(fn, top=True)   # the part itself always counts, wherever it lives
        # ids differ run to run: key on the hashed content only
        material = sorted(v for v in self.seen.values() if v is not None)
        material += [f'file:{self._name(path)}:{_sha_file(path)}' for path in sorted(self.coarse_files)]
        material += [f'unread:{why}' for why in self.unread]
        return _sha_bytes('\n'.join(material).encode()), bool(self.coarse_files), list(self.unread)


def export_cadquery(solid, step_path, stl_path, tolerance=0.1, angular_tolerance=0.1):
    """Write the STEP and STL of a CadQuery solid; return its bounding box [x0,y0,z0,x1,y1,z1]."""
    import cadquery as cq
    cq.exporters.export(solid, str(step_path), exportType='STEP')
    cq.exporters.export(solid, str(stl_path), exportType='STL', tolerance=tolerance,
                        angularTolerance=angular_tolerance)
    shape = solid.val() if hasattr(solid, 'val') else solid
    if hasattr(solid, 'vals') and len(solid.vals()) > 1:
        shape = cq.Compound.makeCompound([v for v in solid.vals() if isinstance(v, cq.Shape)])
    bb = shape.BoundingBox()
    return [round(v, 2) for v in (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)]


def _cadquery_version():
    try:
        import cadquery
        return getattr(cadquery, '__version__', '?')
    except Exception:  # noqa: BLE001 - absent or broken: a token, never a crash
        return 'none'


class CaseBuild:
    """The parts of one case and their cached build into case/out/."""

    def __init__(self, script, root=None, out=None, inputs=(), contract=None, manifest=None,
                 stl_tolerance=0.1, angular_tolerance=0.1, exporter=None, default_inputs=DEFAULT_INPUTS):
        script = Path(script).resolve()
        self.script = script
        self.root = Path(root).resolve() if root else script.parent.parent
        self.out = Path(out).resolve() if out else self.root / 'case' / 'out'
        self.inputs = list(default_inputs) + list(inputs)
        self.contract = contract
        self.extra = dict(manifest or {})
        self.stl_tolerance = stl_tolerance
        self.angular_tolerance = angular_tolerance
        self.exporter = exporter
        self.parts = {}

    # -- registration
    def add(self, name, fn, inputs=(), **meta):
        if not isinstance(name, str) or not name or '/' in name or '\\' in name or name.startswith('.'):
            raise ValueError(f'bad part name {name!r}')
        if name in self.parts:
            raise ValueError(f'part {name!r} is registered twice')
        if not callable(fn):
            raise ValueError(f'part {name!r}: fn is not callable')
        self.parts[name] = {'fn': fn, 'inputs': list(inputs), 'meta': meta}
        return fn

    def part(self, name=None, **meta):
        def register(fn):
            self.add(name or fn.__name__, fn, **meta)
            return fn
        return register

    def add_parts(self, table):
        """A {name: {'fn': callable, **meta}} table, as many build scripts already keep."""
        for name, spec in table.items():
            spec = dict(spec)
            self.add(name, spec.pop('fn'), **spec)

    # -- keys
    def _files(self, patterns):
        found = []
        for pattern in patterns:
            matches = sorted(glob.glob(str(self.root / pattern)))
            found += matches
        return sorted(set(found))

    def _contract_file(self):
        if self.contract:
            path = self.root / self.contract
            return path if path.is_file() else None
        enclosures = sorted((self.root / 'boards').glob('*.enclosure.json'))
        if enclosures:
            return enclosures[0]     # what `kicadpy.case check` compares contractSha against
        pilot = self.root / 'PILOT_CONTRACT.json'
        return pilot if pilot.is_file() else None

    def key(self, name):
        spec = self.parts[name]
        digest, coarse, unread = _Fingerprint(self.root).digest(spec['fn'])
        material = [f'v{CACHE_VERSION}', f'python {sys.version_info[0]}.{sys.version_info[1]}',
                    f'part {name}', f'code {digest}',
                    f'stl {self.stl_tolerance!r} {self.angular_tolerance!r}',
                    f"exporter {getattr(self.exporter, '__qualname__', 'cadquery')}"]
        if self.exporter is None:
            material.append('cadquery ' + _cadquery_version())
        for path in self._files(self.inputs + spec['inputs']):
            material.append(f'input {os.path.relpath(path, self.root)} {_sha_file(path)}')
        for pattern in spec['inputs']:
            if not glob.glob(str(self.root / pattern)):
                material.append(f'missing {pattern}')
        return _sha_bytes('\n'.join(material).encode()), {'coarse': coarse, 'unread': unread}

    # -- state
    def _read_json(self, path):
        try:
            value = json.loads(Path(path).read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def frozen(self):
        """Parts the team waterfall closed: case/stages.json valid, done or note."""
        parts = self._read_json(self.root / STATUS_FILE).get('parts')
        if not isinstance(parts, dict):
            return set()
        return {name for name, info in parts.items()
                if isinstance(info, dict) and info.get('status') in FROZEN_STATUSES}

    def _paths(self, name):
        return self.out / f'{name}.step', self.out / f'{name}.stl'

    def _outputs_match(self, name, record):
        step, stl = self._paths(name)
        files = record.get('files') or {}
        return (step.is_file() and stl.is_file() and files.get('step') == _sha_file(step) and
                files.get('stl') == _sha_file(stl))

    def plan(self, only=(), force=False):
        """{part: (action, reason)}: action is 'build', 'cached' or 'frozen'."""
        cache = self._read_json(self.out / CACHE_FILE).get('parts') or {}
        frozen = self.frozen()
        decisions, keys = {}, {}
        for name in self.parts:
            record = cache.get(name) if isinstance(cache.get(name), dict) else {}
            step, stl = self._paths(name)
            if only:
                if name in only:
                    decisions[name] = ('build', '--only')
                elif step.is_file() and stl.is_file():
                    decisions[name] = ('cached', 'not in --only')
                else:
                    decisions[name] = ('missing', 'not in --only and never built')
                continue
            if force:
                decisions[name] = ('build', '--force')
                continue
            if name in frozen and step.is_file() and stl.is_file():
                decisions[name] = ('frozen', 'closed by the waterfall (case/stages.json)')
                continue
            keys[name], info = self.key(name)
            if info['unread']:
                decisions[name] = ('build', 'could not read all it depends on: ' + ', '.join(info['unread'][:3]))
            elif not record:
                decisions[name] = ('build', 'new part')
            elif record.get('key') != keys[name]:
                decisions[name] = ('build', 'source or inputs changed' + (' (coarse: whole file)' if info['coarse'] else ''))
            elif not self._outputs_match(name, record):
                decisions[name] = ('build', 'case/out files missing or changed')
            else:
                decisions[name] = ('cached', 'unchanged')
        return decisions, keys, cache

    # -- build
    def _export(self, solid, step, stl):
        if self.exporter is not None:
            return self.exporter(solid, step, stl)
        return export_cadquery(solid, step, stl, self.stl_tolerance, self.angular_tolerance)

    def _remove(self, name):
        """Drop a part's case/out files (only ever <name>.step and <name>.stl)."""
        for path in self._paths(name):
            if path.is_file():
                path.unlink()

    def run(self, only=(), force=False, log=print):
        only = list(only or ())
        unknown = [name for name in only if name not in self.parts]
        if unknown:
            raise SystemExit(f'case/build.py: unknown part(s) for --only: {", ".join(unknown)}; '
                             f'parts: {", ".join(self.parts)}')
        self.out.mkdir(parents=True, exist_ok=True)
        decisions, keys, cache = self.plan(only, force)
        cache = {name: record for name, record in cache.items() if isinstance(record, dict)}
        manifest_path = self.out / MANIFEST
        previous = self._read_json(manifest_path)
        previous_entries = {e.get('name'): e for e in previous.get('parts', []) if isinstance(e, dict)}

        # parts no longer declared: drop the files this builder wrote for them
        for name in sorted(set(cache) - set(self.parts)):
            cache.pop(name)
            self._remove(name)

        result = {'built': [], 'cached': [], 'frozen': [], 'failed': [], 'missing': [], 'seconds': {}}
        for name, (action, reason) in decisions.items():
            if action != 'build':
                result[action].append(name)
                log(f'{action:7} {name}: {reason}')
                continue
            step, stl = self._paths(name)
            tmp_step, tmp_stl = self.out / f'.{name}.tmp.step', self.out / f'.{name}.tmp.stl'
            started = time.monotonic()
            key = keys.get(name) or self.key(name)[0]
            try:
                solid = self.parts[name]['fn']()
                bbox = self._export(solid, tmp_step, tmp_stl)
                os.replace(tmp_step, step)
                os.replace(tmp_stl, stl)
            except Exception:  # noqa: BLE001 - one part's failure is reported, the rest still build
                for path in (tmp_step, tmp_stl):
                    if path.exists():
                        path.unlink()
                cache.pop(name, None)
                self._remove(name)
                result['failed'].append(name)
                log(f'FAILED  {name}:\n{traceback.format_exc()}')
                continue
            seconds = round(time.monotonic() - started, 2)
            cache[name] = {'key': key, 'files': {'step': _sha_file(step), 'stl': _sha_file(stl)},
                           'bboxMm': bbox, 'seconds': seconds, 'builtAt': round(time.time())}
            result['built'].append(name)
            result['seconds'][name] = seconds
            log(f'built   {name}: {reason} ({seconds}s) bbox {bbox}')

        entries = []
        for name, spec in self.parts.items():
            step, stl = self._paths(name)
            if name in result['failed'] or not (step.is_file() and stl.is_file()):
                continue
            entry = {'name': name, 'role': 'other', 'stl': stl.name, 'step': step.name,
                     'printRotateDeg': [0, 0, 0], 'supports': False}
            entry.update(spec['meta'])
            bbox = (cache.get(name) or {}).get('bboxMm') or (previous_entries.get(name) or {}).get('bboxMm')
            if bbox is not None:
                entry['bboxMm'] = bbox
            entries.append(entry)

        manifest = {key: value for key, value in previous.items() if key not in ('parts', 'contractSha')}
        manifest.setdefault('schema', 1)
        manifest.update(self.extra)
        contract = self._contract_file()
        if contract is not None:
            manifest['contractSha'] = _sha_file(contract)
        manifest['parts'] = entries
        _write_json(manifest_path, manifest)
        _write_json(self.out / CACHE_FILE, {'schema': 1, 'version': CACHE_VERSION, 'parts': cache})
        result['changed'] = bool(result['built'] or result['failed'])
        log(f"case/build.py: {len(result['built'])} built, {len(result['cached'])} cached, "
            f"{len(result['frozen'])} frozen, {len(result['failed'])} failed"
            + (f", {len(result['missing'])} never built" if result['missing'] else ''))
        return result

    def main(self, argv=None):
        parser = argparse.ArgumentParser(description='Build the case parts that changed into case/out/.')
        parser.add_argument('--only', action='append', default=[],
                            help='rebuild just these parts (comma separated, repeatable)')
        parser.add_argument('--force', action='store_true', help='rebuild every part (only when asked)')
        parser.add_argument('--plan', action='store_true', help='print what would be rebuilt and exit')
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        only = [name for chunk in args.only for name in chunk.split(',') if name]
        if args.plan:
            decisions, _, _ = self.plan(only, args.force)
            for name, (action, reason) in decisions.items():
                print(f'{action:7} {name}: {reason}')
            return {'plan': decisions}
        result = self.run(only, args.force)
        if result['failed']:
            raise SystemExit(1)
        return result


def _write_json(path, value):
    path = Path(path)
    tmp = path.with_name('.' + path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=1) + '\n', encoding='utf-8')
    os.replace(tmp, path)
