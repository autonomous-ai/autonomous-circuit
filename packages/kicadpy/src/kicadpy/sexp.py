"""Lossless atoms for semantic comparison; never a KiCad file writer."""
import json
import re


def parse(text):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text)
    stack, roots = [], []
    for token in tokens:
        if token == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == ')':
            if not stack:
                raise ValueError('unbalanced S-expression')
            stack.pop()
        elif stack:
            stack[-1].append(token)
        else:
            raise ValueError('atom outside S-expression')
    if stack or len(roots) != 1:
        raise ValueError('expected one balanced S-expression')
    return roots[0]


def child(node, kind):
    return next((x for x in node if isinstance(x, list) and x and x[0] == kind), None)


def value(atom):
    return json.loads(atom) if atom.startswith('"') else atom


def board_state(text):
    root = parse(text)
    if root[0] != 'kicad_pcb':
        raise ValueError('not a KiCad PCB')
    objects, other, fills = {}, [], {}
    for node in root[1:]:
        if not isinstance(node, list):
            raise ValueError('malformed PCB')
        kind = node[0]
        if kind in ('generator', 'generator_version', 'version'):
            continue
        uid = child(node, 'uuid')
        if uid:
            key = value(uid[1])
            if key in objects:
                raise ValueError('duplicate UUID')
            if kind == 'zone':
                fills[key] = [x for x in node if isinstance(x, list) and x[0] in ('filled_polygon', 'fill_segments')]
                node = [x for x in node if not (isinstance(x, list) and x[0] in ('filled_polygon', 'fill_segments'))]
            objects[key] = node
        else:
            other.append(node)
    return {'objects': objects, 'other': other, 'fills': fills}


def diff(before, after):
    a, b = board_state(before), board_state(after)
    keys = set(a['objects']) | set(b['objects'])
    return {
        'objects': [{'uuid': k, 'kind': (b['objects'].get(k) or a['objects'][k])[0],
                     'change': 'added' if k not in a['objects'] else 'removed' if k not in b['objects'] else 'modified'}
                    for k in sorted(keys) if a['objects'].get(k) != b['objects'].get(k)],
        'settingsChanged': a['other'] != b['other'],
        'zoneFillsChanged': sorted(k for k in set(a['fills']) | set(b['fills']) if a['fills'].get(k) != b['fills'].get(k)),
    }
