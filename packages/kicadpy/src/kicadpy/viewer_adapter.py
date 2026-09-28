"""Versioned read model for the future viewer, never a writable source file."""


def board_view(native):
    return {
        'schemaVersion': 1,
        'engine': 'kicad-native',
        'revision': native['revision'],
        'coordinates': native['coordinateSystem'],
        'boundsMm': native['boardBoundsMm'],
        'footprints': native['footprints'],
        'copper': native['copper'],
        'zones': native['zones'],
        'nets': native['nets'],
        'capabilities': {
            'copperInspection': True,
            'replaceTrack': True,
            'setWidth': True,
            'moveFootprint': False,
            'schematicCanvas': False,
            'zonePolygonRendering': False,
        },
        'writeContract': 'UUID + expected revision + scoped operation to kicadpy; never write this JSON back',
    }
