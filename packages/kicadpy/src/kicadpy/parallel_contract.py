"""Versioned interface contract and Codex response schemas for the opt-in pilot."""
import hashlib
import json
import math

ROLES = ('board', 'firmware', 'case')
OWNED = {
    'board': ('design/', 'boards/', 'engineering/', 'product.json', 'parts.json', 'manufacturing.json',
              'tools/board/', 'build/board/'),
    'firmware': ('firmware/', 'tools/firmware/', 'build/firmware/'),
    'case': ('case/', 'tools/case/', 'build/case/'),
}
# Where a team records a sealed detail it had to change inside its own domain.
DEVIATIONS = {'board': 'engineering/DEVIATIONS.md', 'firmware': 'firmware/DEVIATIONS.md',
              'case': 'case/DEVIATIONS.md'}


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def array(items):
    return {'type': 'array', 'items': items}


STRING = {'type': 'string'}
NUMBER = {'type': 'number'}
POINT = array(NUMBER)
CONTRACT_SCHEMA = obj({
    'name': STRING,
    'coordinateFrame': obj({'units': {'type': 'string', 'enum': ['mm']}, 'origin': STRING, 'axes': STRING}),
    'board': obj({'outlineMm': array(POINT), 'thicknessMm': NUMBER,
                  'mounts': array(obj({'id': STRING, 'xMm': NUMBER, 'yMm': NUMBER, 'holeDiameterMm': NUMBER}))}),
    'powerRails': array(obj({'name': STRING, 'voltageV': NUMBER, 'source': STRING})),
    'pins': array(obj({'signal': STRING, 'device': STRING, 'pin': STRING, 'net': STRING, 'voltageV': NUMBER})),
    'modules': array(obj({'id': STRING, 'interface': STRING, 'positionMm': POINT, 'sizeMm': POINT})),
    'mechanicalEnvelopeMm': POINT,
    'firmwareBehavior': array(STRING), 'assumptions': array(STRING),
    'tasks': obj({role: STRING for role in ROLES}),
})
RESULT_SCHEMA = obj({'contractSha256': STRING, 'stage': STRING, 'summary': STRING, 'blockers': array(STRING)})
REVIEW_SCHEMA = obj({'contractSha256': STRING, 'decision': {'type': 'string', 'enum': ['approve', 'revise']},
                     'summary': STRING, 'findings': array(obj({'path': STRING, 'message': STRING}))})
ADVICE_SCHEMA = obj({'contractSha256': STRING,
                     'stage': STRING,
                     'decision': {'type': 'string',
                                  'enum': ['repair', 'interface_change', 'external_blocker', 'waive']},
                     'summary': STRING,
                     'actions': array(obj({'path': STRING, 'message': STRING})),
                     'changedFields': array(STRING)})


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def validate(value, schema):
    """Validate this small strict schema subset without a runtime dependency."""
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['required']):
            raise ValueError('Unexpected or missing contract/response fields')
        for key, child in schema['properties'].items():
            validate(value[key], child)
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > 1000:
            raise ValueError('Expected a bounded array')
        for item in value:
            validate(item, schema['items'])
    elif kind == 'string':
        if not isinstance(value, str) or not value.strip() or len(value) > 16000:
            raise ValueError('Expected nonempty bounded text')
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Expected finite number')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Unexpected enum value')


def validate_contract(contract):
    validate(contract, CONTRACT_SCHEMA)
    board = contract['board']
    outline = board['outlineMm']
    if len(outline) < 3 or any(len(p) != 2 for p in outline):
        raise ValueError('Board outline needs at least three XY points')
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(outline, outline[1:] + outline[:1]))
    if abs(area2) < 1e-6:
        raise ValueError('Degenerate board outline')
    if board['thicknessMm'] <= 0:
        raise ValueError('Invalid board thickness')
    envelope = contract['mechanicalEnvelopeMm']
    if len(envelope) != 3 or min(envelope) <= 0:
        raise ValueError('Invalid mechanical envelope')
    if not contract['powerRails'] or not contract['pins'] or not contract['firmwareBehavior']:
        raise ValueError('Power, pins and firmware behavior must be planned')
    seen = {}
    for pin in contract['pins']:
        key = (pin['device'], pin['pin'])
        if key in seen:
            raise ValueError('Duplicate or conflicting device pin assignment')
        seen[key] = pin['net']
        if pin['voltageV'] < 0:
            raise ValueError('Negative pin voltage')
    for collection, key in ((contract['powerRails'], 'name'), (contract['modules'], 'id'), (board['mounts'], 'id')):
        if len({p[key] for p in collection}) != len(collection):
            raise ValueError('Duplicate interface identifier')
    if any(rail['voltageV'] < 0 for rail in contract['powerRails']):
        raise ValueError('Negative rail voltage')
    for module in contract['modules']:
        if len(module['positionMm']) != 3 or len(module['sizeMm']) != 3 or min(module['sizeMm']) <= 0:
            raise ValueError('Invalid module envelope')
    if any(m['holeDiameterMm'] <= 0 for m in board['mounts']):
        raise ValueError('Invalid mounting hole')
    return contract


def owns(role, name):
    roles = ROLES if role == 'solo' else (role,)
    return any(name.startswith(p) if p.endswith('/') else name == p for r in roles for p in OWNED[r])
