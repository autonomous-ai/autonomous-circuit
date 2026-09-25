"""KiCad Python 3.9 compatible subprocess. No imports from host packages."""
import json
import math
import os
from pathlib import Path
import sys
import pcbnew as p


def uid(item):
    return item.m_Uuid.AsString()


def point(pt):
    return [p.ToMM(pt.x), p.ToMM(pt.y)]


def vector(xy):
    if len(xy) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in xy):
        raise ValueError('expected finite x,y millimetres')
    return p.VECTOR2I(p.FromMM(xy[0]), p.FromMM(xy[1]))


def copper(item):
    result = {'uuid': uid(item), 'net': item.GetNetname(), 'layer': item.GetLayerName(),
              'widthMm': p.ToMM(item.GetWidth(item.TopLayer()) if isinstance(item, p.PCB_VIA) else item.GetWidth()), 'locked': item.IsLocked()}
    if isinstance(item, p.PCB_VIA):
        result.update(kind='via', at=point(item.GetPosition()), drillMm=p.ToMM(item.GetDrillValue()),
                      layers=[p.BOARD.GetStandardLayerName(item.TopLayer()), p.BOARD.GetStandardLayerName(item.BottomLayer())])
    else:
        result.update(kind='arc' if isinstance(item, p.PCB_ARC) else 'track',
                      start=point(item.GetStart()), end=point(item.GetEnd()))
        if isinstance(item, p.PCB_ARC):
            result['mid'] = point(item.GetMid())
    return result


def inspect(board):
    footprints = []
    for f in board.GetFootprints():
        pads = [{'uuid': uid(pad), 'number': pad.GetNumber(), 'net': pad.GetNetname(),
                 'at': point(pad.GetPosition()), 'sizeMm': point(pad.GetSize()),
                 'drillMm': point(pad.GetDrillSize()), 'attribute': int(pad.GetAttribute()),
                 'plated': pad.GetAttribute() != p.PAD_ATTRIB_NPTH,
                 'rotationDeg': pad.GetOrientationDegrees(),
                 'layers': [board.GetLayerName(l) for l in pad.GetLayerSet().Seq()]} for pad in f.Pads()]
        footprints.append({'uuid': uid(f), 'reference': f.GetReference(), 'value': f.GetValue(),
                           'footprint': str(f.GetFPID().GetLibItemName()), 'attributes': int(f.GetAttributes()),
                           'dnp': f.IsDNP(), 'excludedFromBOM': f.IsExcludedFromBOM(),
                           'excludedFromPosition': f.IsExcludedFromPosFiles(),
                           'at': point(f.GetPosition()), 'rotationDeg': f.GetOrientationDegrees(),
                           'layer': f.GetLayerName(), 'pads': pads})
    # The board's size is the closed outline's centreline, not the drawing stroke around it:
    # GetBoardEdgesBoundingBox() adds the Edge.Cuts pen width (0.05 mm on a 40 mm board reads
    # as a 0.9988 'scale error' in the packet check — seen 2026-09-21). Fall back to the edges
    # box only when there is no closed outline to measure.
    poly = p.SHAPE_POLY_SET()
    if board.GetBoardPolygonOutlines(poly, False) and poly.OutlineCount() > 0:
        bbox, bounds_source = poly.BBox(), 'outline'
    else:
        bbox, bounds_source = board.GetBoardEdgesBoundingBox(), 'edges'
    return {'kicad': p.GetBuildVersion(), 'coordinateSystem': 'KiCad absolute mm, y down; layers unmirrored',
            'thicknessMm': p.ToMM(board.GetDesignSettings().GetBoardThickness()),
            'copperLayers': [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()],
            'boardBoundsMm': [p.ToMM(bbox.GetX()), p.ToMM(bbox.GetY()), p.ToMM(bbox.GetRight()), p.ToMM(bbox.GetBottom())],
            'boardBoundsSource': bounds_source,
            'nets': sorted(n.GetNetname() for n in board.GetNetInfo().NetsByName().values()),
            'footprints': footprints, 'copper': [copper(t) for t in board.GetTracks()],
            'zones': [{'uuid': uid(z), 'net': z.GetNetname(), 'layer': z.GetLayerName()} for z in board.Zones()],
            'coverage': {'schematicGraphics': False, 'nativeGeometry': True, 'viewerIntegration': False}}


def islands(board, net, near=None):
    """Which pads and vias of `net` sit on which filled-zone island, per copper layer; optional via-site search.

    Island 0 is the largest fill on that layer; a pad on island >= 1 is stranded (it has copper
    but that copper is not the plane). With `near` = [x, y, r], every 0.05 mm point in that
    square is scored by its clearance to non-net copper on BOTH layers and to the board edge;
    the top sites are where a stitching via can go. Read-only.
    """
    layers = ['F.Cu', 'B.Cu']
    zones = {}
    for z in board.Zones():
        if z.GetNetname() == net:
            zones.setdefault(board.GetLayerName(z.GetFirstLayer()), z)
    polys = {}
    for ln, z in zones.items():
        lid = board.GetLayerID(ln)
        ps = z.GetFilledPolysList(lid)
        order = sorted(range(ps.OutlineCount()), key=lambda i: -ps.Outline(i).Area())
        polys[ln] = (ps, {orig: rank for rank, orig in enumerate(order)})

    def island_of(ln, x, y):
        if ln not in polys:
            return None
        ps, rank = polys[ln]
        pt = p.VECTOR2I(p.FromMM(x), p.FromMM(y))
        for i in range(ps.OutlineCount()):
            if ps.Contains(pt, i):
                return rank[i]
        return None

    result = {'net': net, 'layers': {}, 'pads': [], 'vias': [], 'sites': []}
    for ln, (ps, rank) in polys.items():
        areas = sorted((p.ToMM(p.ToMM(ps.Outline(i).Area())) for i in range(ps.OutlineCount())), reverse=True)
        result['layers'][ln] = {'islands': ps.OutlineCount(), 'areasMm2': [round(a, 2) for a in areas]}
    for f in board.GetFootprints():
        for pad in f.Pads():
            if pad.GetNetname() != net:
                continue
            x, y = p.ToMM(pad.GetPosition().x), p.ToMM(pad.GetPosition().y)
            for ln in layers:
                if pad.IsOnLayer(board.GetLayerID(ln)) and ln in polys:
                    result['pads'].append({'ref': f.GetReference(), 'pad': pad.GetNumber(), 'x': x, 'y': y,
                                           'layer': ln, 'island': island_of(ln, x, y)})
    for t in board.GetTracks():
        if isinstance(t, p.PCB_VIA) and t.GetNetname() == net:
            x, y = p.ToMM(t.GetPosition().x), p.ToMM(t.GetPosition().y)
            result['vias'].append({'x': x, 'y': y, 'islandF': island_of('F.Cu', x, y), 'islandB': island_of('B.Cu', x, y)})
    # An island is reachable when a via or a through pad of the net joins it to a reachable island
    # on the other layer; the largest fill of each layer (island 0) is the plane. Iterate to closure.
    links = [(v['islandF'], v['islandB']) for v in result['vias']]
    for f in board.GetFootprints():
        for pad in f.Pads():
            if pad.GetNetname() == net and pad.GetAttribute() == p.PAD_ATTRIB_PTH:
                x, y = p.ToMM(pad.GetPosition().x), p.ToMM(pad.GetPosition().y)
                links.append((island_of('F.Cu', x, y), island_of('B.Cu', x, y)))
    reach = {'F.Cu': {0} if 'F.Cu' in polys else set(), 'B.Cu': {0} if 'B.Cu' in polys else set()}
    changed = True
    while changed:
        changed = False
        for fi, bi in links:
            if fi is not None and bi is not None:
                if fi in reach['F.Cu'] and bi not in reach['B.Cu']:
                    reach['B.Cu'].add(bi); changed = True
                if bi in reach['B.Cu'] and fi not in reach['F.Cu']:
                    reach['F.Cu'].add(fi); changed = True
    stranded_islands = {ln: sorted(k for k in range(info['islands']) if k not in reach[ln]) for ln, info in result['layers'].items()}
    for entry in result['pads']:
        entry['stranded'] = entry['island'] is not None and entry['island'] not in reach[entry['layer']]
    result['reachableIslands'] = {ln: sorted(v) for ln, v in reach.items()}
    result['strandedIslands'] = stranded_islands
    if near:
        cx, cy, r = (float(v) for v in near)

        def seg_dist(px, py, ax, ay, bx, by):
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                return math.hypot(px - ax, py - ay)
            k = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            return math.hypot(px - (ax + k * dx), py - (ay + k * dy))

        obstacles = {ln: [] for ln in layers}
        for t in board.GetTracks():
            if t.GetNetname() == net:
                continue
            if isinstance(t, p.PCB_VIA):
                for ln in layers:
                    obstacles[ln].append(('via', p.ToMM(t.GetPosition().x), p.ToMM(t.GetPosition().y), 0, 0, p.ToMM(t.GetWidth(t.TopLayer())) / 2))
            else:
                ln = board.GetLayerName(t.GetLayer())
                if ln in obstacles:
                    s, e = t.GetStart(), t.GetEnd()
                    obstacles[ln].append(('trk', p.ToMM(s.x), p.ToMM(s.y), p.ToMM(e.x), p.ToMM(e.y), p.ToMM(t.GetWidth()) / 2))
        for f in board.GetFootprints():
            for pad in f.Pads():
                if pad.GetNetname() == net:
                    continue
                for ln in layers:
                    if pad.IsOnLayer(board.GetLayerID(ln)):
                        obstacles[ln].append(('pad', p.ToMM(pad.GetPosition().x), p.ToMM(pad.GetPosition().y), 0, 0,
                                              math.hypot(p.ToMM(pad.GetSize().x), p.ToMM(pad.GetSize().y)) / 2))
        bb = board.GetBoardEdgesBoundingBox()
        ex0, ey0, ex1, ey1 = p.ToMM(bb.GetLeft()), p.ToMM(bb.GetTop()), p.ToMM(bb.GetRight()), p.ToMM(bb.GetBottom())
        sites = []
        n = int(2 * r / 0.05)
        for i in range(n + 1):
            for j in range(n + 1):
                x, y = cx - r + i * 0.05, cy - r + j * 0.05
                d = min(x - ex0, ex1 - x, y - ey0, ey1 - y) - 0.3
                for ln, obs in obstacles.items():
                    for kind, ax, ay, bx, by, rad in obs:
                        dd = (math.hypot(x - ax, y - ay) if kind != 'trk' else seg_dist(x, y, ax, ay, bx, by)) - rad - 0.3
                        if dd < d:
                            d = dd
                sites.append((round(d, 3), round(x, 3), round(y, 3)))
        sites.sort(reverse=True)
        result['sites'] = [{'clearanceMm': d, 'x': x, 'y': y, 'islandF': island_of('F.Cu', x, y), 'islandB': island_of('B.Cu', x, y)}
                           for d, x, y in sites[:25]]
    return result


def pads(board):
    """Every footprint with its pads in absolute board coordinates (mm, y down) and their nets.

    The host-side measurements (`kicadpy.verify power`) need real positions; the .kicad_pcb text
    holds pads in the footprint's own frame, and getting the rotation convention wrong by a sign
    would silently mis-measure every distance. pcbnew already knows.
    """
    out = []
    for f in board.GetFootprints():
        entry = {'ref': f.GetReference(), 'value': f.GetValue(), 'lib': f.GetFPIDAsString(),
                 'x': p.ToMM(f.GetPosition().x), 'y': p.ToMM(f.GetPosition().y),
                 'rotationDeg': f.GetOrientationDegrees(), 'pads': []}
        for pad in f.Pads():
            layers = [board.GetLayerName(board.GetLayerID(ln)) for ln in ('F.Cu', 'B.Cu') if pad.IsOnLayer(board.GetLayerID(ln))]
            entry['pads'].append({'number': pad.GetNumber(), 'net': pad.GetNetname(),
                                  'x': p.ToMM(pad.GetPosition().x), 'y': p.ToMM(pad.GetPosition().y),
                                  'layers': layers, 'through': pad.GetAttribute() == p.PAD_ATTRIB_PTH})
        out.append(entry)
    return {'footprints': out}


def copper_area(board, ref, pad_number):
    """The filled copper a pad actually sits on: the zone island under it, per layer, and the vias of
    its net inside that island. 0 mm2 means the "heatsink pour" a report describes does not exist.
    """
    target = None
    for f in board.GetFootprints():
        if f.GetReference() == ref:
            for pad in f.Pads():
                if pad.GetNumber() == pad_number:
                    target = pad
    if target is None:
        raise ValueError(f'{ref}.{pad_number}: no such pad')
    net = target.GetNetname()
    pt = target.GetPosition()
    result = {'ref': ref, 'pad': pad_number, 'net': net, 'layers': {}}
    for ln in ('F.Cu', 'B.Cu'):
        lid = board.GetLayerID(ln)
        if not target.IsOnLayer(lid):
            continue
        found = None
        for z in board.Zones():
            if z.GetNetname() != net or not z.IsOnLayer(lid):
                continue
            ps = z.GetFilledPolysList(lid)
            for i in range(ps.OutlineCount()):
                if ps.Contains(pt, i):
                    area = p.ToMM(p.ToMM(ps.Outline(i).Area()))
                    vias = sum(1 for t in board.GetTracks() if isinstance(t, p.PCB_VIA) and t.GetNetname() == net
                               and ps.Contains(t.GetPosition(), i))
                    found = {'areaMm2': round(area, 3), 'viasOnIsland': vias}
        result['layers'][ln] = found or {'areaMm2': 0.0, 'viasOnIsland': 0}
    return result


def main(req):
    if req['operation'] == 'version':
        return {'pcbnew': p.GetBuildVersion()}
    path = Path(req['pcb']).absolute()
    settings = p.GetSettingsManager()
    pro = str(path.with_suffix('.kicad_pro'))
    settings.LoadProject(pro)
    board = p.LoadBoard(str(path))
    board.SetProject(settings.GetProject(pro))
    if not p.GetBuildVersion().startswith('10.'):
        raise ValueError('spike requires KiCad 10')
    op = req['operation']
    if op == 'inspect':
        return inspect(board)
    if op == 'islands':
        return islands(board, req.get('net') or 'GND', req.get('near'))
    if op == 'pads':
        return pads(board)
    if op == 'copper_area':
        return copper_area(board, req['ref'], req['pad'])
    if op in ('route_export', 'route_import'):
        scope = req['scope']
        original = {uid(t): t for t in board.GetTracks()}
        selected = set(scope['uuids'])
        region = scope['regionMm']
        def inside_route(t):
            bb = t.GetBoundingBox()
            return (p.ToMM(bb.GetX()) >= region[0] and p.ToMM(bb.GetY()) >= region[1]
                    and p.ToMM(bb.GetRight()) <= region[2] and p.ToMM(bb.GetBottom()) <= region[3])
        if not selected or not selected <= set(original):
            raise ValueError('unknown routing UUIDs')
        for key in selected:
            t = original[key]
            if t.IsLocked() or t.GetNetname() not in scope['nets'] or not inside_route(t):
                raise ValueError('routing scope violation')
        if op == 'route_export':
            for key, t in original.items():
                if key in selected:
                    board.Remove(t)
                else:
                    t.SetLocked(True)
            if not p.ExportSpecctraDSN(board, req['output']):
                raise ValueError('DSN export failed')
            return {'ok': True}
        def signature(t):
            data = copper(t)
            data.pop('uuid'); data.pop('locked')
            # Segment orientation is irrelevant to preservation.
            if data['kind'] == 'track':
                data['start'], data['end'] = sorted([data['start'], data['end']])
            return json.dumps(data, sort_keys=True)
        preserved_board = p.LoadBoard(str(path))
        preserved = {uid(t): t for t in preserved_board.GetTracks()}
        protected = {}
        for key, t in original.items():
            if key not in selected:
                protected.setdefault(signature(t), []).append((key, t.IsLocked()))
        if not p.ImportSpecctraSES(board, req['input']):
            raise ValueError('SES import failed')
        created = []
        for t in board.GetTracks():
            matches = protected.get(signature(t), [])
            if matches:
                key, locked = matches.pop()
                t.m_Uuid = p.KIID(key)
                t.SetLocked(locked)
            else:
                if t.GetNetname() not in scope['nets'] or not inside_route(t):
                    raise ValueError('router changed copper outside scope: net=%s startMm=%s endMm=%s allowedNets=%s regionMm=%s; inspect the proposed geometry and adjust local scope or placement, not the guard' % (t.GetNetname(), point(t.GetStart()), point(t.GetEnd()), scope['nets'], region))
                t.SetLocked(False)
                created.append(uid(t))
        # Native SES omits protected wiring, while KiCad's importer clears
        # existing tracks. Restore omitted items from the untouched board,
        # never by re-creating approximate geometry from the router result.
        for matches in protected.values():
            for key, locked in matches:
                item = preserved[key]
                preserved_board.Remove(item)
                item.SetParent(board)
                board.Add(item)

        p.SaveBoard(str(path), board)
        return {'touched': sorted(selected), 'created': created}
    if op != 'apply':
        raise ValueError('unknown worker operation')
    tracks = {uid(t): t for t in board.GetTracks()}
    scope = req['scope']
    region = scope['regionMm']
    if len(region) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in region) or region[0] >= region[2] or region[1] >= region[3]:
        raise ValueError('invalid regionMm')
    allowed = set(scope['uuids'])
    touched, created = set(), []

    def inside(item):
        box = item.GetBoundingBox()
        return (p.ToMM(box.GetX()) >= region[0] and p.ToMM(box.GetY()) >= region[1]
                and p.ToMM(box.GetRight()) <= region[2] and p.ToMM(box.GetBottom()) <= region[3])

    for edit in req['edits']:
        key = edit['uuid']
        if key not in allowed or key not in tracks or key in touched:
            raise ValueError('unknown, repeated or out-of-scope UUID')
        t = tracks[key]
        if t.IsLocked() or t.GetNetname() not in scope['nets'] or not inside(t):
            raise ValueError('locked or out-of-scope copper')
        if edit['op'] == 'set_width' and not isinstance(t, p.PCB_VIA):
            width = edit['widthMm']
            if not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0:
                raise ValueError('invalid widthMm')
            t.SetWidth(p.FromMM(width))
            if not inside(t):
                raise ValueError('new width leaves allowed region')
            touched.add(key)
            continue
        if isinstance(t, (p.PCB_VIA, p.PCB_ARC)) or edit['op'] != 'replace_track':
            raise ValueError('spike supports replace_track on straight tracks only')
        pts = [vector(xy) for xy in edit['points']]
        if len(pts) < 2 or pts[0] != t.GetStart() or pts[-1] != t.GetEnd():
            raise ValueError('replacement must preserve both endpoints')
        for a, b in zip(pts, pts[1:]):
            if a == b:
                raise ValueError('zero-length segment')
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            segment = t if i == 0 else p.PCB_TRACK(board)
            segment.SetStart(a)
            segment.SetEnd(b)
            if i:
                segment.SetWidth(t.GetWidth())
                segment.SetLayer(t.GetLayer())
                segment.SetNet(t.GetNet())
                board.Add(segment)
                created.append(uid(segment))
            if not inside(segment):
                raise ValueError('replacement leaves allowed region')
        touched.add(key)
    p.SaveBoard(str(path), board)
    return {'touched': sorted(touched), 'created': created}


if __name__ == '__main__':
    print('KICADPY: ' + json.dumps(main(json.load(sys.stdin))))
    # pcbnew's SWIG objects segfault in Py_FinalizeEx (GC visit_decref) once the
    # work is done: 17 "Python quit unexpectedly" reports in eight minutes on
    # 2026-09-16, every one after the response line was already printed, and
    # every one read by toolchain.worker as a failed call. Skip finalization.
    sys.stdout.flush()
    os._exit(0)
