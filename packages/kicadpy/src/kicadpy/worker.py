"""KiCad Python 3.9 compatible subprocess. No imports from host packages."""
import json
import math
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
    bbox = board.GetBoardEdgesBoundingBox()
    return {'kicad': p.GetBuildVersion(), 'coordinateSystem': 'KiCad absolute mm, y down; layers unmirrored',
            'thicknessMm': p.ToMM(board.GetDesignSettings().GetBoardThickness()),
            'copperLayers': [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()],
            'boardBoundsMm': [p.ToMM(bbox.GetX()), p.ToMM(bbox.GetY()), p.ToMM(bbox.GetRight()), p.ToMM(bbox.GetBottom())],
            'nets': sorted(n.GetNetname() for n in board.GetNetInfo().NetsByName().values()),
            'footprints': footprints, 'copper': [copper(t) for t in board.GetTracks()],
            'zones': [{'uuid': uid(z), 'net': z.GetNetname(), 'layer': z.GetLayerName()} for z in board.Zones()],
            'coverage': {'schematicGraphics': False, 'nativeGeometry': True, 'viewerIntegration': False}}


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
                    raise ValueError('router changed copper outside scope')
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
