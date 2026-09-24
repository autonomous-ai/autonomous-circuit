"""Write a KiCad schematic from a net table, correctly, so no agent writes another emitter.

Every engine that built the Claude Pet wrote its own schematic generator and fell into the same
four holes (harness-11, -12, -14, -15): symbols placed off the 1.27 mm grid, sheet-local labels
whose nets (`/GND`) never match the PCB (`GND`), footprints without a library nickname, and
derived (`extends`) symbols embedded unresolved so the IC had no pins at all — "ERC 0" on a
drawing that described nothing. This module is Claude's harness-14 generator made generic.

    python -m kicadpy.author write spec.json design/     # main.kicad_sch + <nick>.kicad_sym + sym-lib-table entry
    python -m kicadpy.author check design/main.kicad_pro spec.json   # exported netlist == spec (kicad-cli)

The spec (JSON):
    {"title": "...", "rev": "A", "company": "...", "lib": "pet", "stem": "main",
     "libraries": ["Device", "power", "Connector", "design/mylib.kicad_sym"],   # KiCad bundled names or paths
     "parts": [{"ref": "R1", "value": "10k", "symbol": "R", "footprint": "Resistor_SMD:R_0402_1005Metric",
                "at": [88.9, 193.04], "in_bom": true}, ...],
     "nets": {"GND": [["R1", "2"], ["C1", "2"]], "V3_3": [["R1", "1"]], ...},
     "keep_uuids_from": "design/main.kicad_sch"}        # optional: reuse instance UUIDs so PCB paths survive

What it guarantees: every symbol flattened (no `extends`), placed at 0° on the 2.54 mm grid;
every pin in a net gets a 2.54 mm stub and a GLOBAL label named exactly like the net; every
unused pin gets a no_connect; every net pin is checked against the symbol's real pin numbers
and no pin sits in two nets; footprints must carry `Lib:Name`. Host Python, writes only the
files named above.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import uuid as uuidlib

from . import toolchain
from .sexp import parse

GRID = 2.54
BUNDLED_SYMBOLS = toolchain.BUNDLE / 'SharedSupport' / 'symbols'


# ---------------------------------------------------------------- s-expression helpers

def _kids(node, key):
    return [k for k in node[1:] if isinstance(k, list) and k and k[0] == key]


def _first(node, key):
    k = _kids(node, key)
    return k[0] if k else None


def _unq(atom):
    return json.loads(atom) if isinstance(atom, str) and atom.startswith('"') else atom


def _q(s):
    return json.dumps(s)


def _dump(node, depth=0):
    """Serialise a parsed S-expression; KiCad accepts any whitespace layout."""
    if isinstance(node, str):
        return node
    pad = '\t' * depth
    atoms, lists = [], []
    for c in node:
        (lists if isinstance(c, list) else atoms).append(c)
    head = '(' + ' '.join(atoms)
    if not lists:
        return head + ')'
    body = '\n'.join(pad + '\t' + _dump(c, depth + 1) for c in lists)
    return head + '\n' + body + '\n' + pad + ')'


# ---------------------------------------------------------------- libraries

def load_library(path):
    tree = parse(Path(path).read_text(encoding='utf-8'))
    if tree[0] != 'kicad_symbol_lib':
        raise ValueError(f'{path}: not a KiCad symbol library')
    return {_unq(s[1]): s for s in _kids(tree, 'symbol')}


def resolve_library(name_or_path, base=None):
    p = Path(name_or_path)
    if p.suffix == '.kicad_sym':
        return p if p.is_absolute() else (Path(base or '.') / p)
    return BUNDLED_SYMBOLS / f'{name_or_path}.kicad_sym'


def flatten(libs, name):
    """A standalone copy of symbol `name`: the parent's body with the child's overrides, no `extends`."""
    if name not in libs:
        raise KeyError(f'symbol {name!r} is in none of the libraries')
    node = copy.deepcopy(libs[name])
    ext = _first(node, 'extends')
    if not ext:
        return node
    parent = flatten(libs, _unq(ext[1]))
    pname = _unq(parent[1])
    out = ['symbol', _q(name)]
    child_settings = {c[0]: c for c in node[2:] if isinstance(c, list) and c[0] not in ('extends', 'property', 'symbol')}
    seen = set()
    for c in parent[2:]:
        if not isinstance(c, list) or c[0] in ('property', 'symbol', 'embedded_fonts'):
            continue
        seen.add(c[0])
        out.append(child_settings.get(c[0], c))
    for k, c in child_settings.items():
        if k not in seen and k != 'embedded_fonts':
            out.append(c)
    props = {_unq(p[1]): p for p in _kids(parent, 'property')}
    for p in _kids(node, 'property'):
        props[_unq(p[1])] = p
    out.extend(props.values())
    for unit in _kids(parent, 'symbol'):
        u = copy.deepcopy(unit)
        u[1] = _q(_unq(unit[1]).replace(pname, name, 1))
        out.append(u)
    out.append(['embedded_fonts', 'no'])
    return out


def pins_of(symbol):
    """{pin number: (x, y, angle)} in symbol coordinates (y up, as the library stores them)."""
    out = {}
    for unit in _kids(symbol, 'symbol'):
        for pin in _kids(unit, 'pin'):
            num = _unq(_first(pin, 'number')[1])
            at = _first(pin, 'at')
            out.setdefault(num, (float(at[1]), float(at[2]), float(at[3]) % 360))
    return out


# ---------------------------------------------------------------- the spec

def _grid(v):
    return round(round(v / GRID) * GRID, 4)


def validate(spec, pins_by_symbol):
    """Every net pin exists on its symbol, no pin sits in two nets, every footprint has a nickname."""
    by_ref = {p['ref']: p for p in spec['parts']}
    if len(by_ref) != len(spec['parts']):
        raise ValueError('duplicate reference in parts')
    owner = {}
    for net, pins in spec['nets'].items():
        for ref, pin in pins:
            if ref not in by_ref:
                raise ValueError(f'net {net}: unknown reference {ref}')
            sym = by_ref[ref]['symbol']
            if str(pin) not in pins_by_symbol[sym]:
                raise ValueError(f'net {net}: {ref} ({sym}) has no pin {pin}; it has {sorted(pins_by_symbol[sym])}')
            if (ref, str(pin)) in owner:
                raise ValueError(f'pin {ref}.{pin} is in two nets: {owner[(ref, str(pin))]} and {net}')
            owner[(ref, str(pin))] = net
    for p in spec['parts']:
        fp = p.get('footprint') or ''
        if fp and ':' not in fp:
            raise ValueError(f"{p['ref']}: footprint {fp!r} needs a library nickname (Lib:Name)")
    return owner


# ---------------------------------------------------------------- writing

OUTWARD = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}     # pin direction -> stub direction, sheet coords (y down)
LABEL_ANGLE = {0: 180, 180: 0, 90: 270, 270: 90}


def write(spec, out_dir):
    """Write `<stem>.kicad_sch` and `<lib>.kicad_sym` under out_dir; register the library in sym-lib-table."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nick, stem = spec.get('lib', 'local'), spec.get('stem', 'main')
    libs = {}
    for entry in spec.get('libraries', []):
        libs.update(load_library(resolve_library(entry, spec.get('base'))))
    used = []
    for p in spec['parts']:
        if p['symbol'] not in used:
            used.append(p['symbol'])
    flat = {name: flatten(libs, name) for name in used}
    pins = {name: pins_of(sym) for name, sym in flat.items()}
    owner = validate(spec, pins)

    old_uuid, root_uuid = {}, None
    keep = spec.get('keep_uuids_from')
    if keep and Path(keep).is_file():
        old = parse(Path(keep).read_text(encoding='utf-8'))
        root_uuid = _unq(_first(old, 'uuid')[1])
        for s in _kids(old, 'symbol'):
            props = {_unq(x[1]): _unq(x[2]) for x in _kids(s, 'property')}
            if props.get('Reference') and _first(s, 'uuid'):
                old_uuid[props['Reference']] = _unq(_first(s, 'uuid')[1])
    root_uuid = root_uuid or str(uuidlib.uuid4())
    new = lambda: str(uuidlib.uuid4())  # noqa: E731

    parts = []
    for p in spec['parts']:
        parts.append(dict(p, x=_grid(p['at'][0]), y=_grid(p['at'][1]), uuid=old_uuid.get(p['ref']) or new()))
    by_ref = {p['ref']: p for p in parts}

    def pin_xy(part, pin):
        lx, ly, ang = pins[part['symbol']][pin]
        return round(part['x'] + lx, 4), round(part['y'] - ly, 4), ang   # KiCad flips Y when placing a symbol

    out = [f'(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0")',
           f'\t(uuid {_q(root_uuid)})', f'\t(paper {_q(spec.get("paper", "A1"))})',
           f'\t(title_block (title {_q(spec.get("title", stem))}) (date {_q(spec.get("date", ""))}) '
           f'(rev {_q(spec.get("rev", ""))}) (company {_q(spec.get("company", ""))}))',
           '\t(lib_symbols']
    for name in used:
        sym = copy.deepcopy(flat[name])
        sym[1] = _q(f'{nick}:{name}')
        out.append(_dump(sym, 2))
    out.append('\t)')
    for p in parts:
        x, y = p['x'], p['y']
        is_flag = p['ref'].startswith('#')
        in_bom = 'yes' if p.get('in_bom', True) and not is_flag else 'no'
        out += ['\t(symbol', f'\t\t(lib_id {_q(nick + ":" + p["symbol"])})', f'\t\t(at {x} {y} 0)', '\t\t(unit 1)',
                f'\t\t(exclude_from_sim no) (in_bom {in_bom}) (on_board yes) (dnp no)', f'\t\t(uuid {_q(p["uuid"])})',
                f'\t\t(property "Reference" {_q(p["ref"])} (at {x + 3.81} {y - 5.08} 0) (effects (font (size 1.27 1.27)) (justify left){" (hide yes)" if is_flag else ""}))',
                f'\t\t(property "Value" {_q(p.get("value", ""))} (at {x + 3.81} {y + 5.08} 0) (effects (font (size 1.27 1.27)) (justify left)))',
                f'\t\t(property "Footprint" {_q(p.get("footprint", ""))} (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))',
                f'\t\t(property "Datasheet" {_q(p.get("datasheet", ""))} (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))',
                f'\t\t(property "Description" {_q(p.get("description", ""))} (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))']
        for num in pins[p['symbol']]:
            out.append(f'\t\t(pin {_q(num)} (uuid {_q(new())}))')
        out.append(f'\t\t(instances (project {_q(stem)} (path {_q("/" + root_uuid)} (reference {_q(p["ref"])}) (unit 1))))')
        out.append('\t)')
    placed = set()
    for net, net_pins in spec['nets'].items():
        for ref, pin in net_pins:
            part = by_ref[ref]
            x, y, ang = pin_xy(part, str(pin))
            if (ref, x, y) in placed:      # stacked pins (same position) share one stub
                continue
            placed.add((ref, x, y))
            dx, dy = OUTWARD[int(ang)]
            sx, sy = round(x + GRID * dx, 4), round(y + GRID * dy, 4)
            out.append(f'\t(wire (pts (xy {x} {y}) (xy {sx} {sy})) (stroke (width 0) (type default)) (uuid {_q(new())}))')
            la = LABEL_ANGLE[int(ang)]
            justify = 'left' if la in (0, 90) else 'right'
            out.append(f'\t(global_label {_q(net)} (shape passive) (at {sx} {sy} {la}) (fields_autoplaced yes)'
                       f' (effects (font (size 1.27 1.27)) (justify {justify})) (uuid {_q(new())})'
                       f' (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {sx} {sy} 0) (effects (font (size 1.27 1.27)) (hide yes))))')
    nc = set()
    for p in parts:
        for num in pins[p['symbol']]:
            if (p['ref'], num) in owner:
                continue
            x, y, _ = pin_xy(p, num)
            if (p['ref'], x, y) in placed or (p['ref'], x, y) in nc:
                continue
            nc.add((p['ref'], x, y))
            out.append(f'\t(no_connect (at {x} {y}) (uuid {_q(new())}))')
    out += ['\t(sheet_instances (path "/" (page "1")))', '\t(embedded_fonts no)', ')']
    (out_dir / f'{stem}.kicad_sch').write_text('\n'.join(out) + '\n', encoding='utf-8')

    lib = ['(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") (generator_version "9.0")']
    lib += [_dump(flat[name], 1) for name in used]
    lib.append(')')
    (out_dir / f'{nick}.kicad_sym').write_text('\n'.join(lib) + '\n', encoding='utf-8')

    table = out_dir / 'sym-lib-table'
    entry = f'  (lib (name "{nick}")(type "KiCad")(uri "${{KIPRJMOD}}/{nick}.kicad_sym")(options "")(descr "written by kicadpy.author"))'
    if table.is_file():
        text = table.read_text(encoding='utf-8')
        if f'(name "{nick}")' not in text:
            text = text.rstrip().rstrip(')') + '\n' + entry + '\n)\n'
            table.write_text(text, encoding='utf-8')
    else:
        table.write_text('(sym_lib_table\n  (version 7)\n' + entry + '\n)\n', encoding='utf-8')
    return {'schematic': str(out_dir / f'{stem}.kicad_sch'), 'library': str(out_dir / f'{nick}.kicad_sym'),
            'symbols': len(parts), 'nets': len(spec['nets']), 'stubs': len(placed), 'noConnects': len(nc),
            'reusedUuids': sum(1 for p in parts if p['ref'] in old_uuid)}


def check(project, spec):
    """Export the netlist with kicad-cli and compare it with the spec: the drawing must say what the table says."""
    from . import verify
    project = Path(project)
    sch = project.with_suffix('.kicad_sch')
    with tempfile.TemporaryDirectory() as tmp:
        net = Path(tmp) / 'n.net'
        toolchain.run([toolchain.executable('cli'), 'sch', 'export', 'netlist', '--format', 'kicadsexpr', '-o', str(net), str(sch)])
        pins, refs = verify.schematic_pins(net.read_text(encoding='utf-8'))
    # power flags (#FLG…) are not components in KiCad's netlist export; they are not pins to compare
    want = {f'{ref}.{pin}': net_name for net_name, net_pins in spec['nets'].items() for ref, pin in net_pins if not str(ref).startswith('#')}
    missing = sorted(k for k in want if k not in pins)
    wrong = sorted(f'{k}: drawing says {pins[k]!r}, spec says {want[k]!r}' for k in want if k in pins and pins[k] != want[k])
    extra = sorted(k for k in pins if k not in want and not k.startswith('#'))
    return {'ok': not (missing or wrong or extra), 'missing': missing, 'wrong': wrong, 'extra': extra,
            'schematicPins': len(pins), 'specPins': len(want)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == 'write' and len(argv) == 3:
            spec = json.loads(Path(argv[1]).read_text(encoding='utf-8'))
            spec.setdefault('base', str(Path(argv[1]).parent))
            result = write(spec, argv[2])
        elif argv and argv[0] == 'check' and len(argv) == 3:
            result = check(argv[1], json.loads(Path(argv[2]).read_text(encoding='utf-8')))
        else:
            print(__doc__)
            sys.exit(2)
        print(json.dumps({'ok': result.get('ok', True), 'result': result}))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'kind': type(exc).__name__}))
        sys.exit(1)


if __name__ == '__main__':
    main()
