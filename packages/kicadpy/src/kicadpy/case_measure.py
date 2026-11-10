"""What the case checker measures itself, on the exported files, instead of trusting a report.

Every measurement ends in one of four levels:

    pass        measured and fine
    review      measured, and a person should look (a thin spot, a gap close to its limit)
    unverified  could not be measured or was never declared; never counted as a pass
    fail        measured and wrong

Moving parts, wires and test fits are declared in `case/interfaces.json`:

    "joints": [{"name": "hip", "originMm": [0, 0, 0], "axis": [0, 0, 1], "rangeDeg": [-30, 30],
                "moves": ["thigh"], "clearOf": ["base", "lid"], "minClearanceMm": 0.5,
                "allowContact": [["thigh", "base"]]},
               {"name": "knee", "parent": "hip", ...}],
    "wires": [{"name": "hip servo", "clearDiameterMm": 8.5,
               "routes": [{"name": "under the rail", "pointsMm": [[0, 0, 0], [10, 0, 5]]}]}],
    "fits": [{"name": "servo in its pocket", "testPart": "coupon_TEST_ONLY"}],
    "skins": [{"part": "lid", "centerMm": [0, 0, 45], "radiusMm": 12, "reason": "light window"}]

A part name may be a whole part in parts.json, or `name#3` for its solid number 3. `"joints": []`
says that nothing moves. A sampled sweep is not continuous proof, so a gap close to its limit is a
review. Joints on different chains are each checked against the other's resting position only.
"""
from __future__ import annotations

import itertools
from pathlib import Path
import struct

PASS, REVIEW, UNVERIFIED, FAIL = 'pass', 'review', 'unverified', 'fail'
DEFAULT_MIN_CLEARANCE_MM = 0.5
REVIEW_FACTOR = 1.5           # a sampled gap within this factor of its limit gets a person's eye
SWEEP_STEPS = 5
DEFAULT_WIRE_DIAMETER_MM = 8.5   # a three-pin servo plug housing, corner to corner
MIN_WIRE_ROUTES = 2           # if one route is blocked, the wire goes another way
WIRE_BLOCK_MM3 = 0.5
OVERLAP_MM3 = 0.5
DEFAULT_NOZZLE_MM = 0.4
WALL_SAMPLES = 2000
MIN_WALL_SAMPLES = 100
MAX_RAY_TRIANGLE_TESTS = 40_000_000
MESH_REVIEW_MM = 0.1          # the STL against a 0.05 mm reference: a coarser mesh eats into fits
_FACING = -0.5                # the far face of a wall turns at least 120 degrees from the near one


def item(check, level, message, **facts):
    return {'check': check, 'level': level, 'message': message, **facts}


def verdict(items):
    """The whole case's level: a failure outranks what was not measured, which outranks a review."""
    levels = {entry['level'] for entry in items}
    return next((level for level in (FAIL, UNVERIFIED, REVIEW) if level in levels), PASS)


def stl_triangles(path):
    """An STL, binary or ASCII, as an (n, 3, 3) float array."""
    import numpy as np
    import re
    data = Path(path).read_bytes()
    if len(data) >= 84:
        count = struct.unpack('<I', data[80:84])[0]
        if 84 + count * 50 == len(data):
            record = np.dtype([('normal', '<3f4'), ('vertices', '<9f4'), ('attribute', '<u2')])
            return np.frombuffer(data, dtype=record, count=count, offset=84)['vertices'].reshape(-1, 3, 3).astype(float)
    values = [tuple(map(float, m.groups())) for m in re.finditer(rb'vertex\s+(\S+)\s+(\S+)\s+(\S+)', data)]
    if not values or len(values) % 3:
        raise ValueError('STL contains no readable triangles')
    return np.array(values, dtype=float).reshape(-1, 3, 3)


def wall_samples(triangles, samples=WALL_SAMPLES, seed=1):
    """Wall thickness at random surface points -> (thickness, points).

    Each ray goes straight in from the surface and counts when it leaves through a face turned at
    least 120 degrees from its own: a wall's other side or a sharp wedge, never a corner.
    """
    import numpy as np
    first = triangles[:, 0]
    edge1, edge2 = triangles[:, 1] - first, triangles[:, 2] - first
    cross = np.cross(edge1, edge2)
    double_area = np.linalg.norm(cross, axis=1)
    usable = double_area > 1e-12
    first, edge1, edge2, cross, double_area = first[usable], edge1[usable], edge2[usable], cross[usable], double_area[usable]
    if not len(first):
        raise ValueError('the mesh has no area')
    normals = cross / double_area[:, None]
    rays = max(MIN_WALL_SAMPLES, min(samples, MAX_RAY_TRIANGLE_TESTS // len(first)))
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(first), size=rays, p=double_area / double_area.sum())
    u, v = rng.random(rays), rng.random(rays)
    outside = u + v > 1
    u[outside], v[outside] = 1 - u[outside], 1 - v[outside]
    points = first[chosen] + edge1[chosen] * u[:, None] + edge2[chosen] * v[:, None]
    thickness, found = [], []
    for point, face in zip(points, chosen):
        direction = -normals[face]
        origin = point + direction * 1e-3
        p = np.cross(direction, edge2)
        det = (edge1 * p).sum(axis=1)
        with np.errstate(divide='ignore', invalid='ignore'):
            inverse = 1 / det
            s = origin - first
            a = (s * p).sum(axis=1) * inverse
            q = np.cross(s, edge1)
            b = (q * direction).sum(axis=1) * inverse
            t = (q * edge2).sum(axis=1) * inverse
            hit = (np.abs(det) > 1e-12) & (a >= -1e-9) & (b >= -1e-9) & (a + b <= 1 + 1e-9) & (t > 1e-6)
        if not hit.any():
            continue
        indices = np.nonzero(hit)[0]
        nearest = indices[np.argmin(t[indices])]
        if normals[nearest] @ normals[face] <= _FACING:
            thickness.append(float(t[nearest]) + 1e-3)
            found.append(point)
    return np.array(thickness), np.array(found).reshape(-1, 3)


def check_walls(name, path, nozzle_mm, skins=()):
    """Thin spots are for a person to judge: a deliberate skin and a broken wall read the same."""
    import numpy as np
    check = f'walls:{name}'
    try:
        thickness, points = wall_samples(stl_triangles(path))
    except Exception as exc:  # any unreadable or degenerate mesh is a measurement that did not happen
        return item(check, UNVERIFIED, f'{name}: wall thickness could not be measured: {exc}', part=name)
    if len(thickness) < MIN_WALL_SAMPLES:
        return item(check, UNVERIFIED, f'{name}: only {len(thickness)} wall samples, too few to judge', part=name)
    limit = 2 * nozzle_mm - 0.05       # two extrusion lines, less a little for the sampled surface
    keep = np.ones(len(thickness), dtype=bool)
    for skin in skins:
        if skin.get('part') == name:
            keep &= np.linalg.norm(points - np.array(skin['centerMm'], dtype=float), axis=1) > float(skin['radiusMm'])
    thin = np.nonzero(keep & (thickness < limit))[0]
    facts = {'part': name, 'samples': len(thickness), 'minMm': round(float(thickness.min()), 2),
             'p05Mm': round(float(np.percentile(thickness, 5)), 2), 'limitMm': round(limit, 2)}
    if not len(thin):
        return item(check, PASS, f"{name}: walls are at least {facts['limitMm']} mm outside declared skins", **facts)
    worst = thin[np.argsort(thickness[thin])][:5]
    spots = [{'atMm': [round(float(v), 1) for v in points[i]], 'thicknessMm': round(float(thickness[i]), 2)} for i in worst]
    where = spots[0]
    return item(check, REVIEW, f"{name}: {len(thin)} of {len(thickness)} samples are under {facts['limitMm']} mm, thinnest "
                               f"{where['thicknessMm']} mm at {where['atMm']}. Thicken it, or declare a deliberate skin in case/interfaces.json",
                thinnest=spots, **facts)


def _location(cq, joint, angle):
    origin = cq.Vector(*joint['originMm'])
    axis = cq.Vector(*joint['axis'])
    turn = cq.Location(cq.Vector(0, 0, 0), axis, angle)
    return cq.Location(origin) * turn * cq.Location(origin * -1)


def _angles(joint):
    low, high = (float(v) for v in joint['rangeDeg'])
    values = {round(low + (high - low) * i / (SWEEP_STEPS - 1), 6) for i in range(SWEEP_STEPS)}
    if low <= 0 <= high:
        values.add(0.0)
    return sorted(values)


def _chain(joint, by_name):
    chain, seen = [joint], {joint['name']}
    while chain[0].get('parent'):
        parent = by_name[chain[0]['parent']]
        if parent['name'] in seen:
            raise ValueError(f"joint {joint['name']} has a parent loop")
        seen.add(parent['name'])
        chain.insert(0, parent)
    return chain


def _gap(cq, moving, fixed, limit):
    """The distance between two shapes; a pair clearly apart by its boxes costs nothing."""
    a, b = moving.BoundingBox(), fixed.BoundingBox()
    apart = max(a.xmin - b.xmax, b.xmin - a.xmax, a.ymin - b.ymax, b.ymin - a.ymax, a.zmin - b.zmax, b.zmin - a.zmax)
    if apart > limit * REVIEW_FACTOR:
        return apart
    return moving.distance(fixed)


def check_joints(cq, joints, shape_of):
    """Sweep every joint chain over its range against what it must clear, in every combination."""
    by_name = {joint['name']: joint for joint in joints}
    results = []
    for joint in joints:
        check = f"joint:{joint['name']}"
        try:
            chain = _chain(joint, by_name)
            limit = float(joint.get('minClearanceMm', DEFAULT_MIN_CLEARANCE_MM))
            allowed = {frozenset(pair) for pair in joint.get('allowContact', ())}
            moving = [(name, shape_of(name)) for name in joint['moves']]
            fixed = [(name, shape_of(name)) for name in joint['clearOf']]
            worst, poses = None, 0
            for angles in itertools.product(*(_angles(link) for link in chain)):
                poses += 1
                location = cq.Location(cq.Vector(0, 0, 0))
                for link, angle in zip(chain, angles):
                    location = location * _location(cq, link, angle)
                pose = {link['name']: angle for link, angle in zip(chain, angles)}
                for name, shape in moving:
                    placed = shape.moved(location)
                    for other, target in fixed:
                        if other == name or frozenset((name, other)) in allowed:
                            continue
                        gap = _gap(cq, placed, target, limit)
                        if worst is None or gap < worst['gapMm']:
                            worst = {'gapMm': gap, 'part': name, 'against': other, 'pose': pose}
        except Exception as exc:  # a bad declaration or kernel failure: nothing was measured
            results.append(item(check, UNVERIFIED, f"{joint.get('name', '?')}: travel could not be measured: {exc}"))
            continue
        if worst is None:
            results.append(item(check, UNVERIFIED, f"{joint['name']}: declares nothing to clear"))
            continue
        worst['gapMm'] = round(worst['gapMm'], 2)
        where = f"{worst['part']} to {worst['against']} is {worst['gapMm']} mm at {worst['pose']}"
        facts = dict(limitMm=limit, poses=poses, closest=worst)
        if worst['gapMm'] < limit:
            results.append(item(check, FAIL, f"{joint['name']}: {where}, needs at least {limit} mm", **facts))
        elif worst['gapMm'] < limit * REVIEW_FACTOR:
            results.append(item(check, REVIEW, f"{joint['name']}: {where}, close to the {limit} mm limit between samples", **facts))
        else:
            results.append(item(check, PASS, f"{joint['name']}: {where} over {poses} poses", **facts))
    return results


def _route_volume(cq, points, diameter, solids):
    blocked = {}
    for start, end in zip(points, points[1:]):
        a, b = cq.Vector(*start), cq.Vector(*end)
        length = (b - a).Length
        if length < 1e-6:
            raise ValueError('route has a zero-length segment')
        tube = cq.Solid.makeCylinder(diameter / 2, length, a, b - a)
        for name, shape in solids:
            common = tube.intersect(shape)
            volume = sum(solid.Volume() for solid in common.Solids())
            if volume > WIRE_BLOCK_MM3:
                blocked[name] = round(blocked.get(name, 0.0) + volume, 1)
    return blocked


def check_wires(cq, wires, solids):
    """Each wire needs open air the size of its plug, and a second way round if the first is blocked."""
    results = []
    for wire in wires:
        check = f"wire:{wire.get('name', '?')}"
        try:
            diameter = float(wire.get('clearDiameterMm', DEFAULT_WIRE_DIAMETER_MM))
            routes = wire['routes']
            for route in routes:
                if len(route['pointsMm']) < 2:
                    raise ValueError(f"route {route.get('name', '?')} needs at least two points")
            blocked = {route['name']: _route_volume(cq, route['pointsMm'], diameter, solids) for route in routes}
        except Exception as exc:
            results.append(item(check, UNVERIFIED, f"{wire.get('name', '?')}: wire routes could not be measured: {exc}"))
            continue
        clear = [name for name, hits in blocked.items() if not hits]
        facts = dict(clearDiameterMm=diameter, clearRoutes=clear, blockedRoutes={k: v for k, v in blocked.items() if v})
        if not clear:
            results.append(item(check, FAIL, f"{wire['name']}: no route is open for a {diameter} mm plug", **facts))
        elif len(clear) < MIN_WIRE_ROUTES:
            results.append(item(check, REVIEW, f"{wire['name']}: only one open route ({clear[0]}); nowhere to go if it is blocked", **facts))
        else:
            results.append(item(check, PASS, f"{wire['name']}: {len(clear)} open routes for a {diameter} mm plug", **facts))
    return results


def check_fits(fits, manifest):
    """Every mate a person must make needs a real piece in the test kit, or it is found at full print."""
    coupons = {part.get('name') for part in manifest.get('parts') or [] if part.get('role') == 'coupon'}
    results = []
    for fit in fits:
        name, test = fit.get('name', '?'), fit.get('testPart')
        check = f'fit:{name}'
        if test in coupons:
            results.append(item(check, PASS, f'{name}: tested by {test}', testPart=test))
        else:
            results.append(item(check, REVIEW, f'{name}: no coupon part in the test kit covers this fit (testPart: {test})', testPart=test))
    return results


def declared(cq, workspace, out, manifest, intent, solids, require):
    """Joints, wires and fits from the intent. A handwired build must state them, even as []."""
    results = []
    parts = {part.get('name'): part for part in manifest.get('parts') or []}
    cache = {}

    def shape_of(reference):
        if reference not in cache:
            name, _, index = reference.partition('#')
            shape = cq.importers.importStep(str(Path(out) / parts[name]['step'])).val()
            cache[reference] = shape.Solids()[int(index)] if index else shape
        return cache[reference]

    printed = [(name, shape.val()) for name, _, shape in solids]
    steps = (('joints', lambda v: check_joints(cq, v, shape_of)), ('wires', lambda v: check_wires(cq, v, printed)),
             ('fits', lambda v: check_fits(v, manifest)))
    for key, run in steps:
        value = intent.get(key)
        if value is None:
            if require:
                results.append(item(key, UNVERIFIED, f'case/interfaces.json does not declare {key} ([] if there are none)'))
            continue
        results.extend(run(value))
    return results


def tolerance_for(clearance_mm):
    """STL deviation for a part designed around this clearance: about a tenth of it."""
    return min(max(clearance_mm / 10, 0.01), 0.05)
