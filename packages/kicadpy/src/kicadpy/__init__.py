"""Native KiCad repair spike; no fabrication readiness is implied."""
from pathlib import Path as _Path

# The checker is the tile's. On 2026-09-30 an agent copied this package into its workspace and
# patched two files; `python -m kicadpy` run from that workspace then imported the copy, so every
# gate number it quoted came from a checker only it had read. A copy that sits inside a board
# workspace (its parent holds `project.json`, the marker Harness lays down) refuses to import.
_here = _Path(__file__).resolve().parent
if (_here.parent / 'project.json').is_file() or (_here.parent.parent / 'project.json').is_file():
    raise ImportError(
        f'kicadpy at {_here} sits inside a board workspace. Run the tile\'s copy — '
        '"$KICAD_HARNESS_PYTHON" -m kicadpy… from a directory without a kicadpy/ of its own — '
        'and report a tool that is wrong instead of forking it (AGENTS.md, "The checker is the tile\'s").')
del _here
